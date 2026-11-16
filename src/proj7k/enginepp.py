"""
Engine pp: osu!mania's performance points with the difficulty engine's stars in place of osu!'s star rating (ADR-0061).

The formula is osu!'s own `ManiaPerformanceCalculator`, unchanged but for its one input:

    pp = 8 · max(★ − 0.15, 0.05)^2.2 · max(0, 5·acc − 4) · (1 + 0.1 · min(1, hits / 1500)) · mods

- ★ is the engine's total stars of the chart as played (a rate mod plays a different chart: the song compressed);
- acc is osu!mania's pp accuracy, not the score's displayed one: MAX counts 320 for a stable (classic) score and 305
  for a lazer one (ppy/osu#25487), 300 / 200 / 100 / 50 for the rest, over every judgement including misses;
- hits is the number of judgements (a lazer hold note is judged at its head and its tail);
- mods: No Fail × 0.75, Easy × 0.5.

A player's total is osu!'s weighting: the best play per chart, sorted, the i-th (from 0) counted at 0.95^i. osu!'s
"bonus pp" for the number of ranked scores is left out, as it is from osu!'s per-key-count (7K) pp.

Engine stars sit on their own scale (calibrated to the Jinjin dan ladder, higher than osu!'s at the top), so engine pp
is comparable only with engine pp: it says how a player's plays rank by this engine's difficulty, not what osu! pays.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

#: osu!'s top-play weighting: the i-th best play (from 0) counts 0.95^i of its pp.
WEIGHT_DECAY = 0.95
#: How many plays a total counts (osu! shows and weights a player's best 100 per mode on the profile).
TOP_PLAYS = 100
#: Mod multipliers osu!mania's pp applies.
MOD_MULTIPLIERS = {"NF": 0.75, "EZ": 0.5}
#: The lazer mod osu! marks a score imported from (or played as) stable with.
CLASSIC = "CL"

_JUDGEMENT_VALUES = (("great", 300), ("good", 200), ("ok", 100), ("meh", 50), ("miss", 0))


def _acronym(mod: Any) -> str:
    if isinstance(mod, Mapping):
        mod = mod.get("acronym", "")
    text = str(mod).upper()
    # Display labels carry a custom rate ("DT1.2x"); the mod is the letters before it.
    letters = ""
    for ch in text:
        if not ch.isalpha():
            break
        letters += ch
    return letters or text


def acronyms(mods: Iterable[Any]) -> List[str]:
    """The mods' plain acronyms, from lazer mod dicts, display labels ("DT1.2x") or acronyms."""
    return [_acronym(m) for m in mods or ()]


def pp_accuracy(judgements: Mapping[str, int], legacy: bool = False) -> float:
    """osu!mania's pp accuracy: MAX at 320 (stable) or 305 (lazer), then 300/200/100/50/0, over every judgement."""
    perfect_value = 320 if legacy else 305
    hits = total_hits(judgements)
    if hits <= 0:
        return 0.0
    points = int(judgements.get("perfect", 0) or 0) * perfect_value
    points += sum(int(judgements.get(k, 0) or 0) * v for k, v in _JUDGEMENT_VALUES)
    return points / (hits * perfect_value)


def total_hits(judgements: Mapping[str, int]) -> int:
    return sum(int(judgements.get(k, 0) or 0) for k in ("perfect", "great", "good", "ok", "meh", "miss"))


def mod_multiplier(mods: Iterable[Any]) -> float:
    out = 1.0
    for a in set(acronyms(mods)):
        out *= MOD_MULTIPLIERS.get(a, 1.0)
    return out


def is_legacy(mods: Iterable[Any], legacy: Optional[bool] = None) -> bool:
    """A stable score: said so outright, or carrying lazer's Classic mod."""
    return bool(legacy) if legacy is not None else CLASSIC in acronyms(mods)


def difficulty_value(stars: float, accuracy: float, hits: int) -> float:
    """osu!mania's pp curve: stars to pp, scaled by accuracy above 80% and a length bonus capped at 1500 hits."""
    return (8.0 * max(stars - 0.15, 0.05) ** 2.2
            * max(0.0, 5.0 * accuracy - 4.0)
            * (1.0 + 0.1 * min(1.0, hits / 1500.0)))


def play_pp(stars: float, judgements: Mapping[str, int], mods: Sequence[Any] = (), legacy: Optional[bool] = None) -> float:
    """One play's pp at `stars` (the engine's, for engine pp; osu!'s reproduces osu!'s own figure)."""
    if stars is None or stars <= 0:
        return 0.0
    acc = pp_accuracy(judgements, is_legacy(mods, legacy))
    return difficulty_value(float(stars), acc, total_hits(judgements)) * mod_multiplier(mods)


def best_per_chart(plays: Iterable[Dict[str, Any]], key: str = "md5", value: str = "pp") -> List[Dict[str, Any]]:
    """Each chart's highest-pp play, best first (osu! counts one play per chart)."""
    best: Dict[Any, Dict[str, Any]] = {}
    for p in plays:
        k = p.get(key)
        if k is None:
            continue
        if k not in best or (p.get(value) or 0.0) > (best[k].get(value) or 0.0):
            best[k] = p
    return sorted(best.values(), key=lambda p: -(p.get(value) or 0.0))


def weighted_total(pps: Iterable[float], limit: int = TOP_PLAYS) -> float:
    """osu!'s total: the best `limit` pp values, the i-th (from 0) at 0.95^i."""
    ranked = sorted((float(x) for x in pps if x), reverse=True)[:limit]
    return sum(x * WEIGHT_DECAY ** i for i, x in enumerate(ranked))
