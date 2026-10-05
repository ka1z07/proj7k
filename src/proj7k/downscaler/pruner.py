"""
ExcessLossPruner - closed-loop pruner on the spec-v0.2 engine's difficulty field (ADR-0021, ADR-0011).

The engine answers the downscaler's question exactly: how hard is this chart, in stars, after this
batch of deletions? So the loop converges on the engine's star directly (no strain proxy, no second
scale), and what is left to the pruner is choosing *what* to delete:

1. Trace the chart (`proj7k.field`) and take the target level `D*` that the target star stands for.
2. The peak windows are the one-second windows whose expected loss at `D*` runs above the rate the
   tolerance allows: the stretches carrying the loss that a player at the target level would still suffer.
3. Candidates are the notes in those windows that the metric skeleton lets go (1/1 downbeats, chords).
4. Each candidate is scored by `downscaler.marginal.removal_benefit` (how much of the expected loss at
   `D*` its demand carries through the hand accumulator), damped for the notes that carry the dominant
   skill, divided by the bimanual asymmetry penalty.
5. The top 15-25% of each peak window is deleted as one batch, and the engine re-reads it. The batch is
   halved when it breaks technique preservation or overshoots the target below tolerance, so the star
   lands on the target rather than under it.

Pure deletion, skeleton protection, flux balance, and the dual-gate validation are the ADR-0011 invariants
and are unchanged.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from proj7k.engine.skills import SKILL_TECH_KEY, SKILLS
from proj7k.engine.solver import loss
from proj7k.field import ChartField, d_of_stars, trace_beatmap
from proj7k.parser import Beatmap7K, HitObject
from proj7k.downscaler.balancer import LEFT_LANES, RIGHT_LANES, BimanualFluxBalancer
from proj7k.downscaler.marginal import removal_benefit
from proj7k.downscaler.mutation import apply_pure_deletion
from proj7k.downscaler.skeleton import MetricSkeletonDetector
from proj7k.downscaler.validator import DualGateValidator

#: A window is a peak from this share of the allowed loss rate up (windows just under it still carry loss).
PEAK_MARGIN = 0.10

#: How much a note's score is damped by the share of it that the dominant skill carries (attribution `a_ik`).
#: Thinning is steered toward what the dominant skill does not need, without making it untouchable.
DOMINANCE_BIAS = 0.35


def engine_skill(name: Optional[str]) -> Optional[str]:
    """An engine skill name (`rc_jack`) or the short key the consumers speak (`jack`, `stream`) as the engine name."""
    if not name:
        return None
    clean = name.strip().lower().replace(" ", "_").replace("-", "_")
    if clean in SKILLS:
        return clean
    for engine_name, short in SKILL_TECH_KEY.items():
        if clean == short:
            return engine_name
    return None


@dataclass(frozen=True)
class PruneIterationRecord:
    """Telemetry for each committed batch."""
    iteration: int
    surviving_notes: int
    removed_in_batch: int
    star: float             # the engine's star of the chart before the batch
    total_D: float
    excess_loss: float      # expected loss at the target level minus the tolerance, before the batch
    flux_ratio: Tuple[float, float]
    passed_validation: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration,
            "surviving_notes": self.surviving_notes,
            "removed_in_batch": self.removed_in_batch,
            "star": round(self.star, 3),
            "total_D": round(self.total_D, 3),
            "excess_loss": round(self.excess_loss, 3),
            "flux_ratio": (round(self.flux_ratio[0], 3), round(self.flux_ratio[1], 3)),
            "passed_validation": self.passed_validation,
        }


@dataclass(frozen=True)
class PruningResult:
    """Result of closed-loop pruning."""
    downscaled_beatmap: Beatmap7K
    iterations_run: int
    converged: bool
    target_sr: float
    initial_star: float
    final_star: float
    initial_D: float
    final_D: float
    total_notes_removed: int
    warnings: List[str] = field(default_factory=list)
    history: List[PruneIterationRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iterations_run": self.iterations_run,
            "converged": self.converged,
            "target_sr": round(self.target_sr, 3),
            "initial_star": round(self.initial_star, 3),
            "final_star": round(self.final_star, 3),
            "initial_D": round(self.initial_D, 3),
            "final_D": round(self.final_D, 3),
            "total_notes_removed": self.total_notes_removed,
            "warnings": self.warnings,
            "history": [rec.to_dict() for rec in self.history],
        }


class ExcessLossPruner:
    """Closed-loop iterative pruner for osu!mania 7K beatmaps (SPEC-P5.1-03, ADR-0011, ADR-0021)."""

    def __init__(
        self,
        prune_ratio: float = 0.20,
        max_iterations: int = 25,
        tolerance: float = 0.05,
        window_s: float = 1.0,
        validator: Optional[DualGateValidator] = None,
        balancer: Optional[BimanualFluxBalancer] = None,
    ):
        # Canonical single-round pruning bounds: 15% ~ 25% of a peak window (ADR-0011)
        self.prune_ratio = max(0.15, min(0.25, prune_ratio))
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.window_s = window_s
        self.validator = validator or DualGateValidator()
        self.balancer = balancer or BimanualFluxBalancer()

    # -- scoring ---------------------------------------------------------------------------------

    def candidate_scores(
        self,
        beatmap: Beatmap7K,
        field: ChartField,
        target_sr: float,
        dominant: Optional[str],
    ) -> Tuple[List[Tuple[float, HitObject]], List[HitObject]]:
        """
        (scored removable notes in peak windows, the peak-window notes the skeleton refused), for one trace.
        """
        D_star = d_of_stars(target_sr)
        benefit = removal_benefit(field, D_star)
        k_dom = SKILLS.index(dominant) if dominant in SKILLS else None
        window_ms = max(100.0, self.window_s * 1000.0)

        # Peak windows: where the expected loss at the target level runs above the rate the total's tolerance
        # allows. (A chart's D can sit above every single reading, since D is where the *mass* of near-top
        # events meets the tolerance; so peaks are windows of loss density, not events above D*.)
        pl = loss(field.d, D_star, field.params)
        win_of_event = (field.t * 1000.0 // window_ms).astype(int)
        win_loss = np.bincount(win_of_event, weights=pl)
        win_count = np.bincount(win_of_event)
        allowed_rate = (field.eps_total * (field.events.n + field.params.N0)) / field.events.n
        peak_windows: Set[int] = {
            int(w) for w in np.flatnonzero(win_count) if win_loss[w] / win_count[w] > allowed_rate * (1.0 - PEAK_MARGIN)
        }

        in_peak: List[Tuple[HitObject, float, float]] = []   # (note, first-order benefit, dominant skill's share)
        for ho in beatmap.hit_objects:
            if int(ho.time // window_ms) not in peak_windows:
                continue
            i = field.press_index(ho.column, ho.time / 1000.0)
            if i is None:
                in_peak.append((ho, 0.0, 0.0))
                continue
            r = field.release_index(i)
            b = float(benefit[i] + (benefit[r] if r is not None else 0.0))
            in_peak.append((ho, b, float(field.a[i, k_dom]) if k_dom is not None else 0.0))
        detector = MetricSkeletonDetector(beatmap)
        allowed = {id(ho) for ho in detector.filter_candidate_removals([ho for ho, _, _ in in_peak])}

        l_flux, r_flux = self.balancer.compute_hand_flux(beatmap.hit_objects)
        penalty_l, penalty_r = self.balancer.compute_asymmetry_penalty(l_flux, r_flux)

        scored: List[Tuple[float, HitObject]] = []
        refused: List[HitObject] = []
        for ho, b, share in in_peak:
            if id(ho) not in allowed:
                refused.append(ho)
                continue
            if ho.column in LEFT_LANES:
                pen = penalty_l
            elif ho.column in RIGHT_LANES:
                pen = penalty_r
            else:
                pen = (penalty_l + penalty_r) / 2.0
            scored.append((b * (1.0 - DOMINANCE_BIAS * share) / max(0.01, pen), ho))
        return scored, refused

    def _batch(self, scored: List[Tuple[float, HitObject]]) -> List[HitObject]:
        """The top `prune_ratio` of each peak window, best first."""
        window_ms = max(100.0, self.window_s * 1000.0)
        by_window: Dict[int, List[Tuple[float, HitObject]]] = {}
        for score, ho in scored:
            by_window.setdefault(int(ho.time // window_ms), []).append((score, ho))
        picked: List[Tuple[float, HitObject]] = []
        for items in by_window.values():
            items.sort(key=lambda x: x[0], reverse=True)
            picked.extend(items[: max(1, int(round(len(items) * self.prune_ratio)))])
        picked.sort(key=lambda x: x[0], reverse=True)
        return [ho for _, ho in picked]

    # -- the loop --------------------------------------------------------------------------------

    def prune(
        self,
        beatmap: Beatmap7K,
        target_sr: float,
        dominant_skill: Optional[str] = None,
    ) -> PruningResult:
        """
        Prunes `beatmap` until the engine reads it at `target_sr` stars (within `tolerance`, from above),
        or no more can be taken without breaking technique preservation.
        """
        dominant = engine_skill(dominant_skill)
        warnings: List[str] = []
        field = trace_beatmap(beatmap)
        initial_star, initial_D = field.profile.total_stars, field.total_D
        upper = target_sr * (1.0 + self.tolerance)
        lower = target_sr * (1.0 - self.tolerance)

        if initial_star <= upper:
            warnings.append(
                f"Beatmap initial star rating ({initial_star:.3f}) is already <= target ({target_sr:.3f}); no downscaling required."
            )
            return PruningResult(
                downscaled_beatmap=beatmap, iterations_run=0, converged=True, target_sr=target_sr,
                initial_star=initial_star, final_star=initial_star, initial_D=initial_D, final_D=initial_D,
                total_notes_removed=0, warnings=warnings,
            )
        if dominant is None:
            dominant = field.profile.dominant_skill

        current = beatmap
        history: List[PruneIterationRecord] = []
        converged = False

        for it in range(1, self.max_iterations + 1):
            star = field.profile.total_stars
            if star <= upper:
                converged = True
                break

            scored, refused = self.candidate_scores(current, field, target_sr, dominant)
            if not scored:
                warnings.append(
                    "Metric skeleton protected all peak candidates; cannot prune further safely."
                    if refused else "No note carries the excess loss; cannot prune further."
                )
                break
            ordered = self._batch(scored)

            # Take the whole batch, then fewer: halved while it breaks technique preservation, and while it
            # overshoots the target below tolerance (a single note is always accepted on the second count).
            size, committed, passed = len(ordered), None, False
            while size >= 1:
                cand = apply_pure_deletion(current, notes_to_remove=ordered[:size])
                if not cand.hit_objects:
                    size //= 2
                    continue
                val = self.validator.validate(beatmap, cand)
                if not val.passed:
                    size //= 2
                    continue
                cand_field = trace_beatmap(cand)
                if cand_field.profile.total_stars < lower and size > 1:
                    size //= 2
                    continue
                committed, passed = (cand, cand_field, size), True
                break

            if committed is None:
                warnings.append(f"Pruning stopped at iteration {it} to preserve technique invariants.")
                break

            cand, cand_field, size = committed
            excess = _excess_loss(field, d_of_stars(target_sr))
            history.append(PruneIterationRecord(
                iteration=it, surviving_notes=len(cand.hit_objects), removed_in_batch=size, star=star,
                total_D=field.total_D, excess_loss=excess,
                flux_ratio=self.balancer.compute_flux_ratio(cand.hit_objects), passed_validation=passed,
            ))
            current, field = cand, cand_field

        final_star = field.profile.total_stars
        return PruningResult(
            downscaled_beatmap=current,
            iterations_run=len(history),
            converged=converged or final_star <= upper,
            target_sr=target_sr,
            initial_star=initial_star,
            final_star=final_star,
            initial_D=initial_D,
            final_D=field.total_D,
            total_notes_removed=len(beatmap.hit_objects) - len(current.hit_objects),
            warnings=warnings,
            history=history,
        )


def _excess_loss(field: ChartField, level: float) -> float:
    """Expected events lost at `level` beyond what the total's tolerance allows (what pruning has to remove)."""
    p = field.params
    return float(loss(field.d, level, p).sum() - field.eps_total * (field.events.n + p.N0))
