"""
`python3 -m proj7k.generator`: a chart from a song.

    # a song file, timing estimated from the music
    python3 -m proj7k.generator --audio song.mp3 --target-dan 5th --title "Song" --artist "Artist"

    # borrow the timing (and title, artist, background) of a chart of the same song
    python3 -m proj7k.generator --timing-from "Songs/123 Artist - Song/Artist - Song (Mapper) [Hard].osu" --target-sr 4.5

    # the same, from osu!lazer's library: an .osu MD5, beatmap id or osu! link
    python3 -m proj7k.generator --timing-from https://osu.ppy.sh/b/5271675 --target-dan 3rd

The chart is written as an .osu and, unless --no-package, an .osz with the audio (and the reference
chart's background) that osu! and osu!lazer import directly; --open hands the .osz to the system,
which imports it into osu!lazer.
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from proj7k.dan import CANONICAL_DAN_SR, parse_dan_tier
from proj7k.generator.generate import GenerationResult, GeneratorOptions, generate_from_file
from proj7k.parser import Beatmap7K, dump_osu_7k, parse_osu_7k

logger = logging.getLogger("proj7k.generator")


@dataclass
class Reference:
    """A chart of the same song whose timing, metadata, audio and background the generated chart can borrow."""
    beatmap: Beatmap7K
    osu_path: Optional[Path]
    audio_path: Optional[Path]
    bg_path: Optional[Path]
    bg_filename: Optional[str]


@dataclass
class GenerateRun:
    result: GenerationResult
    osu_path: Path
    osz_path: Optional[Path]
    audio_path: Path
    reference: Optional[Reference] = None

    def to_dict(self) -> Dict[str, Any]:
        r = self.result
        tempo = r.tempo
        return {
            "title": f"{r.beatmap.artist} - {r.beatmap.title} [{r.beatmap.version}]",
            "version": r.beatmap.version,
            "target_stars": round(r.target_stars, 3),
            "stars": round(r.stars, 3),
            "dominant_skill": r.profile.dominant_skill,
            "skills": {k: round(v.stars, 3) for k, v in r.profile.skills.items()},
            "notes": r.n_notes,
            "rows": len({o.time for o in r.beatmap.hit_objects}),
            "intensity": round(r.intensity, 3),
            "timing_source": r.timing_source,
            "bpm": round(60000.0 / r.beatmap.timing_points[0].beat_length, 3) if r.beatmap.timing_points else None,
            "offset_ms": r.beatmap.timing_points[0].time if r.beatmap.timing_points else None,
            "tempo": None if tempo is None else {
                "bpm": tempo.bpm, "offset_ms": tempo.offset_ms, "confidence": round(tempo.confidence, 3),
                "steady": tempo.steady, "drift_ms": tempo.drift_ms,
            },
            "warnings": list(r.warnings),
            "audio": str(self.audio_path),
            "reference": str(self.reference.osu_path) if self.reference and self.reference.osu_path else None,
            "output_file": str(self.osu_path),
            "osz_file": str(self.osz_path) if self.osz_path else None,
        }


def resolve_target(target_dan: Optional[str], target_sr: Optional[float]) -> tuple:
    """(stars, label) from a dan name or a star value."""
    if target_sr is not None:
        if target_sr <= 0:
            raise ValueError("目标星级必须大于 0")
        return float(target_sr), f"{target_sr:.2f}"
    if target_dan:
        dan = parse_dan_tier(target_dan)
        return CANONICAL_DAN_SR[dan], dan
    raise ValueError("请指定 --target-dan 或 --target-sr")


def resolve_reference(spec: str, realm_path: Optional[Path] = None, files_dir: Optional[Path] = None) -> Reference:
    """An .osu path, or anything osu!lazer's library can be searched by (MD5, beatmap id, osu! link)."""
    path = Path(spec.strip('"')).expanduser()
    if path.is_file():
        from proj7k.profiler.replay_view import find_audio

        bm = parse_osu_7k(path.read_text(encoding="utf-8", errors="replace"))
        bg_name = _background_name(bm)
        bg = path.parent / bg_name if bg_name and (path.parent / bg_name).is_file() else None
        return Reference(bm, path, find_audio(path, bm.audio_filename), bg, bg_name if bg else None)

    from proj7k.downscaler.locator import locate_beatmap_in_lazer

    kwargs: Dict[str, Any] = {}
    if realm_path is not None:
        kwargs["realm_path"] = realm_path
    if files_dir is not None:
        kwargs["files_dir"] = files_dir
    asset = locate_beatmap_in_lazer(spec, **kwargs)
    if asset is None:
        raise FileNotFoundError(f"找不到参考谱面：{spec}（既不是 .osu 文件，也不在 osu!lazer 曲库里）")
    bm = parse_osu_7k(Path(asset.osu_path).read_text(encoding="utf-8", errors="replace"))
    if asset.audio_filename:
        bm.audio_filename = asset.audio_filename
    return Reference(bm, Path(asset.osu_path), asset.audio_path, asset.bg_path, asset.bg_filename if asset.bg_path else None)


def _background_name(bm: Beatmap7K) -> Optional[str]:
    for line in bm.raw_events:
        s = line.strip()
        if s.startswith("0,0,") and '"' in s:
            return s.split('"')[1].strip() or None
    return None


def safe_filename(text: str) -> str:
    import re

    return re.sub(r'[\\/*?:"<>|]', "_", text).strip()[:180]


def run_generate(
    options: GeneratorOptions,
    output_dir: Path,
    audio: Optional[Path] = None,
    reference: Optional[Reference] = None,
    auto_timing: bool = False,
    package: bool = True,
) -> GenerateRun:
    if reference is not None:
        options.reference = reference.beatmap
        if not auto_timing and options.bpm is None:
            options.timing = [tp for tp in reference.beatmap.timing_points if tp.uninherited]
    audio_path = audio or (reference.audio_path if reference else None)
    if audio_path is None:
        raise ValueError("缺少音频：请用 --audio 指定，或用 --timing-from 指向一张带音频的谱面")
    audio_path = Path(audio_path)

    logger.info(f"Generating from {audio_path.name} toward {options.target_stars:.2f} stars...")
    result = generate_from_file(audio_path, options)
    bm = result.beatmap
    audio_name = bm.audio_filename or audio_path.name
    if not Path(audio_name).suffix and audio_path.suffix:
        audio_name += audio_path.suffix
    bm.audio_filename = audio_name

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(f"{bm.artist} - {bm.title} ({bm.creator}) [{bm.version}]")
    osu_path = output_dir / f"{stem}.osu"
    osu_path.write_text(dump_osu_7k(bm), encoding="utf-8")

    osz_path = None
    if package:
        from proj7k.downscaler.locator import package_into_osz

        osz_path = package_into_osz(
            osu_path, output_dir / f"{stem}.osz",
            audio_path=audio_path, audio_filename=audio_name,
            bg_path=reference.bg_path if reference else None,
            bg_filename=reference.bg_filename if reference else None,
        )
    return GenerateRun(result=result, osu_path=osu_path, osz_path=osz_path, audio_path=audio_path, reference=reference)


def format_report(run: GenerateRun) -> str:
    d = run.to_dict()
    lines = [
        f"生成：{d['title']}",
        f"  星级 {d['stars']:.2f}★（目标 {d['target_stars']:.2f}★），主技能 {d['dominant_skill']}",
        f"  {d['notes']} 个音符 / {d['rows']} 行，强度 {d['intensity']:.2f}",
    ]
    source = {"reference": "借用参考谱面", "manual": "手动指定", "auto": "自动测得"}[d["timing_source"]]
    lines.append(f"  timing：{source}，{d['bpm']:g} BPM，offset {d['offset_ms']:g} ms")
    t = d["tempo"]
    if t is not None:
        lines.append(f"  自动测速：置信度 {t['confidence']:.2f}，前后段拍点差 {t['drift_ms']:g} ms")
    for w in d["warnings"]:
        lines.append(f"  注意：{w}")
    lines.append(f"  谱面：{d['output_file']}")
    if d["osz_file"]:
        lines.append(f"  .osz：{d['osz_file']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m proj7k.generator", description="从音频生成 osu!mania 7K 谱面（ADR-0027）")
    p.add_argument("--audio", help="歌曲音频（mp3/ogg/wav/flac）。不给时用 --timing-from 谱面的音频")
    p.add_argument("--timing-from", dest="timing_from",
                   help="参考谱面：.osu 路径，或 osu!lazer 里的 MD5 / 谱面 ID / 链接。借用它的 timing、标题、背景与音频")
    p.add_argument("--auto-timing", action="store_true", help="有参考谱面时也自动测 BPM（只借用音频与元数据）")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--target-dan", help="目标段位：0th … 10th、Gamma、Azimuth、Zenith、Stellium")
    target.add_argument("--target-sr", type=float, help="目标星级（引擎星级）")
    p.add_argument("--bpm", type=float, help="手动指定 BPM（恒定）")
    p.add_argument("--offset", type=float, help="手动指定第一个强拍的时间（毫秒）")
    p.add_argument("--title", default="")
    p.add_argument("--artist", default="")
    p.add_argument("--creator", default="proj7k")
    p.add_argument("--version", help="难度名，默认 'Gen <目标> (<星级>)'")
    p.add_argument("--od", type=float, default=8.0, help="OverallDifficulty，默认 8")
    p.add_argument("--seed", type=int, default=0, help="配键随机种子；换一个数得到另一种配法")
    p.add_argument("--tolerance", type=float, default=0.10, help="星级容差，默认 0.10")
    p.add_argument("--output-dir", default="generated_maps", help="输出目录，默认 generated_maps")
    p.add_argument("--no-package", action="store_true", help="只写 .osu，不打 .osz")
    p.add_argument("--open", action="store_true", help="生成后用系统打开 .osz（osu!lazer 会导入）")
    p.add_argument("--realm", help="osu!lazer client.realm 路径（--timing-from 用曲库查找时）")
    p.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(message)s")
    try:
        stars, label = resolve_target(args.target_dan, args.target_sr)
        reference = None
        if args.timing_from:
            realm = Path(args.realm).expanduser() if args.realm else None
            reference = resolve_reference(args.timing_from, realm_path=realm)
        options = GeneratorOptions(
            target_stars=stars, target_label=label, bpm=args.bpm, offset_ms=args.offset, seed=args.seed,
            tolerance=args.tolerance, title=args.title, artist=args.artist, creator=args.creator,
            version=args.version, overall_difficulty=args.od,
        )
        run = run_generate(options, Path(args.output_dir).expanduser(),
                           audio=Path(args.audio).expanduser() if args.audio else None,
                           reference=reference, auto_timing=args.auto_timing, package=not args.no_package)
    except Exception as e:  # every failure is reported, not traced
        if args.verbose:
            raise
        print(f"生成失败：{e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(run.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_report(run))
    if args.open and run.osz_path:
        from proj7k.downscaler.cli import open_with_system_handler

        open_with_system_handler(run.osz_path)
    return 0
