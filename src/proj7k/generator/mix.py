"""
The technique mix (ADR-0047 phase 3): for each of the engine's eight pools, more (+1), as it comes (0) or less (-1).

Mapperatorinator's descriptor tags don't move a 7K chart (phase 2, meow 2026-10-29: smaller than a change of seed,
several the wrong way), so the mix asks the model for nothing. It picks and it trims: of the runs (or a section's
candidates), the one whose pools lean most the way the mix asks wins among those near the target, and the
downscaler deletes the notes of the pools weighted down first and those weighted up last. It can't add a technique
the model didn't write.
"""

from typing import Dict, Mapping, Optional, Union

from proj7k.engine import SKILLS
from proj7k.i18n import tr

#: How many stars off target one unit of lean is worth (`lean`: a pool's share of the total, times its weight):
#: a run whose weighted pools are 0.1 of the total more the way the mix asks may be 0.1★ further from the target.
WEIGHT = 1.0

NAMES = {
    "rc_jack": ("Jack", "Jack"), "rc_tech": ("Tech", "Tech"), "rc_speed": ("Speed", "Speed"),
    "rc_stamina": ("Stamina", "Stamina"), "ln_general": ("LN 综合", "LN General"), "ln_tech": ("LN Tech", "LN Tech"),
    "ln_inverse": ("反键", "LN Inverse"), "ln_release": ("LN 放键", "LN Release"),
}


def parse(value: Union[None, str, Mapping[str, float]]) -> Optional[Dict[str, float]]:
    """A mix from {pool: weight} or "rc_jack=1,ln_release=-1" (short names such as jack/stream also work); weights
    clamp to -1..1, zero weights drop out, an empty mix is None."""
    from proj7k.downscaler.pruner import engine_skill

    if not value:
        return None
    if isinstance(value, str):
        items = []
        for part in value.replace(";", ",").split(","):
            if part.strip():
                if "=" not in part:
                    raise ValueError(tr(f"技法配比写成 池=权重：{part}", f"Write the mix as pool=weight: {part}"))
                k, v = part.split("=", 1)
                items.append((k, float(v)))
    else:
        items = list(value.items())
    out: Dict[str, float] = {}
    for k, v in items:
        pool = engine_skill(str(k))
        if pool is None:
            raise ValueError(tr(f"没有这个技法池：{k}（可用 {', '.join(SKILLS)}）",
                                f"No such pool: {k} (one of {', '.join(SKILLS)})"))
        w = min(max(float(v), -1.0), 1.0)
        if w:
            out[pool] = w
    return out or None


def lean(profile, mix: Optional[Mapping[str, float]]) -> float:
    """How far a chart's pools lean the way the mix asks: each pool's stars over the total, times its weight."""
    if not mix:
        return 0.0
    total = float(profile.total_stars) or 1.0
    return sum(w * float(profile.skills[k].stars) / total for k, w in mix.items() if k in profile.skills)


def describe(mix: Optional[Mapping[str, float]]) -> str:
    if not mix:
        return ""
    return ", ".join(f"{tr(*NAMES[k])} {'+' if w > 0 else '−'}{abs(w):g}" for k, w in mix.items())


def pools(profile, mix: Optional[Mapping[str, float]]) -> str:
    """The stars of the pools the mix names, for a log line."""
    if not mix:
        return ""
    return " ".join(f"{tr(*NAMES[k])} {float(profile.skills[k].stars):.2f}" for k in mix if k in profile.skills)
