"""
Micro-Biomechanics & Reading Pathology Diagnostic Analyzer.
Quantifies track-by-track variance, bimanual asymmetry, Jack stagnation drift,
LN release bias, and cascade failure precursors (ADR-0012).
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from proj7k.parser import Beatmap7K, HitObject, NoteType
from proj7k.physics import BRACKET_PHASE_INVERSION_WINDOW_MS, CHORDJACK_STEP_INTERVAL_MS
from proj7k.profiler.matcher import (
    AlignedHit,
    HitAlignmentResult,
    HitJudgment,
    column_to_canonical_lane,
)
from proj7k.radar import _partition_chord_steps


class PrecursorMotif(str, Enum):
    BRACKET = "bracket"
    LN_INVERSE = "ln_inverse"
    CHORDJACK = "chordjack"
    STREAM = "stream"


@dataclass
class TrackPathology:
    """Per-track error and unstable rate statistics."""
    column: int
    lane: str
    hit_count: int
    mean_error_ms: float
    std_error_ms: float
    ur: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "column": self.column,
            "lane": self.lane,
            "hit_count": self.hit_count,
            "mean_error_ms": round(self.mean_error_ms, 2),
            "std_error_ms": round(self.std_error_ms, 2),
            "ur": round(self.ur, 2),
        }


@dataclass
class BimanualPathology:
    """Left hand vs Right hand bimanual asymmetry."""
    left_hit_count: int
    right_hit_count: int
    left_mean_error_ms: float
    right_mean_error_ms: float
    left_ur: float
    right_ur: float
    load_asymmetry_ratio: float
    ur_asymmetry_ratio: float
    asymmetry_ratio: float  # Alias to ur_asymmetry_ratio for compatibility

    def to_dict(self) -> Dict[str, Any]:
        return {
            "left_hit_count": self.left_hit_count,
            "right_hit_count": self.right_hit_count,
            "left_mean_error_ms": round(self.left_mean_error_ms, 2),
            "right_mean_error_ms": round(self.right_mean_error_ms, 2),
            "left_ur": round(self.left_ur, 2),
            "right_ur": round(self.right_ur, 2),
            "load_asymmetry_ratio": round(self.load_asymmetry_ratio, 3),
            "ur_asymmetry_ratio": round(self.ur_asymmetry_ratio, 3),
            "asymmetry_ratio": round(self.asymmetry_ratio, 3),
        }


@dataclass
class JackDriftReport:
    """Linear regression drift analysis across Delta k = 1 stagnation intervals."""
    stagnation_jack_count: int = 0
    slope_ms_per_s: float = 0.0
    intercept_ms: float = 0.0
    r_squared: float = 0.0
    fatigue_alert: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stagnation_jack_count": self.stagnation_jack_count,
            "slope_ms_per_s": round(self.slope_ms_per_s, 3),
            "intercept_ms": round(self.intercept_ms, 2),
            "r_squared": round(self.r_squared, 3),
            "fatigue_alert": self.fatigue_alert,
        }


@dataclass
class LNReleasePathology:
    """LN release decoupling: head press accuracy vs tail hold stickiness and panic."""
    total_lns: int = 0
    head_hit_count: int = 0
    head_mean_error_ms: float = 0.0
    head_ur: float = 0.0
    evaluated_releases: int = 0
    mean_tail_offset_ms: float = 0.0
    tail_ur: float = 0.0
    sticky_count: int = 0
    sticky_rate: float = 0.0
    panic_release_count: int = 0
    panic_release_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_lns": self.total_lns,
            "head_hit_count": self.head_hit_count,
            "head_mean_error_ms": round(self.head_mean_error_ms, 2),
            "head_ur": round(self.head_ur, 2),
            "evaluated_releases": self.evaluated_releases,
            "mean_tail_offset_ms": round(self.mean_tail_offset_ms, 2),
            "tail_ur": round(self.tail_ur, 2),
            "sticky_count": self.sticky_count,
            "sticky_rate": round(self.sticky_rate, 4),
            "panic_release_count": self.panic_release_count,
            "panic_release_rate": round(self.panic_release_rate, 4),
        }


@dataclass
class CascadePrecursor:
    """Precursor motif analysis leading up to first fatal combo break."""
    fatal_time_ms: Optional[float] = None
    fatal_column: Optional[int] = None
    precursor_start_ms: Optional[float] = None
    precursor_note_count: int = 0
    has_bracket_inversion: bool = False
    has_ln_negative_space: bool = False
    dominant_technique: str = PrecursorMotif.STREAM.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fatal_time_ms": round(self.fatal_time_ms, 1) if self.fatal_time_ms is not None else None,
            "fatal_column": self.fatal_column,
            "fatal_lane": column_to_canonical_lane(self.fatal_column) if self.fatal_column is not None else None,
            "precursor_start_ms": round(self.precursor_start_ms, 1) if self.precursor_start_ms is not None else None,
            "precursor_note_count": self.precursor_note_count,
            "has_bracket_inversion": self.has_bracket_inversion,
            "has_ln_negative_space": self.has_ln_negative_space,
            "dominant_technique": self.dominant_technique,
        }


@dataclass
class PathologyReport:
    """Comprehensive micro-pathology diagnostic report."""
    tracks: Dict[str, TrackPathology] = field(default_factory=dict)
    bimanual: BimanualPathology = field(
        default_factory=lambda: BimanualPathology(0, 0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    )
    jack_drift: JackDriftReport = field(default_factory=JackDriftReport)
    ln_release: LNReleasePathology = field(default_factory=LNReleasePathology)
    cascade_precursor: Optional[CascadePrecursor] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tracks": {lane: t.to_dict() for lane, t in self.tracks.items()},
            "bimanual": self.bimanual.to_dict(),
            "jack_drift": self.jack_drift.to_dict(),
            "ln_release": self.ln_release.to_dict(),
            "cascade_precursor": self.cascade_precursor.to_dict() if self.cascade_precursor else None,
        }


def _compute_distribution(data: List[float]) -> Tuple[int, float, float, float]:
    """Computes count, mean, standard deviation, and Unstable Rate (UR)."""
    if not data:
        return 0, 0.0, 0.0, 0.0
    mean_val = float(np.mean(data))
    std_val = float(np.std(data, ddof=0))
    ur_val = std_val * 10.0
    return len(data), mean_val, std_val, ur_val


def analyze_pathology(
    alignment: HitAlignmentResult,
    beatmap: Beatmap7K,
) -> PathologyReport:
    """
    Analyzes alignment hits to produce biomechanical and pathology diagnostics.
    """
    valid_hits = [h for h in alignment.aligned_hits if h.offset_ms is not None]

    num_cols = beatmap.circle_size if beatmap.circle_size > 0 else 7
    col_offsets: Dict[int, List[float]] = {col: [] for col in range(num_cols)}
    for h in valid_hits:
        if h.offset_ms is not None and 0 <= h.column < num_cols:
            col_offsets[h.column].append(h.offset_ms)

    tracks: Dict[str, TrackPathology] = {}
    for col in range(num_cols):
        lane = column_to_canonical_lane(col)
        cnt, mean_val, std_val, ur_val = _compute_distribution(col_offsets[col])
        tracks[lane] = TrackPathology(
            column=col,
            lane=lane,
            hit_count=cnt,
            mean_error_ms=mean_val,
            std_error_ms=std_val,
            ur=ur_val,
        )

    # Bimanual: Left = cols 0, 1, 2; Right = cols 4, 5, 6; Center col 3 = S (isolated)
    left_offsets: List[float] = []
    right_offsets: List[float] = []
    for col in range(num_cols):
        if col in (0, 1, 2):
            left_offsets.extend(col_offsets[col])
        elif col in (4, 5, 6):
            right_offsets.extend(col_offsets[col])

    l_cnt, l_m, _, l_ur = _compute_distribution(left_offsets)
    r_cnt, r_m, _, r_ur = _compute_distribution(right_offsets)

    load_asymmetry = max(l_cnt, r_cnt) / max(1, min(l_cnt, r_cnt))
    if l_ur > 0.0 and r_ur > 0.0:
        ur_asymmetry = max(l_ur, r_ur) / min(l_ur, r_ur)
    elif l_ur > 0.0 and r_ur == 0.0:
        ur_asymmetry = l_ur
    elif r_ur > 0.0 and l_ur == 0.0:
        ur_asymmetry = r_ur
    else:
        ur_asymmetry = 1.0

    bimanual = BimanualPathology(
        left_hit_count=l_cnt,
        right_hit_count=r_cnt,
        left_mean_error_ms=l_m,
        right_mean_error_ms=r_m,
        left_ur=l_ur,
        right_ur=r_ur,
        load_asymmetry_ratio=load_asymmetry,
        ur_asymmetry_ratio=ur_asymmetry,
        asymmetry_ratio=ur_asymmetry,
    )

    # Jack Stagnation Drift Analysis (Delta k = 1 intervals)
    jack_drift = _compute_jack_stagnation_drift(valid_hits, beatmap, num_cols)

    # LN Release Decoupling (Head vs Tail, Panic early releases vs Sticky late holds)
    ln_release = _compute_ln_release_pathology(alignment.aligned_hits)

    # Cascade Failure Precursor (500ms motif topology leading to first fatal break)
    cascade_precursor = _compute_cascade_precursor(alignment.aligned_hits, beatmap)

    return PathologyReport(
        tracks=tracks,
        bimanual=bimanual,
        jack_drift=jack_drift,
        ln_release=ln_release,
        cascade_precursor=cascade_precursor,
    )


def _compute_ln_release_pathology(hits: List[AlignedHit]) -> LNReleasePathology:
    """
    Decouples LN head press from tail release, quantifying release bias and hold stickiness.
    """
    ln_hits = [h for h in hits if h.note_type == NoteType.LN]
    total_lns = len(ln_hits)
    if total_lns == 0:
        return LNReleasePathology()

    head_offsets = [h.offset_ms for h in ln_hits if h.offset_ms is not None]
    head_cnt, head_m, _, head_ur = _compute_distribution(head_offsets)

    tail_offsets: List[float] = [
        h.tail_offset_ms for h in ln_hits if h.tail_offset_ms is not None
    ]
    evaluated_releases = len(tail_offsets)
    if evaluated_releases == 0:
        return LNReleasePathology(
            total_lns=total_lns,
            head_hit_count=head_cnt,
            head_mean_error_ms=head_m,
            head_ur=head_ur,
        )

    PANIC_THRESHOLD_MS = -30.0
    STICKY_THRESHOLD_MS = 30.0

    panic_count = sum(1 for off in tail_offsets if off < PANIC_THRESHOLD_MS)
    sticky_count = sum(1 for off in tail_offsets if off > STICKY_THRESHOLD_MS)

    _, tail_m, _, tail_ur = _compute_distribution(tail_offsets)

    return LNReleasePathology(
        total_lns=total_lns,
        head_hit_count=head_cnt,
        head_mean_error_ms=head_m,
        head_ur=head_ur,
        evaluated_releases=evaluated_releases,
        mean_tail_offset_ms=tail_m,
        tail_ur=tail_ur,
        sticky_count=sticky_count,
        sticky_rate=sticky_count / evaluated_releases,
        panic_release_count=panic_count,
        panic_release_rate=panic_count / evaluated_releases,
    )


def _compute_jack_stagnation_drift(
    valid_hits: List[AlignedHit],
    beatmap: Beatmap7K,
    num_cols: int,
) -> JackDriftReport:
    """
    Fits linear regression on hit offsets across Delta k = 1 continuous stagnation intervals (ADR-0007).
    """
    # Map each hit object to its chord step index k
    steps = _partition_chord_steps(beatmap)
    note_to_step_k: Dict[Tuple[int, float], int] = {}
    for k, step in enumerate(steps):
        for ho in step:
            note_to_step_k[(ho.column, ho.time)] = k

    col_hits: Dict[int, List[AlignedHit]] = {c: [] for c in range(num_cols)}
    for h in valid_hits:
        if 0 <= h.column < num_cols:
            col_hits[h.column].append(h)

    streak_x: List[float] = []
    streak_y: List[float] = []
    total_stagnation_notes = 0

    def _flush_streak(streak: List[AlignedHit]) -> None:
        nonlocal total_stagnation_notes
        if len(streak) >= 2:
            t0 = streak[0].target_time
            for sh in streak:
                if sh.offset_ms is not None:
                    streak_x.append((sh.target_time - t0) / 1000.0)
                    streak_y.append(sh.offset_ms)
            total_stagnation_notes += len(streak)

    for col in range(num_cols):
        hits = sorted(col_hits[col], key=lambda h: h.target_time)
        if len(hits) < 2:
            continue

        current_streak: List[AlignedHit] = []
        for h in hits:
            if not current_streak:
                current_streak.append(h)
            else:
                prev = current_streak[-1]
                dt = h.target_time - prev.target_time
                prev_k = note_to_step_k.get((prev.column, prev.target_time), -1)
                curr_k = note_to_step_k.get((h.column, h.target_time), -1)
                delta_k = curr_k - prev_k if (curr_k >= 0 and prev_k >= 0) else 1

                # Stagnation criterion: Delta k = 1 and dt <= T_jack (ADR-0007)
                if delta_k == 1 and 0.0 < dt <= CHORDJACK_STEP_INTERVAL_MS:
                    current_streak.append(h)
                else:
                    _flush_streak(current_streak)
                    current_streak = [h]

        _flush_streak(current_streak)

    if len(streak_x) < 2:
        return JackDriftReport(stagnation_jack_count=total_stagnation_notes)

    x_arr = np.array(streak_x, dtype=float)
    y_arr = np.array(streak_y, dtype=float)

    if np.all(x_arr == x_arr[0]):
        return JackDriftReport(stagnation_jack_count=total_stagnation_notes)

    slope, intercept = np.polyfit(x_arr, y_arr, 1)
    y_pred = slope * x_arr + intercept
    ss_tot = float(np.sum((y_arr - np.mean(y_arr)) ** 2))
    ss_res = float(np.sum((y_arr - y_pred) ** 2))
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-6 else 1.0
    r_squared = max(0.0, min(1.0, float(r_squared)))

    fatigue_alert = bool(slope > 15.0 and total_stagnation_notes >= 4)

    return JackDriftReport(
        stagnation_jack_count=total_stagnation_notes,
        slope_ms_per_s=float(slope),
        intercept_ms=float(intercept),
        r_squared=float(r_squared),
        fatigue_alert=fatigue_alert,
    )


def _compute_cascade_precursor(
    hits: List[AlignedHit],
    beatmap: Beatmap7K,
) -> Optional[CascadePrecursor]:
    """
    Identifies the first fatal combo break (Miss/Bad) and extracts preceding 500ms motif topology.
    """
    sorted_hits = sorted(hits, key=lambda h: h.target_time)
    first_break = next(
        (h for h in sorted_hits if h.judgment in (HitJudgment.MISS, HitJudgment.MEH)),
        None,
    )
    if first_break is None:
        return None

    t_fatal = first_break.target_time
    col_fatal = first_break.column
    t_start = max(0.0, t_fatal - 500.0)

    precursor_notes = [
        ho for ho in beatmap.hit_objects if t_start <= ho.time <= t_fatal
    ]
    precursor_notes.sort(key=lambda ho: ho.time)
    note_count = len(precursor_notes)

    # Check Bracket Phase Inversion (ADR-0008, CONTEXT.md)
    # Consecutive steps with dt < 120ms between {0, 2} (outer) and {1} (inner), or {4, 6} vs {5}
    has_bracket = False
    prec_steps = _partition_chord_steps(
        Beatmap7K(hit_objects=precursor_notes, circle_size=beatmap.circle_size, overall_difficulty=beatmap.overall_difficulty)
    )
    for i in range(1, len(prec_steps)):
        s_prev = prec_steps[i - 1]
        s_curr = prec_steps[i]
        dt_step = s_curr[0].time - s_prev[0].time
        if 0.0 < dt_step < BRACKET_PHASE_INVERSION_WINDOW_MS:
            prev_cols = {ho.column for ho in s_prev}
            curr_cols = {ho.column for ho in s_curr}

            prev_left = {c for c in prev_cols if c in (0, 1, 2)}
            curr_left = {c for c in curr_cols if c in (0, 1, 2)}
            if ({0, 2}.issubset(prev_left) and 1 in curr_left) or (1 in prev_left and {0, 2}.issubset(curr_left)):
                has_bracket = True
                break

            prev_right = {c for c in prev_cols if c in (4, 5, 6)}
            curr_right = {c for c in curr_cols if c in (4, 5, 6)}
            if ({4, 6}.issubset(prev_right) and 5 in curr_right) or (5 in prev_right and {4, 6}.issubset(curr_right)):
                has_bracket = True
                break

    # Fallback to loose bracket if only two notes in steps
    if not has_bracket:
        left_cols = {ho.column for ho in precursor_notes if ho.column in (0, 1, 2)}
        right_cols = {ho.column for ho in precursor_notes if ho.column in (4, 5, 6)}
        if (0 in left_cols and 2 in left_cols and 1 in left_cols) or (4 in right_cols and 6 in right_cols and 5 in right_cols):
            has_bracket = True

    # Check for LN negative-space density
    ln_count = sum(
        1 for ho in precursor_notes
        if ho.note_type == NoteType.LN or (ho.end_time is not None and ho.end_time > t_start)
    )
    has_ln_negative_space = ln_count >= 2

    # Check for jack stagnation in precursor
    has_jack = False
    for col in range(beatmap.circle_size if beatmap.circle_size > 0 else 7):
        c_notes = [ho for ho in precursor_notes if ho.column == col]
        for i in range(1, len(c_notes)):
            if 0 < (c_notes[i].time - c_notes[i - 1].time) <= CHORDJACK_STEP_INTERVAL_MS:
                has_jack = True
                break
        if has_jack:
            break

    if has_bracket:
        dominant_tech = PrecursorMotif.BRACKET.value
    elif has_ln_negative_space:
        dominant_tech = PrecursorMotif.LN_INVERSE.value
    elif has_jack:
        dominant_tech = PrecursorMotif.CHORDJACK.value
    else:
        dominant_tech = PrecursorMotif.STREAM.value

    return CascadePrecursor(
        fatal_time_ms=t_fatal,
        fatal_column=col_fatal,
        precursor_start_ms=t_start,
        precursor_note_count=note_count,
        has_bracket_inversion=has_bracket,
        has_ln_negative_space=has_ln_negative_space,
        dominant_technique=dominant_tech,
    )
