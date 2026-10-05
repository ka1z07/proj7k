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

#: The engine's skill names against the short technique keys the consumers of the legacy radar speak
#: (`radar.TECHNIQUE_NAMES`: the live radar canvas, the downscaler's pruner and mapper).
SKILL_TECH_KEY = {
    "rc_jack": "jack",
    "rc_tech": "tech",
    "rc_speed": "speed",
    "rc_stamina": "stream",
    "ln_general": "ln_general",
    "ln_tech": "ln_tech",
    "ln_inverse": "ln_inverse",
    "ln_release": "ln_release",
}

#: The benchmark manifest's technique pools (`docs/research/structured_index.json`) against the skill each
#: pool is the ladder of. A pool's own skill is the one its charts are meant to be hard at, so it is the
#: skill whose D_k must climb the 15 tiers (ADR-0018 decision 3).
BENCHMARK_POOL_SKILL = {
    "Regular Jack": "rc_jack",
    "Regular Tech": "rc_tech",
    "Regular Speed": "rc_speed",
    "Regular Stream": "rc_stamina",
    "LN General": "ln_general",
    "LN Tech": "ln_tech",
    "LN Inverse": "ln_inverse",
    "LN Release": "ln_release",
}

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
