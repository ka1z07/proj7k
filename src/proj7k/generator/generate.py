"""
From audio to a chart at a target star rating (ADR-0027).

With the rhythm model (`rhythm`, ADR-0032; it ships with the package) the rows come from a model of
charts people wrote to the same audio, and the knob is the density, log2 rows per second; the rule
below (onset strength and section energy) is what is used when the model file is missing.

    audio ─ onset envelope ─┬─ beat grid (timing given, or estimated) ─┬─ sections ─ pick points ─ alike measures, one rhythm ─ keys ─ engine
                            └─ tempo estimate (when no timing is given)  └───────────────┘    ▲                                       │
                                                                                              └──────────────── intensity ────────────┘

One knob, the *intensity* x, sets how much of the grid becomes notes and how wide the chords are.
The song is cut into sections by how its sound changes, each with an energy (`structure`, ADR-0031);
each section takes its strongest grid points (onset strength plus place in the measure), as many as
x and its energy allow, so a loud part is denser than a quiet one; past the point where the loudest
part is full, x widens the chords. The engine's total star rating rises
with x, so x is bisected until the chart lands within the tolerance of the target, or the knob runs
out (the song is too sparse to reach the target, or too busy to go under it), which is reported.

The keys each row presses are drawn from a model of charts people wrote (`patterns`, ADR-0030), and
measures that sound alike are given one rhythm (`phrases`), so the chart repeats where the music
does. The seed fixes the draw; the same seed gives the same chart at every intensity tried, up to the
rows that intensity adds or removes (and, when the target falls between two steps of the rating, a few
other draws derived from the seed are tried).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from proj7k.engine import DifficultyProfile, evaluate_notes
from proj7k.generator.audio import Audio, load_audio
from proj7k.generator import holds, rhythm, stems as stems_mod
from proj7k.generator.grid import BEAT, DOWNBEAT, HALF, SILENCE_DB, GridPoint, build_grid, onset_shift_ms
from proj7k.generator.onset import onset_envelope
from proj7k.generator.patterns import PatternSampler, default_model, mask_columns
from proj7k.generator.phrases import group_measures, harmonize
from proj7k.generator.structure import find_sections, section_energy, section_styles
from proj7k.generator.tempo import TempoEstimate, estimate_tempo
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint

GENERATOR_VERSION = "gen-4"
LEVEL_BONUS = {DOWNBEAT: 0.30, BEAT: 0.18, HALF: 0.06}
MIN_STRENGTH = 0.06
X_MIN, X_MAX = 0.0, 3.5
#: The song's quietest section gets this share of the notes per grid point its loudest gets (ADR-0031).
QUIET_SHARE = 0.45
#: Grid points with almost no onset are filled into a stream only in sections at least this loud.
FILL_ENERGY = 0.6
#: Past this intensity the quieter sections fill up too (and their weak points open), reaching the
#: loudest section's density at X_MAX, and chords widen faster: the contrast gives way so a hard
#: target can still be reached.
SPREAD_FROM = 2.5
MAX_ITERATIONS = 18
#: Other draws of the keys tried when the bisection ends outside the tolerance.
KEY_RETRIES = 8


@dataclass
class GeneratorOptions:
    target_stars: float
    target_label: str = ""                    # how the target was asked for: "5th" or "5.20★"
    timing: Optional[List[TimingPoint]] = None  # from a reference chart; wins over bpm/offset
    bpm: Optional[float] = None
    offset_ms: Optional[float] = None
    seed: int = 0
    tolerance: float = 0.10                   # stars
    title: str = ""
    artist: str = ""
    creator: str = "proj7k"
    version: Optional[str] = None
    overall_difficulty: float = 8.0
    reference: Optional[Beatmap7K] = None     # copy its metadata, background and General section
    stems: bool = False                       # split the song (Demucs) and follow a lead layer per section (ADR-0033)
    leads: Optional[Dict[int, str]] = None    # a section's first bar -> its lead stem, overriding the automatic choice
    separator: Optional[Callable] = None      # stands in for Demucs (tests)
    ln_ratio: float = 0.0                     # the chart's share of long notes, spread over sections by how the sound rings
    section_holds: Optional[Dict[int, Tuple[float, str]]] = None  # a section's first bar -> (share, kind), overriding
    whole_inverse: bool = False               # every section written as inverse long notes
    log: Optional[Callable[[str], None]] = None


@dataclass
class GenerationResult:
    beatmap: Beatmap7K
    profile: DifficultyProfile
    target_stars: float
    intensity: float
    timing_source: str                        # "reference", "manual" or "auto"
    onset_shift_ms: float                     # how much later the music's onsets sit than the timing's beats
    tempo: Optional[TempoEstimate]
    iterations: int
    warnings: List[str] = field(default_factory=list)
    leads: List[stems_mod.Lead] = field(default_factory=list)  # each section's lead layer, with stems
    holds: List[holds.SectionHolds] = field(default_factory=list)  # each section's long notes

    @property
    def stars(self) -> float:
        return float(self.profile.total_stars)

    @property
    def n_notes(self) -> int:
        return len(self.beatmap.hit_objects)


def generate_from_file(audio_path: Union[str, Path], options: GeneratorOptions) -> GenerationResult:
    audio_path = Path(audio_path)
    result = generate(load_audio(audio_path), options)
    if not (options.reference and options.reference.audio_filename):
        result.beatmap.audio_filename = audio_path.name
    return result


def generate(audio: Audio, options: GeneratorOptions) -> GenerationResult:
    env = onset_envelope(audio)
    warnings: List[str] = []
    tempo: Optional[TempoEstimate] = None
    if options.timing:
        timing, source = list(options.timing), "reference"
    elif options.bpm:
        offset = options.offset_ms if options.offset_ms is not None else 0.0
        timing, source = [TimingPoint(time=float(offset), beat_length=60000.0 / options.bpm)], "manual"
    else:
        tempo = estimate_tempo(env)
        offset = options.offset_ms if options.offset_ms is not None else tempo.offset_ms
        timing, source = [TimingPoint(time=float(offset), beat_length=tempo.beat_ms)], "auto"
        if not tempo.steady:
            warnings.append(
                f"自动测得 {tempo.bpm:g} BPM，但歌曲前后段的拍点相差 {tempo.drift_ms:g} ms，可能有变速；"
                "节奏不对时请用 --timing-from 借用已有谱面的 timing，或用 --bpm/--offset 指定。"
            )

    # A chart's beats sit a little before the envelope's reading of them (tempo.CHART_SYNC_MS for an
    # estimated timing; whatever it is for a borrowed or typed-in one): read the music through the shift
    # that lines its onsets up with the beats, and keep writing notes on the beats themselves.
    shift = onset_shift_ms(env, timing)
    model = rhythm.default_model()
    leads: List[stems_mod.Lead] = []
    if options.stems and model is None:
        warnings.append("分轨需要节奏模型（rhythm_model.json.gz），这次没有分轨。")
    env_read = env
    if model is not None:
        places = rhythm.places(timing, env.duration_s * 1000.0)
        feats = rhythm.features(env, places, shift)
        grid = rhythm.grid_points(places, feats, SILENCE_DB)
        if options.stems:
            leads, stem_envs = _follow_leads(audio, env, grid, options)
            env_read = stems_mod.blend(env, stem_envs, leads, stems_mod.bar_spans(grid))
            feats = rhythm.features(env_read, places, shift)
            grid = rhythm.grid_points(places, feats, SILENCE_DB)
        same_draw = _same_draw(grid, group_measures(grid, runs=False))
        lo, hi = rhythm.D_MIN, rhythm.D_MAX + rhythm.CHORD_RANGE
    else:
        grid = build_grid(env, timing, shift_ms=shift)
        lo, hi = X_MIN, X_MAX
    groups = group_measures(grid)
    sections = find_sections(env, grid)
    energy = section_energy(grid, sections)
    styles = section_styles(grid, sections, options.seed)
    hold_model = holds.default_model()
    hold_plan: List[holds.SectionHolds] = []
    if model is not None and (options.ln_ratio > 0 or options.section_holds or options.whole_inverse):
        if hold_model is None:
            warnings.append("没有长条模型（hold_model.json.gz），这次只出米键。")
        else:
            sustain = holds.sustain_by_section(env, sections, stems_mod.bar_spans(grid))
            override = dict(options.section_holds or {})
            if options.whole_inverse:
                override = {s.first_bar: (1.0, "inverse") for s in sections}
            hold_plan = holds.plan(sections, options.ln_ratio, sustain, override)
            if not any(h.ratio > 0 or h.kind == "inverse" for h in hold_plan):
                hold_plan = []
    if hold_plan:
        hold_rng = np.random.default_rng([options.seed, 7])
        hold_u = hold_rng.random((len(grid), 7))[same_draw]
        hold_v = hold_rng.random((len(grid), 7))[same_draw]
        place_at = {int(round(p.time_ms)): i for i, p in enumerate(grid)}
    x_top = hi
    best: Optional[Tuple[float, List[HitObject], DifficultyProfile]] = None
    iterations = 0

    def attempt(x: float, retry: int = 0) -> Tuple[List[HitObject], DifficultyProfile]:
        seed = options.seed if retry == 0 else [options.seed, retry]
        rng = np.random.default_rng(seed)
        draws = dict(zip((p.time_ms for p in grid), rng.random(len(grid))))
        if model is not None:
            # Up to D_MAX the knob is the density; past it, the rows stay and the chords widen.
            d = min(x, rhythm.D_MAX)
            feats[:, -1] = d
            u, v = rng.random(len(grid)), rng.random(len(grid))
            # Alike measures share their draws place by place, so where the model rates them alike they
            # get the same rhythm, wherever in the song they come back.
            u, v = u[same_draw], v[same_draw]
            sizes = rhythm.pick(model.predict(feats), places, u, v,
                                np.array([p.silent for p in grid]), rows_per_s=2.0 ** d,
                                chords=max(0.0, x - rhythm.D_MAX))
            sizes = harmonize(grid, groups, list(sizes))
            picked = [(p, int(n)) for p, n in zip(grid, sizes) if n > 0]
        else:
            picked = select_rows(grid, x, groups, energy)
        objs = _chart(picked, styles, seed, draws)
        if not objs:
            return objs, evaluate_notes([])
        if hold_plan:
            objs = _with_holds(objs, hold_plan, hold_model, feats, places, grid, place_at, env_read, shift,
                               hold_u, hold_v)
        return objs, evaluate_notes([(o.column, o.time / 1000.0, o.end_time / 1000.0 if o.note_type == NoteType.LN else None)
                                     for o in objs])

    for iterations in range(1, MAX_ITERATIONS + 1):
        x = (lo + hi) / 2.0
        objs, prof = attempt(x)
        err = prof.total_stars - options.target_stars
        if best is None or abs(err) < abs(best[2].total_stars - options.target_stars):
            best = (x, objs, prof)
        if abs(err) <= options.tolerance:
            break
        if err > 0:
            hi = x
        else:
            lo = x
    assert best is not None
    # Alike measures change their rhythm together, so the star rating climbs with the intensity in
    # steps, and a step can jump over the target. The keys move the rating too (a jack, a trill): when
    # the bisection ends outside the tolerance, other draws of the keys on either side of the step
    # usually land inside it.
    for retry in range(1, KEY_RETRIES + 1):
        if abs(best[2].total_stars - options.target_stars) <= options.tolerance:
            break
        for xv in (lo, hi):
            objs, prof = attempt(xv, retry)
            if abs(prof.total_stars - options.target_stars) < abs(best[2].total_stars - options.target_stars):
                best = (xv, objs, prof)
    x, objs, prof = best
    if abs(prof.total_stars - options.target_stars) > options.tolerance:
        top_objs, top = attempt(x_top)
        if top.total_stars < options.target_stars - options.tolerance:
            x, objs, prof = x_top, top_objs, top
            warnings.append(
                f"这首歌的起音不够密，最高只能生成 {top.total_stars:.2f}★（目标 {options.target_stars:.2f}★）。"
            )
        else:
            warnings.append(
                f"没能落到目标 ±{options.tolerance:.2f}★ 以内，最接近的是 {prof.total_stars:.2f}★。"
            )
    if len(objs) < 5:
        raise ValueError("生成的谱面少于 5 个音符：音频里几乎找不到起音，或者 timing 与音乐对不上。")

    beatmap = _beatmap(objs, timing, options, prof.total_stars)
    return GenerationResult(beatmap=beatmap, profile=prof, target_stars=options.target_stars, intensity=x,
                            timing_source=source, onset_shift_ms=shift, tempo=tempo, iterations=iterations, warnings=warnings,
                            leads=leads, holds=hold_plan)


def _with_holds(objs: List[HitObject], plan: Sequence[holds.SectionHolds], model, feats: np.ndarray,
                places, grid: Sequence[GridPoint], place_at: dict, env, shift: float,
                hold_u: np.ndarray, hold_v: np.ndarray) -> List[HitObject]:
    """The chart's taps with the planned long notes made (ADR-0033)."""
    heads = np.array([o.time for o in objs])
    cols = np.array([o.column for o in objs])
    row_times = np.unique(heads)
    row_of = np.searchsorted(row_times, heads)
    sizes = np.bincount(row_of, minlength=len(row_times))
    pidx = np.array([place_at.get(int(round(t)), -1) for t in row_times])
    if (pidx < 0).any():
        return objs
    bars = np.array([grid[i].bar for i in pidx])
    sec_of_bar = {}
    for k, h in enumerate(plan):
        for b in range(h.first_bar, h.last_bar + 1):
            sec_of_bar[b] = k
    row_sec = np.array([sec_of_bar.get(int(b), -1) for b in bars])
    ratios = np.array([h.ratio for h in plan] + [0.0])
    beat_ms = places.beat_ms[pidx]
    X = holds.features(feats[pidx], row_times, sizes, beat_ms, env, shift, ratios[row_sec])
    probs = model.predict(X)[row_of]
    note_sec = row_sec[row_of]
    u = hold_u[pidx[row_of], cols]
    v = hold_v[pidx[row_of], cols]
    v_row = hold_v[pidx, 0]
    kinds = [h.kind for h in plan]
    classes = holds.assign(probs, note_sec, ratios, kinds, u, v, v_row, row_of)
    nb = beat_ms[row_of]
    tails = holds.tail_times(heads, classes, nb, places, feats[:, rhythm.F_TOTAL])
    inverse = np.array([s >= 0 and kinds[s] == "inverse" for s in note_sec])
    tails = holds.settle(cols, heads, tails, nb, inverse)
    out = []
    for o, t in zip(objs, tails):
        if np.isnan(t):
            out.append(o)
        else:
            out.append(HitObject(column=o.column, time=o.time, note_type=NoteType.LN, end_time=float(round(t))))
    return out


def _follow_leads(audio: Audio, env, grid: Sequence[GridPoint], options: GeneratorOptions):
    """Each section's lead layer (ADR-0033), and the stems' envelopes."""
    kw = {"log": options.log} if options.log else {}
    parts = stems_mod.separate(audio, separator=options.separator, **kw)
    stem_envs = stems_mod.stem_envelopes(parts, audio)
    return stems_mod.choose_leads(stem_envs, grid, find_sections(env, grid), options.leads), stem_envs


def select_rows(grid: Sequence[GridPoint], x: float, groups: Optional[Sequence[int]] = None,
                energy: Optional[Sequence[float]] = None) -> List[Tuple[GridPoint, int]]:
    """The grid points that become notes at intensity `x`, each with its chord size (ADR-0031).

    Each section (its points share an `energy`, from `structure`) is given a share of its grid points:
    `density(x)` in the song's loudest part and down to `QUIET_SHARE` of that in its quietest, so the
    chorus can be a full 1/16 stream while the verse stays at eighths with a few sixteenths. Within a
    section the points are ranked by onset strength plus their place in the measure (downbeat, beat,
    half beat) and the strongest are taken; a point with almost no onset (`MIN_STRENGTH`) is taken only
    in sections at least `FILL_ENERGY` loud, once x reaches 1. Chords widen with x as well (the picked
    rows are ordered by weight: strength, metric place, kick; the strongest get the widest, more in
    loud sections and fewer in quiet ones). Past `SPREAD_FROM` the quieter sections fill up to the
    loudest one's density by `X_MAX`, so a hard target is still reachable. With `groups` (from
    `phrases.group_measures`) the measures of a group are then given one rhythm.
    """
    energy = list(energy) if energy is not None else [1.0] * len(grid)
    share = density(x)
    by_energy: dict = {}
    for i, (p, e) in enumerate(zip(grid, energy)):
        if p.silent or (p.strength < MIN_STRENGTH and (x < 1.0 or (e < FILL_ENERGY and x < SPREAD_FROM))):
            continue
        by_energy.setdefault(e, []).append(i)
    take = set()
    for e, idx in by_energy.items():
        n_section = sum(1 for p, pe in zip(grid, energy) if pe == e and not p.silent)
        fill = QUIET_SHARE + (1.0 - QUIET_SHARE) * e
        fill += (1.0 - fill) * min(1.0, max(0.0, x - SPREAD_FROM))
        k = int(round(share * fill * n_section))
        ranked = sorted(idx, key=lambda i: (-(grid[i].strength + LEVEL_BONUS.get(grid[i].level, 0.0)), grid[i].time_ms))
        take.update(ranked[:k])
    picked = [grid[i] for i in sorted(take)]
    picked_e = [energy[i] for i in sorted(take)]
    if not picked:
        return []
    weight = [p.strength + 0.25 * (p.level == DOWNBEAT) + 0.1 * (p.level == BEAT) + 0.1 * p.low for p in picked]
    order = sorted(range(len(picked)), key=lambda i: (weight[i], -picked[i].time_ms))
    q = [0.0] * len(picked)
    for r, i in enumerate(order):
        q[i] = (r + 0.5) / len(picked)
    extra = chord_extra(x)
    sized = [(p, 1 + int(2.0 * extra * (0.5 + picked_e[i]) * q[i] + 0.5)) for i, p in enumerate(picked)]
    if groups is None:
        return sized
    size_at = {id(p): n for p, n in sized}
    sizes = harmonize(grid, groups, [size_at.get(id(p), 0) for p in grid])
    return [(p, n) for p, n in zip(grid, sizes) if n > 0]


def _same_draw(grid: Sequence[GridPoint], clusters: Sequence[int]) -> np.ndarray:
    """For each point, the index of the first point at its place in the measure among the measures of
    its cluster (itself when it has none)."""
    first: dict = {}
    out = np.arange(len(grid))
    for i, (p, c) in enumerate(zip(grid, clusters)):
        if c >= 0:
            out[i] = first.setdefault((c, p.slot), i)
    return out


def density(x: float) -> float:
    """The share of its grid points the loudest section gets at intensity `x`."""
    return min(1.0, max(0.0, x))


def chord_extra(x: float) -> float:
    """Mean notes per row beyond the first, at intensity `x`."""
    x = max(0.0, x)
    if x <= 1.0:
        return 0.45 * x
    if x <= SPREAD_FROM:
        return 0.45 + 0.6 * (x - 1.0)
    return min(2.2, 0.45 + 0.6 * (SPREAD_FROM - 1.0) + 0.9 * (x - SPREAD_FROM))


def _chart(picked: Sequence[Tuple[GridPoint, int]], styles: dict, seed, draws: dict) -> List[HitObject]:
    sampler = PatternSampler(default_model())
    masks = sampler.sample([(p.time_ms, n) for p, n in picked], np.random.default_rng(seed),
                           beats=[p.beat for p, _ in picked], draws=[draws[p.time_ms] for p, _ in picked],
                           styles=[styles.get(p.bar, "plain") for p, _ in picked])
    objs = []
    for (p, _n), mask in zip(picked, masks):
        t = float(round(p.time_ms))
        for c in mask_columns(mask):
            objs.append(HitObject(column=c, time=t, note_type=NoteType.RICE))
    return objs


def _beatmap(objs: List[HitObject], timing: List[TimingPoint], options: GeneratorOptions, stars: float) -> Beatmap7K:
    label = options.target_label or f"{options.target_stars:.2f}"
    version = options.version or f"Gen {label} ({stars:.2f})"
    ref = options.reference
    bm = Beatmap7K(
        title=options.title or (ref.title if ref else "") or "Untitled",
        artist=options.artist or (ref.artist if ref else "") or "Unknown",
        creator=options.creator,
        version=version,
        overall_difficulty=options.overall_difficulty,
        timing_points=[TimingPoint(time=tp.time, beat_length=tp.beat_length, meter=tp.meter, uninherited=True)
                       for tp in timing if tp.uninherited],
        hit_objects=sorted(objs, key=lambda o: (o.time, o.column)),
        audio_filename=ref.audio_filename if ref else "",
        tags=f"proj7k {GENERATOR_VERSION} seed{options.seed}",
    )
    if ref is not None:
        if ref.extra_sections.get("General"):
            bm.extra_sections["General"] = dict(ref.extra_sections["General"])
        if ref.extra_sections.get("Metadata"):
            meta = dict(ref.extra_sections["Metadata"])
            # An independent local set: osu!lazer marks any existing set sharing the imported set's online ID
            # for deletion, so keeping the reference's BeatmapSetID would replace the original chart.
            meta["BeatmapID"] = "0"
            meta["BeatmapSetID"] = "-1"
            bm.extra_sections["Metadata"] = meta
        # The background only: a video or storyboard would name files the .osz does not carry.
        bm.raw_events = [e for e in ref.raw_events if e.lstrip().startswith(("0,0,", "//"))]
    return bm
