"""
WindowedPeakBatchPruner - Closed-loop strain-driven batch pruner (ADR-0011, SPEC-P5.1-03).

Iteratively identifies and dampens localized peak strain windows:
1. Computes continuous bimanual strain timeseries S(t) using proj7k.strain.
2. Identifies peak windows where S(t) > S_target.
3. Filters candidates against MetricSkeletonDetector composite invariants.
4. Ranks candidate removals incorporating marginal strain contributions and
   bimanual flux asymmetry penalties.
5. Batch-prunes candidates (15% - 25% per iteration) with DualGateValidator rollback.

**What decides convergence (issue #50).** The windows are located on the strain curve — they
have to be, since pruning picks notes off it — but *whether the chart has arrived* is the
engine's own star rating when the caller names a target star (`target_sr`). It used to be a
strain threshold, which was the same claim only while the rating was a monotone function of
P90 strain; since ADR-0016 a chart's rating is the largest of its eight absolute technique
stars, so a strain reading can no longer decide it. The strain threshold stays as the *window
selector*: `two_tier.TwoTierDanMapper` derives it from the target star through the strain anchor
law, which is a monotone proxy for "how high a peak is too high", and the star is the judge of
where the loop stops. Callers that name an explicit `--target-strain` keep the old criterion —
asking for a strain is asking for a strain.
"""

from dataclasses import dataclass, field
import math
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from proj7k.features import extract_beatmap_features
from proj7k.parser import Beatmap7K, HitObject, NoteType
from proj7k.radar import RadarOptions, compute_technique_radar
from proj7k.rating import RatingOptions, synthesize_star_rating
from proj7k.strain import StrainOptions, StrainTimeseriesProfile, compute_dual_hand_strain
from proj7k.downscaler.mutation import apply_pure_deletion
from proj7k.downscaler.skeleton import MetricSkeletonDetector
from proj7k.downscaler.balancer import (
    BimanualFluxBalancer,
    LEFT_LANES,
    RIGHT_LANES,
    CENTER_LANE,
)
from proj7k.downscaler.validator import DualGateValidator, ValidationResult


@dataclass(frozen=True)
class PruneIterationRecord:
    """Telemetry record for each batch pruning iteration."""
    iteration: int
    surviving_notes: int
    removed_in_batch: int
    p90_strain: float
    peak_strain: float
    flux_ratio: Tuple[float, float]
    passed_validation: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration,
            "surviving_notes": self.surviving_notes,
            "removed_in_batch": self.removed_in_batch,
            "p90_strain": round(self.p90_strain, 3),
            "peak_strain": round(self.peak_strain, 3),
            "flux_ratio": (round(self.flux_ratio[0], 3), round(self.flux_ratio[1], 3)),
            "passed_validation": self.passed_validation,
        }


@dataclass(frozen=True)
class PruningResult:
    """Result of closed-loop windowed peak batch pruning."""
    downscaled_beatmap: Beatmap7K
    iterations_run: int
    converged: bool
    initial_p90_strain: float
    final_p90_strain: float
    initial_peak_strain: float
    final_peak_strain: float
    total_notes_removed: int
    warnings: List[str] = field(default_factory=list)
    history: List[PruneIterationRecord] = field(default_factory=list)
    #: The star criterion's readings, present only when the caller named a target star. The
    #: strain readings above stay reported either way: they are what the windows were chosen on.
    target_sr: Optional[float] = None
    initial_star: Optional[float] = None
    final_star: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "iterations_run": self.iterations_run,
            "converged": self.converged,
            "initial_p90_strain": round(self.initial_p90_strain, 3),
            "final_p90_strain": round(self.final_p90_strain, 3),
            "initial_peak_strain": round(self.initial_peak_strain, 3),
            "final_peak_strain": round(self.final_peak_strain, 3),
            "total_notes_removed": self.total_notes_removed,
            "warnings": self.warnings,
            "history": [rec.to_dict() for rec in self.history],
        }
        if self.target_sr is not None:
            d["target_sr"] = round(self.target_sr, 3)
            d["initial_star"] = None if self.initial_star is None else round(self.initial_star, 3)
            d["final_star"] = None if self.final_star is None else round(self.final_star, 3)
        return d


class WindowedPeakBatchPruner:
    """
    Closed-loop iterative pruner for osu!mania 7K beatmaps (SPEC-P5.1-03, ADR-0011).
    """

    def __init__(
        self,
        prune_ratio: float = 0.20,
        max_iterations: int = 25,
        tolerance: float = 0.05,
        strain_options: Optional[StrainOptions] = None,
        validator: Optional[DualGateValidator] = None,
        balancer: Optional[BimanualFluxBalancer] = None,
    ):
        # Enforce canonical single-round pruning bounds: 15% ~ 25% (ADR-0011, Spec line 65)
        self.prune_ratio = max(0.15, min(0.25, prune_ratio))
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.strain_options = strain_options or StrainOptions()
        self.validator = validator or DualGateValidator()
        self.balancer = balancer or BimanualFluxBalancer()

    def star_of(
        self,
        beatmap: Beatmap7K,
        p90_strain: float,
        rating_options: Optional[RatingOptions] = None,
        radar_options: Optional[RadarOptions] = None,
    ) -> float:
        """
        The engine's own rating of a candidate, read off the strain profile the loop already has.

        The profile is passed in rather than recomputed: `synthesize_star_rating` needs the P90
        strain, and the pruner computes one every iteration for the window search anyway. The
        feature tensor and the radar are what the star needs beyond it.
        """
        options = rating_options or RatingOptions()
        radar = compute_technique_radar(
            beatmap,
            features=extract_beatmap_features(beatmap),
            options=radar_options,
            calibration=options.calibration,
        )
        return synthesize_star_rating(radar, p90_strain=p90_strain, options=options).star_rating

    def prune(
        self,
        beatmap: Beatmap7K,
        target_strain: float,
        dominant_technique: Optional[str] = None,
        target_sr: Optional[float] = None,
        rating_options: Optional[RatingOptions] = None,
        radar_options: Optional[RadarOptions] = None,
    ) -> PruningResult:
        """
        Executes multi-round closed-loop peak-window pruning towards the target.

        `target_sr` switches the convergence criterion to the engine's star (see the module
        docstring); `target_strain` is then the peak-window threshold rather than the loop's
        stopping rule. Without it the loop stops on strain, which is what an explicit
        `--target-strain` asks for.
        """
        initial_profile = compute_dual_hand_strain(beatmap, options=self.strain_options)
        init_p90 = initial_profile.p90_strain
        init_peak = initial_profile.peak_strain
        warnings: List[str] = []
        initial_star = (
            self.star_of(beatmap, init_p90, rating_options, radar_options)
            if target_sr is not None
            else None
        )

        arrived = (
            initial_star <= target_sr * (1.0 + self.tolerance)
            if initial_star is not None and target_sr is not None
            else init_p90 <= target_strain * (1.0 + self.tolerance)
        )
        if initial_star is not None:
            if arrived:
                warnings.append(
                    f"Beatmap initial star rating ({initial_star:.3f}) is already <= target "
                    f"({target_sr:.3f}); no downscaling required."
                )
        if arrived:
            if initial_star is None:
                warnings.append(
                    f"Beatmap initial P90 strain ({init_p90:.2f}) is already <= target threshold ({target_strain:.2f}); no downscaling required."
                )
            return PruningResult(
                downscaled_beatmap=beatmap,
                iterations_run=0,
                converged=True,
                initial_p90_strain=init_p90,
                final_p90_strain=init_p90,
                initial_peak_strain=init_peak,
                final_peak_strain=init_peak,
                total_notes_removed=0,
                warnings=warnings,
                history=[],
                target_sr=target_sr,
                initial_star=initial_star,
                final_star=initial_star,
            )

        current_bm = beatmap
        history: List[PruneIterationRecord] = []
        converged = False

        half_window_s = self.strain_options.window_s / 2.0

        for it in range(1, self.max_iterations + 1):
            prof = compute_dual_hand_strain(current_bm, options=self.strain_options)

            # Check convergence condition: the star when a target star was named, else strain.
            current_star = (
                self.star_of(current_bm, prof.p90_strain, rating_options, radar_options)
                if target_sr is not None
                else None
            )
            if current_star is not None:
                if current_star <= target_sr * (1.0 + self.tolerance):
                    converged = True
                    break
            elif prof.p90_strain <= target_strain * (1.0 + self.tolerance):
                converged = True
                break

            # 1. Locate Peak Windows where S(t) > target_strain
            peak_time_windows: List[Tuple[float, float]] = []
            for t_sec, s_val in zip(prof.times, prof.combined_strain):
                if s_val > target_strain:
                    w_start_ms = (t_sec - half_window_s) * 1000.0
                    w_end_ms = (t_sec + half_window_s) * 1000.0
                    peak_time_windows.append((w_start_ms, w_end_ms))

            if not peak_time_windows:
                # If no sample exceeds target_strain, we have effectively eliminated all peaks
                converged = True
                break

            # 2. Extract hit objects strictly residing within peak windows
            peak_candidates: List[HitObject] = [
                ho for ho in current_bm.hit_objects
                if any(w_start <= ho.time <= w_end for w_start, w_end in peak_time_windows)
            ]

            # 3. Filter candidates against composite metric invariants (Downbeat + Chord protection)
            detector = MetricSkeletonDetector(current_bm)
            allowed_cands = detector.filter_candidate_removals(peak_candidates)

            if not allowed_cands:
                warnings.append("Metric skeleton protected all peak candidates; cannot prune further safely.")
                break

            # 4. Rank candidates incorporating marginal strain contributions and bimanual flux penalty
            l_flux, r_flux = self.balancer.compute_hand_flux(current_bm.hit_objects)
            penalty_l, penalty_r = self.balancer.compute_asymmetry_penalty(l_flux, r_flux)

            # Pre-compute column-wise hit timestamps for local density / jack strain estimation
            col_times: Dict[int, List[float]] = {c: [] for c in range(7)}
            time_counts: Dict[float, int] = {}
            for ho in current_bm.hit_objects:
                col_times[ho.column].append(ho.time)
                t_key = round(ho.time, 1)
                time_counts[t_key] = time_counts.get(t_key, 0) + 1

            for c in range(7):
                col_times[c].sort()

            time_seen: Set[float] = set()
            scored_candidates: List[Tuple[float, HitObject]] = []
            for ho in allowed_cands:
                t_key = round(ho.time, 1)
                chord_size = time_counts.get(t_key, 1)

                # Marginal strain component 1: Chord density
                # Thin at most one note per multi-note chord in each round to maintain continuity
                if chord_size >= 2:
                    if t_key not in time_seen:
                        chord_component = 1.0 + (chord_size - 1) * 0.60
                        time_seen.add(t_key)
                    else:
                        chord_component = 0.60
                else:
                    chord_component = 1.0

                # Marginal strain component 2: Jack & Burst intensity on same column
                times = col_times[ho.column]
                idx = times.index(ho.time) if ho.time in times else -1
                jack_component = 1.0
                if idx >= 0:
                    dt_prev = (ho.time - times[idx - 1]) if idx > 0 else float("inf")
                    dt_next = (times[idx + 1] - ho.time) if idx + 1 < len(times) else float("inf")
                    min_dt = min(dt_prev, dt_next)
                    if min_dt < self.strain_options.jack_threshold_ms:
                        jack_component += (self.strain_options.jack_threshold_ms - min_dt) / self.strain_options.jack_threshold_ms

                # Marginal strain component 3: Hand asymmetry penalty
                if ho.column in LEFT_LANES:
                    hand_pen = penalty_l
                elif ho.column in RIGHT_LANES:
                    hand_pen = penalty_r
                else:
                    hand_pen = (penalty_l + penalty_r) / 2.0

                # If dominant technique is Jack, prioritize thinning chords while preserving single jack stems
                tech_bias = 1.0
                if dominant_technique == "jack" and chord_size >= 2:
                    tech_bias = 1.3
                elif dominant_technique in ("stream", "speed") and chord_size == 1:
                    tech_bias = 1.1
                elif dominant_technique and dominant_technique.lower().startswith("ln_"):
                    if ho.note_type == NoteType.RICE:
                        tech_bias = 1.3
                    else:
                        tech_bias = 0.7

                score = (chord_component * jack_component * tech_bias) / max(0.01, hand_pen)
                scored_candidates.append((score, ho))

            # 5. Windowed Batch pruning (15% ~ 25% per peak window slice)
            window_ms = max(500.0, self.strain_options.window_s * 1000.0)
            cands_by_win: Dict[int, List[Tuple[float, HitObject]]] = {}
            for score, ho in scored_candidates:
                win_idx = int(ho.time // window_ms)
                cands_by_win.setdefault(win_idx, []).append((score, ho))

            batch: List[HitObject] = []
            for win_idx, win_scored in cands_by_win.items():
                win_scored.sort(key=lambda x: x[0], reverse=True)
                k = max(1, int(round(len(win_scored) * self.prune_ratio)))
                batch.extend(x[1] for x in win_scored[:k])

            if not batch and scored_candidates:
                scored_candidates.sort(key=lambda x: x[0], reverse=True)
                batch = [scored_candidates[0][1]]

            cand_bm = apply_pure_deletion(current_bm, notes_to_remove=batch)

            # 6. Validate with DualGateValidator (Rollback on violation)
            val_res = self.validator.validate(beatmap, cand_bm)
            if not val_res.passed:
                if len(batch) > 1:
                    smaller_batch = batch[::2]
                    cand_bm = apply_pure_deletion(current_bm, notes_to_remove=smaller_batch)
                    val_res = self.validator.validate(beatmap, cand_bm)
                    if not val_res.passed and len(smaller_batch) > 1:
                        smaller_batch_4 = smaller_batch[::2]
                        cand_bm = apply_pure_deletion(current_bm, notes_to_remove=smaller_batch_4)
                        val_res = self.validator.validate(beatmap, cand_bm)
                        if val_res.passed:
                            batch = smaller_batch_4
                    elif val_res.passed:
                        batch = smaller_batch

                    if not val_res.passed:
                        warnings.append(f"Pruning stopped at iteration {it} to preserve technique invariants.")
                        break
                else:
                    warnings.append(f"Pruning stopped at iteration {it} due to technique gate constraint.")
                    break

            # Commit batch
            current_bm = cand_bm
            current_flux_ratio = self.balancer.compute_flux_ratio(current_bm.hit_objects)

            history.append(
                PruneIterationRecord(
                    iteration=it,
                    surviving_notes=len(current_bm.hit_objects),
                    removed_in_batch=len(batch),
                    p90_strain=prof.p90_strain,
                    peak_strain=prof.peak_strain,
                    flux_ratio=current_flux_ratio,
                    passed_validation=True,
                )
            )

        final_profile = compute_dual_hand_strain(current_bm, options=self.strain_options)
        total_removed = len(beatmap.hit_objects) - len(current_bm.hit_objects)
        final_star = (
            self.star_of(current_bm, final_profile.p90_strain, rating_options, radar_options)
            if target_sr is not None
            else None
        )
        if final_star is not None:
            arrived_at_end = final_star <= target_sr * (1.0 + self.tolerance)
        else:
            arrived_at_end = final_profile.p90_strain <= target_strain * (1.0 + self.tolerance)

        return PruningResult(
            downscaled_beatmap=current_bm,
            iterations_run=len(history),
            converged=converged or arrived_at_end,
            initial_p90_strain=init_p90,
            final_p90_strain=final_profile.p90_strain,
            initial_peak_strain=init_peak,
            final_peak_strain=final_profile.peak_strain,
            total_notes_removed=total_removed,
            warnings=warnings,
            history=history,
            target_sr=target_sr,
            initial_star=initial_star,
            final_star=final_star,
        )
