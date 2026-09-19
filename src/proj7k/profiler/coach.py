"""
proj7k.profiler.coach - Dual Coaching Strategy Recommendations and Downscaler Practice Bundle Pipeline.

Implements ADR-0012 & SPEC-P4.1-05 (ka1z07/proj7k#37):
1. Dual Coaching Strategies:
   - Bottleneck Breaker (targeting lowest limiting dimension)
   - Specialty Push (advancing highest developed dimension)
2. Local Realm Candidate Beatmap Recall:
   - Scans installed library for matching skill tags and tiers
   - Strictly excludes Jinjin Dan and other Dan test courses
3. High-Strain Section Slice Extraction:
   - Extracts [t_fatal - 10s, t_fatal + 5s] window with rhythmic/audio buffers
4. Three-Tier Targeted Practice Bundle:
   - Derives Recovery (current capacity), Bridge (intermediate), and Push (original) tiers
   - Packages standalone .osz files and combined progression bundles
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from proj7k.dan import estimate_canonical_dan, parse_dan_tier
from proj7k.downscaler.locator import package_into_osz
from proj7k.downscaler.mapper import CANONICAL_TO_TECH_KEY, TECH_ALIAS_MAP
from proj7k.downscaler.mutation import update_practice_metadata
from proj7k.downscaler.pipeline import DownscaleOptions, downscale_beatmap
from proj7k.features import extract_beatmap_features
from proj7k.lazer.annotator import _normalize_tech_key, get_technique_collection_name
from proj7k.lazer.bridge import DEFAULT_REALM_PATH, LazerBeatmapRecord, RealmBridgeClient
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, dump_osu_7k, parse_osu_7k
from proj7k.radar import (
    RadarOptions,
    TechniqueRadar,
    compute_technique_radar,
)
from proj7k.rating import RatingOptions, synthesize_star_rating
from proj7k.strain import StrainOptions, compute_dual_hand_strain


class CoachingStrategy(str, Enum):
    BOTTLENECK_BREAKER = "bottleneck_breaker"
    SPECIALTY_PUSH = "specialty_push"


DAN_TEST_PATTERNS: List[re.Pattern] = [
    # Explicit Jinjin course indicators
    re.compile(r"\[jinjin(?:'s)?\b", re.IGNORECASE),
    re.compile(r"\bjinjin\b", re.IGNORECASE),
    # Dan courses and exams
    re.compile(r"\bdan\s+course\b", re.IGNORECASE),
    re.compile(r"\bdan\s+test\b", re.IGNORECASE),
    re.compile(r"\bdan\s+exam\b", re.IGNORECASE),
    re.compile(r"\bdan\s+qualification\b", re.IGNORECASE),
    re.compile(r"\bdan\s+phase\b", re.IGNORECASE),
    re.compile(r"\bregular\s+dan\b", re.IGNORECASE),
    re.compile(r"\bln\s+dan\b", re.IGNORECASE),
    re.compile(r"\binsane\s+dan\b", re.IGNORECASE),
    re.compile(r"\breform\s+dan\b", re.IGNORECASE),
    re.compile(r"\bextra\s+dan\b", re.IGNORECASE),
    # Numbered Dan courses (e.g. "4th Dan", "10th Dan", "Gamma Dan", "Stellium Dan")
    re.compile(r"\b(?:\d{1,2}(?:st|nd|rd|th)|gamma|azimuth|zenith|stellium)\s+dan\b", re.IGNORECASE),
    # Jinjin course brackets format (e.g. "[7K] ~ 5th ~" or "~ 7th ~")
    re.compile(r"~\s*(?:\d{1,2}(?:st|nd|rd|th)|gamma|azimuth|zenith|stellium)\s*~", re.IGNORECASE),
]


def is_dan_beatmap(beatmap_or_record: Union[LazerBeatmapRecord, Dict[str, Any], Beatmap7K]) -> bool:
    """
    Strictly identifies whether a beatmap belongs to Jinjin 7K Dan or other Dan test courses.
    ADR-0012: Dan maps are authoritative benchmarks and strictly prohibited from recommendations.
    """
    creator = ""
    title = ""
    diff_name = ""
    tags = ""

    if isinstance(beatmap_or_record, LazerBeatmapRecord):
        creator = getattr(beatmap_or_record, "creator", "") or ""
        title = beatmap_or_record.title or ""
        diff_name = beatmap_or_record.difficulty_name or ""
        tags = beatmap_or_record.tags or ""
    elif isinstance(beatmap_or_record, dict):
        creator = str(beatmap_or_record.get("creator", ""))
        title = str(beatmap_or_record.get("title", ""))
        diff_name = str(beatmap_or_record.get("difficulty_name", ""))
        tags = str(beatmap_or_record.get("tags", ""))
    elif isinstance(beatmap_or_record, Beatmap7K):
        creator = beatmap_or_record.creator or ""
        title = beatmap_or_record.title or ""
        diff_name = beatmap_or_record.version or ""
        tags = beatmap_or_record.tags or ""

    # Check 1: Author is Jinjin
    if "jinjin" in creator.strip().lower():
        return True

    # Check 2: Pattern matches in Title or Difficulty Name
    for pat in DAN_TEST_PATTERNS:
        if pat.search(title) or pat.search(diff_name):
            return True

    # Check 3: Raw tags inspection (excluding proj7k injected tags like dan_7th)
    tokens = tags.strip().lower().split()
    for tok in tokens:
        if tok in ["jinjin", "dan", "dan_course", "dan_test", "regular_dan", "ln_dan"]:
            return True
        if tok.startswith("dan_") and len(tok) > 4:
            # Check if this tag is like 'dan_course' or 'dan_test'
            if tok in ["dan_course", "dan_test", "dan_phase", "dan_exam"]:
                return True

    return False


@dataclass
class CandidateBeatmap:
    """A candidate practice beatmap recalled from player's local library."""
    id: str
    title: str
    artist: str
    difficulty_name: str
    star_rating: float
    dan_tier: str
    matched_skill: str
    tags: str
    file_hash: str = ""
    md5_hash: str = ""
    match_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "difficulty_name": self.difficulty_name,
            "star_rating": round(self.star_rating, 2),
            "dan_tier": self.dan_tier,
            "matched_skill": self.matched_skill,
            "tags": self.tags,
            "match_score": round(self.match_score, 2),
        }


def recall_candidate_beatmaps(
    records: Optional[Sequence[LazerBeatmapRecord]] = None,
    target_technique: str = "jack",
    target_sr: float = 5.0,
    target_dan: Optional[str] = None,
    max_candidates: int = 5,
    exclude_dan: bool = True,
    realm_path: Optional[Path] = None,
) -> List[CandidateBeatmap]:
    """
    Scans local Realm records to recall candidate practice beatmaps matching target skill & tier.
    Enforces strict exclusion of Dan test maps and downscaled practice derivatives.
    """
    target_tech_key = _normalize_tech_key(target_technique)

    beatmap_records: Sequence[LazerBeatmapRecord] = records or []
    if not beatmap_records:
        r_path = realm_path or DEFAULT_REALM_PATH
        if r_path.exists():
            try:
                client = RealmBridgeClient(default_realm_path=r_path)
                beatmap_records = client.dump_7k_beatmaps(auto_setup=False)
            except Exception:
                beatmap_records = []

    candidates: List[Tuple[float, CandidateBeatmap]] = []

    for rec in beatmap_records:
        # Must be 7K mania
        if not rec.is_7k_mania:
            continue

        # Strict Dan map exclusion (ADR-0012)
        if exclude_dan and is_dan_beatmap(rec):
            continue

        # Exclude existing practice derivatives
        if "[p-" in rec.difficulty_name.lower():
            continue

        tags_lower = rec.tags.lower()
        title_lower = rec.title.lower()
        diff_lower = rec.difficulty_name.lower()

        # Technique relevance scoring
        score = 0.0
        if f"dominant_{target_tech_key}" in tags_lower:
            score += 50.0
        elif f"{target_tech_key}_" in tags_lower:
            score += 30.0
        elif target_tech_key in tags_lower:
            score += 20.0
        elif target_tech_key in diff_lower or target_tech_key in title_lower:
            score += 15.0

        # Star rating proximity scoring
        sr_diff = abs(rec.star_rating - target_sr)
        # If SR difference is beyond 1.5 stars, penalize heavily
        if sr_diff > 1.5:
            score -= (sr_diff - 1.5) * 20.0

        if target_dan and f"dan_{target_dan.lower()}" in tags_lower:
            score += 25.0

        tier = estimate_canonical_dan(rec.star_rating)
        total_score = score - (sr_diff * 10.0)

        if total_score > 0.0:
            cb = CandidateBeatmap(
                id=rec.id,
                title=rec.title,
                artist=rec.artist,
                difficulty_name=rec.difficulty_name,
                star_rating=rec.star_rating,
                dan_tier=tier,
                matched_skill=target_tech_key,
                tags=rec.tags,
                file_hash=rec.file_hash,
                md5_hash=rec.md5_hash,
                match_score=total_score,
            )
            candidates.append((total_score, cb))

    # Sort descending by match score
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [c[1] for c in candidates[:max_candidates]]


@dataclass
class CoachingRecommendation:
    """Prescriptive coaching recommendation with recalled candidate practice maps."""
    strategy: str
    target_technique: str
    current_capacity: float
    current_star_rating: float
    current_dan_tier: str
    target_star_rating: float
    target_dan_tier: str
    rationale: str
    candidates: List[CandidateBeatmap] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "target_technique": self.target_technique,
            "current_capacity": round(self.current_capacity, 2),
            "current_star_rating": round(self.current_star_rating, 2),
            "current_dan_tier": self.current_dan_tier,
            "target_star_rating": round(self.target_star_rating, 2),
            "target_dan_tier": self.target_dan_tier,
            "rationale": self.rationale,
            "candidates": [c.to_dict() for c in self.candidates],
        }


def generate_coaching_recommendations(
    report_or_profile: Any,
    strategy: str = "both",
    realm_path: Optional[Path] = None,
    records: Optional[List[LazerBeatmapRecord]] = None,
    max_candidates: int = 5,
) -> List[CoachingRecommendation]:
    """
    Generates prescriptive coaching recommendations based on player's diagnosis or macro profile.
    Supports Bottleneck Breaker and Specialty Push strategies.
    """
    # Extract radar breakdown
    dominant_tech = "jack"
    bottleneck_tech = "jack"
    dim_dict: Dict[str, Any] = {}

    if hasattr(report_or_profile, "dominant_technique") and hasattr(report_or_profile, "bottleneck_technique"):
        dominant_tech = report_or_profile.dominant_technique
        bottleneck_tech = report_or_profile.bottleneck_technique
        dim_dict = report_or_profile.dimensions
    elif hasattr(report_or_profile, "skill_radar") and report_or_profile.skill_radar:
        radar = report_or_profile.skill_radar
        dominant_tech = radar.dominant_technique
        bottleneck_tech = radar.bottleneck_technique
        dim_dict = radar.dimensions
    else:
        dominant_tech = "jack"
        bottleneck_tech = "jack"

    recommendations: List[CoachingRecommendation] = []
    strat_lower = strategy.lower()

    # 1. Bottleneck Breaker (targeting lowest limiting dimension)
    if strat_lower in ["both", "all", "bottleneck", "bottleneck_breaker", "bottleneck-breaker"]:
        b_info = dim_dict.get(bottleneck_tech)
        b_cap = getattr(b_info, "effective_capacity", getattr(b_info, "peak_capacity", 15.0)) if b_info else 15.0
        b_sr = getattr(b_info, "star_rating", 4.5) if b_info else 4.5
        b_dan = getattr(b_info, "dan_tier", estimate_canonical_dan(b_sr)) if b_info else estimate_canonical_dan(b_sr)

        target_sr = round(b_sr + 0.25, 2)
        target_dan = estimate_canonical_dan(target_sr)
        tech_title = bottleneck_tech.replace("_", " ").title()

        candidates = recall_candidate_beatmaps(
            records=records,
            target_technique=bottleneck_tech,
            target_sr=target_sr,
            target_dan=target_dan,
            max_candidates=max_candidates,
            exclude_dan=True,
            realm_path=realm_path,
        )

        recommendations.append(
            CoachingRecommendation(
                strategy=CoachingStrategy.BOTTLENECK_BREAKER.value,
                target_technique=bottleneck_tech,
                current_capacity=b_cap,
                current_star_rating=b_sr,
                current_dan_tier=b_dan,
                target_star_rating=target_sr,
                target_dan_tier=target_dan,
                rationale=(
                    f"Targeting your lowest limiting dimension ({tech_title}) to eliminate performance "
                    "drop-offs and unlock progression to the next canonical Dan tier."
                ),
                candidates=candidates,
            )
        )

    # 2. Specialty Push (advancing highest developed dimension)
    if strat_lower in ["both", "all", "specialty", "specialty_push", "specialty-push"]:
        d_info = dim_dict.get(dominant_tech)
        d_cap = getattr(d_info, "effective_capacity", getattr(d_info, "peak_capacity", 25.0)) if d_info else 25.0
        d_sr = getattr(d_info, "star_rating", 6.5) if d_info else 6.5
        d_dan = getattr(d_info, "dan_tier", estimate_canonical_dan(d_sr)) if d_info else estimate_canonical_dan(d_sr)

        target_sr = round(d_sr + 0.40, 2)
        target_dan = estimate_canonical_dan(target_sr)
        tech_title = dominant_tech.replace("_", " ").title()

        candidates = recall_candidate_beatmaps(
            records=records,
            target_technique=dominant_tech,
            target_sr=target_sr,
            target_dan=target_dan,
            max_candidates=max_candidates,
            exclude_dan=True,
            realm_path=realm_path,
        )

        recommendations.append(
            CoachingRecommendation(
                strategy=CoachingStrategy.SPECIALTY_PUSH.value,
                target_technique=dominant_tech,
                current_capacity=d_cap,
                current_star_rating=d_sr,
                current_dan_tier=d_dan,
                target_star_rating=target_sr,
                target_dan_tier=target_dan,
                rationale=(
                    f"Advancing your strongest skill dimension ({tech_title}) towards elite peak mastery "
                    "and upper Dan tier breakthroughs."
                ),
                candidates=candidates,
            )
        )

    return recommendations


def extract_high_strain_slice(
    beatmap: Beatmap7K,
    fatal_time_ms: float,
    buffer_before_ms: float = 10000.0,
    buffer_after_ms: float = 5000.0,
) -> Beatmap7K:
    """
    Extracts High-Strain Section Slice [t_fatal - 10s, t_fatal + 5s] with proper rhythmic buffers.
    Preserves timing points and audio references for independent gameplay.
    """
    slice_start = max(0.0, fatal_time_ms - buffer_before_ms)
    slice_end = fatal_time_ms + buffer_after_ms

    # 1. Filter hit objects within slice window
    sliced_notes: List[HitObject] = []
    for ho in beatmap.hit_objects:
        if slice_start <= ho.time <= slice_end:
            note_type = ho.note_type
            end_t = ho.end_time
            if note_type == NoteType.LN:
                if end_t is not None and end_t > slice_end:
                    end_t = slice_end
                if end_t is None or end_t <= ho.time:
                    # Degenerate zero-duration LN converted to rice note
                    note_type = NoteType.RICE
                    end_t = None

            sliced_notes.append(
                HitObject(
                    column=ho.column,
                    time=ho.time,
                    note_type=note_type,
                    end_time=end_t,
                    hit_sound=ho.hit_sound,
                    addition=ho.addition,
                )
            )

    # Fallback if window is completely empty: take surrounding notes
    if not sliced_notes and beatmap.hit_objects:
        sorted_notes = sorted(beatmap.hit_objects, key=lambda h: abs(h.time - fatal_time_ms))
        sliced_notes = sorted(sorted_notes[:20], key=lambda h: h.time)

    # 2. Filter timing points
    # Keep the last active uninherited timing point (BPM) and inherited timing point (SV) prior to slice_start
    prior_uninherited: Optional[TimingPoint] = None
    prior_inherited: Optional[TimingPoint] = None
    for tp in sorted(beatmap.timing_points, key=lambda t: t.time):
        if tp.time <= slice_start:
            if tp.uninherited:
                prior_uninherited = tp
            else:
                prior_inherited = tp

    sliced_tp: List[TimingPoint] = []
    if prior_uninherited:
        sliced_tp.append(prior_uninherited)
    elif beatmap.timing_points:
        sliced_tp.append(beatmap.timing_points[0])

    if prior_inherited and (prior_uninherited is None or prior_inherited.time >= prior_uninherited.time):
        sliced_tp.append(prior_inherited)

    for tp in sorted(beatmap.timing_points, key=lambda t: t.time):
        if slice_start < tp.time <= slice_end:
            sliced_tp.append(tp)

    # Enforce Independent Local Beatmap metadata identity (ADR-0011, CONTEXT.md)
    extra_sections = {k: dict(v) for k, v in beatmap.extra_sections.items()}
    if "Metadata" not in extra_sections:
        extra_sections["Metadata"] = {}
    extra_sections["Metadata"]["BeatmapID"] = "0"
    extra_sections["Metadata"]["BeatmapSetID"] = "-1"
    for online_key in ["BeatmapOnlineID", "BeatmapSetOnlineID"]:
        extra_sections["Metadata"].pop(online_key, None)

    return Beatmap7K(
        title=beatmap.title,
        artist=beatmap.artist,
        creator=beatmap.creator,
        version=f"{beatmap.version} [p-Slice]",
        mode=beatmap.mode,
        circle_size=beatmap.circle_size,
        overall_difficulty=beatmap.overall_difficulty,
        timing_points=sliced_tp,
        hit_objects=sliced_notes,
        audio_filename=beatmap.audio_filename,
        tags=beatmap.tags,
        extra_sections=extra_sections,
        raw_events=beatmap.raw_events.copy(),
    )


@dataclass
class ProgressionTierResult:
    """Result for an individual tier in the practice bundle."""
    tier_name: str
    target_strain: float
    star_rating: float
    dan_tier: str
    beatmap: Beatmap7K
    osu_path: Optional[Path] = None
    osz_path: Optional[Path] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier_name": self.tier_name,
            "target_strain": round(self.target_strain, 2),
            "star_rating": round(self.star_rating, 2),
            "dan_tier": self.dan_tier,
            "total_notes": len(self.beatmap.hit_objects),
            "osu_path": str(self.osu_path) if self.osu_path else None,
            "osz_path": str(self.osz_path) if self.osz_path else None,
        }


@dataclass
class PracticeBundleResult:
    """Complete Three-Tier Targeted Practice Bundle report."""
    fatal_time_ms: float
    slice_start_ms: float
    slice_end_ms: float
    dominant_technique: str
    original_strain: float
    tiers: Dict[str, ProgressionTierResult]
    combined_osz_path: Optional[Path] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fatal_time_ms": round(self.fatal_time_ms, 1),
            "slice_start_ms": round(self.slice_start_ms, 1),
            "slice_end_ms": round(self.slice_end_ms, 1),
            "dominant_technique": self.dominant_technique,
            "original_strain": round(self.original_strain, 2),
            "tiers": {k: v.to_dict() for k, v in self.tiers.items()},
            "combined_osz_path": str(self.combined_osz_path) if self.combined_osz_path else None,
        }


def generate_targeted_practice_bundle(
    beatmap: Beatmap7K,
    fatal_time_ms: float,
    player_capacity: Optional[float] = None,
    dominant_technique: Optional[str] = None,
    output_dir: Optional[Path] = None,
    audio_path: Optional[Path] = None,
    bg_path: Optional[Path] = None,
    buffer_before_ms: float = 10000.0,
    buffer_after_ms: float = 5000.0,
) -> PracticeBundleResult:
    """
    Extracts fatal section slice and generates Three-Tier Progression Bundle (ADR-0012):
    - Recovery: Downscaled to player's stable capacity (or 70% strain) for 98%+ accuracy rebuilding.
    - Bridge: Intermediate transition strain midpoint.
    - Push: Original full-strain section slice for targeted breakthrough.
    Packages standalone .osz files and combined progression bundle.
    """
    slice_start = max(0.0, fatal_time_ms - buffer_before_ms)
    slice_end = fatal_time_ms + buffer_after_ms

    # 1. Extract slice
    sliced_bm = extract_high_strain_slice(
        beatmap=beatmap,
        fatal_time_ms=fatal_time_ms,
        buffer_before_ms=buffer_before_ms,
        buffer_after_ms=buffer_after_ms,
    )

    # 2. Analyze slice baseline characteristics
    slice_radar = compute_technique_radar(sliced_bm)
    dom_tech = dominant_technique or slice_radar.dominant_technique
    slice_strain_prof = compute_dual_hand_strain(sliced_bm)
    push_strain = slice_strain_prof.p90_strain
    push_sr = synthesize_star_rating(slice_radar, p90_strain=push_strain).star_rating
    push_dan = estimate_canonical_dan(push_sr)

    # 3. Determine strain targets for Recovery and Bridge tiers
    if player_capacity is not None and player_capacity > 0:
        # Recovery targets player's current capacity, ensuring at least 20% reduction if near peak
        recovery_strain = min(player_capacity, push_strain * 0.80)
    else:
        recovery_strain = push_strain * 0.70

    recovery_strain = max(1.0, recovery_strain)
    bridge_strain = (recovery_strain + push_strain) / 2.0

    # 4. Generate tiers via downscaler
    # Push Tier (Original slice)
    push_bm = update_practice_metadata(
        sliced_bm,
        target_dan=push_dan,
        dominant_skill=dom_tech,
    )
    push_bm.version = f"[p-{push_dan} {dom_tech} Push] {beatmap.version} (Slice)"
    if "Metadata" in push_bm.extra_sections:
        push_bm.extra_sections["Metadata"]["Version"] = push_bm.version

    # Bridge Tier
    bridge_res = downscale_beatmap(
        sliced_bm,
        DownscaleOptions(
            target_strain=bridge_strain,
            dominant_skill=dom_tech,
            prune_ratio=0.15,
            max_iterations=20,
        ),
    )
    bridge_bm = bridge_res.downscaled_beatmap
    bridge_sr = bridge_res.downscaled_rating.star_rating
    bridge_dan = estimate_canonical_dan(bridge_sr)
    bridge_bm.version = f"[p-{bridge_dan} {dom_tech} Bridge] {beatmap.version} (Slice)"
    if "Metadata" in bridge_bm.extra_sections:
        bridge_bm.extra_sections["Metadata"]["Version"] = bridge_bm.version

    # Recovery Tier
    rec_res = downscale_beatmap(
        sliced_bm,
        DownscaleOptions(
            target_strain=recovery_strain,
            dominant_skill=dom_tech,
            prune_ratio=0.25,
            max_iterations=25,
        ),
    )
    rec_bm = rec_res.downscaled_beatmap
    rec_sr = rec_res.downscaled_rating.star_rating
    rec_dan = estimate_canonical_dan(rec_sr)
    rec_bm.version = f"[p-{rec_dan} {dom_tech} Recovery] {beatmap.version} (Slice)"
    if "Metadata" in rec_bm.extra_sections:
        rec_bm.extra_sections["Metadata"]["Version"] = rec_bm.version

    # 5. File persistence and .osz packaging if output_dir provided
    tier_results: Dict[str, ProgressionTierResult] = {
        "recovery": ProgressionTierResult(
            tier_name="Recovery",
            target_strain=recovery_strain,
            star_rating=rec_sr,
            dan_tier=rec_dan,
            beatmap=rec_bm,
        ),
        "bridge": ProgressionTierResult(
            tier_name="Bridge",
            target_strain=bridge_strain,
            star_rating=bridge_sr,
            dan_tier=bridge_dan,
            beatmap=bridge_bm,
        ),
        "push": ProgressionTierResult(
            tier_name="Push",
            target_strain=push_strain,
            star_rating=push_sr,
            dan_tier=push_dan,
            beatmap=push_bm,
        ),
    }

    combined_osz_path: Optional[Path] = None

    if output_dir:
        out_p = Path(output_dir)
        out_p.mkdir(parents=True, exist_ok=True)

        clean_title = "".join(c for c in beatmap.title if c.isalnum() or c in (" ", "-", "_")).strip() or "Practice"

        # Write .osu files
        osu_paths: Dict[str, Path] = {}
        for tier_key, tier_res in tier_results.items():
            f_name = f"{clean_title} [{tier_res.beatmap.version}].osu"
            f_path = out_p / f_name
            f_path.write_text(dump_osu_7k(tier_res.beatmap), encoding="utf-8")
            tier_res.osu_path = f_path
            osu_paths[tier_key] = f_path

        # Package individual .osz files
        for tier_key, tier_res in tier_results.items():
            osz_target = out_p / f"{clean_title}_{tier_res.tier_name}.osz"
            package_into_osz(
                practice_osu_path=osu_paths[tier_key],
                output_osz_path=osz_target,
                audio_path=audio_path,
                bg_path=bg_path,
            )
            tier_res.osz_path = osz_target

        # Package combined bundle .osz containing all 3 tiers
        combined_osz_target = out_p / f"{clean_title}_Practice_Bundle.osz"
        extra = [
            (osu_paths["bridge"], osu_paths["bridge"].name),
            (osu_paths["recovery"], osu_paths["recovery"].name),
        ]
        package_into_osz(
            practice_osu_path=osu_paths["push"],
            output_osz_path=combined_osz_target,
            audio_path=audio_path,
            bg_path=bg_path,
            extra_files=extra,
        )
        combined_osz_path = combined_osz_target

    return PracticeBundleResult(
        fatal_time_ms=fatal_time_ms,
        slice_start_ms=slice_start,
        slice_end_ms=slice_end,
        dominant_technique=dom_tech,
        original_strain=push_strain,
        tiers=tier_results,
        combined_osz_path=combined_osz_path,
    )


def format_coaching_report(recommendations: List[CoachingRecommendation]) -> str:
    """Formats coaching recommendations into a clean terminal report."""
    lines = [
        "------------------------------------------------------------",
        "         proj7k Adaptive Coaching Recommendations           ",
        "------------------------------------------------------------",
    ]

    for rec in recommendations:
        strat_title = (
            "Bottleneck Breaker (Limiting Skill)"
            if rec.strategy == CoachingStrategy.BOTTLENECK_BREAKER.value
            else "Specialty Push (Peak Skill)"
        )
        lines.extend([
            f"Strategy:       {strat_title}",
            f"Target Skill:   {rec.target_technique.replace('_', ' ').title()}",
            f"Current Level:  {rec.current_star_rating:.2f}★ ({rec.current_dan_tier} Dan, Cap: {rec.current_capacity:.1f})",
            f"Target Level:   {rec.target_star_rating:.2f}★ ({rec.target_dan_tier} Dan)",
            f"Rationale:      {rec.rationale}",
            "Recalled Local Beatmaps (Dan Maps Excluded):",
        ])

        if rec.candidates:
            lines.append("  # | Title - Difficulty                      | Star Rating | Dan  | Tags")
            lines.append("  --+-----------------------------------------+-------------+------+-------------------------")
            for idx, c in enumerate(rec.candidates, start=1):
                name = f"{c.title} [{c.difficulty_name}]"
                if len(name) > 40:
                    name = name[:37] + "..."
                tag_sample = c.tags[:25] if c.tags else "-"
                lines.append(
                    f"  {idx:<2}| {name:<41} | {c.star_rating:>9.2f}★  | {c.dan_tier:<4} | {tag_sample}"
                )
        else:
            lines.append("  (No installed local beatmaps found matching criteria)")
        lines.append("------------------------------------------------------------")

    return "\n".join(lines)


def format_bundle_report(bundle: PracticeBundleResult) -> str:
    """Formats practice bundle results into a clean terminal report."""
    lines = [
        "------------------------------------------------------------",
        "     Targeted Practice Bundle (High-Strain Section Slice)   ",
        "------------------------------------------------------------",
        f"Fatal Failure Time: {bundle.fatal_time_ms / 1000.0:.2f}s",
        f"Slice Window:       {bundle.slice_start_ms / 1000.0:.2f}s -> {bundle.slice_end_ms / 1000.0:.2f}s (-10s, +5s buffer)",
        f"Dominant Technique: {bundle.dominant_technique.replace('_', ' ').title()}",
        f"Original Strain:    {bundle.original_strain:.2f}",
        "Three-Tier Progression Hierarchy:",
        "  Tier     | Strain | Star Rating | Dan  | Notes | Package (.osz)",
        "  ---------+--------+-------------+------+-------+------------------------------",
    ]

    for t_key in ["recovery", "bridge", "push"]:
        if t_key in bundle.tiers:
            t = bundle.tiers[t_key]
            pkg = t.osz_path.name if t.osz_path else "Generated"
            lines.append(
                f"  {t.tier_name:<8} | {t.target_strain:>6.2f} | {t.star_rating:>9.2f}★ | {t.dan_tier:<4} | {len(t.beatmap.hit_objects):>5} | {pkg}"
            )

    if bundle.combined_osz_path:
        lines.extend([
            "------------------------------------------------------------",
            f"Combined Bundle Archive: {bundle.combined_osz_path}",
        ])
    lines.append("------------------------------------------------------------")
    return "\n".join(lines)
