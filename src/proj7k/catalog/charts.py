"""
One `.osu` file to one catalog row: the chart's metadata as osu! shows it, a few listing statistics,
and the engine's profile stamped with the engine version that produced it.

The official star rating is never computed here: it comes with the chart from wherever the chart came
from (the osu! API, the benchmark manifest, the player's osu!lazer library before proj7k rewrote it), and
a chart without one is listed without one.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from proj7k.dan import estimate_canonical_dan
from proj7k.engine import SKILLS, engine_version, evaluate_osu
from proj7k.parser import Beatmap7K, NoteType, dominant_bpm, parse_osu_7k

#: Bins of the note-density outline drawn on a chart's page.
DENSITY_BINS = 100


@dataclass
class ChartRow:
    # The beatmap (one difficulty).
    beatmap_id: int
    beatmapset_id: int
    checksum: str            # MD5 of the `.osu` content, as osu! and osu!lazer key a difficulty
    version: str
    official_sr: Optional[float]
    official_sr_source: Optional[str]
    engine_sr: float
    engine_version: str
    dan: str
    dominant_skill: str
    dominance_margin: float
    skills: Dict[str, Dict[str, float]]   # skill -> {"stars", "dominance", "coverage"}
    bpm: float
    length_s: float
    note_count: int
    ln_count: int
    od: float
    hp: float
    density: List[int]
    # The beatmapset it belongs to.
    title: str
    title_unicode: str
    artist: str
    artist_unicode: str
    creator: str
    source: str
    tags: str
    status: str = "unknown"
    ranked_date: Optional[str] = None
    submitted_date: Optional[str] = None
    creator_id: Optional[int] = None
    # Who wrote this difficulty: osu!'s owner for a guest difficulty, else the set's creator.
    mapper_id: Optional[int] = None
    mapper_name: str = ""
    patterns: Dict[str, Any] = field(default_factory=dict)
    osu_content: str = field(default="", repr=False)

    @property
    def ln_ratio(self) -> float:
        return self.ln_count / self.note_count if self.note_count else 0.0


def pattern_features(beatmap: Beatmap7K) -> Dict[str, Any]:
    """How a chart is written, independent of how hard it is: what a mapper's style is made of.

    - `chord`: mean notes per row (a row is the notes whose heads share a millisecond)
    - `jack`: share of notes on a column that the previous row also hit
    - `nps`: notes per second between the first head and the last release
    - `columns`: share of notes on each of the seven columns (left to right)
    """
    rows: Dict[int, set] = {}
    for h in beatmap.hit_objects:
        rows.setdefault(int(round(h.time)), set()).add(h.column)
    times = sorted(rows)
    jacks = sum(len(rows[t] & rows[p]) for p, t in zip(times, times[1:]))
    n = len(beatmap.hit_objects)
    columns = [0] * 7
    for h in beatmap.hit_objects:
        if 0 <= h.column < 7:
            columns[h.column] += 1
    first = min(h.time for h in beatmap.hit_objects)
    last = max(max(h.time, h.end_time or 0.0) for h in beatmap.hit_objects)
    return {
        "chord": round(n / len(times), 4),
        "jack": round(jacks / n, 4),
        "nps": round(n / max((last - first) / 1000.0, 1.0), 3),
        "columns": [round(c / n, 4) for c in columns],
    }


def local_id(seed: str) -> int:
    """A stable negative id for a chart or set osu! has not given an id (an unsubmitted map)."""
    return -int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:12], 16)


def _int(value: Optional[str]) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _float(value: Optional[str], default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def chart_row(
    content: str,
    official_sr: Optional[float] = None,
    official_sr_source: Optional[str] = None,
    status: str = "unknown",
    ranked_date: Optional[str] = None,
    submitted_date: Optional[str] = None,
    creator_id: Optional[int] = None,
    mapper_id: Optional[int] = None,
    mapper_name: Optional[str] = None,
) -> ChartRow:
    """The catalog row of a 7K `.osu` file's content. Raises `ValueError` for anything but a 7K mania chart."""
    beatmap = parse_osu_7k(content)
    if not beatmap.hit_objects:
        raise ValueError("chart has no notes")
    meta = beatmap.extra_sections.get("Metadata", {})
    diff = beatmap.extra_sections.get("Difficulty", {})

    profile = evaluate_osu(content)

    title = meta.get("Title", beatmap.title)
    artist = meta.get("Artist", beatmap.artist)
    creator = meta.get("Creator", beatmap.creator)
    beatmap_id = _int(meta.get("BeatmapID"))
    set_id = _int(meta.get("BeatmapSetID"))
    if beatmap_id <= 0:
        beatmap_id = local_id(beatmap.md5)
    if set_id <= 0:
        set_id = local_id(f"{artist}\0{title}\0{creator}")

    heads = [h.time for h in beatmap.hit_objects]
    ends = [max(h.time, h.end_time or 0.0) for h in beatmap.hit_objects]
    first, last = min(heads), max(ends)
    span = max(last - first, 1.0)
    density = [0] * DENSITY_BINS
    for t in heads:
        density[min(DENSITY_BINS - 1, int((t - first) / span * DENSITY_BINS))] += 1

    return ChartRow(
        beatmap_id=beatmap_id,
        beatmapset_id=set_id,
        checksum=beatmap.md5,
        version=meta.get("Version", beatmap.version),
        official_sr=official_sr if official_sr is not None and official_sr >= 0 else None,
        official_sr_source=official_sr_source if official_sr is not None and official_sr >= 0 else None,
        engine_sr=profile.total_stars,
        engine_version=engine_version(),
        dan=estimate_canonical_dan(profile.total_stars),
        dominant_skill=profile.dominant_skill,
        dominance_margin=profile.dominance_margin,
        skills={
            k: {
                "stars": profile.skills[k].stars,
                "dominance": profile.skills[k].dominance,
                "coverage": profile.skills[k].coverage,
            }
            for k in SKILLS
        },
        bpm=dominant_bpm(beatmap),
        length_s=last / 1000.0,
        note_count=len(beatmap.hit_objects),
        ln_count=sum(1 for h in beatmap.hit_objects if h.note_type == NoteType.LN),
        od=_float(diff.get("OverallDifficulty"), beatmap.overall_difficulty),
        hp=_float(diff.get("HPDrainRate"), 0.0),
        density=density,
        title=title,
        title_unicode=meta.get("TitleUnicode", "") or title,
        artist=artist,
        artist_unicode=meta.get("ArtistUnicode", "") or artist,
        creator=creator,
        source=meta.get("Source", ""),
        tags=meta.get("Tags", beatmap.tags),
        status=status,
        ranked_date=ranked_date,
        submitted_date=submitted_date,
        creator_id=creator_id,
        mapper_id=mapper_id,
        mapper_name=mapper_name or creator,
        patterns=pattern_features(beatmap),
        osu_content=content,
    )
