"""
The search syntax of the catalog: osu!'s beatmap search (free words plus `key<op>value` filters, e.g.
`stars>5 status=ranked`) with the engine's own keys added.

    camellia engine>=7 dan<=10th skill=ln_inverse ln>50 length<180

A beatmapset matches when one of its difficulties satisfies every filter and every free word appears in
the set's title, artist, mapper, source or tags, or in that difficulty's name (as on osu!).

Keys (aliases in brackets):
    stars [sr, official]  official star rating           engine [esr, proj7k]  engine star rating
    delta                 engine minus official           dan                   engine tier, compared on the ladder
    skill [dominant]      the dominant skill              bpm, length (s, or m:ss), notes, ln (% of notes), od, hp
    status                ranked/loved/qualified/...      creator, artist, title, source, tag  (substring)
    mapper                who wrote the difficulty (guest difficulties included; substring)
    mapperid              that mapper's osu! user id

`=` on a number matches its written precision: `stars=5` is [5, 6), `engine=6.5` is [6.5, 6.6).
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from proj7k.dan import CANONICAL_DAN_TIERS, parse_dan_tier
from proj7k.engine import SKILLS
from proj7k.engine.skills import SKILL_TECH_KEY

NUMERIC = {
    "stars": "b.official_sr", "sr": "b.official_sr", "official": "b.official_sr",
    "engine": "b.engine_sr", "esr": "b.engine_sr", "proj7k": "b.engine_sr",
    "delta": "(b.engine_sr - b.official_sr)",
    "bpm": "b.bpm",
    "length": "b.length_s",
    "notes": "b.note_count",
    "ln": "(100.0 * b.ln_count / b.note_count)",
    "od": "b.od", "hp": "b.hp",
}
TEXT = {
    "creator": "s.creator_search", "mapper": "b.mapper_search",
    "artist": "s.artist_search", "title": "s.title_search",
    "source": "s.source_search", "tag": "s.tags_search", "tags": "s.tags_search",
}
STATUSES = ("ranked", "approved", "qualified", "loved", "pending", "wip", "graveyard", "unknown")

#: Accepted spellings of a skill: the engine's name, the legacy short key, and the part after `ln_`.
SKILL_ALIASES = {**{k: k for k in SKILLS}, **{v: k for k, v in SKILL_TECH_KEY.items()},
                 "inverse": "ln_inverse", "release": "ln_release", "stamina": "rc_stamina"}

_TOKEN = re.compile(r'(\w+)(<=|>=|!=|==|=|:|<|>)("[^"]*"|\S+)|"([^"]*)"|(\S+)', re.UNICODE)


@dataclass
class SearchQuery:
    words: List[str] = field(default_factory=list)
    conditions: List[Tuple[str, tuple]] = field(default_factory=list)   # SQL fragment, parameters
    filters: List[str] = field(default_factory=list)                    # the recognised `key op value` tokens

    def where(self) -> Tuple[str, tuple]:
        parts, params = [], []
        for word in self.words:
            parts.append("(instr(s.search_text, ?) > 0 OR instr(b.version_search, ?) > 0)")
            params += [word, word]
        for sql, p in self.conditions:
            parts.append(sql)
            params += list(p)
        return (" AND ".join(parts) or "1"), tuple(params)


def _precision_range(text: str) -> Tuple[float, float]:
    value = float(text)
    decimals = len(text.split(".", 1)[1]) if "." in text else 0
    return value, value + 10.0 ** -decimals


def _length_seconds(text: str) -> str:
    """`2:30` -> 150, `3m` -> 180, `90s` / `90` -> 90 (kept as text for `_precision_range`)."""
    if ":" in text:
        m, s = text.split(":", 1)
        return str(int(m) * 60 + float(s))
    if text.endswith("m"):
        return str(float(text[:-1]) * 60)
    return text[:-1] if text.endswith("s") else text


def _compare(expr: str, op: str, raw: str) -> Optional[Tuple[str, tuple]]:
    try:
        if op in ("=", ":", "=="):
            lo, hi = _precision_range(raw)
            return f"({expr} >= ? AND {expr} < ?)", (lo, hi)
        value = float(raw)
    except ValueError:
        return None
    if op == "!=":
        lo, hi = _precision_range(raw)
        return f"NOT ({expr} >= ? AND {expr} < ?)", (lo, hi)
    return f"{expr} {op} ?", (value,)


def _filter(key: str, op: str, raw: str) -> Optional[Tuple[str, tuple]]:
    key = key.lower()
    if key in NUMERIC:
        if key == "length":
            raw = _length_seconds(raw)
        return _compare(NUMERIC[key], op, raw)
    if key in TEXT:
        if op not in ("=", ":", "=="):
            return None
        return f"instr({TEXT[key]}, ?) > 0", (raw.casefold(),)
    if key == "status":
        status = raw.lower()
        if status not in STATUSES or op not in ("=", ":", "==", "!="):
            return None
        return ("s.status != ?" if op == "!=" else "s.status = ?"), (status,)
    if key in ("skill", "dominant"):
        skill = SKILL_ALIASES.get(raw.lower())
        if skill is None or op not in ("=", ":", "==", "!="):
            return None
        return ("b.dominant_skill != ?" if op == "!=" else "b.dominant_skill = ?"), (skill,)
    if key == "mapperid":
        if op not in ("=", ":", "==") or not raw.lstrip("-").isdigit():
            return None
        return "b.mapper_id = ?", (int(raw),)
    if key == "dan":
        try:
            tier = CANONICAL_DAN_TIERS.index(parse_dan_tier(raw))
        except ValueError:
            return None
        sql_op = {":": "=", "==": "="}.get(op, op)
        return f"b.dan_index {sql_op} ?", (tier,)
    return None


def parse_query(text: str) -> SearchQuery:
    """The query of a search box's text. Unknown or malformed filters are searched as plain words, as on osu!."""
    query = SearchQuery()
    for m in _TOKEN.finditer(text or ""):
        key, op, raw, quoted, word = m.groups()
        if key is not None:
            value = raw[1:-1] if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2 else raw
            cond = _filter(key, op, value)
            if cond is not None:
                query.conditions.append(cond)
                query.filters.append(f"{key.lower()}{op}{value}")
                continue
            word = m.group(0)
        word = quoted if quoted is not None else word
        if word and word.strip():
            query.words.append(word.strip().casefold())
    return query
