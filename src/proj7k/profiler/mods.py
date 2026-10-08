"""
What a replay's mods did to the chart that was played, as far as alignment and the difficulty field care.

osu!lazer writes two records of a score's mods into the .osr: the legacy bitmask in the header, and, after the
frames, its own mod list with settings. Only the second knows a rate mod's actual speed (DT at 1.1x is still bit 64)
and lazer-only mods. When it is there it decides; a replay without it (osu!stable) falls back to the bitmask.

- Rate (DT/NC/HT/DC): replay frames are in song time, and osu!mania keeps its hit windows fixed in real time, so in
  song time the windows are `rate` times as wide; the chart's real-time demand is the song compressed by `rate`.
- HR/EZ: the hit windows divided / multiplied by 1.4. Difficulty Adjust (DA) can override the OD.
- Mirror: lazer flips the chart's columns before play, so the frames are in flipped column space.
- Mods that rearrange or convert the chart beyond that (Random, key mods, Invert, Hold Off, Dual Stages, ...),
  change the clock during the song (Wind Up/Down, Adaptive Speed), change how LNs are judged (No Release) or are
  not the player at all (Autoplay, Cinema) cannot be aligned against the chart file: `unsupported` lists them.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

#: osu!mania's HR/EZ hit window multipliers.
HARD_ROCK_WINDOW_MULTIPLIER = 1.0 / 1.4
EASY_WINDOW_MULTIPLIER = 1.4

# Legacy bitmask (osu!stable `Mods`).
EZ, HR, DT, HT, NC = 2, 16, 64, 256, 512
AUTOPLAY, CINEMA = 2048, 1 << 22
RANDOM, MIRROR = 1 << 21, 1 << 30
KEY7 = 1 << 18
#: Key-count mods other than 7K, and co-op (dual stages): they convert a 7K chart into a different one.
OTHER_KEY_MODS = {
    1 << 15: "4K", 1 << 16: "5K", 1 << 17: "6K", 1 << 19: "8K", 1 << 24: "9K",
    1 << 26: "1K", 1 << 27: "3K", 1 << 28: "2K", 1 << 25: "DS",
}
#: Bits that change what alignment or the field sees; a snapshot written before mods were honoured is stale if
#: its bitmask carries one of them.
AFFECTING_BITS = EZ | HR | DT | HT | NC | AUTOPLAY | CINEMA | RANDOM | MIRROR | sum(OTHER_KEY_MODS)

#: lazer acronyms that cannot be aligned against the chart file (see the module docstring).
UNSUPPORTED_ACRONYMS = frozenset({
    "RD", "IN", "HO", "DS", "NR", "WU", "WD", "AS", "AT", "CN",
    "1K", "2K", "3K", "4K", "5K", "6K", "8K", "9K", "10K",
})
_RATE_DEFAULTS = {"DT": 1.5, "NC": 1.5, "HT": 0.75, "DC": 0.75}


@dataclass
class PlayMods:
    """The parts of a play's mods that alignment and the difficulty field need."""

    clock_rate: float = 1.0
    #: Hit windows (in real time) multiplied by this: HR 1/1.4, EZ 1.4.
    window_multiplier: float = 1.0
    #: Difficulty Adjust's overall difficulty, when it set one.
    od_override: Optional[float] = None
    mirror: bool = False
    #: Mods the profiler cannot honour; a play with any is not a measurement of the chart file.
    unsupported: List[str] = field(default_factory=list)
    #: The mods as read, for display: lazer acronyms (with a custom rate, e.g. "DT1.1x") or the bitmask's names.
    acronyms: List[str] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return not self.unsupported

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clock_rate": self.clock_rate,
            "window_multiplier": round(self.window_multiplier, 4),
            "od_override": self.od_override,
            "mirror": self.mirror,
            "unsupported": list(self.unsupported),
            "acronyms": list(self.acronyms),
        }


def _mods_from_lazer(lazer_mods: Sequence[Dict[str, Any]]) -> PlayMods:
    pm = PlayMods()
    for mod in lazer_mods:
        acronym = str(mod.get("acronym", "")).upper()
        settings = mod.get("settings") or {}
        label = acronym
        if acronym in _RATE_DEFAULTS:
            pm.clock_rate = float(settings.get("speed_change", _RATE_DEFAULTS[acronym]))
            if abs(pm.clock_rate - _RATE_DEFAULTS[acronym]) > 1e-6:
                label = f"{acronym}{pm.clock_rate:g}x"
        elif acronym == "HR":
            pm.window_multiplier *= HARD_ROCK_WINDOW_MULTIPLIER
        elif acronym == "EZ":
            pm.window_multiplier *= EASY_WINDOW_MULTIPLIER
        elif acronym == "MR":
            pm.mirror = True
        elif acronym == "DA" and settings.get("overall_difficulty") is not None:
            pm.od_override = float(settings["overall_difficulty"])
        if acronym in UNSUPPORTED_ACRONYMS:
            pm.unsupported.append(acronym)
        pm.acronyms.append(label)
    return pm


_LEGACY_NAMES = [
    (1, "NF"), (EZ, "EZ"), (8, "HD"), (HR, "HR"), (32, "SD"), (NC, "NC"), (DT, "DT"), (HT, "HT"),
    (1024, "FL"), (AUTOPLAY, "AT"), (16384, "PF"), (1 << 20, "FI"), (RANDOM, "RD"), (CINEMA, "CN"),
    (KEY7, "7K"), (1 << 29, "SV2"), (MIRROR, "MR"),
]


def _mods_from_bitmask(mods: int) -> PlayMods:
    pm = PlayMods()
    if mods & (DT | NC):
        pm.clock_rate = 1.5
    elif mods & HT:
        pm.clock_rate = 0.75
    if mods & HR:
        pm.window_multiplier *= HARD_ROCK_WINDOW_MULTIPLIER
    if mods & EZ:
        pm.window_multiplier *= EASY_WINDOW_MULTIPLIER
    pm.mirror = bool(mods & MIRROR)
    for bit, name in _LEGACY_NAMES:
        if mods & bit and not (bit == DT and mods & NC):  # NC carries the DT bit too
            pm.acronyms.append(name)
    for bit, name in ((RANDOM, "RD"), (AUTOPLAY, "AT"), (CINEMA, "CN"), *OTHER_KEY_MODS.items()):
        if mods & bit:
            pm.unsupported.append(name)
            if name not in pm.acronyms:
                pm.acronyms.append(name)
    return pm


def resolve_play_mods(mods: int, lazer_mods: Optional[Sequence[Dict[str, Any]]] = None) -> PlayMods:
    """The play's mods: lazer's own list when the replay carries one, else the legacy bitmask."""
    if lazer_mods is not None:
        return _mods_from_lazer(lazer_mods)
    return _mods_from_bitmask(mods)
