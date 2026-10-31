"""
The technique mix (ADR-0047 phase 3, ranges ADR-0050): for each of the engine's eight pools either a lean, more (+1),
as it comes (0) or less (-1), or a range of stars [low, high] (either end open).

Mapperatorinator's descriptor tags don't move a 7K chart (phase 2, meow 2026-10-29: smaller than a change of seed,
several the wrong way), so the mix asks the model for nothing. It picks and it trims: of the runs (or a section's
candidates), the one whose pools lean most the way the mix asks, or miss its ranges by the least, wins among those
near the target, and the downscaler deletes the notes of the pools weighted down (or above their range) first and
those weighted up (or below their range) last. It can't add a technique the model didn't write: a pool left below
its range is reported (`misses`), not mended.
"""

import math
import re
from typing import Dict, List, Mapping, Optional, Tuple, Union

from proj7k.engine import SKILLS
from proj7k.i18n import tr

#: How many stars off target one unit of lean is worth (`lean`: a pool's share of the total, times its weight):
#: a run whose weighted pools are 0.1 of the total more the way the mix asks may be 0.1★ further from the target.
WEIGHT = 1.0
#: How many stars off target one star outside a pool's range is worth.
RANGE_WEIGHT = 1.0
#: The highest star a range may name.
MAX_STARS = 20.0

NAMES = {
    "rc_jack": ("Jack", "Jack"), "rc_tech": ("Tech", "Tech"), "rc_speed": ("Speed", "Speed"),
    "rc_stamina": ("Stamina", "Stamina"), "ln_general": ("LN 综合", "LN General"), "ln_tech": ("LN Tech", "LN Tech"),
    "ln_inverse": ("反键", "LN Inverse"), "ln_release": ("LN 放键", "LN Release"),
}

Range = Tuple[Optional[float], Optional[float]]
Mix = Dict[str, Union[float, Range]]

_NUM = r"\d+(?:\.\d+)?"
_RANGE = re.compile(rf"^\s*({_NUM})?\s*(?:\.\.|~|–|—|-(?=\s*\d)|-$)\s*({_NUM})?\s*$")


def _stars(v, what: str) -> Optional[float]:
    if v is None or v == "":
        return None
    x = float(v)
    if not math.isfinite(x) or x < 0 or x > MAX_STARS:
        raise ValueError(tr(f"{what} 的星级要在 0–{MAX_STARS:g} 之间：{v}", f"{what}: stars go from 0 to {MAX_STARS:g}: {v}"))
    return x


def _value(pool: str, v) -> Optional[Union[float, Range]]:
    """A lean (a number -1..1, clamped) or a range ("4.5..6", "..6", "4.5..", "4.5-6", [lo, hi], {"min", "max"})."""
    lo = hi = None
    if isinstance(v, Mapping):
        lo, hi = v.get("min", v.get("lo")), v.get("max", v.get("hi"))
    elif isinstance(v, (list, tuple)):
        if len(v) != 2:
            raise ValueError(tr(f"{pool} 的范围写成 [下限, 上限]", f"{pool}: write the range as [low, high]"))
        lo, hi = v
    elif isinstance(v, str) and not re.fullmatch(r"\s*[-+]?\d+(?:\.\d+)?\s*", v):
        m = _RANGE.match(v)
        if not m:
            raise ValueError(tr(f"看不懂 {pool} 的范围：{v}（写成 4.5..6、..6 或 4.5..）",
                                f"Can't read the range for {pool}: {v} (write 4.5..6, ..6 or 4.5..)"))
        lo, hi = m.group(1), m.group(2)
    else:
        w = min(max(float(v), -1.0), 1.0)
        return w or None
    name = tr(*NAMES[pool])
    lo, hi = _stars(lo, name), _stars(hi, name)
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None and lo > hi:
        raise ValueError(tr(f"{name} 的下限 {lo:g} 高于上限 {hi:g}", f"{name}: low {lo:g} is above high {hi:g}"))
    return (lo, hi)


def parse(value: Union[None, str, Mapping]) -> Optional[Mix]:
    """A mix from {pool: weight or range} or "rc_jack=1,ln_release=-1,rc_speed=4..6" (short names such as
    jack/stream also work); weights clamp to -1..1, zero weights and open ranges drop out, an empty mix is None."""
    from proj7k.downscaler.pruner import engine_skill

    if not value:
        return None
    if isinstance(value, str):
        items = []
        for part in value.replace(";", ",").split(","):
            if part.strip():
                if "=" not in part:
                    raise ValueError(tr(f"技法配比写成 池=权重 或 池=下限..上限：{part}",
                                        f"Write the mix as pool=weight or pool=low..high: {part}"))
                k, v = part.split("=", 1)
                items.append((k, v.strip()))
    else:
        items = list(value.items())
    out: Mix = {}
    for k, v in items:
        pool = engine_skill(str(k))
        if pool is None:
            raise ValueError(tr(f"没有这个技法池：{k}（可用 {', '.join(SKILLS)}）",
                                f"No such pool: {k} (one of {', '.join(SKILLS)})"))
        parsed = _value(pool, v)
        if parsed is not None:
            out[pool] = parsed
    return out or None


def is_range(v) -> bool:
    return isinstance(v, (tuple, list))


def to_json(mix: Optional[Mix]) -> Optional[Dict[str, Union[float, List[Optional[float]]]]]:
    """The mix as a project file or a page keeps it: ranges as [low, high] lists."""
    if not mix:
        return None
    return {k: list(v) if is_range(v) else float(v) for k, v in mix.items()}


def lean(profile, mix: Optional[Mapping]) -> float:
    """How far a chart's pools lean the way the mix's weights ask: each pool's stars over the total, times its weight."""
    if not mix:
        return 0.0
    total = float(profile.total_stars) or 1.0
    return sum(w * float(profile.skills[k].stars) / total for k, w in mix.items()
               if not is_range(w) and k in profile.skills)


def outside(stars: float, rng) -> float:
    """How many stars `stars` lies outside the range (0 inside)."""
    lo, hi = rng
    if lo is not None and stars < lo:
        return lo - stars
    if hi is not None and stars > hi:
        return stars - hi
    return 0.0


def miss(profile, mix: Optional[Mapping]) -> float:
    """The stars the pools lie outside their ranges, summed."""
    if not mix:
        return 0.0
    return sum(outside(float(profile.skills[k].stars), r) for k, r in mix.items() if is_range(r) and k in profile.skills)


def penalty(profile, mix: Optional[Mapping]) -> float:
    """What the mix holds against a chart, in stars off target: its misses less its lean (lower is better)."""
    if not mix or profile is None:
        return 0.0
    return RANGE_WEIGHT * miss(profile, mix) - WEIGHT * lean(profile, mix)


def misses(profile, mix: Optional[Mapping]) -> List[Dict]:
    """Each pool outside its range: pool, low, high and the stars it has."""
    if not mix or profile is None:
        return []
    out = []
    for k, r in mix.items():
        if is_range(r) and k in profile.skills:
            stars = float(profile.skills[k].stars)
            if outside(stars, r) > 0.005:
                out.append({"pool": k, "low": r[0], "high": r[1], "stars": round(stars, 2)})
    return out


def miss_text(profile, mix: Optional[Mapping]) -> str:
    """The misses as one line for a warning: "Jack wants ≥ 6.00★, has 4.80★"."""
    parts = []
    for m in misses(profile, mix):
        want = _range_text((m["low"], m["high"]))
        parts.append(tr(f"{tr(*NAMES[m['pool']])} 要 {want}，实际 {m['stars']:.2f}★",
                        f"{tr(*NAMES[m['pool']])} wants {want}, has {m['stars']:.2f}★"))
    return tr("；", "; ").join(parts)


def skill_weights(mix: Optional[Mapping], profile=None) -> Optional[Dict[str, float]]:
    """The downscaler's skill weights (-1..1) for the mix: weights as they are; a pool above its range is deleted
    first (-1), one below it or at its floor kept to the last (+1). Without a profile, a range with a ceiling only
    leans down, one with a floor only up."""
    if not mix:
        return None
    out: Dict[str, float] = {}
    for k, v in mix.items():
        if not is_range(v):
            out[k] = float(v)
            continue
        lo, hi = v
        if profile is not None and k in profile.skills:
            stars = float(profile.skills[k].stars)
            if hi is not None and stars > hi:
                out[k] = -1.0
            elif lo is not None and stars <= lo + 0.25:
                out[k] = 1.0
        elif hi is not None and lo is None:
            out[k] = -0.5
        elif lo is not None and hi is None:
            out[k] = 0.5
    return {k: w for k, w in out.items() if w} or None


def _range_text(r) -> str:
    lo, hi = r
    if lo is not None and hi is not None:
        return f"{lo:g}–{hi:g}★"
    return f"≥ {lo:g}★" if lo is not None else f"≤ {hi:g}★"


def describe(mix: Optional[Mapping]) -> str:
    if not mix:
        return ""
    return ", ".join(f"{tr(*NAMES[k])} {_range_text(w)}" if is_range(w) else
                     f"{tr(*NAMES[k])} {'+' if w > 0 else '−'}{abs(w):g}" for k, w in mix.items())


def pools(profile, mix: Optional[Mapping]) -> str:
    """The stars of the pools the mix names, for a log line."""
    if not mix:
        return ""
    return " ".join(f"{tr(*NAMES[k])} {float(profile.skills[k].stars):.2f}" for k in mix if k in profile.skills)
