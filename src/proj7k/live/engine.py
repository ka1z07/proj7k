"""
LiveEngine: Analysis orchestrator and caching seam for real-time live radar.

SPEC-P2.4-01 / ADR-0010. Stars, dan tier and the radar come from the spec v0.2 difficulty engine
(ADR-0017/0018); the dual-hand strain canvas, the 4D tech breakdown and the legacy synthesis are
the driver/strain engine's and ride in the frame under `legacy`, labelled, until they are redesigned.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Union

from proj7k.cache import TwoLayerCache
from proj7k.engine import evaluate_osu
from proj7k.engine.skills import SKILL_TECH_KEY
from proj7k.features import extract_beatmap_features
from proj7k.parser import parse_osu_7k
from proj7k.radar import compute_tech_4d_components, compute_technique_radar
from proj7k.rating import synthesize_star_rating
from proj7k.strain import compute_dual_hand_strain


from proj7k.dan import estimate_canonical_dan


def estimate_dan_tier(star_rating: float) -> str:
    """
    Estimates canonical Jinjin 7K Dan benchmark tier from the difficulty engine's star rating.
    Delegates directly to canonical proj7k.dan.estimate_canonical_dan.
    """
    return estimate_canonical_dan(star_rating)


class LiveEngine:
    """
    Evaluates 7K charts for real-time visualization with TwoLayerCache integration.
    Produces canonical JSON state frames: the engine's profile, the radar the canvas draws, and the
    labelled `legacy` readings.
    """

    def __init__(
        self,
        cache: Optional[TwoLayerCache] = None,
        max_mem_entries: int = 500,
    ):
        self.cache = cache if cache is not None else TwoLayerCache()
        self.max_mem_entries = max_mem_entries
        self._result_cache: Dict[str, Dict[str, Any]] = {}

    def analyze_content(self, content: Union[str, bytes]) -> Dict[str, Any]:
        """
        Analyzes raw .osu content string or bytes.
        Returns a canonical 'beatmap_update' frame.
        """
        content_str = content.decode("utf-8") if isinstance(content, bytes) else content
        content_hash = TwoLayerCache.compute_content_hash(content_str)

        if content_hash in self._result_cache:
            cached_frame = dict(self._result_cache[content_hash])
            cached_frame["cached"] = True
            return cached_frame

        # Layer 1: AST cache
        beatmap = self.cache.get_ast(content_hash)
        if beatmap is None:
            beatmap = parse_osu_7k(content_str)
            self.cache.put_ast(content_hash, beatmap)

        # Layer 2: Features cache
        features = self.cache.get_features(content_hash)
        if features is None:
            features = extract_beatmap_features(beatmap)
            self.cache.put_features(content_hash, None, features)

        profile = evaluate_osu(content_str)
        dan_tier = estimate_dan_tier(profile.total_stars)
        dominant_key = SKILL_TECH_KEY[profile.dominant_skill]
        radar: Dict[str, Any] = {SKILL_TECH_KEY[name]: reading.stars for name, reading in profile.skills.items()}
        radar["dominant_technique"] = dominant_key
        radar["dominant_score"] = radar[dominant_key]

        strain_profile = compute_dual_hand_strain(beatmap)
        legacy_radar = compute_technique_radar(beatmap, features=features)
        synthesis = synthesize_star_rating(legacy_radar, p90_strain=strain_profile.p90_strain)
        tech_breakdown = legacy_radar.tech_4d.to_dict() if legacy_radar.tech_4d else {}
        tech_breakdown["speed_burst"] = round(legacy_radar.speed, 4)

        metadata: Dict[str, Any] = {
            "title": beatmap.title,
            "artist": beatmap.artist,
            "creator": beatmap.creator,
            "version": beatmap.version,
            "total_notes": features.total_notes,
            "hold_pct": features.hold_pct,
            "duration_seconds": features.duration_seconds,
            "avg_nps": features.avg_nps,
            "dominant_technique": dominant_key,
            "dominant_score": radar["dominant_score"],
            "dan_tier": dan_tier,
        }

        frame = {
            "type": "beatmap_update",
            "cached": False,
            "star_rating": profile.total_stars,
            "dan_tier": dan_tier,
            "radar": radar,
            "profile": profile.to_dict(),
            "legacy": {
                "engine": "legacy driver/strain engine (to be redesigned, ADR-0018)",
                "star_rating": synthesis.star_rating,
                "synthesis": synthesis.to_dict(),
                "strain_profile": strain_profile.to_dict(),
                "tech_breakdown": tech_breakdown,
            },
            "metadata": metadata,
        }

        # Enforce bounded in-memory LRU eviction
        if len(self._result_cache) >= self.max_mem_entries:
            self._result_cache.pop(next(iter(self._result_cache)), None)

        self._result_cache[content_hash] = frame
        return dict(frame)

    def analyze_file(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        """
        Reads and analyzes a local .osu chart file.
        Returns a canonical 'beatmap_update' frame.
        """
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        return self.analyze_content(content)
