import math
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any, Tuple, Union
from proj7k.parser import (
    Beatmap7K,
    NoteType,
    dominant_bpm,
    notation_normalized_bpm,
    uninherited_timing_points,
)
from proj7k.physics import DEFAULT_BPM
from proj7k.window import generate_all_barlines
from proj7k.scaling import compute_action_window, compute_inverse_score


@dataclass(frozen=True)
class FeatureOptions:
    """
    Configuration of the feature tensor's own calibration.

    The feature tensor is not a neutral summary of a chart: which inter-note gaps count as a
    snap, how irregular a rhythm has to be before it reads as unorthodox, and how finely the
    locked-finger step function is sampled are all calibration decisions, and every one of them
    reaches a star rating through the radar's drivers. They live here, next to the stage that
    applies them, for the same reason `RadarOptions` and `StrainOptions` do — so a change moves
    the engine version (ADR-0014) instead of silently leaving stale ratings in the game.

    The three rhythm weights are a convex combination: they are read as a weighted mean of the
    three irregularity signals, so `snap_variance_weight + jerk_weight + snap_mix_weight = 1`.
    """
    #: Resolution, in milliseconds, of the locked-finger step function L(t): the chart is
    #: sampled on this grid to average how many fingers are held down at once.
    step_ms: int = 10

    #: How far past the last note the barline generator is asked to run, so the final measure
    #: has a complete beat grid to close on.
    barline_overrun_ms: float = 30000.0

    #: Snap matching: an inter-note gap is read as a canonical snap when it lands within
    #: `abs_tol` or `rel_tol * snap` of it. Used to bucket notes for the snap-variance entropy.
    snap_match_abs_tol: float = 0.02
    snap_match_rel_tol: float = 0.10

    #: Snap banding: the looser tolerance that only decides whether a gap is binary, ternary or
    #: irregular. Deliberately looser than the matching tolerance — this splits the distribution
    #: into three classes rather than naming the snap.
    snap_band_abs_tol: float = 0.015
    snap_band_rel_tol: float = 0.08

    #: The interval band, in milliseconds, of a pair of steps that is read at all: shorter is a
    #: chord or duplicate, longer is a break in the pattern rather than a rhythm.
    min_step_ms: float = 10.0
    max_step_ms: float = 2000.0

    #: Micro-timing jerk: |Δt_next - Δt| / max(Δt, DENOMINATOR_MS), clipped at `jerk_cap` so a
    #: single torn 1/8 does not dominate the average, then scaled by `jerk_normalizer` on its
    #: way into the rhythm term.
    jerk_denominator_ms: float = 25.0
    jerk_cap: float = 2.5
    jerk_normalizer: float = 2.5

    #: Weights of the three irregularity signals in `rhythm_irreg` (see the class docstring).
    snap_variance_weight: float = 0.40
    jerk_weight: float = 0.30
    snap_mix_weight: float = 0.30

    #: Canonical snap table: (label, beat-fraction) pairs, in ascending fraction order, with the
    #: first match winning. The labels are the buckets the snap-variance entropy is taken over.
    canonical_snaps: Tuple[Tuple[float, float], ...] = (
        (1 / 16, 0.0625), (1 / 12, 0.0833), (1 / 8, 0.125), (1 / 6, 0.1667),
        (1 / 4, 0.25), (1 / 3, 0.3333), (1 / 2, 0.5), (3 / 4, 0.75), (1.0, 1.0),
    )

    #: The binary and ternary subdivisions the snap banding tests against, as beat fractions.
    binary_snaps: Tuple[float, ...] = (1 / 16, 1 / 8, 1 / 4, 1 / 2, 1.0, 2.0)
    ternary_snaps: Tuple[float, ...] = (1 / 24, 1 / 12, 1 / 6, 1 / 3, 2 / 3)


@dataclass
class BeatmapFeatures:
    total_notes: int
    rice_count: int
    ln_count: int
    hold_pct: float
    avg_nps: float
    peak_4m_nps: float
    duration_seconds: float
    peak_1b_nps: float = 0.0

    # Hand topology
    gap1_count: int = 0
    gap1_density: float = 0.0
    adj_count: int = 0
    adj_density: float = 0.0

    # Degree-of-freedom suppression
    mean_locked_fingers: float = 0.0
    lockout_profile: Dict[int, float] = field(default_factory=lambda: {i: 0.0 for i in range(8)})

    # Antiphase articulation
    antiphase_count: int = 0
    antiphase_rate: float = 0.0

    # Release articulation (see `_extract` step 5b)
    isolated_tail_count: int = 0
    isolated_tail_share: float = 0.0
    release_lock_depth: float = 0.0

    # Action clock window and calibrated inverse score
    delta_t_action: float = 0.0
    inverse_score: float = 0.0

    # 4D Unorthodox Permutation and Rhythm Features (ADR-0008)
    spatial_entropy: float = 0.0
    snap_variance_entropy: float = 0.0
    microtiming_jerk: float = 0.0
    rhythm_irreg: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["lockout_profile"] = {str(k): v for k, v in self.lockout_profile.items()}
        return d


def _lands_in_snap_band(
    frac: float, snaps: Tuple[float, ...], options: FeatureOptions
) -> bool:
    """Whether a beat fraction falls within the banding tolerance of any of `snaps`."""
    return any(
        abs(frac - snap) <= max(options.snap_band_abs_tol, snap * options.snap_band_rel_tol)
        for snap in snaps
    )


def _calc_rate(count: int, duration_s: float) -> float:
    return round(count / duration_s, 4) if duration_s > 0 else 0.0


def get_dominant_bpm(beatmap: Beatmap7K, default: float = DEFAULT_BPM) -> float:
    """
    Dominant BPM rounded to 2 decimals, for feature/checksum-stable consumption.

    The tempo selection itself lives in `parser.dominant_bpm` (the single source shared with
    the strain accumulator); only the 2-decimal presentation rounding is applied here.
    """
    return round(dominant_bpm(beatmap, default=default), 2)


def extract_beatmap_features(
    beatmap: Beatmap7K,
    bpm: Optional[float] = None,
    options: Optional[FeatureOptions] = None,
) -> BeatmapFeatures:
    """
    Extracts baseline spatiotemporal density and timing features, as well as
    physiological topology, degree-of-freedom suppression, and antiphase events:
    - total_notes, rice_count, ln_count
    - hold_pct (LN notes percentage)
    - avg_nps (Full-beatmap average NPS)
    - peak_4m_nps (Peak 4-measure rolling window NPS)
    - peak_1b_nps (Peak 1-beat burst window NPS)
    - duration_seconds
    - gap1_count, gap1_density
    - adj_count, adj_density
    - mean_locked_fingers, lockout_profile
    - antiphase_count, antiphase_rate

    `bpm` is a call-site override; its fallback is the chart's own dominant tempo.
    """
    opts = options or FeatureOptions()

    total_notes = len(beatmap.hit_objects)
    if total_notes == 0:
        return BeatmapFeatures(
            total_notes=0,
            rice_count=0,
            ln_count=0,
            hold_pct=0.0,
            avg_nps=0.0,
            peak_4m_nps=0.0,
            duration_seconds=0.0,
            peak_1b_nps=0.0,
            gap1_count=0,
            gap1_density=0.0,
            adj_count=0,
            adj_density=0.0,
            mean_locked_fingers=0.0,
            lockout_profile={i: 0.0 for i in range(8)},
            antiphase_count=0,
            antiphase_rate=0.0,
            delta_t_action=0.0,
            inverse_score=0.0,
            spatial_entropy=0.0,
            snap_variance_entropy=0.0,
            microtiming_jerk=0.0,
            rhythm_irreg=0.0,
        )

    rice_count = sum(1 for ho in beatmap.hit_objects if ho.note_type == NoteType.RICE)
    ln_count = total_notes - rice_count
    hold_pct = (ln_count / total_notes) * 100.0

    # Calculate overall duration
    start_ms = min(ho.time for ho in beatmap.hit_objects)
    end_ms = max(
        (ho.end_time if (ho.note_type == NoteType.LN and ho.end_time is not None) else ho.time)
        for ho in beatmap.hit_objects
    )

    duration_s = max((end_ms - start_ms) / 1000.0, 0.0)
    avg_nps = (total_notes / duration_s) if duration_s > 0 else 0.0

    barlines = generate_all_barlines(beatmap, max_time_ms=end_ms + opts.barline_overrun_ms)
    measure_starts = [b for b in barlines if b.is_measure_start]

    # 1. Calculate Peak-4M NPS (4-measure rolling window, as the field name says)
    peak_4m_nps = avg_nps
    if len(measure_starts) >= 5:
        max_window_nps = 0.0
        found_valid_window = False
        for i in range(len(measure_starts) - 4):
            window_start = measure_starts[i].time
            window_end = measure_starts[i + 4].time
            if window_start > end_ms:
                break
            if window_start < start_ms or window_end > end_ms:
                continue
            window_duration_s = (window_end - window_start) / 1000.0
            if window_duration_s <= 0:
                continue

            found_valid_window = True
            window_notes_count = sum(1 for ho in beatmap.hit_objects if window_start <= ho.time < window_end)
            window_nps = window_notes_count / window_duration_s
            if window_nps > max_window_nps:
                max_window_nps = window_nps

        if found_valid_window:
            peak_4m_nps = max_window_nps

    # 2. Calculate Peak-1B NPS (1-beat burst window)
    peak_1b_nps = avg_nps
    if len(barlines) >= 2:
        max_beat_nps = 0.0
        found_valid_beat = False
        for i in range(len(barlines) - 1):
            beat_start = barlines[i].time
            beat_end = barlines[i + 1].time
            if beat_start > end_ms:
                break
            if beat_start < start_ms or beat_end > end_ms:
                continue
            beat_duration_s = (beat_end - beat_start) / 1000.0
            if beat_duration_s <= 0:
                continue

            found_valid_beat = True
            beat_notes_count = sum(1 for ho in beatmap.hit_objects if beat_start <= ho.time < beat_end)
            beat_nps = beat_notes_count / beat_duration_s
            if beat_nps > max_beat_nps:
                max_beat_nps = beat_nps

        if found_valid_beat:
            peak_1b_nps = max_beat_nps

    # Single pass over hit objects to collect chord press ticks and release ticks
    notes_by_time: Dict[int, List[int]] = {}
    ticks_released: Dict[int, List[int]] = {}

    for ho in beatmap.hit_objects:
        st_tick = int(round(ho.time))
        notes_by_time.setdefault(st_tick, []).append(ho.column)
        if ho.note_type == NoteType.LN and ho.end_time is not None:
            et_tick = int(round(ho.end_time))
            ticks_released.setdefault(et_tick, []).append(ho.column)

    # 3. Calculate hand topology metrics ([gap:1] and [adj])
    gap1_count = 0
    adj_count = 0
    for t_key, cols in notes_by_time.items():
        unique_cols = sorted(set(cols))
        l_cols = [c for c in unique_cols if c in (0, 1, 2)]
        r_cols = [c for c in unique_cols if c in (4, 5, 6)]

        # Left hand evaluation
        if len(l_cols) == 2:
            if l_cols == [0, 2]:
                gap1_count += 1
            elif l_cols in ([0, 1], [1, 2]):
                adj_count += 1

        # Right hand evaluation
        if len(r_cols) == 2:
            if r_cols == [4, 6]:
                gap1_count += 1
            elif r_cols in ([4, 5], [5, 6]):
                adj_count += 1

    gap1_density = _calc_rate(gap1_count, duration_s)
    adj_density = _calc_rate(adj_count, duration_s)

    # 4. Compute finger lockout profile and mean locked fingers (sampling every step_ms)
    # Merge overlapping LN intervals per column to guarantee each physical finger is at most locked once
    merged_ln_intervals: List[Tuple[float, float]] = []
    for col in range(7):
        col_lns = sorted(
            [
                (ho.time, ho.end_time)
                for ho in beatmap.hit_objects
                if ho.column == col and ho.note_type == NoteType.LN and ho.end_time is not None
            ],
            key=lambda x: x[0],
        )
        if not col_lns:
            continue
        cur_st, cur_et = col_lns[0]
        for st, et in col_lns[1:]:
            if st <= cur_et:
                cur_et = max(cur_et, et)
            else:
                merged_ln_intervals.append((cur_st, cur_et))
                cur_st, cur_et = st, et
        merged_ln_intervals.append((cur_st, cur_et))

    lock_counts: Dict[int, int] = {i: 0 for i in range(8)}
    total_samples = 0
    sum_locked = 0

    if opts.step_ms > 0 and end_ms >= start_ms:
        import math
        num_samples = int((end_ms - start_ms) // opts.step_ms) + 1
        total_samples = num_samples

        if merged_ln_intervals:
            diff = [0] * (num_samples + 1)
            for st, et in merged_ln_intervals:
                if et <= start_ms or st >= end_ms:
                    continue
                j_start = max(0, math.ceil((st - start_ms) / opts.step_ms))
                j_end = min(num_samples, math.ceil((et - start_ms) / opts.step_ms))
                if j_start < j_end:
                    diff[j_start] += 1
                    diff[j_end] -= 1

            cur_holds = 0
            for j in range(num_samples):
                cur_holds += diff[j]
                active = min(max(cur_holds, 0), 7)
                lock_counts[active] += 1
                sum_locked += active
        else:
            lock_counts[0] = num_samples
    else:
        total_samples = 1
        lock_counts[0] = 1

    mean_locked_fingers = round(sum_locked / max(total_samples, 1), 4)
    lockout_profile = {
        k: round((v / max(total_samples, 1)) * 100.0, 4) for k, v in lock_counts.items()
    }

    # 5. Detect and count antiphase articulation events (ADR-0008)
    # An antiphase event occurs when at the same tick (rounded ms), one track releases (LN tail)
    # while another track (different column) is pressed (Rice or LN head).
    # To prevent Cartesian O(R x P) explosion during concurrent multi-key chord releases/presses,
    # we compute the exclusive physical hand transitions: min(|R_diff|, |P_diff|).
    antiphase_count = 0
    for tick, released_cols in ticks_released.items():
        if tick in notes_by_time:
            pressed_cols = notes_by_time[tick]
            r_set = set(released_cols)
            p_set = set(pressed_cols)
            r_diff = r_set - p_set
            p_diff = p_set - r_set
            antiphase_count += min(len(r_diff), len(p_diff))

    antiphase_rate = _calc_rate(antiphase_count, duration_s)

    # 5b. Release articulation: the tails that have to be timed on their own.
    # Antiphase above counts lifts that coincide with a press elsewhere; those ride along with an
    # action the hands are already making. A tail with nothing else pressed at its own tick has
    # no such anchor and must be placed by itself — that isolated lift is what ADR-0008 calls
    # 抬手时基窗口精度, and it is the quantity the LN Release axis reads.
    isolated_tail_count = sum(
        1
        for ho in beatmap.hit_objects
        if ho.note_type == NoteType.LN
        and ho.end_time is not None
        and not (set(notes_by_time.get(int(round(ho.end_time)), ())) - {ho.column})
    )
    isolated_tail_share = round(isolated_tail_count / ln_count, 4) if ln_count else 0.0

    # 5c. How tied up the hand is when each lift has to happen (CONTEXT.md 自由度压制).
    # A lift's difficulty is not the lift alone: it is the lift placed while the same hand is
    # still holding other keys down — the 尾判难度 + 卡手 the Release ladder is built around. Read
    # per tail against the LN intervals still open in that hand at the tail's own instant, and
    # averaged over the tails rather than summed: the mean is a shape of the chart (how deep
    # into a chord each release lands), while a per-second total mostly restates how dense the
    # chart is — and restating density is what put this axis on top of every hold chart.
    # An isolated tail (`isolated_tail_share` above) is one special case of this: nothing else
    # is pressed anywhere, so nothing anchors the lift either.
    ln_intervals: Dict[int, List[Tuple[float, float]]] = {}
    for ho in beatmap.hit_objects:
        if ho.note_type == NoteType.LN and ho.end_time is not None:
            ln_intervals.setdefault(ho.column, []).append((ho.time, ho.end_time))
    locked_lift_count = 0
    for ho in beatmap.hit_objects:
        if ho.note_type != NoteType.LN or ho.end_time is None:
            continue
        same_hand = (0, 1, 2) if ho.column in (0, 1, 2) else (4, 5, 6)
        locked_lift_count += sum(
            1
            for column in same_hand
            if column != ho.column
            and any(start < ho.end_time < end for start, end in ln_intervals.get(column, ()))
        )
    release_lock_depth = round(locked_lift_count / ln_count, 4) if ln_count else 0.0

    effective_bpm = notation_normalized_bpm(beatmap, override=bpm)
    delta_t_action = compute_action_window(effective_bpm)
    inverse_score = compute_inverse_score(mean_locked_fingers, bpm=effective_bpm, nps=avg_nps)

    # 6. Spatial Transition Entropy (7-track conditional transition entropy) (ADR-0008)
    hos_sorted = sorted(beatmap.hit_objects, key=lambda x: x.time)
    trans: Dict[Tuple[int, int], int] = {}
    lane_counts: Dict[int, int] = {c: 0 for c in range(7)}
    for k in range(len(hos_sorted) - 1):
        c1, c2 = hos_sorted[k].column, hos_sorted[k + 1].column
        trans[(c1, c2)] = trans.get((c1, c2), 0) + 1
        lane_counts[c1] += 1

    tot_trans = sum(trans.values())
    h_spatial = 0.0
    if tot_trans > 0:
        for c1 in range(7):
            n_c1 = lane_counts[c1]
            if n_c1 > 0:
                h_c = 0.0
                for c2 in range(7):
                    cnt = trans.get((c1, c2), 0)
                    if cnt > 0:
                        p_c = cnt / n_c1
                        h_c -= p_c * math.log2(p_c)
                h_spatial += (n_c1 / tot_trans) * h_c
        spatial_entropy = round(h_spatial / math.log2(7.0), 4)
    else:
        spatial_entropy = 0.0

    # 7. Rhythmic Irregularity (Snap Variance Entropy, Micro-timing Jerk, and Mixing) (ADR-0008)
    uninherited = uninherited_timing_points(beatmap)
    # A chart with no usable tempo falls back to the default tempo's beat length rather than a
    # bare millisecond figure, so the fallback cannot drift from DEFAULT_BPM.
    default_bl = (60000.0 / effective_bpm) if effective_bpm > 0 else (60000.0 / DEFAULT_BPM)
    step_times = sorted(list(set(ho.time for ho in hos_sorted)))

    snap_counts: Dict[Union[float, str], int] = {}
    jerks: List[float] = []
    tp_idx = 0
    tot_steps = 0
    bin_cnt = 0
    ter_cnt = 0
    irr_cnt = 0

    for i in range(len(step_times) - 1):
        t1, t2 = step_times[i], step_times[i + 1]
        dt = t2 - t1
        if dt < opts.min_step_ms or dt > opts.max_step_ms:
            continue
        while tp_idx + 1 < len(uninherited) and uninherited[tp_idx + 1].time <= t1:
            tp_idx += 1
        bl = uninherited[tp_idx].beat_length if uninherited else default_bl
        frac = dt / bl

        matched: Union[float, str] = "irr"
        for s_val, s_num in opts.canonical_snaps:
            if abs(frac - s_num) <= max(opts.snap_match_abs_tol, s_num * opts.snap_match_rel_tol):
                matched = s_val
                break
        snap_counts[matched] = snap_counts.get(matched, 0) + 1
        tot_steps += 1

        is_bin = _lands_in_snap_band(frac, opts.binary_snaps, opts)
        is_ter = _lands_in_snap_band(frac, opts.ternary_snaps, opts)
        if is_bin:
            bin_cnt += 1
        elif is_ter:
            ter_cnt += 1
        else:
            irr_cnt += 1

        if i + 2 < len(step_times):
            dt_next = step_times[i + 2] - t2
            if opts.min_step_ms <= dt_next <= opts.max_step_ms:
                j = abs(dt_next - dt) / max(dt, opts.jerk_denominator_ms)
                jerks.append(min(j, opts.jerk_cap))

    h_snap = 0.0
    if tot_steps > 0:
        for s_key, cnt in snap_counts.items():
            p = cnt / tot_steps
            h_snap -= p * math.log2(p)
    # The entropy is over the canonical snap buckets plus the irregular bucket, so that is the
    # distribution's maximum entropy — deriving it keeps the normaliser in step with the table.
    snap_variance_entropy = round(h_snap / math.log2(len(opts.canonical_snaps) + 1), 4)
    microtiming_jerk = round(sum(jerks) / len(jerks), 4) if jerks else 0.0

    p_bin = bin_cnt / max(1, tot_steps)
    p_ter = ter_cnt / max(1, tot_steps)
    p_irr = irr_cnt / max(1, tot_steps)
    h_mix = 0.0
    for p_val in (p_bin, p_ter, p_irr):
        if p_val > 0:
            h_mix -= p_val * math.log2(p_val)
    norm_h_mix = h_mix / math.log2(3.0)

    rhythm_irreg = round(
        snap_variance_entropy * opts.snap_variance_weight
        + min(1.0, microtiming_jerk * opts.jerk_normalizer) * opts.jerk_weight
        + norm_h_mix * opts.snap_mix_weight,
        4,
    )

    return BeatmapFeatures(
        total_notes=total_notes,
        rice_count=rice_count,
        ln_count=ln_count,
        hold_pct=round(hold_pct, 4),
        avg_nps=round(avg_nps, 4),
        peak_4m_nps=round(peak_4m_nps, 4),
        duration_seconds=round(duration_s, 4),
        peak_1b_nps=round(peak_1b_nps, 4),
        gap1_count=gap1_count,
        gap1_density=gap1_density,
        adj_count=adj_count,
        adj_density=adj_density,
        mean_locked_fingers=mean_locked_fingers,
        lockout_profile=lockout_profile,
        antiphase_count=antiphase_count,
        antiphase_rate=antiphase_rate,
        isolated_tail_count=isolated_tail_count,
        isolated_tail_share=isolated_tail_share,
        release_lock_depth=release_lock_depth,
        delta_t_action=delta_t_action,
        inverse_score=inverse_score,
        spatial_entropy=spatial_entropy,
        snap_variance_entropy=snap_variance_entropy,
        microtiming_jerk=microtiming_jerk,
        rhythm_irreg=rhythm_irreg,
    )
