"""
The chart design brief: what a mapper decides before placing a note (ADR-0033).

A brief is a JSON file written by the chart-design skill (or by hand). For each section of the song it
names the section's role, the layer the chart follows, one primary technique with its key pattern, at
most one accent pattern and where it goes, how dense the section is relative to the others, and its long
notes. The generator places the notes; the brief decides what they are about.

    {
      "song": "XenjeS - Inorganic Dystopia",
      "ln_ratio": 0.0,
      "sections": [
        {"bars": [0, 15], "role": "intro", "lead": "other", "motive": "piano arpeggio, sparse",
         "intensity": 0.3, "technique": "rc_speed", "pattern": "stairs",
         "accent": {"pattern": "jumptrill", "at": "phrase_end"}, "ln": {"ratio": 0.2, "kind": "release"}},
        ...
      ]
    }

Bars count from 0, the first measure of the chart's timing; `bars` is inclusive. Sections must not
overlap; bars no section covers keep the generator's own choices.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

from proj7k.engine import SKILLS
from proj7k.generator.holds import KINDS
from proj7k.generator.patterns import popcount
from proj7k.generator.stems import STEMS

ROLES = {
    "intro": "前奏", "verse": "主歌", "build": "铺垫", "chorus": "高潮", "drop": "高潮",
    "break": "间奏", "bridge": "过渡", "outro": "尾奏",
}


def _stairs(m: int, r1: int, r2: int) -> bool:
    if popcount(m) != 1 or popcount(r1) != 1:
        return False
    a, b = r1.bit_length() - 1, m.bit_length() - 1
    step = (a - (r2.bit_length() - 1)) if popcount(r2) == 1 else 0
    # A stair runs one way and turns at the edge of the keys.
    return abs(b - a) == 1 and (step == 0 or b - a == step or a in (0, 6))


#: Key patterns: name -> (what players call it, whether a row `m` continues it after rows `r1`, `r2`).
PATTERNS: Dict[str, Tuple[str, Callable[[int, int, int], bool]]] = {
    "stairs": ("楼梯", _stairs),
    "split": ("切", lambda m, r1, r2: popcount(m) == 1 and not m & r1 and (r1.bit_length() - 1 < 3) != (m.bit_length() - 1 < 3)),
    "trill": ("颤", lambda m, r1, r2: popcount(m) == 1 and m == r2 and m != r1),
    "jumptrill": ("跳颤", lambda m, r1, r2: popcount(m) >= 2 and m == r2 and not m & r1),
    "jack": ("单叠", lambda m, r1, r2: bool(m & r1) and popcount(m) == 1),
    "chordjack": ("跳叠", lambda m, r1, r2: popcount(m) >= 2 and bool(m & r1)),
    "chordstream": ("和弦串", lambda m, r1, r2: popcount(m) >= 2 and not m & r1),
    "handstream": ("手流", lambda m, r1, r2: popcount(m) >= 2 and not m & r1 and popcount(m & r2) <= 1),
    "free": ("随意", lambda m, r1, r2: True),
}
#: The pattern a technique gets when a brief names the technique alone.
DEFAULT_PATTERN = {
    "rc_jack": "chordjack", "rc_tech": "free", "rc_speed": "stairs", "rc_stamina": "chordstream",
    "ln_general": "free", "ln_tech": "free", "ln_inverse": "free", "ln_release": "free",
}
#: The long notes a long-note technique gets when the brief gives none.
DEFAULT_LN = {"ln_general": (0.6, "general"), "ln_tech": (0.6, "release"), "ln_release": (0.6, "release"),
              "ln_inverse": (1.0, "inverse")}
#: Patterns of single notes and of chords: a row's size follows its pattern.
SINGLE = frozenset({"stairs", "split", "trill", "jack"})
CHORD = frozenset({"jumptrill", "chordjack", "chordstream", "handstream"})
#: What a pattern falls back to on a row it cannot continue (a single note in a chordjack section).
FALLBACK = {"chordjack": "jack", "jumptrill": "trill", "chordstream": "split", "handstream": "split",
            "jack": "chordjack", "trill": "jumptrill", "stairs": "split"}
ACCENT_AT = ("phrase_end", "phrase_start")


@dataclass
class Accent:
    pattern: str
    at: Union[str, List[int]] = "phrase_end"   # "phrase_end", "phrase_start", or a list of bars


@dataclass
class BriefSection:
    first_bar: int
    last_bar: int
    role: str = ""
    lead: Optional[str] = None
    motive: str = ""
    intensity: float = 0.5
    technique: Optional[str] = None
    pattern: Optional[str] = None
    accent: Optional[Accent] = None
    ln: Optional[Tuple[float, str]] = None


@dataclass
class Brief:
    sections: List[BriefSection]
    song: str = ""
    ln_ratio: float = 0.0
    notes: str = ""
    raw: dict = field(default_factory=dict)

    def leads(self) -> Dict[int, str]:
        return {s.first_bar: s.lead for s in self.sections if s.lead}

    def holds(self) -> Dict[int, Tuple[float, str]]:
        out = {}
        for s in self.sections:
            if s.ln is not None:
                out[s.first_bar] = s.ln
            elif s.technique in DEFAULT_LN:
                out[s.first_bar] = DEFAULT_LN[s.technique]
            else:
                out[s.first_bar] = (self.ln_ratio, "general")
        return out

    def section_at(self, bar: int) -> Optional[BriefSection]:
        for s in self.sections:
            if s.first_bar <= bar <= s.last_bar:
                return s
        return None


class BriefError(ValueError):
    pass


def load(path: Union[str, Path]) -> Brief:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise BriefError(f"读不了设计稿 {path}：{e}") from e
    return parse(data)


def parse(data: dict) -> Brief:
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list) or not data["sections"]:
        raise BriefError("设计稿需要一个非空的 sections 列表")
    secs = [_section(i, s) for i, s in enumerate(data["sections"])]
    secs.sort(key=lambda s: s.first_bar)
    for a, b in zip(secs, secs[1:]):
        if b.first_bar <= a.last_bar:
            raise BriefError(f"段落重叠：{a.first_bar}–{a.last_bar} 与 {b.first_bar}–{b.last_bar}")
    ln_ratio = float(data.get("ln_ratio", 0.0))
    if not 0.0 <= ln_ratio <= 1.0:
        raise BriefError("ln_ratio 要在 0 到 1 之间")
    return Brief(secs, str(data.get("song", "")), ln_ratio, str(data.get("notes", "")), data)


def _section(i: int, s: dict) -> BriefSection:
    where = f"第 {i + 1} 段"
    bars = s.get("bars")
    if not (isinstance(bars, list) and len(bars) == 2 and all(isinstance(b, int) for b in bars) and 0 <= bars[0] <= bars[1]):
        raise BriefError(f"{where}：bars 要写成 [起始小节, 结束小节]（从 0 数，含结束）")
    lead = s.get("lead")
    if lead is not None and lead not in STEMS:
        raise BriefError(f"{where}：lead 只能是 {', '.join(STEMS)}")
    tech = s.get("technique")
    if tech is not None and tech not in SKILLS:
        raise BriefError(f"{where}：technique 只能是 {', '.join(SKILLS)}")
    pattern = s.get("pattern") or (DEFAULT_PATTERN.get(tech) if tech else None)
    if pattern is not None and pattern not in PATTERNS:
        raise BriefError(f"{where}：pattern 只能是 {', '.join(PATTERNS)}")
    accent = None
    if s.get("accent"):
        a = s["accent"]
        if not isinstance(a, dict) or a.get("pattern") not in PATTERNS:
            raise BriefError(f"{where}：accent 要写成 {{\"pattern\": 键形, \"at\": \"phrase_end\"}}")
        at = a.get("at", "phrase_end")
        if not (at in ACCENT_AT or (isinstance(at, list) and all(isinstance(b, int) for b in at))):
            raise BriefError(f"{where}：accent.at 只能是 phrase_end、phrase_start 或小节列表")
        accent = Accent(a["pattern"], at)
    ln = None
    if s.get("ln"):
        l = s["ln"]
        kind = l.get("kind", "general")
        ratio = float(l.get("ratio", 1.0 if kind == "inverse" else 0.5))
        if kind not in KINDS or not 0.0 <= ratio <= 1.0:
            raise BriefError(f"{where}：ln 要写成 {{\"ratio\": 0–1, \"kind\": {' / '.join(KINDS)}}}")
        ln = (ratio, kind)
    intensity = float(s.get("intensity", 0.5))
    if not 0.0 <= intensity <= 1.0:
        raise BriefError(f"{where}：intensity 要在 0 到 1 之间")
    return BriefSection(bars[0], bars[1], str(s.get("role", "")), lead, str(s.get("motive", "")), intensity,
                        tech, pattern, accent, ln)


def row_shapes(brief: Brief, bars: Sequence[int], in_bar: Sequence[float], beats_per_bar: int = 4,
               phrase_bars: int = 4) -> List[Optional[str]]:
    """The key pattern each row must continue (rows given by bar and beats into the bar): the section's
    accent at its accent places, else its primary pattern (None where no section of the brief covers the
    row, or the pattern is "free")."""
    out: List[Optional[str]] = []
    for bar, beat in zip(bars, in_bar):
        s = brief.section_at(int(bar))
        if s is None:
            out.append(None)
            continue
        shape = s.pattern
        if s.accent is not None and _accent_here(s, int(bar), float(beat), beats_per_bar, phrase_bars):
            shape = s.accent.pattern
        out.append(None if shape in (None, "free") else shape)
    return out


def _accent_here(s: BriefSection, bar: int, beat: float, beats_per_bar: int, phrase_bars: int) -> bool:
    at = s.accent.at
    if isinstance(at, list):
        return bar in at
    k = (bar - s.first_bar) % phrase_bars
    if at == "phrase_end":
        return k == phrase_bars - 1 and beat >= beats_per_bar - 1 - 1e-6
    return k == 0 and beat < 1.0 - 1e-6


def sized(size: int, shape: Optional[str]) -> int:
    """A row's chord size under its pattern: one note for a single-note pattern, at least two for a chord one."""
    if size <= 0 or shape is None:
        return size
    if shape in SINGLE:
        return 1
    if shape in CHORD:
        return max(2, size)
    return size


def predicates(shape: Optional[str]) -> Optional[List[Callable[[int, int, int], bool]]]:
    """The tests a row's mask is drawn under: the shape's, then its fallback's."""
    if shape is None:
        return None
    out = [PATTERNS[shape][1]]
    if shape in FALLBACK:
        out.append(PATTERNS[FALLBACK[shape]][1])
    return out


PRIOR_PATH = Path(__file__).with_name("technique_prior.json")


def load_prior() -> Optional[dict]:
    try:
        return json.loads(PRIOR_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def auto(sections: Sequence, target_stars: float, ln_ratio: float = 0.0, seed: int = 0,
         prior: Optional[dict] = None) -> Optional[Brief]:
    """A brief made without a mapper (ADR-0033 decision 8): the chart uses as many techniques as people's
    charts at its level do, the loud parts get what loud parts get, sections that sound alike share one,
    and long-note techniques only when the chart has long notes. None without a prior."""
    import numpy as np

    prior = prior or load_prior()
    if not prior or not sections:
        return None
    band = next((b for b in prior["bands"].values() if b["stars"][0] <= target_stars < b["stars"][1]), None)
    if band is None:
        return None
    rng = np.random.default_rng([seed, 33])
    allowed = [s for s in SKILLS if ln_ratio > 0 or s.startswith("rc_")]

    def pick(weights: Dict[str, float], among: Sequence[str]) -> Optional[str]:
        w = np.array([max(weights.get(s, 0.0), 0.0) for s in among])
        if not len(among) or w.sum() <= 0:
            return among[0] if among else None
        return among[int(rng.choice(len(among), p=w / w.sum()))]

    counts = {int(k): v for k, v in band["techniques_per_chart"].items() if int(k) > 0}
    k = pick({str(n): v for n, v in counts.items()}, [str(n) for n in sorted(counts)]) if counts else "2"
    k = max(1, min(3, int(k)))
    chosen = [pick(band["by_energy"].get("loud", band["primary_share"]), allowed)]
    while len(chosen) < k:
        rest = [s for s in allowed if s not in chosen]
        if not rest:
            break
        w = {s: band["primary_share"].get(s, 0.0) * (1e-3 + sum(band["used_with"].get(c, {}).get(s, 0.0) for c in chosen))
             for s in rest}
        nxt = pick(w, rest)
        if nxt is None:
            break
        chosen.append(nxt)
    chosen = [c for c in chosen if c]
    by_label: Dict[int, str] = {}
    out = []
    for s in sections:
        tier = "quiet" if s.energy < 1 / 3 else "middle" if s.energy < 2 / 3 else "loud"
        if s.label not in by_label:
            w = band["by_energy"].get(tier, {})
            by_label[s.label] = pick({c: w.get(c, 0.0) + 0.05 for c in chosen}, chosen)
        tech = by_label[s.label]
        pattern = DEFAULT_PATTERN[tech]
        if tech == "rc_speed":
            pattern = ("stairs", "split")[int(rng.integers(2))]
        elif tech == "rc_jack" and s.energy < 0.5:
            pattern = "jack"
        ln = None
        if tech in DEFAULT_LN:
            r, kind = DEFAULT_LN[tech]
            ln = (r if kind == "inverse" else max(ln_ratio, 0.3), kind)
        out.append(BriefSection(s.first_bar, s.last_bar, "", None, "", round(0.35 + 0.4 * s.energy, 2), tech,
                                pattern, None, ln))
    return Brief(out, "", ln_ratio, "auto")
