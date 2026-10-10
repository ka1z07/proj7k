"""
From audio to a chart at a target star rating (ADR-0027).

    audio ─ onset envelope ─┬─ beat grid (timing given, or estimated) ─ pick points ─ columns ─ engine
                            └─ tempo estimate (when no timing is given)          ▲               │
                                                                                 └── intensity ──┘

One knob, the *intensity* x, sets how much of the grid becomes notes and how wide the chords are.
A grid point is a note when its onset strength plus a bonus for its place in the measure clears
`1 - x`; a note's chord grows with its strength and with x. The engine's total star rating rises
with x, so x is bisected until the chart lands within the tolerance of the target, or the knob runs
out (the song is too sparse to reach the target, or too busy to go under it), which is reported.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

from proj7k.engine import DifficultyProfile, evaluate_notes
from proj7k.generator.audio import Audio, load_audio
from proj7k.generator.columns import Row, assign_columns
from proj7k.generator.grid import BEAT, DOWNBEAT, HALF, GridPoint, build_grid
from proj7k.generator.onset import onset_envelope
from proj7k.generator.tempo import TempoEstimate, estimate_tempo
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint

GENERATOR_VERSION = "gen-1"
LEVEL_BONUS = {DOWNBEAT: 0.30, BEAT: 0.18, HALF: 0.06}
MIN_STRENGTH = 0.06
X_MIN, X_MAX = 0.0, 3.0
#: Above this intensity the weak-onset gate opens gradually, so a busy target can fill a passage's
#: quieter grid points (a continuous stream) instead of only stacking wider chords.
FILL_FROM = 1.6
MAX_ITERATIONS = 18


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


@dataclass
class GenerationResult:
    beatmap: Beatmap7K
    profile: DifficultyProfile
    target_stars: float
    intensity: float
    timing_source: str                        # "reference", "manual" or "auto"
    tempo: Optional[TempoEstimate]
    iterations: int
    warnings: List[str] = field(default_factory=list)

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

    grid = build_grid(env, timing)
    lo, hi = X_MIN, X_MAX
    best: Optional[Tuple[float, List[HitObject], DifficultyProfile]] = None
    iterations = 0

    def attempt(x: float) -> Tuple[List[HitObject], DifficultyProfile]:
        objs = _chart(grid, x, options.seed)
        if not objs:
            return objs, evaluate_notes([])
        return objs, evaluate_notes([(o.column, o.time / 1000.0, None) for o in objs])

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
    x, objs, prof = best
    if abs(prof.total_stars - options.target_stars) > options.tolerance:
        top_objs, top = attempt(X_MAX)
        if top.total_stars < options.target_stars - options.tolerance:
            x, objs, prof = X_MAX, top_objs, top
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
                            timing_source=source, tempo=tempo, iterations=iterations, warnings=warnings)


def select_rows(grid: Sequence[GridPoint], x: float) -> List[Tuple[GridPoint, int]]:
    """The grid points that become notes at intensity `x`, each with its chord size.

    A point is a note when its strength plus its metric bonus clears `1 - x`. Chord sizes are handed
    out by rank: the picked rows are ordered by weight (strength, metric place, kick), and the
    strongest get the widest chords, so that the mean row carries `1 + chord_extra(x)` notes.
    """
    threshold = 1.0 - x
    gate = MIN_STRENGTH * min(1.0, max(0.0, (X_MAX - 0.4 - x) / (X_MAX - 0.4 - FILL_FROM)))
    picked = [p for p in grid
              if not p.silent and p.strength >= gate
              and p.strength + LEVEL_BONUS.get(p.level, 0.0) >= threshold]
    if not picked:
        return []
    weight = [p.strength + 0.25 * (p.level == DOWNBEAT) + 0.1 * (p.level == BEAT) + 0.1 * p.low for p in picked]
    order = sorted(range(len(picked)), key=lambda i: (weight[i], -picked[i].time_ms))
    q = [0.0] * len(picked)
    for r, i in enumerate(order):
        q[i] = (r + 0.5) / len(picked)
    extra = chord_extra(x)
    return [(p, 1 + int(2.0 * extra * q[i] + 0.5)) for i, p in enumerate(picked)]


def chord_extra(x: float) -> float:
    """Mean notes per row beyond the first, at intensity `x`."""
    return min(1.8, 0.6 * max(0.0, x))


def _chart(grid: Sequence[GridPoint], x: float, seed: int) -> List[HitObject]:
    picked = select_rows(grid, x)
    rows = [Row(time_ms=p.time_ms, size=n, brightness=p.brightness) for p, n in picked]
    cols = assign_columns(rows, seed=seed)
    objs = []
    for row, cs in zip(rows, cols):
        t = float(round(row.time_ms))
        for c in cs:
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
            meta["BeatmapID"] = "0"
            bm.extra_sections["Metadata"] = meta
        # The background only: a video or storyboard would name files the .osz does not carry.
        bm.raw_events = [e for e in ref.raw_events if e.lstrip().startswith(("0,0,", "//"))]
    return bm
