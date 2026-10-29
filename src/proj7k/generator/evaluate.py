"""
`python3 -m proj7k.generator.evaluate`: how close generated charts come to charts people wrote.

For every 7K chart that has its audio beside it (an osu!stable Songs folder, extracted .osz files) or
in osu!lazer's library, Mapperatorinator charts the song at the human chart's engine star rating (ADR-0046),
timing it itself, and the result is held against the human chart: precision (generated rows within ±20 ms
of a human row), recall (human rows within ±20 ms of a generated row), how far its stars land from the
target, the BPM it chose against the chart's main BPM (exact, or off by an octave), and both charts' keys
as `chartstats.pattern_stats` reads them (stairs, trills, jacks, repeated key patterns and measures).
The model has seen ranked charts in training: `--skip-ranked` leaves them out (ADR-0040).

Nothing is written into the library; the summary goes to the terminal and, with --output, a JSON file.

    python3 -m proj7k.generator.evaluate --lazer --limit 30 --skip-ranked ~/.proj7k/catalog.db
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
from proj7k.generator.chartstats import pattern_stats, rows_of_chart
from proj7k.generator.generate import GeneratorOptions, generate_from_file
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
    estimated_bpm: Optional[float]   # the main BPM of the timing the model wrote
    bpm_match: str          # "exact", "octave" or "wrong"
    human_patterns: Optional[dict] = None      # patterns.pattern_stats of the human chart
    generated_patterns: Optional[dict] = None  # and of the generated one
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


def score_chart(name: str, chart: Beatmap7K, content: str, audio_path: Path, seed: int = 0,
                runner=None) -> ChartScore:
    human_stars = float(evaluate_osu(content).total_stars)
    # The model times the song itself, so its rows are held against the human ones as they fall (ADR-0040).
    result = generate_from_file(audio_path, GeneratorOptions(target_stars=human_stars, seed=seed, model_runner=runner))
    gen_rows = sorted({o.time for o in result.beatmap.hit_objects})
    human_rows = sorted({o.time for o in chart.hit_objects})
    precision, recall = row_match(gen_rows, human_rows)

    main_tp = dominant_timing_point(chart)
    true_bpm = 60000.0 / main_tp.beat_length if main_tp else 0.0
    gen_tp = dominant_timing_point(result.beatmap)
    est_bpm = round(60000.0 / gen_tp.beat_length, 2) if gen_tp else None
    match = bpm_match(est_bpm, true_bpm) if est_bpm and true_bpm else "wrong"
    beat_ms = main_tp.beat_length if main_tp else 0.0
    patterns = [{k: round(v, 3) for k, v in pattern_stats(rows_of_chart(c.hit_objects), beat_ms).items()}
                for c in (chart, result.beatmap)]
    return ChartScore(name, round(human_stars, 3), round(result.stars, 3), len(human_rows), len(gen_rows),
                      round(precision, 3), round(recall, 3), round(true_bpm, 2), est_bpm, match,
                      patterns[0], patterns[1])


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


RANKED = ("ranked", "approved", "loved", "qualified")


def ranked_sets(catalog_db: Path) -> set:
    """The ids of the sets the catalog lists as ranked, loved or qualified (read-only, safe while it is written)."""
    import sqlite3

    db = sqlite3.connect(f"{catalog_db.resolve().as_uri()}?immutable=1", uri=True)
    try:
        rows = db.execute(f"SELECT id FROM beatmapsets WHERE status IN ({','.join('?' * len(RANKED))})", RANKED)
        return {int(r[0]) for r in rows}
    finally:
        db.close()


def set_id(content: str) -> Optional[int]:
    """The chart's BeatmapSetID, when it has an online one."""
    for line in content.splitlines():
        if line.strip().startswith("BeatmapSetID"):
            try:
                v = int(line.split(":", 1)[1].strip())
            except (IndexError, ValueError):
                return None
            return v if v > 0 else None
    return None


def summarize(scores: List[ChartScore]) -> dict:
    ok = [s for s in scores if s.error is None]
    if not ok:
        return {"charts": 0, "failed": len(scores)}
    return {
        "charts": len(ok),
        "failed": len(scores) - len(ok),
        "precision_median": float(np.median([s.precision for s in ok])),
        "recall_median": float(np.median([s.recall for s in ok])),
        "star_error_median": float(np.median([abs(s.generated_stars - s.human_stars) for s in ok])),
        "bpm_exact": sum(s.bpm_match == "exact" for s in ok) / len(ok),
        "bpm_octave": sum(s.bpm_match == "octave" for s in ok) / len(ok),
        "patterns_median": {
            who: {k: float(np.median([getattr(s, attr)[k] for s in ok if getattr(s, attr)]))
                  for k in ("roll", "trill", "jack_half", "key_repeat", "rhythm_repeat", "density_cv", "density_floor", "density_peak", "rows_per_s", "notes_per_s",
                            "quarter", "triplet")}
            for who, attr in (("human", "human_patterns"), ("generated", "generated_patterns"))
            if any(getattr(s, attr) for s in ok)
        },
        # Generated over human, chart by chart, at the same star rating: below 1 the generator reaches
        # the rating with fewer notes than the person did.
        "notes_ratio_median": _ratio_median(ok, "notes_per_s"),
        "rows_ratio_median": _ratio_median(ok, "rows_per_s"),
    }


def _ratio_median(scores: List[ChartScore], key: str) -> Optional[float]:
    r = [s.generated_patterns[key] / s.human_patterns[key] for s in scores
         if s.human_patterns and s.generated_patterns and s.human_patterns.get(key) and key in s.generated_patterns]
    return float(np.median(r)) if r else None


def main(argv: Optional[List[str]] = None) -> int:
    from proj7k.generator import mapperatorinator

    p = argparse.ArgumentParser(prog="python3 -m proj7k.generator.evaluate", description=__doc__.split("\n\n")[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--songs", help="osu!stable 的 Songs 文件夹，或解压后的 .osz 所在文件夹")
    src.add_argument("--lazer", action="store_true", help="osu!lazer 曲库（经 Realm 桥只读）")
    p.add_argument("--realm", help="client.realm 路径")
    p.add_argument("--limit", type=int, default=30, help="最多评测几张谱，默认 30")
    p.add_argument("--one-per-song", action="store_true", help="同一首歌只取一个难度")
    p.add_argument("--output", help="把逐谱结果与汇总写成 JSON")
    p.add_argument("--skip-ranked", metavar="CATALOG_DB",
                   help="跳过线上谱面库（catalog.db）里 ranked/loved/qualified 的谱：Mapperatorinator 训练时见过它们（ADR-0040）")
    args = p.parse_args(argv)
    mapperatorinator.install(lambda m: print(m, flush=True))

    charts = songs_folder_charts(Path(args.songs).expanduser()) if args.songs else \
        lazer_charts(Path(args.realm).expanduser() if args.realm else None)
    ranked = ranked_sets(Path(args.skip_ranked).expanduser()) if args.skip_ranked else set()
    scores: List[ChartScore] = []
    seen_audio = set()
    for name, osu, audio in charts:
        if len(scores) >= args.limit:
            break
        if args.one_per_song and audio in seen_audio:
            continue
        try:
            content = osu.read_text(encoding="utf-8", errors="replace")
            if ranked and set_id(content) in ranked:
                continue
            seen_audio.add(audio)
            s = score_chart(name, parse_osu_7k(content), content, audio)
        except Exception as e:
            s = ChartScore(name, 0, 0, 0, 0, 0, 0, 0, None, "wrong", error=str(e)[:200])
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
