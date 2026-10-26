"""
Our changes to Mapperatorinator's pinned source (ADR-0040, ADR-0045), applied by `mapperatorinator.patch`.

Fast mode writes the song as `parallel_chains` runs of windows side by side: each run goes in order, every
window reading the notes before it (as its sequential mode), and all runs take a step together in one batch.
Its own parallel mode writes every window with no notes before it, so the patterns broke every 8 seconds
(meow, 2026-10-26: chord size jumped 0.75–0.87 at the seams against ~0.55 elsewhere).
"""

#: Marks a checkout patched with this version; a checkout with another version is restored and patched again.
MARK = "proj7k patch v2"

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
                        # A run after the first starts where the run before it stops writing (its last window
                        # writes up to its lookahead), so it too leaves its first window's lookback alone.
                        flags = ((c, step) != (0, 0) and self.lookback_time > 0, wi != n_windows - 1)
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
            context["events"] = context["events"][:given] + [e for run in runs for e in run["events"][given:]]
            context["event_times"] = context["event_times"][:given] + [t for run in runs for t in run["event_times"][given:]]
'''

#: (file, line it replaces, new lines). Each line must occur once in the pinned file.
PATCHES = (
    ("config.py", "    parallel: bool = False  # Use parallel sampling", [
        "    parallel: bool = False  # Use parallel sampling",
        "    parallel_chains: int = 0  # proj7k: write the windows as this many runs side by side (0: off)",
    ]),
    ("osuT5/osuT5/inference/processor.py", "        self.parallel = args.parallel", [
        "        self.parallel = args.parallel",
        "        self.parallel_chains = int(getattr(args, 'parallel_chains', 0) or 0)",
    ]),
    ("osuT5/osuT5/inference/processor.py",
     "        generate_func = self.generate_parallel if self.parallel else self.generate_sequential", [
        "        generate_func = self.generate_parallel if self.parallel else self.generate_sequential",
        "        if self.parallel_chains > 1 and not self.parallel:",
        "            generate_func = self.generate_chains",
    ]),
    ("osuT5/osuT5/inference/processor.py", "    def generate_parallel(",
     CHAINS_METHOD.replace("{MARK}", MARK).split("\n") + ["    def generate_parallel("]),
)
