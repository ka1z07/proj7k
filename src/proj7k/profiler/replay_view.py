"""
Replay viewer: a replay's keystrokes played back against the falling chart, with the chart's
difficulty laid along a draggable timeline (ADR-0021).

`build_replay_view` turns an ingestion report into one JSON-able payload: the notes with what the
player did to each, the keystrokes as press intervals per column, the engine's difficulty field as a
timeline (`proj7k.field`), and the radar. `render_replay_html` embeds it in a single self-contained
page (`static/replay_view.html`) that plays it back in the browser; no server, no network.

Times in the payload are song time in milliseconds, the clock the replay frames and the audio run on.
The engine reads a chart in physical time, so under DT/HT its seconds are divided by the clock rate
there and are scaled back here.
"""

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from proj7k.engine.skills import SKILLS, SKILL_TECH_KEY
from proj7k.parser import NoteType
from proj7k.profiler.cli import ProfilerIngestionReport
from proj7k.profiler.matcher import (
    LN_TAIL_WINDOW_SCALE,
    HitJudgment,
    _extract_column_key_presses,
    compute_mania_hit_windows,
)
from proj7k.profiler.osr import ReplayFrame
from proj7k.profiler.response import JUDGMENT_LOSS

TEMPLATE_PATH = Path(__file__).parent / "static" / "replay_view.html"
PAYLOAD_MARK = "/*__PAYLOAD__*/null"

#: Judgments in the order the page indexes them.
JUDGMENT_ORDER = [HitJudgment.MAX, HitJudgment.PERFECT, HitJudgment.GREAT, HitJudgment.GOOD, HitJudgment.MEH, HitJudgment.MISS]
_JUDGMENT_INDEX = {j: i for i, j in enumerate(JUDGMENT_ORDER)}

#: The timeline holds at most about this many bins, however long the chart is.
TIMELINE_BINS = 400
MIN_BIN_MS = 500.0


def _r(x: Optional[float], nd: int = 1) -> Optional[float]:
    return None if x is None else round(float(x), nd)


def _accuracy(counts: Dict[HitJudgment, int], total: int) -> float:
    if total <= 0:
        return 0.0
    pts = 300 * (counts.get(HitJudgment.MAX, 0) + counts.get(HitJudgment.PERFECT, 0)) \
        + 200 * counts.get(HitJudgment.GREAT, 0) + 100 * counts.get(HitJudgment.GOOD, 0) + 50 * counts.get(HitJudgment.MEH, 0)
    return pts / (300.0 * total)


def build_replay_view(
    report: ProfilerIngestionReport,
    frames: Optional[Sequence[ReplayFrame]] = None,
    audio_src: Optional[str] = None,
    sources: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    The viewer's payload for an ingested replay. `frames` defaults to the report's own; `audio_src` is the
    audio file as the page should load it (a URL or a path relative to where the page is written); `sources` are
    the replay and chart paths, so the page can hand back the command that turns a moment into a practice bundle.
    """
    field, beatmap = report.field, report.beatmap
    if field is None or beatmap is None:
        raise ValueError("the report has no difficulty field or chart to view (an empty chart?)")
    frames = list(report.replay_frames if frames is None else frames)
    rate = report.clock_rate or 1.0
    align = report.alignment_result
    mods = report.play_mods
    od = beatmap.overall_difficulty if mods.od_override is None else mods.od_override
    windows = compute_mania_hit_windows(od, clock_rate=rate, window_multiplier=mods.window_multiplier)
    D = field.total_D if field.total_D > 0 else 1.0

    # 1. the notes, each with what the player did to it and how hard the engine reads it
    notes: List[Dict[str, Any]] = []
    for h in align.aligned_hits:
        i = field.press_index(h.column, h.target_time / 1000.0 / rate)
        d_ratio = skill = None
        if i is not None:
            d_ratio = float(field.d[i]) / D
            skill = int(field.a[i].argmax())
        notes.append({
            "c": h.column,
            "t": _r(h.target_time),
            "e": _r(h.end_time) if h.note_type == NoteType.LN else None,
            "j": _JUDGMENT_INDEX[h.judgment],
            "o": _r(h.offset_ms),
            "tj": None if h.tail_judgment is None else _JUDGMENT_INDEX[h.tail_judgment],
            "to": _r(h.tail_offset_ms),
            "l": _r(d_ratio, 3),
            "s": skill,
        })

    # 2. the keystrokes: per column, [down, up] intervals; the last may be open
    keys: List[List[List[Optional[float]]]] = []
    for col in range(7):
        keys.append([[_r(p.press_time), _r(p.release_time)] for p in _extract_column_key_presses(frames, col)])
    ghosts = [{"c": g.column, "t": _r(g.time_ms), "why": g.reason.value} for g in align.ghost_taps]

    # 3. the timeline: the field's curve in song time, and where the player lost accuracy
    last_note_ms = max((n["e"] or n["t"] for n in notes), default=0.0)
    last_frame_ms = frames[-1].time_ms if frames else 0.0
    duration_ms = max(last_note_ms, last_frame_ms) + 1500.0
    bin_ms = max(MIN_BIN_MS, math.ceil(duration_ms / TIMELINE_BINS / 100.0) * 100.0)
    n_bins = int(math.ceil(duration_ms / bin_ms))
    curve = field.curve(bin_s=bin_ms / 1000.0 / rate, start_s=0.0, end_s=n_bins * bin_ms / 1000.0 / rate)
    err = [0.0] * len(curve.load)
    ghost = [0] * len(curve.load)
    for n in notes:
        k = min(len(err) - 1, int(n["t"] // bin_ms))
        err[k] += JUDGMENT_LOSS[JUDGMENT_ORDER[n["j"]]]
        if n["tj"] is not None:
            err[k] += JUDGMENT_LOSS[JUDGMENT_ORDER[n["tj"]]]
    for g in ghosts:
        ghost[min(len(ghost) - 1, int(g["t"] // bin_ms))] += 1
    hot = []
    for spot in field.hot_spots(window_s=8.0, top=5):
        hot.append({
            "start": _r(spot.start_s * rate * 1000.0), "end": _r(spot.end_s * rate * 1000.0),
            "risk": _r(spot.risk, 2), "share": _r(spot.share, 3), "skill": SKILLS.index(spot.skill),
        })

    # 4. the radar the profiler read from this play
    radar: Dict[str, Any] = {}
    if report.skill_radar is not None:
        radar = {
            "overall_dan": report.skill_radar.overall_dan,
            "skills": {
                SKILL_TECH_KEY[name]: {
                    "stars": _r(res.star_rating, 2), "dan": res.dan_tier, "tested": res.tested,
                    "broke_down": res.broke_down, "chart": _r(res.chart_level, 2),
                }
                for name, res in ((n, report.skill_radar.dimensions[SKILL_TECH_KEY[n]]) for n in SKILLS)
            },
        }

    fatal_ms = None
    if report.pathology and report.pathology.cascade_precursor:
        fatal_ms = _r(report.pathology.cascade_precursor.fatal_time_ms)

    counts = {j.value: report.judgment_counts.get(j, 0) for j in JUDGMENT_ORDER}
    return {
        "format": 1,
        "meta": {
            "title": beatmap.title, "artist": beatmap.artist, "creator": beatmap.creator, "version": beatmap.version,
            "player": report.player_name, "mods": report.mods, "rate": rate, "od": beatmap.overall_difficulty,
            "stars": _r(field.profile.total_stars, 2), "dominant": SKILL_TECH_KEY[field.profile.dominant_skill],
            "hash_matched": report.hash_matched,
        },
        "duration_ms": _r(duration_ms),
        "judgments": [j.value for j in JUDGMENT_ORDER],
        "windows": {
            "max": _r(windows.w_max), "300": _r(windows.w_300), "200": _r(windows.w_200),
            "100": _r(windows.w_100), "50": _r(windows.w_50), "miss": _r(windows.w_miss),
            "tail_scale": LN_TAIL_WINDOW_SCALE,
        },
        "skills": [SKILL_TECH_KEY[s] for s in SKILLS],
        "notes": notes,
        "keys": keys,
        "ghosts": ghosts,
        "timeline": {
            "bin_ms": bin_ms,
            "load": [round(float(x), 3) for x in curve.load],
            "risk": [round(float(x), 3) for x in curve.risk],
            "skill": [int(x) for x in curve.skill],
            "errors": [round(x, 2) for x in err],
            "ghosts": ghost,
            "hot": hot,
        },
        "radar": radar,
        "summary": {
            "counts": counts, "total": len(notes), "accuracy": round(_accuracy(report.judgment_counts, len(notes)), 4),
            "ghost_taps": report.ghost_tap_count, "fatal_ms": fatal_ms,
        },
        "audio": audio_src,
        "sources": sources or {},
    }


def render_replay_html(payload: Dict[str, Any]) -> str:
    """The self-contained page for a payload."""
    page = TEMPLATE_PATH.read_text(encoding="utf-8")
    if PAYLOAD_MARK not in page:
        raise RuntimeError("replay_view.html has no payload slot")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return page.replace(PAYLOAD_MARK, data, 1)


def find_audio(beatmap_path: Path, audio_filename: str = "") -> Optional[Path]:
    """The chart's audio beside the .osu: the file it names, else the first audio file in the folder."""
    folder = Path(beatmap_path).parent
    if audio_filename and (folder / audio_filename).is_file():
        return folder / audio_filename
    for f in sorted(folder.iterdir()) if folder.is_dir() else []:
        if f.suffix.lower() in (".mp3", ".ogg", ".wav"):
            return f
    return None


def write_replay_view(
    report: ProfilerIngestionReport,
    output: Path | str,
    audio_path: Optional[Path | str] = None,
    replay_path: Optional[Path | str] = None,
    beatmap_path: Optional[Path | str] = None,
) -> Path:
    """Write the viewer page for `report` to `output`; the audio is referenced, relative to the page, not embedded."""
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    audio_src = None
    if audio_path is not None:
        audio_src = Path(os.path.relpath(Path(audio_path).resolve(), out.resolve().parent)).as_posix()
    sources = {k: str(Path(v).resolve()) for k, v in (("replay", replay_path), ("beatmap", beatmap_path)) if v is not None}
    out.write_text(render_replay_html(build_replay_view(report, audio_src=audio_src, sources=sources)), encoding="utf-8")
    return out
