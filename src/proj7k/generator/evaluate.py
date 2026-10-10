"""
`python3 -m proj7k.generator.evaluate`: how close generated charts come to charts people wrote.

For every 7K chart that has its audio beside it (an osu!stable Songs folder, extracted .osz files) or
in osu!lazer's library, the song is charted twice by the generator:

1. **with the human chart's timing, at the human chart's engine star rating** — the rows it picks are
   compared with the human rows: precision (generated rows within ±20 ms of a human row) and recall
   (human rows within ±20 ms of a generated row), and how far its stars land from the target;
2. **with the timing estimated from the music** — the estimate is compared with the chart's main BPM
   (exact, or off by an octave) and, when the BPM is right, with its beat phase.

Nothing is written into the library; the summary goes to the terminal and, with --output, a JSON file.

    python3 -m proj7k.generator.evaluate --lazer --limit 30
    python3 -m proj7k.generator.evaluate --songs "C:/osu!/Songs" --limit 30 --output eval.json
"""

import argparse
import json
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import numpy as np

from proj7k.engine import evaluate_osu
from proj7k.generator.audio import load_audio
from proj7k.generator.generate import GeneratorOptions, generate
from proj7k.generator.onset import onset_envelope
from proj7k.generator.tempo import estimate_tempo
from proj7k.parser import Beatmap7K, dominant_timing_point, parse_osu_7k

MATCH_MS = 20.0


@dataclass
class ChartScore:
    name: str
    human_stars: float
    generated_stars: float
    human_rows: int
    generated_rows: int
    precision: float
    recall: float
    true_bpm: float
    estimated_bpm: Optional[float]
    bpm_match: str          # "exact", "octave" or "wrong"
    phase_error_ms: Optional[float]
    error: Optional[str] = None


def row_match(generated: List[float], human: List[float], window_ms: float = MATCH_MS) -> Tuple[float, float]:
    """(precision, recall) of generated row times against human row times."""
    if not generated or not human:
        return 0.0, 0.0
    g, h = np.array(sorted(generated)), np.array(sorted(human))

    def hit_rate(a: np.ndarray, b: np.ndarray) -> float:
        idx = np.clip(np.searchsorted(b, a), 1, len(b) - 1) if len(b) > 1 else np.zeros(len(a), dtype=int)
        near = np.minimum(np.abs(a - b[idx]), np.abs(a - b[np.maximum(idx - 1, 0)]))
        return float(np.mean(near <= window_ms))

    return hit_rate(g, h), hit_rate(h, g)


def bpm_match(estimated: float, true: float) -> str:
    for factor, label in ((1.0, "exact"), (2.0, "octave"), (0.5, "octave"), (1.5, "wrong"), (2 / 3, "wrong")):
        if abs(estimated / (true * factor) - 1.0) <= 0.005:
            return label
    return "wrong"


def score_chart(name: str, chart: Beatmap7K, content: str, audio_path: Path, seed: int = 0) -> ChartScore:
    human_stars = float(evaluate_osu(content).total_stars)
    audio = load_audio(audio_path)
    timing = [tp for tp in chart.timing_points if tp.uninherited]
    result = generate(audio, GeneratorOptions(target_stars=human_stars, timing=timing, seed=seed))
    gen_rows = sorted({o.time for o in result.beatmap.hit_objects})
    human_rows = sorted({o.time for o in chart.hit_objects})
    precision, recall = row_match(gen_rows, human_rows)

    main_tp = dominant_timing_point(chart)
    true_bpm = 60000.0 / main_tp.beat_length if main_tp else 0.0
    est_bpm, match, phase = None, "wrong", None
    try:
        est = estimate_tempo(onset_envelope(audio))
        est_bpm, match = est.bpm, bpm_match(est.bpm, true_bpm) if true_bpm else "wrong"
        if match == "exact" and main_tp is not None:
            beat = main_tp.beat_length
            phase = float((est.offset_ms - main_tp.time + beat / 2) % beat - beat / 2)
    except ValueError:
        pass
    return ChartScore(name, round(human_stars, 3), round(result.stars, 3), len(human_rows), len(gen_rows),
                      round(precision, 3), round(recall, 3), round(true_bpm, 2), est_bpm, match,
                      None if phase is None else round(phase, 1))


def songs_folder_charts(root: Path) -> Iterator[Tuple[str, Path, Path]]:
    """(name, .osu path, audio path) of every 7K chart under `root` whose audio is beside it."""
    from proj7k.profiler.replay_view import find_audio

    for osu in sorted(root.rglob("*.osu")):
        try:
            head = osu.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "Mode: 3" not in head and "Mode:3" not in head:
            continue
        chart = parse_osu_7k(head)
        if chart.circle_size != 7:
            continue
        audio = find_audio(osu, chart.audio_filename)
        if audio is not None:
            yield osu.stem, osu, audio


def lazer_charts(realm_path: Optional[Path] = None, files_dir: Optional[Path] = None) -> Iterator[Tuple[str, Path, Path]]:
    from proj7k.downscaler.locator import locate_beatmap_in_lazer
    from proj7k.lazer.bridge import DEFAULT_REALM_PATH, RealmBridgeClient

    realm = realm_path or DEFAULT_REALM_PATH
    records = RealmBridgeClient(default_realm_path=realm).dump_7k_beatmaps(realm_path=realm)
    random.Random(0).shuffle(records)
    for rec in records:
        kwargs = {"realm_path": realm}
        if files_dir is not None:
            kwargs["files_dir"] = files_dir
        asset = locate_beatmap_in_lazer(rec.md5_hash, **kwargs)
        if asset is None or asset.audio_path is None or not Path(asset.audio_path).is_file():
            continue
        yield f"{rec.artist} - {rec.title} [{rec.difficulty_name}]", Path(asset.osu_path), Path(asset.audio_path)


def summarize(scores: List[ChartScore]) -> dict:
    ok = [s for s in scores if s.error is None]
    if not ok:
        return {"charts": 0, "failed": len(scores)}
    phases = [abs(s.phase_error_ms) for s in ok if s.phase_error_ms is not None]
    return {
        "charts": len(ok),
        "failed": len(scores) - len(ok),
        "precision_median": float(np.median([s.precision for s in ok])),
        "recall_median": float(np.median([s.recall for s in ok])),
        "star_error_median": float(np.median([abs(s.generated_stars - s.human_stars) for s in ok])),
        "bpm_exact": sum(s.bpm_match == "exact" for s in ok) / len(ok),
        "bpm_octave": sum(s.bpm_match == "octave" for s in ok) / len(ok),
        "phase_within_10ms": (sum(p <= 10.0 for p in phases) / len(phases)) if phases else None,
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m proj7k.generator.evaluate", description=__doc__.split("\n\n")[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--songs", help="osu!stable 的 Songs 文件夹，或解压后的 .osz 所在文件夹")
    src.add_argument("--lazer", action="store_true", help="osu!lazer 曲库（经 Realm 桥只读）")
    p.add_argument("--realm", help="client.realm 路径")
    p.add_argument("--limit", type=int, default=30, help="最多评测几张谱，默认 30")
    p.add_argument("--one-per-song", action="store_true", help="同一首歌只取一个难度")
    p.add_argument("--output", help="把逐谱结果与汇总写成 JSON")
    args = p.parse_args(argv)

    charts = songs_folder_charts(Path(args.songs).expanduser()) if args.songs else \
        lazer_charts(Path(args.realm).expanduser() if args.realm else None)
    scores: List[ChartScore] = []
    seen_audio = set()
    for name, osu, audio in charts:
        if len(scores) >= args.limit:
            break
        if args.one_per_song and audio in seen_audio:
            continue
        seen_audio.add(audio)
        try:
            content = osu.read_text(encoding="utf-8", errors="replace")
            s = score_chart(name, parse_osu_7k(content), content, audio)
        except Exception as e:
            s = ChartScore(name, 0, 0, 0, 0, 0, 0, 0, None, "wrong", None, error=str(e)[:200])
        scores.append(s)
        line = f"[{len(scores):3d}] {name[:60]:60s} "
        print(line + (f"失败：{s.error}" if s.error else
                      f"{s.human_stars:5.2f}★→{s.generated_stars:5.2f}★  准 {s.precision:.2f} 全 {s.recall:.2f}  "
                      f"BPM {s.true_bpm:g}/{s.estimated_bpm} {s.bpm_match}"), flush=True)
    summary = summarize(scores)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output:
        Path(args.output).write_text(json.dumps({"summary": summary, "charts": [asdict(s) for s in scores]},
                                                ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if scores else 1


if __name__ == "__main__":
    sys.exit(main())
