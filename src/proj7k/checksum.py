import hashlib
import json
from typing import List, Dict, Any
from proj7k.features import BeatmapFeatures

TIER_ORDER_MAP: Dict[str, int] = {
    "0th": 0,
    "1st": 1,
    "2nd": 2,
    "3rd": 3,
    "4th": 4,
    "5th": 5,
    "6th": 6,
    "7th": 7,
    "8th": 8,
    "9th": 9,
    "10th": 10,
    "Gamma": 11,
    "Azimuth": 12,
    "Zenith": 13,
    "Stellium": 14,
}


def _canonicalize_features(features: BeatmapFeatures, precision: int) -> Dict[str, Any]:
    lockout_sorted = {
        str(k): round(float(v), precision)
        for k, v in sorted(features.lockout_profile.items(), key=lambda item: int(item[0]))
    }

    return {
        "total_notes": int(features.total_notes),
        "rice_count": int(features.rice_count),
        "ln_count": int(features.ln_count),
        "hold_pct": round(float(features.hold_pct), precision),
        "avg_nps": round(float(features.avg_nps), precision),
        "peak_4m_nps": round(float(features.peak_4m_nps), precision),
        "duration_seconds": round(float(features.duration_seconds), precision),
        "peak_1b_nps": round(float(features.peak_1b_nps), precision),
        "gap1_count": int(features.gap1_count),
        "gap1_density": round(float(features.gap1_density), precision),
        "adj_count": int(features.adj_count),
        "adj_density": round(float(features.adj_density), precision),
        "mean_locked_fingers": round(float(features.mean_locked_fingers), precision),
        "lockout_profile": lockout_sorted,
        "antiphase_count": int(features.antiphase_count),
        "antiphase_rate": round(float(features.antiphase_rate), precision),
        "delta_t_action": round(float(features.delta_t_action), precision),
        "inverse_score": round(float(features.inverse_score), precision),
    }


def compute_feature_checksum(
    results: List[Any],
    precision: int = 6,
) -> str:
    """
    Computes a deterministic SHA-256 checksum across all ingested benchmark items,
    formatting floats to a fixed precision to ensure cross-platform stability.
    """
    def sort_key(r: Any) -> tuple:
        tier_idx = TIER_ORDER_MAP.get(r.tier, 999)
        return (
            r.technique or "",
            tier_idx,
            r.tier or "",
            r.id if r.id is not None else 0,
            r.song or "",
        )

    sorted_items = sorted(results, key=sort_key)

    canonical_records = []
    for r in sorted_items:
        if r.status == "SUCCESS" and r.features is not None:
            record = {
                "technique": r.technique,
                "tier": r.tier,
                "id": r.id,
                "song": r.song,
                "status": "SUCCESS",
                "bpm": round(float(r.bpm), precision) if r.bpm is not None else None,
                "features": _canonicalize_features(r.features, precision),
            }
        else:
            record = {
                "technique": r.technique,
                "tier": r.tier,
                "id": r.id,
                "song": r.song,
                "status": "FAILED_INGESTION",
                "bpm": None,
                "features": None,
            }
        canonical_records.append(record)

    serialized = json.dumps(canonical_records, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
