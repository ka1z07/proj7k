from typing import Collection, List, Optional, Tuple

from proj7k.parser import HitObject

LEFT_LANES: Tuple[int, int, int] = (0, 1, 2)
CENTER_LANE: int = 3
RIGHT_LANES: Tuple[int, int, int] = (4, 5, 6)


def get_note_flux_contribution(column: int) -> Tuple[float, float]:
    """Returns (left_flux, right_flux) contributed by a note on the given column."""
    if column in LEFT_LANES:
        return (1.0, 0.0)
    if column in RIGHT_LANES:
        return (0.0, 1.0)
    if column == CENTER_LANE:
        return (0.5, 0.5)
    return (0.0, 0.0)


class BimanualFluxBalancer:
    """
    Evaluates and dynamically balances bimanual striking flux between left and right hands
    (SPEC-P5.1-02, ADR-0011):
    - Left hand (L3-L1): columns 0, 1, 2
    - Center lane (S): column 3 (shared 50% left, 50% right)
    - Right hand (R1-R3): columns 4, 5, 6
    - Asymmetry penalty: penalizes further removal of underloaded hand, prioritizing
      pruning of overloaded hand to converge bimanual flux ratio within [45%, 55%].
    """

    def __init__(self, target_band: Tuple[float, float] = (0.45, 0.55)):
        self.target_min, self.target_max = target_band

    def compute_hand_flux(self, hit_objects: Collection[HitObject]) -> Tuple[float, float]:
        """
        Computes striking flux for left and right hands.
        Center column 3 is allocated 50% to left and 50% to right.
        """
        left_flux = 0.0
        right_flux = 0.0

        for ho in hit_objects:
            dl, dr = get_note_flux_contribution(ho.column)
            left_flux += dl
            right_flux += dr

        return (left_flux, right_flux)

    def compute_flux_ratio(self, hit_objects: Collection[HitObject]) -> Tuple[float, float]:
        """
        Returns (left_ratio, right_ratio) where ratio = hand_flux / total_flux.
        Returns (0.5, 0.5) if total_flux is 0.
        """
        l_flux, r_flux = self.compute_hand_flux(hit_objects)
        total = l_flux + r_flux
        if total <= 0:
            return (0.5, 0.5)
        return (l_flux / total, r_flux / total)

    def compute_asymmetry_penalty(
        self, left_flux: float, right_flux: float
    ) -> Tuple[float, float]:
        """
        Computes the asymmetry removal penalty factor (penalty_L, penalty_R):
        - When balanced (50/50), penalty_L == 1.0, penalty_R == 1.0.
        - When an hand is overloaded (> 50%), its removal penalty is < 1.0 (favors pruning).
        - When an hand is underloaded (< 50%), its removal penalty is > 1.0 (inhibits pruning).
        """
        total = left_flux + right_flux
        if total <= 0:
            return (1.0, 1.0)

        ratio_l = max(0.01, min(0.99, left_flux / total))
        ratio_r = 1.0 - ratio_l

        penalty_l = 0.50 / ratio_l
        penalty_r = 0.50 / ratio_r

        return (penalty_l, penalty_r)

    def balance_candidate_removals(
        self,
        current_notes: Collection[HitObject],
        candidates: Collection[HitObject],
        target_ratio_range: Optional[Tuple[float, float]] = None,
        max_removals: Optional[int] = None,
    ) -> List[HitObject]:
        """
        Adaptively filters/reorders candidate removals so that after removal,
        surviving notes' bimanual flux ratio is guided towards [45%, 55%].
        Prioritizes pruning candidates from the overloaded hand using asymmetry penalty weighting.
        """
        if not candidates:
            return []

        target_min, target_max = target_ratio_range or (self.target_min, self.target_max)
        limit = max_removals if max_removals is not None else len(candidates)

        surviving_l, surviving_r = self.compute_hand_flux(current_notes)
        remaining_cands = list(candidates)
        selected_removals: List[HitObject] = []

        for _ in range(limit):
            if not remaining_cands:
                break

            total_surviving = surviving_l + surviving_r
            if total_surviving <= 0:
                break

            cur_ratio_l = surviving_l / total_surviving

            # Compute dynamic asymmetry penalty based on current surviving load
            penalty_l, penalty_r = self.compute_asymmetry_penalty(surviving_l, surviving_r)

            # If max_removals was not explicitly set and we started out of balance but now entered the target band, stop
            if max_removals is None and (target_min <= cur_ratio_l <= target_max):
                # Only stop if current_notes originally started outside target range
                orig_l, orig_r = self.compute_hand_flux(current_notes)
                orig_total = orig_l + orig_r
                if orig_total > 0:
                    orig_ratio = orig_l / orig_total
                    if orig_ratio < target_min or orig_ratio > target_max:
                        break

            # Evaluate best candidate to remove next:
            # Score balances distance to 0.50 and asymmetry removal priority
            best_idx = -1
            best_score = float("inf")

            for idx, cand in enumerate(remaining_cands):
                dl, dr = get_note_flux_contribution(cand.column)
                cand_surv_l = surviving_l - dl
                cand_surv_r = surviving_r - dr
                cand_total = cand_surv_l + cand_surv_r
                cand_ratio_l = cand_surv_l / cand_total if cand_total > 0 else 0.50

                dist_to_center = abs(cand_ratio_l - 0.50)
                # Removal priority: higher removal weight means lower penalty -> lower score
                removal_priority = (dl / max(1e-6, penalty_l)) + (dr / max(1e-6, penalty_r))
                score = dist_to_center / max(0.1, removal_priority)

                if score < best_score:
                    best_score = score
                    best_idx = idx

            if best_idx >= 0:
                chosen = remaining_cands.pop(best_idx)
                dl, dr = get_note_flux_contribution(chosen.column)
                surviving_l -= dl
                surviving_r -= dr
                selected_removals.append(chosen)

        return selected_removals
