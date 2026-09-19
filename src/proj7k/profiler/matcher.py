"""
Causal Hit-Window Matcher & Panic Ghost Tap Isolator for osu!mania.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from proj7k.parser import Beatmap7K, NoteType
from proj7k.profiler.osr import ReplayFrame


class HitJudgment(Enum):
    MAX = "MAX"          # Rainbow 300 / Geki (300g)
    PERFECT = "300"      # Standard 300
    GREAT = "200"        # 200 / Katu
    GOOD = "100"         # 100
    MEH = "50"           # 50
    MISS = "MISS"        # Miss


class GhostTapReason(str, Enum):
    OUT_OF_WINDOW = "out_of_window"
    EMPTY_TRACK = "empty_track"


CANONICAL_7K_LANES: List[str] = ["L3", "L2", "L1", "S", "R1", "R2", "R3"]


def column_to_canonical_lane(col: int) -> str:
    """Returns canonical topological notation (L3..S..R3) for 7K columns."""
    if 0 <= col < len(CANONICAL_7K_LANES):
        return CANONICAL_7K_LANES[col]
    return f"Col{col}"


@dataclass(frozen=True)
class ManiaHitWindows:
    """
    Hit window thresholds in milliseconds for osu!mania based on Overall Difficulty (OD).
    """
    w_max: float
    w_300: float
    w_200: float
    w_100: float
    w_50: float
    w_miss: float


def compute_mania_hit_windows(od: float, clock_rate: float = 1.0) -> ManiaHitWindows:
    """
    Computes standard osu!mania OD-dependent hit windows (ms), scaled by mod clock rate.
    """
    od = max(0.0, min(10.0, float(od)))
    scale = 1.0 / clock_rate if clock_rate > 0 else 1.0
    return ManiaHitWindows(
        w_max=16.0 * scale,
        w_300=(64.0 - 3.0 * od) * scale,
        w_200=(97.0 - 3.0 * od) * scale,
        w_100=(127.0 - 3.0 * od) * scale,
        w_50=(151.0 - 3.0 * od) * scale,
        w_miss=(188.0 - 3.0 * od) * scale,
    )


@dataclass
class AlignedHit:
    """
    Result of aligning a single note against player replay input.
    """
    column: int
    target_time: float
    hit_time: Optional[float] = None
    offset_ms: Optional[float] = None
    judgment: HitJudgment = HitJudgment.MISS
    note_type: NoteType = NoteType.RICE
    end_time: Optional[float] = None
    tail_release_time: Optional[float] = None
    tail_offset_ms: Optional[float] = None
    strains: Optional[Dict[str, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "column": self.column,
            "lane": column_to_canonical_lane(self.column),
            "target_time": self.target_time,
            "hit_time": self.hit_time,
            "offset_ms": self.offset_ms,
            "judgment": self.judgment.value,
            "note_type": self.note_type.value,
            "end_time": self.end_time,
            "tail_release_time": self.tail_release_time,
            "tail_offset_ms": self.tail_offset_ms,
        }
        if self.strains is not None:
            d["strains"] = {k: round(v, 2) for k, v in self.strains.items()}
        return d


@dataclass
class PanicGhostTap:
    """
    Invalid key tap outside of any active judgment window or on an empty track.
    Isolated from legitimate hit error calculations.
    """
    column: int
    time_ms: float
    reason: GhostTapReason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "column": self.column,
            "lane": column_to_canonical_lane(self.column),
            "time_ms": self.time_ms,
            "reason": self.reason.value,
        }


@dataclass
class HitAlignmentResult:
    """
    Complete causal alignment report between a beatmap and a replay.
    """
    aligned_hits: List[AlignedHit] = field(default_factory=list)
    ghost_taps: List[PanicGhostTap] = field(default_factory=list)
    judgment_counts: Dict[HitJudgment, int] = field(default_factory=dict)
    total_hits: int = 0
    miss_count: int = 0
    total_ghost_taps: int = 0
    ghost_taps_by_column: Dict[int, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_hits": self.total_hits,
            "miss_count": self.miss_count,
            "total_ghost_taps": self.total_ghost_taps,
            "judgment_counts": {k.value: v for k, v in self.judgment_counts.items()},
            "ghost_taps_by_column": {
                column_to_canonical_lane(col): cnt
                for col, cnt in self.ghost_taps_by_column.items()
            },
            "aligned_hits": [h.to_dict() for h in self.aligned_hits],
            "ghost_taps": [g.to_dict() for g in self.ghost_taps],
        }


@dataclass
class _KeyPress:
    press_time: float
    release_time: Optional[float] = None


def _extract_column_key_presses(frames: List[ReplayFrame], column: int) -> List[_KeyPress]:
    presses: List[_KeyPress] = []
    is_down = False
    current_press_time: float = 0.0

    for f in frames:
        pressed = f.is_pressed(column)
        if not is_down and pressed:
            is_down = True
            current_press_time = f.time_ms
        elif is_down and not pressed:
            is_down = False
            presses.append(_KeyPress(press_time=current_press_time, release_time=f.time_ms))

    if is_down:
        presses.append(_KeyPress(press_time=current_press_time, release_time=None))

    return presses


def align_replay_hits(
    beatmap: Beatmap7K,
    frames: List[ReplayFrame],
    od: Optional[float] = None,
    clock_rate: float = 1.0,
) -> HitAlignmentResult:
    """
    Causally aligns replay discrete input frames to beatmap hit objects column by column.
    """
    effective_od = od if od is not None else beatmap.overall_difficulty
    windows = compute_mania_hit_windows(effective_od, clock_rate=clock_rate)

    all_aligned_hits: List[AlignedHit] = []
    all_ghost_taps: List[PanicGhostTap] = []

    def _append_miss(col_idx: int, note_obj) -> None:
        all_aligned_hits.append(
            AlignedHit(
                column=col_idx,
                target_time=note_obj.time,
                hit_time=None,
                offset_ms=None,
                judgment=HitJudgment.MISS,
                note_type=note_obj.note_type,
                end_time=note_obj.end_time,
            )
        )

    # Process all columns (default 7 columns for 7K)
    num_columns = beatmap.circle_size if beatmap.circle_size > 0 else 7

    for col in range(num_columns):
        # Extract notes on this column
        col_notes = [ho for ho in beatmap.hit_objects if ho.column == col]
        col_notes.sort(key=lambda ho: ho.time)

        # Extract presses on this column
        col_presses = _extract_column_key_presses(frames, col)
        col_presses.sort(key=lambda p: p.press_time)

        if not col_notes:
            # Empty track: any press on this track is a ghost tap
            for p in col_presses:
                all_ghost_taps.append(
                    PanicGhostTap(column=col, time_ms=p.press_time, reason=GhostTapReason.EMPTY_TRACK)
                )
            continue

        note_idx = 0
        press_idx = 0

        while note_idx < len(col_notes) and press_idx < len(col_presses):
            note = col_notes[note_idx]
            press = col_presses[press_idx]

            window_start = note.time - windows.w_miss
            window_end = note.time + windows.w_miss

            if press.press_time < window_start:
                # Press is too early for current note, and all previous notes are finished
                all_ghost_taps.append(
                    PanicGhostTap(column=col, time_ms=press.press_time, reason=GhostTapReason.OUT_OF_WINDOW)
                )
                press_idx += 1
                continue

            if window_start <= press.press_time <= window_end:
                # Hit this note!
                offset = press.press_time - note.time
                abs_offset = abs(offset)

                if abs_offset <= windows.w_max:
                    judg = HitJudgment.MAX
                elif abs_offset <= windows.w_300:
                    judg = HitJudgment.PERFECT
                elif abs_offset <= windows.w_200:
                    judg = HitJudgment.GREAT
                elif abs_offset <= windows.w_100:
                    judg = HitJudgment.GOOD
                elif abs_offset <= windows.w_50:
                    judg = HitJudgment.MEH
                else:
                    judg = HitJudgment.MISS

                tail_release: Optional[float] = None
                tail_offset: Optional[float] = None
                if note.note_type == NoteType.LN and note.end_time is not None:
                    tail_release = press.release_time
                    if tail_release is not None:
                        tail_offset = tail_release - note.end_time

                all_aligned_hits.append(
                    AlignedHit(
                        column=col,
                        target_time=note.time,
                        hit_time=press.press_time,
                        offset_ms=offset,
                        judgment=judg,
                        note_type=note.note_type,
                        end_time=note.end_time,
                        tail_release_time=tail_release,
                        tail_offset_ms=tail_offset,
                    )
                )
                press_idx += 1
                note_idx += 1
                continue

            # press.press_time > window_end: note timed out without a hit
            _append_miss(col, note)
            note_idx += 1

        # Drain remaining unconsumed notes as Miss
        while note_idx < len(col_notes):
            _append_miss(col, col_notes[note_idx])
            note_idx += 1

        # Drain remaining presses after last note window as ghost taps
        while press_idx < len(col_presses):
            press = col_presses[press_idx]
            all_ghost_taps.append(
                PanicGhostTap(column=col, time_ms=press.press_time, reason=GhostTapReason.OUT_OF_WINDOW)
            )
            press_idx += 1

    # Sort all aligned hits and ghost taps by time
    all_aligned_hits.sort(key=lambda h: h.target_time)
    all_ghost_taps.sort(key=lambda g: g.time_ms)

    # Calculate judgment aggregates
    counts: Dict[HitJudgment, int] = {j: 0 for j in HitJudgment}
    for h in all_aligned_hits:
        counts[h.judgment] += 1

    ghost_by_col: Dict[int, int] = {c: 0 for c in range(num_columns)}
    for g in all_ghost_taps:
        ghost_by_col[g.column] = ghost_by_col.get(g.column, 0) + 1

    miss_count = counts[HitJudgment.MISS]
    total_hits = len(all_aligned_hits)

    return HitAlignmentResult(
        aligned_hits=all_aligned_hits,
        ghost_taps=all_ghost_taps,
        judgment_counts=counts,
        total_hits=total_hits,
        miss_count=miss_count,
        total_ghost_taps=len(all_ghost_taps),
        ghost_taps_by_column=ghost_by_col,
    )

