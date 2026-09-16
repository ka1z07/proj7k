"""
7K Beatmap Metadata Annotator and Biaxial Collections Orchestrator.

Implements SPEC-P2.3-02 / ADR-0009 for osu!lazer database ingestion.
"""

from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Sequence, Tuple, Union

from proj7k.lazer.bridge import (
    BeatmapMutationPayload,
    BeatmapRevertPayload,
    BeatmapUpdatePayload,
    LazerBeatmapRecord,
)

# Regular expression matching injected difficulty name suffix
# Format: " ({star_rating:.2f}★ {dominant_title})"
INJECTED_SUFFIX_PATTERN = re.compile(r"\s*\(\d+\.\d+★\s+[A-Za-z_]+\)$")

# Regular expression matching injected binned skill tags
# e.g., "dominant_jack", "jack_6★", "dominant_ln_general", "ln_general_7★"
BINNED_TAG_PATTERN = re.compile(r"^(?:dominant_[a-z0-9_]+|[a-z0-9_]+_\d+★)$")

# Canonical technique title mapping for the 8 technique radar dimensions
TECHNIQUE_TITLE_MAP: Dict[str, str] = {
    "jack": "Jack",
    "tech": "Tech",
    "speed": "Speed",
    "stream": "Stream",
    "ln_general": "LN_General",
    "ln_tech": "LN_Tech",
    "ln_inverse": "LN_Inverse",
    "ln_release": "LN_Release",
}

TECHNIQUE_COLLECTION_MAP: Dict[str, str] = {
    "jack": "7K Jack",
    "tech": "7K Tech",
    "speed": "7K Speed",
    "stream": "7K Stream",
    "ln_general": "7K LN General",
    "ln_tech": "7K LN Tech",
    "ln_inverse": "7K LN Inverse",
    "ln_release": "7K LN Release",
}

# 8 Technique specialization collections derived from canonical map
TECHNIQUE_COLLECTIONS: Tuple[str, ...] = tuple(TECHNIQUE_COLLECTION_MAP.values())

# 4 Progression tier collections
TIER_COLLECTIONS: Tuple[str, ...] = (
    "7K Tier: 00th-03rd (1★-4★)",
    "7K Tier: 04th-06th (4★-6★)",
    "7K Tier: 07th-09th (6★-8★)",
    "7K Tier: 10th+ (8★+)",
)

# 12 All biaxial collections
ALL_BIAXIAL_COLLECTIONS: Tuple[str, ...] = TECHNIQUE_COLLECTIONS + TIER_COLLECTIONS


@dataclass(frozen=True)
class BeatmapCollectionEntry:
    md5_hash: str
    dominant_tech: str
    star_rating: float


def _normalize_tech_key(name: str) -> str:
    """Normalize any technique name string to lowercase underscore format."""
    clean = name.strip().replace(" ", "_").lower()
    if clean in ("", "none"):
        return "tech"
    return clean


def format_dominant_title(dominant: str) -> str:
    """
    Format a technique dimension name into a valid injected suffix title.
    Maps known technique identifiers to capitalized/underscore format (e.g. ln_general -> LN_General).
    """
    clean = dominant.strip()
    key = _normalize_tech_key(clean)
    if key in TECHNIQUE_TITLE_MAP:
        return TECHNIQUE_TITLE_MAP[key]
    return clean.replace(" ", "_")


def strip_injected_suffix(difficulty_name: str) -> str:
    """
    Extract the base difficulty name by stripping any previously injected proj7k suffix.
    Idempotent: repeatedly stripping yields the identical base name.
    Handles potential trailing whitespace gracefully.
    """
    if not difficulty_name:
        return ""
    clean_name = difficulty_name.rstrip()
    return INJECTED_SUFFIX_PATTERN.sub("", clean_name).rstrip()


def format_injected_difficulty_name(
    difficulty_name: str,
    star_rating: float,
    dominant_title: str,
) -> str:
    """
    Format a difficulty name with injected star rating and dominant technique suffix.
    First strips any existing injected suffix to guarantee idempotence.
    """
    base = strip_injected_suffix(difficulty_name)
    title = format_dominant_title(dominant_title)
    suffix = f"({star_rating:.2f}★ {title})"
    if base:
        return f"{base} {suffix}"
    return suffix


def generate_binned_skill_tags(dominant_tech: str, star_rating: float) -> List[str]:
    """
    Generate discrete skill bucket tags for searchability in osu!lazer.
    Returns [f"dominant_{tech_key}", f"{tech_key}_{floor_star}★"].
    Properly sanitizes spaces to underscores to maintain valid space-delimited tags.
    """
    tech_key = _normalize_tech_key(dominant_tech)
    floor_star = max(0, int(star_rating))
    return [f"dominant_{tech_key}", f"{tech_key}_{floor_star}★"]


def strip_binned_skill_tags(tags: str) -> str:
    """
    Remove any previously injected proj7k binned skill tags from a space-delimited tags string.
    """
    if not tags:
        return ""
    tokens = tags.strip().split()
    retained = [t for t in tokens if not BINNED_TAG_PATTERN.match(t)]
    return " ".join(retained)


def inject_binned_skill_tags(
    existing_tags: str,
    dominant_tech: str,
    star_rating: float,
) -> str:
    """
    Inject discrete skill bucket tags into existing tags string without duplicating.
    Replaces any old proj7k bucket tags with the new ones.
    """
    base_tags = strip_binned_skill_tags(existing_tags)
    new_tags = generate_binned_skill_tags(dominant_tech, star_rating)
    tokens = base_tags.split() if base_tags else []
    for tag in new_tags:
        if tag not in tokens:
            tokens.append(tag)
    return " ".join(tokens)


def get_technique_collection_name(dominant_tech: str) -> str:
    """
    Resolve canonical technique collection name (1 of 8) for a dominant technique.
    Falls back gracefully to 7K Tech for empty or 'none' techniques.
    """
    norm = _normalize_tech_key(dominant_tech)
    if norm in TECHNIQUE_COLLECTION_MAP:
        return TECHNIQUE_COLLECTION_MAP[norm]
    return f"7K {dominant_tech.strip()}"



def get_tier_collection_name(star_rating: float) -> str:
    """
    Resolve canonical progression tier collection name (1 of 4) based on star rating.
    - SR < 4.0: 7K Tier: 00th-03rd (1★-4★)
    - 4.0 <= SR < 6.0: 7K Tier: 04th-06th (4★-6★)
    - 6.0 <= SR < 8.0: 7K Tier: 07th-09th (6★-8★)
    - SR >= 8.0: 7K Tier: 10th+ (8★+)
    """
    if star_rating < 4.0:
        return "7K Tier: 00th-03rd (1★-4★)"
    elif star_rating < 6.0:
        return "7K Tier: 04th-06th (4★-6★)"
    elif star_rating < 8.0:
        return "7K Tier: 07th-09th (6★-8★)"
    else:
        return "7K Tier: 10th+ (8★+)"


def get_beatmap_biaxial_collections(
    dominant_tech: str,
    star_rating: float,
) -> Tuple[str, str]:
    """
    Resolve the exactly 2 biaxial collections for any 7K beatmap:
    (1 technique collection, 1 progression tier collection).
    """
    return (
        get_technique_collection_name(dominant_tech),
        get_tier_collection_name(star_rating),
    )


def build_biaxial_collection_map(
    entries: Sequence[Union[BeatmapCollectionEntry, Tuple[str, str, float]]],
    include_empty: bool = True,
) -> Dict[str, List[str]]:
    """
    Compile a dictionary mapping collection names to lists of unique beatmap MD5 hashes.
    Ensures all 12 canonical collections are orchestrated and deduplicated.
    """
    col_map: Dict[str, List[str]] = {}
    if include_empty:
        for col_name in ALL_BIAXIAL_COLLECTIONS:
            col_map[col_name] = []

    for item in entries:
        if isinstance(item, BeatmapCollectionEntry):
            md5_hash = item.md5_hash
            dom_tech = item.dominant_tech
            sr = item.star_rating
        else:
            md5_hash, dom_tech, sr = item

        tech_col, tier_col = get_beatmap_biaxial_collections(dom_tech, sr)
        for col in (tech_col, tier_col):
            if col not in col_map:
                col_map[col] = []
            if md5_hash and md5_hash not in col_map[col]:
                col_map[col].append(md5_hash)

    return col_map


def annotate_lazer_beatmap(
    record: LazerBeatmapRecord,
    star_rating: float,
    dominant_tech: str,
) -> BeatmapUpdatePayload:
    """
    Produce an update payload for a single 7K osu!mania beatmap.
    Strictly verifies ruleset_id == 3 and circle_size == 7.0.
    """
    if not record.is_7k_mania:
        raise ValueError(
            f"Record {record.id} is not a 7K osu!mania beatmap "
            f"(ruleset={record.ruleset_id}, CS={record.circle_size})"
        )

    return BeatmapUpdatePayload(
        id=record.id,
        star_rating=star_rating,
        difficulty_name=format_injected_difficulty_name(
            record.difficulty_name, star_rating, dominant_tech
        ),
        tags=inject_binned_skill_tags(record.tags, dominant_tech, star_rating),
    )


def create_lazer_revert_payload(
    record: LazerBeatmapRecord,
    original_star_rating: Optional[float] = None,
) -> BeatmapRevertPayload:
    """
    Produce a revert payload to restore pristine difficulty name and tags.
    """
    sr = original_star_rating if original_star_rating is not None else record.star_rating
    return BeatmapRevertPayload(
        id=record.id,
        star_rating=sr,
        difficulty_name=strip_injected_suffix(record.difficulty_name),
        tags=strip_binned_skill_tags(record.tags),
    )


def annotate_batch_and_build_collections(
    items: Sequence[Tuple[LazerBeatmapRecord, float, str]],
) -> Tuple[List[BeatmapUpdatePayload], Dict[str, List[str]]]:
    """
    Batch annotate beatmap records and orchestrate 12 biaxial collections.
    Returns (updates, collections_map).
    """
    updates: List[BeatmapUpdatePayload] = []
    col_entries: List[BeatmapCollectionEntry] = []

    for record, star_rating, dom_tech in items:
        if not record.is_7k_mania:
            continue
        update = annotate_lazer_beatmap(record, star_rating, dom_tech)
        updates.append(update)
        hash_for_col = record.md5_hash or record.file_hash or record.hash
        col_entries.append(
            BeatmapCollectionEntry(
                md5_hash=hash_for_col,
                dominant_tech=dom_tech,
                star_rating=star_rating,
            )
        )

    collections = build_biaxial_collection_map(col_entries, include_empty=True)
    return updates, collections
