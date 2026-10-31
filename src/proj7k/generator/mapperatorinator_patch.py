"""
Our changes to Mapperatorinator's pinned source (ADR-0040, ADR-0045), applied by `mapperatorinator.patch`.

Fast mode writes the song as `parallel_chains` runs of windows side by side: each run goes in order, every
window reading the notes before it (as its sequential mode), and all runs take a step together in one batch.
Its own parallel mode writes every window with no notes before it, so the patterns broke every 8 seconds
(meow, 2026-10-26: chord size jumped 0.75–0.87 at the seams against ~0.55 elsewhere).

Its postprocessor kept one open hold note for all columns, so overlapping long notes (chords, long-note
streams) collapsed into one: a second start overwrote the first and the extra ends were dropped (beachcomber,
2026-10-29: Exterminate [FLN] 3,064 notes written, 1,037 in the .osu, 2.6★; with one open hold per column
3,035 notes, 7.5★). Holds are now paired by column (ADR-0048).

On long-note songs some windows ended with EOS a token or two in, and an empty window leaves the next one an empty
lookback, so the song went silent for 30–70 s at a stretch (8–30% of its seconds). A window that comes back with no
notes is drawn again, up to `empty_retries` times; a draw that is still empty costs a few tokens.
"""

#: Marks a checkout patched with this version; a checkout with another version is restored and patched again.
MARK = "proj7k patch v5"

CHAINS_METHOD = '''    # {MARK}
    def generate_chains(
            self,
            *,
            sequences: tuple[torch.Tensor, torch.Tensor, float],
            in_context: list[dict[str, Any]],
            out_context: list[dict[str, Any]],
            model_kwargs: dict[str, Any],
            req_special_tokens: list[str],
            verbose: bool = True,
    ):
        """proj7k: the windows cut into `parallel_chains` runs, each written in order with the notes before it as
        context (as generate_sequential), every run a step at a time in one batch."""
        song_length = sequences[2]
        all_frames = self.prepare_frames(sequences[0])
        frame_times = sequences[1]
        n_windows = all_frames.shape[0]
        k = max(1, min(self.parallel_chains, n_windows))
        bounds = [round(c * n_windows / k) for c in range(k + 1)]
        chains = [list(range(bounds[c], bounds[c + 1])) for c in range(k) if bounds[c + 1] > bounds[c]]

        cond_kwargs = {key: v for key, v in model_kwargs.items()
                       if key in ("beatmap_idx", "difficulty", "mapper_idx") and isinstance(v, torch.Tensor)}
        if self.do_song_position_embed:
            starts = (frame_times / song_length).to(torch.float32)
            ends = ((frame_times + self.miliseconds_per_sequence) / song_length).to(torch.float32)
            song_positions = torch.stack([starts, ends], dim=1)
        else:
            song_positions = None
        if isinstance(self.model, InferenceClient):
            precompute_kwargs = {key: v.expand(n_windows).contiguous() for key, v in cond_kwargs.items()}
            precompute_kwargs["inputs"] = all_frames
            if song_positions is not None:
                precompute_kwargs["song_position"] = song_positions
            enc_hidden = self.model.precompute_encoder(precompute_kwargs)
        else:
            with torch.no_grad():
                enc_hidden = precompute_encoder_outputs(
                    self.model, all_frames, cond_kwargs, song_positions,
                    batch_size=self.max_batch_size,
                )

        for i, context in enumerate(out_context):
            if context["finished"]:
                continue
            # Each run keeps its own events, so a run's context holds only its own notes, in time order.
            given = len(context["events"])
            runs = [dict(context, events=list(context["events"]), event_times=list(context["event_times"]))
                    for _ in chains]
            steps = max(len(c) for c in chains)
            iterator = tqdm(range(steps), dynamic_ncols=True) if verbose else range(steps)
            for step in iterator:
                groups: dict[tuple[bool, bool], list[tuple[int, int]]] = {}
                for c, chain in enumerate(chains):
                    if step < len(chain):
                        wi = chain[step]
                        # A run's first window writes from its start, as the song's first window does; a model
                        # held out of an empty lookback writes the same note over and over. What it writes before
                        # its lookback is the run before's ground: context for this run, dropped at the end.
                        flags = (step != 0 and self.lookback_time > 0, wi != n_windows - 1)
                        groups.setdefault(flags, []).append((c, wi))
                for (trim_lookback, trim_lookahead), members in groups.items():
                    for start in range(0, len(members), self.max_batch_size):
                        batch = members[start:start + self.max_batch_size]
                        conds, unconds = [], []
                        for c, wi in batch:
                            frame_time = frame_times[wi].item()
                            cond, uncond = self.get_prompts(
                                self.prepare_context_sequences(in_context, frame_time, False, req_special_tokens),
                                self.prepare_context_sequences(out_context[:i] + [runs[c]], frame_time, True, req_special_tokens),
                            )
                            conds.append(cond)
                            unconds.append(uncond)
                        prompt, uncond_prompt, max_len = self.stack_prompts(conds, unconds)
                        n = len(batch)
                        kwargs = {key: (v.expand(n, *v.shape[1:]) if isinstance(v, torch.Tensor) and v.dim() > 0 and v.shape[0] == 1 else v)
                                  for key, v in model_kwargs.items()}
                        result, generation_stats = self.model_generate(
                            kwargs | dict(
                                encoder_outputs=enc_hidden[[wi for _, wi in batch]],
                                decoder_input_ids=prompt,
                                decoder_attention_mask=prompt.ne(self.tokenizer.pad_id),
                                negative_prompt=uncond_prompt,
                                negative_prompt_attention_mask=uncond_prompt.ne(self.tokenizer.pad_id) if uncond_prompt is not None else None,
                            ),
                            lookback_time=self.lookback_time if trim_lookback else 0,
                            lookahead_time=self.lookahead_time if trim_lookahead else 0,
                            context_type=context["context_type"].value,
                        )
                        self._record_generation_stats(generation_stats)
                        for b, (c, wi) in enumerate(batch):
                            self.add_predicted_tokens_to_context(runs[c], result[b, max_len:].cpu(), frame_times[wi].item(),
                                                                 trim_lookback, trim_lookahead)
            events, times = context["events"][:given], context["event_times"][:given]
            for c, run in enumerate(runs):
                own = frame_times[chains[c][0]].item() + (self.lookback_time if c else 0)
                for e, t in zip(run["events"][given:], run["event_times"][given:]):
                    if t >= own:
                        events.append(e)
                        times.append(t)
            context["events"], context["event_times"] = events, times
'''

#: Fewer generated tokens than this (EOS and padding aside) is a window with no note: one note takes six or more.
EMPTY_TOKENS = 4

RETRY_METHOD = '''    def model_generate(self, model_kwargs, **generate_kwargs: Any) -> Any:
        """proj7k: draws a window that came back with no notes again, up to `empty_retries` times."""
        result, stats = self._model_generate_once(model_kwargs, **generate_kwargs)
        prompt = model_kwargs.get("decoder_input_ids")
        if self.empty_retries <= 0 or not isinstance(result, torch.Tensor) or not isinstance(prompt, torch.Tensor):
            return result, stats
        n, start = result.shape[0], prompt.shape[1]
        special = torch.tensor([self.tokenizer.pad_id, self.tokenizer.eos_id, *self.tokenizer.context_eos.values()],
                               device=result.device)
        for _ in range(self.empty_retries):
            todo = ((~torch.isin(result[:, start:], special)).sum(dim=1) < {EMPTY}).nonzero(as_tuple=True)[0]
            if len(todo) == 0:
                break
            sub = model_kwargs if len(todo) == n else {
                key: (v[todo] if isinstance(v, torch.Tensor) and v.dim() > 0 and v.shape[0] == n else v)
                for key, v in model_kwargs.items()}
            again, stats = self._model_generate_once(sub, **generate_kwargs)
            width = max(result.shape[1], again.shape[1])
            result = F.pad(result, (0, width - result.shape[1]), value=self.tokenizer.pad_id)
            result[todo] = F.pad(again, (0, width - again.shape[1]), value=self.tokenizer.pad_id).to(result.device)
        return result, stats

    def _model_generate_once(self, model_kwargs, **generate_kwargs: Any) -> Any:'''

#: (file, line it replaces, new lines). Each line must occur once in the pinned file.
PATCHES = (
    ("config.py", "    parallel: bool = False  # Use parallel sampling", [
        "    parallel: bool = False  # Use parallel sampling",
        "    parallel_chains: int = 0  # proj7k: write the windows as this many runs side by side (0: off)",
        "    empty_retries: int = 0  # proj7k: draw a window that comes back with no notes again, this many times",
    ]),
    ("osuT5/osuT5/inference/processor.py", "        self.parallel = args.parallel", [
        "        self.parallel = args.parallel",
        "        self.parallel_chains = int(getattr(args, 'parallel_chains', 0) or 0)",
        "        self.empty_retries = int(getattr(args, 'empty_retries', 0) or 0)",
    ]),
    ("osuT5/osuT5/inference/processor.py", "    def model_generate(self, model_kwargs, **generate_kwargs: Any) -> Any:",
     RETRY_METHOD.replace("{EMPTY}", str(EMPTY_TOKENS)).split("\n")),
    ("osuT5/osuT5/inference/processor.py",
     "        generate_func = self.generate_parallel if self.parallel else self.generate_sequential", [
        "        generate_func = self.generate_parallel if self.parallel else self.generate_sequential",
        "        if self.parallel_chains > 1 and not self.parallel:",
        "            generate_func = self.generate_chains",
    ]),
    ("osuT5/osuT5/inference/processor.py", "    def generate_parallel(",
     CHAINS_METHOD.replace("{MARK}", MARK).split("\n") + ["    def generate_parallel("]),
    # one open hold note per column (keyed by the column's x), not one for the whole chart
    ("osuT5/osuT5/inference/postprocessor.py", "        hold_note_start = None", [
        "        hold_note_start = None",
        "        hold_starts = {}  # proj7k: the open hold note of each column",
    ]),
    ("osuT5/osuT5/inference/postprocessor.py", "            elif hit_type == EventType.HOLD_NOTE:", [
        "            elif hit_type == EventType.HOLD_NOTE:",
        "                hold_note_start = hold_starts.get(int(round(group.x)))",
    ]),
    ("osuT5/osuT5/inference/postprocessor.py", "                hold_note_start = group", [
        "                hold_starts[int(round(group.x))] = group",
    ]),
    ("osuT5/osuT5/inference/postprocessor.py",
     "            elif hit_type == EventType.HOLD_NOTE_END and hold_note_start is not None:", [
        "            elif hit_type == EventType.HOLD_NOTE_END and int(round(group.x)) in hold_starts:",
        "                hold_note_start = hold_starts.pop(int(round(group.x)))",
    ]),
)
