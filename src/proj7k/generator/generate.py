"""
From a song to a chart at a target star rating: Mapperatorinator writes the notes, the engine rates them
(ADR-0040, ADR-0046).

proj7k's own pipeline (onset grid, rhythm/pattern/hold models, Demucs stems, one main technique per section;
ADR-0027 to ADR-0039) is gone: the model hears the music and writes rhythm and keys together, which is what
that pipeline never managed. What stays here is what every chart needs whoever writes it: the options, the
result, and the .osu built from the notes (made playable by `legal`, metadata from the reference chart).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from proj7k.engine import DifficultyProfile
from proj7k.generator import legal
from proj7k.parser import Beatmap7K, HitObject, TimingPoint

GENERATOR_VERSION = "gen-7"


@dataclass
class GeneratorOptions:
    target_stars: float
    target_label: str = ""                    # how the target was asked for: "5th" or "5.20★"
    timing: Optional[List[TimingPoint]] = None  # from a reference chart; wins over bpm/offset
    bpm: Optional[float] = None               # a constant tempo typed in (with offset_ms, its first downbeat)
    offset_ms: Optional[float] = None
    seed: int = 0
    tolerance: float = 0.10                   # stars
    title: str = ""
    artist: str = ""
    creator: str = "proj7k"
    version: Optional[str] = None
    overall_difficulty: float = 8.0
    reference: Optional[Beatmap7K] = None     # copy its metadata, background and General section
    ln_ratio: float = 0.0                     # the chart's share of long notes (the model's hold_note_ratio)
    whole_inverse: bool = False               # ask for long notes throughout (hold_note_ratio 1)
    log: Optional[Callable[[str], None]] = None
    model_runs: Optional[int] = None          # runs at most, each scored by the engine
    model_hold_ratio: Optional[float] = None  # its hold_note_ratio condition; None: from ln_ratio, or the model chooses
    model_args: Optional[List[str]] = None    # extra Hydra overrides (descriptors, mapper_id, ...)
    model_fast: bool = False                  # windows written as batched chains (about 2x faster, fewer long notes)
    model_ln_lora: bool = False               # load the long-note LoRA (ADR-0048, experimental)
    model_reuse: bool = True                  # phrases that sound alike charted alike (ADR-0045)
    pool_mix: Optional[Dict[str, float]] = None  # engine pool -> -1..1, picked and trimmed for (mix.py, ADR-0047)
    model_runner: Optional[Callable] = None   # stands in for the model (tests)


@dataclass
class GenerationResult:
    beatmap: Beatmap7K
    profile: DifficultyProfile
    target_stars: float
    difficulty: float                         # the model's difficulty condition for the run kept
    timing_source: str                        # "reference", "manual" or "model" (the model timed the music)
    iterations: int                           # model runs
    warnings: List[str] = field(default_factory=list)

    @property
    def stars(self) -> float:
        return float(self.profile.total_stars)

    @property
    def n_notes(self) -> int:
        return len(self.beatmap.hit_objects)


def generate_from_file(audio_path: Union[str, Path], options: GeneratorOptions) -> GenerationResult:
    from proj7k.generator import mapperatorinator

    return mapperatorinator.generate_from_file(Path(audio_path), options, runner=options.model_runner)


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
        # The model's windows overlap: the file osu! gets is made playable (legal.py).
        hit_objects=legal.sanitize(objs, timing)[0],
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
