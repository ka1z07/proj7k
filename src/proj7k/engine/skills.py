"""The eight skills (spec §1.2) and the hand layout of a 7K keyboard (§2.5)."""

from typing import Tuple

import numpy as np

from proj7k.engine.params import Params

#: Output order of every per-skill array; the first four are the RC family, the last four LN.
SKILLS: Tuple[str, ...] = (
    "rc_jack", "rc_tech", "rc_speed", "rc_stamina",
    "ln_general", "ln_tech", "ln_inverse", "ln_release",
)
RC_COUNT = 4

FINGER = ("ring", "mid", "idx", "thumb", "idx", "mid", "ring")
_SIDE = (0, 0, 0, None, 1, 1, 1)  # 0 = left hand, 1 = right; the thumb goes with `thumb`


def hand_of(thumb: int) -> np.ndarray:
    """The hand (0 or 1) of each of the seven columns when the thumb is on `thumb`."""
    return np.array([thumb if s is None else s for s in _SIDE])


def kappa_matrix(p: Params, thumb: int) -> np.ndarray:
    """§4.2: coupling between two fingers of the same hand; 0 across hands and on the diagonal."""
    pair = {
        frozenset(("ring", "mid")): p.k_ring_mid, frozenset(("mid", "idx")): p.k_mid_idx,
        frozenset(("ring", "idx")): p.k_ring_idx, frozenset(("thumb", "idx")): p.k_thumb_idx,
        frozenset(("thumb", "mid")): p.k_thumb_other, frozenset(("thumb", "ring")): p.k_thumb_other,
    }
    hand = hand_of(thumb)
    k = np.zeros((7, 7))
    for f in range(7):
        for g in range(7):
            if f != g and hand[f] == hand[g]:
                k[f, g] = p.k_scale * pair[frozenset((FINGER[f], FINGER[g]))]
    return k
