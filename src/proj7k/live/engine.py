"""
LiveEngine: Analysis orchestrator and caching seam for real-time live radar.

SPEC-P2.4-01 / ADR-0010.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Union

from proj7k.cache import TwoLayerCache
from proj7k.difficulty import DifficultyOptions, evaluate_intrinsic_difficulty
from proj7k.features import extract_beatmap_features
from proj7k.parser import parse_osu_7k
from proj7k.radar import compute_technique_radar
from proj7k.rating import synthesize_star_rating
from proj7k.strain import compute_dual_hand_strain


def estimate_dan_tier(star_rating: float) -> str:
    """
    Estimates canonical Jinjin 7K Dan benchmark tier from intrinsic star rating.
    Anchored to [0th Dan ~ 3.5★, 5th Dan ~ 5.5★, 10th Dan ~ 7.5★, Stellium >= 10.2★].
    """
    if star_rating < 3.5:
        return "0th Dan"
    elif star_rating < 3.9:
        return "1st Dan"
    elif star_rating < 4.3:
        return "2nd Dan"
    elif star_rating < 4.7:
        return "3rd Dan"
    elif star_rating < 5.1:
        return "4th Dan"
    elif star_rating < 5.5:
        return "5th Dan"
    elif star_rating < 5.9:
        return "6th Dan"
    elif star_rating < 6.3:
        return "7th Dan"
    elif star_rating < 6.7:
        return "8th Dan"
    elif star_rating < 7.1:
        return "9th Dan"
    elif star_rating < 7.6:
        return "10th Dan"
    elif star_rating < 8.3:
        return "Gamma"
    elif star_rating < 9.2:
        return "Azimuth"
    elif star_rating < 10.2:
        return "Zenith"
    else:
        return "Stellium"


class LiveEngine:
    """
    Evaluates 7K charts for real-time visualization with TwoLayerCache integration.
    Produces canonical JSON state frames conforming to TechniqueRadar and StarRatingSynthesis.
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

        strain_profile = compute_dual_hand_strain(beatmap)
        radar = compute_technique_radar(beatmap, features=features, strain_profile=strain_profile)
        synthesis = synthesize_star_rating(radar, p90_strain=strain_profile.p90_strain)
        dan_tier = estimate_dan_tier(synthesis.star_rating)

        metadata: Dict[str, Any] = {
            "title": beatmap.title,
            "artist": beatmap.artist,
            "creator": beatmap.creator,
            "version": beatmap.version,
            "total_notes": features.total_notes,
            "hold_pct": features.hold_pct,
            "duration_seconds": features.duration_seconds,
            "avg_nps": features.avg_nps,
            "dominant_technique": synthesis.dominant_technique,
            "dominant_score": synthesis.dominant_score,
            "synergy_bonus": synthesis.synergy_bonus,
            "dan_tier": dan_tier,
        }

        frame = {
            "type": "beatmap_update",
            "cached": False,
            "star_rating": synthesis.star_rating,
            "raw_star_rating": synthesis.uncompressed_rating,
            "dan_tier": dan_tier,
            "radar": radar.to_dict(),
            "synthesis": synthesis.to_dict(),
            "strain_profile": strain_profile.to_dict(),
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
