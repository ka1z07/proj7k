"""
Downscaling to a range per skill pool (ADR-0051): the eight pools' own stars are the target, not the total alone.

`ExcessLossPruner` brings the total star down and leans on the technique mix only through the order it deletes
in (`skill_weights`), so whether each pool lands in its range was left to chance: the generator made several
charts and kept the closest. Each pool's star is the engine's own solve over the events it is a member of
(`D_k = solve(d, w[:, k], eps_k)`), so the same closed-form removal benefit that reads the total
(`downscaler.marginal`) reads one pool when the loss is weighted by that pool's membership. The loop:

1. Pools above their ceiling are *over*; pools at or near their floor are *held*.
2. Every note scores the drop it brings to each over pool's loss at that pool's ceiling (as a share of the
   best note's), weighted by how many stars the pool is over, less what it costs each held pool at its floor.
   With a total target the total's own benefit joins as one more over pool.
3. Candidates are notes in the one-second windows where an over pool's loss runs above its tolerance rate,
   with a positive score; the skeleton, the column balance and the batch sizes are `ExcessLossPruner`'s.
4. A batch is halved while it breaks validation, takes a held pool under its floor, or (with a total target)
   the total under its tolerance. It stops when no pool is over and the total is in; bursts are kept within the
   caps of the level the chart ends at, as in `ExcessLossPruner`.

Deletion only: a pool under its floor can't be raised. The generator therefore writes its chart above the
ranges and lets this bring each pool down into its own.
"""

from typing import Dict, List, Mapping, Optional, Tuple

import numpy as np

from proj7k import cancel
from proj7k.engine.skills import SKILLS
from proj7k.engine.solver import loss
from proj7k.field import ChartField, d_of_stars, trace_beatmap
from proj7k.i18n import tr
from proj7k.parser import Beatmap7K, HitObject
from proj7k.downscaler.burst import burst_reading, excess
from proj7k.downscaler.marginal import removal_benefit
from proj7k.downscaler.mutation import apply_pure_deletion
from proj7k.downscaler.pruner import (
    PEAK_MARGIN, SKIPPED_BLOCKERS, ExcessLossPruner, PruneIterationRecord, PruningResult, _excess_loss,
)

Range = Tuple[Optional[float], Optional[float]]

#: A pool within this many stars of its floor is held: notes it needs score lower, and a batch may not take it under.
HOLD_MARGIN = 0.3
#: How much a held pool's loss counts against a note, against one over pool's gain.
HOLD_WEIGHT = 1.0
#: A pool counts as over its ceiling from this many stars above it (the engine's reading moves by ~0.01 per note).
OVER_SLACK = 0.02


def _eps(k: str, field: ChartField) -> float:
    return field.params.eps_rc if k.startswith("rc") else field.params.eps_ln


def pool_stars(field: ChartField) -> Dict[str, float]:
    return {k: float(v.stars) for k, v in field.profile.skills.items()}


def ranges_of(mix: Optional[Mapping]) -> Dict[str, Range]:
    """The pools of a technique mix that carry a star range (`generator.mix`), skipping the leanings."""
    out: Dict[str, Range] = {}
    for k, v in (mix or {}).items():
        if k in SKILLS and isinstance(v, (tuple, list)) and len(v) == 2 and (v[0] is not None or v[1] is not None):
            out[k] = (None if v[0] is None else float(v[0]), None if v[1] is None else float(v[1]))
    return out


def misses(stars: Mapping[str, float], ranges: Mapping[str, Range]) -> Dict[str, float]:
    """Per pool outside its range, how far (stars; negative under the floor)."""
    out = {}
    for k, (lo, hi) in ranges.items():
        s = stars.get(k, 0.0)
        if hi is not None and s > hi + OVER_SLACK:
            out[k] = s - hi
        elif lo is not None and s < lo - OVER_SLACK:
            out[k] = s - lo
    return out


class PoolRangePruner(ExcessLossPruner):
    """`ExcessLossPruner` steered by a star range per pool, with or without a total target."""

    def __init__(self, ranges: Mapping[str, Range], **kwargs):
        kwargs.pop("skill_weights", None)
        super().__init__(**kwargs)
        self.ranges: Dict[str, Range] = {k: r for k, r in ranges.items() if k in SKILLS}

    # -- what the pools ask -----------------------------------------------------------------------

    def over(self, stars: Mapping[str, float]) -> Dict[str, float]:
        """Pools above their ceiling: pool -> its ceiling."""
        return {k: hi for k, (lo, hi) in self.ranges.items() if hi is not None and stars[k] > hi + OVER_SLACK}

    def held(self, stars: Mapping[str, float]) -> Dict[str, float]:
        """Pools at or near their floor: pool -> its floor."""
        return {k: lo for k, (lo, hi) in self.ranges.items() if lo is not None and stars[k] <= lo + HOLD_MARGIN}

    def pool_scores(
        self, beatmap: Beatmap7K, field: ChartField, target_sr: Optional[float], reference: Beatmap7K,
    ) -> Tuple[List[Tuple[float, HitObject]], List[HitObject]]:
        """(scored removable notes, the ones the skeleton refused) for one trace; see the module docstring."""
        stars = pool_stars(field)
        terms: List[Tuple[np.ndarray, float, float, float]] = []   # (weights, level, eps, stars over)
        for k, hi in self.over(stars).items():
            n = SKILLS.index(k)
            terms.append((field.w[:, n], d_of_stars(hi), _eps(k, field), stars[k] - hi))
        if target_sr is not None and field.profile.total_stars > target_sr * (1.0 + self.tolerance):
            terms.append((np.ones(field.events.n), d_of_stars(target_sr), field.eps_total,
                          field.profile.total_stars - target_sr))
        if not terms:
            return [], []

        window_ms = max(100.0, self.window_s * 1000.0)
        win = (field.t * 1000.0 // window_ms).astype(int)
        score = np.zeros(field.events.n)
        peak: set = set()
        for w, level, eps, by in terms:
            b = removal_benefit(field, level, w)
            top = float(b.max())
            if top > 0:
                score += by * b / top
            # this pool's peak windows: its weighted loss above the rate its own tolerance allows
            pl = w * loss(field.d, level, field.params)
            wl = np.bincount(win, weights=pl)
            ww = np.bincount(win, weights=w)
            total_w = float(w.sum())
            if total_w <= 0:
                continue
            allowed = eps * (total_w + field.params.N0) / total_w
            with np.errstate(divide="ignore", invalid="ignore"):
                rate = np.where(ww > 0, wl / ww, 0.0)
            peak.update(int(x) for x in np.flatnonzero(rate > allowed * (1.0 - PEAK_MARGIN)))
        for k, lo in self.held(stars).items():
            n = SKILLS.index(k)
            b = removal_benefit(field, d_of_stars(lo), field.w[:, n])
            top = float(b.max())
            if top > 0:
                score -= HOLD_WEIGHT * b / top

        notes: List[Tuple[HitObject, float, float, float]] = []
        for ho in beatmap.hit_objects:
            if int(ho.time // window_ms) not in peak:
                continue
            i = field.press_index(ho.column, ho.time / 1000.0)
            if i is None:
                continue
            r = field.release_index(i)
            s = float(score[i] + (score[r] if r is not None else 0.0))
            if s > 0:
                notes.append((ho, s, 0.0, 0.0))
        return self._admit(beatmap, notes, 0.0, reference)

    def _breaks_floor(self, before: Mapping[str, float], after: Mapping[str, float]) -> bool:
        """A pool that was at or over its floor and the batch takes under it."""
        return any(lo is not None and before[k] >= lo - OVER_SLACK and after[k] < lo - OVER_SLACK
                   for k, (lo, _) in self.ranges.items())

    # -- the loop ---------------------------------------------------------------------------------

    def prune(self, beatmap: Beatmap7K, target_sr: Optional[float] = None,
              dominant_skill: Optional[str] = None) -> PruningResult:
        warnings: List[str] = []
        field = trace_beatmap(beatmap)
        initial_star, initial_D = field.profile.total_stars, field.total_D
        lower = target_sr * (1.0 - self.tolerance) if target_sr is not None else None
        upper = target_sr * (1.0 + self.tolerance) if target_sr is not None else None

        def level(f: ChartField) -> float:
            # bursts are held to what real charts at the level the result reads (or is asked for) have
            return d_of_stars(target_sr if target_sr is not None else f.profile.total_stars)

        burst = burst_reading(field)
        initial_burst = excess(burst, level(field))
        current = beatmap
        history: List[PruneIterationRecord] = []
        converged = stopped_by_validation = bursts_stuck = False

        for it in range(1, self.max_iterations + 1):
            cancel.checkpoint()
            stars = pool_stars(field)
            star = field.profile.total_stars
            total_over = upper is not None and star > upper
            bursting = not bursts_stuck and max(excess(burst, level(field)).values()) > 1.0
            if not self.over(stars) and not total_over and not bursting:
                converged = True
                break

            scored: List[Tuple[float, HitObject]] = []
            refused: List[HitObject] = []
            if bursting:
                scored, refused = self.burst_scores(current, field, burst, target_sr or star, None, beatmap)
                if not scored:
                    bursts_stuck = True
            if not scored:
                scored, refused = self.pool_scores(current, field, target_sr, beatmap)
            if not scored:
                if bursting:
                    bursts_stuck = True
                    continue
                warnings.append(
                    "Metric skeleton protected every candidate; cannot prune further safely."
                    if refused else "No note left lowers an over pool without taking a held one under its floor."
                )
                break
            ordered = self._batch(scored)

            committed = None
            for _ in range(1 + SKIPPED_BLOCKERS):
                size = len(ordered)
                while size >= 1:
                    cand = apply_pure_deletion(current, notes_to_remove=ordered[:size])
                    if not cand.hit_objects or not self.validator.validate(beatmap, cand).passed:
                        size //= 2
                        continue
                    cand_field = trace_beatmap(cand)
                    if self._breaks_floor(stars, pool_stars(cand_field)):
                        size //= 2
                        continue
                    # the total's floor binds only while the total is what is brought down to the target
                    if lower is not None and star >= lower and cand_field.profile.total_stars < lower and size > 1:
                        size //= 2
                        continue
                    committed = (cand, cand_field, size)
                    break
                if committed is not None or len(ordered) <= 1:
                    break
                ordered = ordered[1:]

            if committed is None and bursting:
                bursts_stuck = True
                continue
            if committed is None:
                warnings.append(f"Pruning stopped at iteration {it}: every deletion left breaks the chart or a floor.")
                stopped_by_validation = True
                break
            cand, cand_field, size = committed
            history.append(PruneIterationRecord(
                iteration=it, surviving_notes=len(cand.hit_objects), removed_in_batch=size, star=star,
                total_D=field.total_D, excess_loss=_excess_loss(field, level(field)),
                flux_ratio=self.balancer.compute_flux_ratio(cand.hit_objects), passed_validation=True,
            ))
            current, field = cand, cand_field
            burst = burst_reading(field)

        stars = pool_stars(field)
        missed = misses(stars, self.ranges)
        if missed:
            text = ", ".join(f"{k} {v:+.2f}★" for k, v in missed.items())
            warnings.append(tr(f"技法超出范围：{text}（负数是低于下限：只删不加，补不上）",
                               f"Pools outside their range: {text} (negative is under the floor: deletion can't raise it)"))
        final_star = field.profile.total_stars
        return PruningResult(
            downscaled_beatmap=current, iterations_run=len(history),
            converged=converged and not missed, target_sr=target_sr if target_sr is not None else final_star,
            initial_star=initial_star, final_star=final_star, initial_D=initial_D, final_D=field.total_D,
            total_notes_removed=len(beatmap.hit_objects) - len(current.hit_objects), warnings=warnings,
            history=history, stopped_by_validation=stopped_by_validation,
            initial_burst=initial_burst, final_burst=excess(burst, level(field)),
        )
