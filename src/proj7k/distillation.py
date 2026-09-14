import json
import math
from typing import List, Dict, Any, Optional, Tuple, Union
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from proj7k.features import BeatmapFeatures

DISTILLATION_FEATURE_KEYS: List[str] = [
    "hold_pct",
    "avg_nps",
    "peak_4m_nps",
    "peak_1b_nps",
    "gap1_density",
    "adj_density",
    "mean_locked_fingers",
    "antiphase_rate",
    "inverse_score",
]


def extract_feature_vector(
    features: BeatmapFeatures,
    keys: Optional[List[str]] = None,
) -> Dict[str, float]:
    """
    Extracts a dictionary of numerical feature dimensions from a BeatmapFeatures instance.
    """
    if keys is None:
        keys = DISTILLATION_FEATURE_KEYS
    vec: Dict[str, float] = {}
    for k in keys:
        val = getattr(features, k, 0.0)
        vec[k] = float(val) if isinstance(val, (int, float)) else 0.0
    return vec


def normalize_feature_vectors(
    vectors: List[Dict[str, float]],
    keys: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, float]], Dict[str, Tuple[float, float]]]:
    """
    Min-max normalizes a list of feature vectors into [0.0, 1.0] coordinates.
    Returns: (normalized_vectors, bounds_dict where bounds_dict[k] = (min_val, max_val))
    """
    if not vectors:
        return ([], {})

    if keys is None:
        keys = list(vectors[0].keys())

    bounds: Dict[str, Tuple[float, float]] = {}
    for k in keys:
        vals = [v.get(k, 0.0) for v in vectors]
        min_v = min(vals)
        max_v = max(vals)
        bounds[k] = (min_v, max_v)

    norm_vectors: List[Dict[str, float]] = []
    for v in vectors:
        norm_v: Dict[str, float] = {}
        for k in keys:
            min_v, max_v = bounds[k]
            span = max_v - min_v
            if span > 1e-9:
                norm_v[k] = round((v.get(k, 0.0) - min_v) / span, 4)
            else:
                norm_v[k] = 0.0
        norm_vectors.append(norm_v)

    return (norm_vectors, bounds)


def _calc_cosine_similarity(v1: Dict[str, float], v2: Dict[str, float], keys: List[str]) -> float:
    dot = sum(v1.get(k, 0.0) * v2.get(k, 0.0) for k in keys)
    norm1 = math.sqrt(sum(v1.get(k, 0.0) ** 2 for k in keys))
    norm2 = math.sqrt(sum(v2.get(k, 0.0) ** 2 for k in keys))
    if norm1 <= 1e-9 or norm2 <= 1e-9:
        return 0.0
    return round(max(0.0, min(1.0, dot / (norm1 * norm2))), 4)


def compute_technique_centroids(
    items: List[Any],
    keys: Optional[List[str]] = None,
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]]:
    """
    Computes raw and normalized centroid vectors for each technique present in the items.
    Returns: (centroids_raw, centroids_normalized)
    """
    if keys is None:
        keys = DISTILLATION_FEATURE_KEYS

    by_tech: Dict[str, List[Dict[str, float]]] = {}
    all_vectors: List[Dict[str, float]] = []

    for item in items:
        status = getattr(item, "status", None)
        feat = getattr(item, "features", None)
        if (status is None or status == "SUCCESS") and feat is not None:
            vec = extract_feature_vector(feat, keys=keys)
            by_tech.setdefault(item.technique, []).append(vec)
            all_vectors.append(vec)

    if not all_vectors:
        return ({}, {})

    _, bounds = normalize_feature_vectors(all_vectors, keys=keys)

    centroids_raw: Dict[str, Dict[str, float]] = {}
    centroids_norm: Dict[str, Dict[str, float]] = {}

    for tech, tech_vecs in by_tech.items():
        n = len(tech_vecs)
        raw_c: Dict[str, float] = {}
        norm_c: Dict[str, float] = {}

        for k in keys:
            avg_val = sum(v[k] for v in tech_vecs) / float(n)
            raw_c[k] = round(avg_val, 4)

            min_v, max_v = bounds[k]
            span = max_v - min_v
            if span > 1e-9:
                norm_c[k] = round((avg_val - min_v) / span, 4)
            else:
                norm_c[k] = 0.0

        centroids_raw[tech] = raw_c
        centroids_norm[tech] = norm_c

    return (centroids_raw, centroids_norm)


def compute_feature_importance(
    centroids_raw: Dict[str, Dict[str, float]],
    centroids_norm: Dict[str, Dict[str, float]],
    top_k: int = 3,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Identifies the top dominant feature dimensions for each technique by computing
    its distinctiveness contrast (absolute standardized difference) against all other techniques.
    """
    techniques = list(centroids_norm.keys())
    importance_by_tech: Dict[str, List[Dict[str, Any]]] = {}

    if not techniques:
        return {}

    keys = list(centroids_norm[techniques[0]].keys())

    for tech in techniques:
        other_techs = [t for t in techniques if t != tech]
        ranked_features = []

        for k in keys:
            tech_val = centroids_norm[tech][k]
            if other_techs:
                other_avg = sum(centroids_norm[t][k] for t in other_techs) / float(len(other_techs))
                contrast = abs(tech_val - other_avg)
            else:
                contrast = tech_val

            ranked_features.append({
                "feature": k,
                "score": round(contrast, 4),
                "raw_value": centroids_raw[tech][k],
                "normalized_value": tech_val,
            })

        # Sort descending by distinctiveness score
        ranked_features.sort(key=lambda x: x["score"], reverse=True)

        for rank_idx, feat_entry in enumerate(ranked_features[:top_k], start=1):
            feat_entry["rank"] = rank_idx

        importance_by_tech[tech] = ranked_features[:top_k]

    return importance_by_tech


@dataclass
class SeparabilityMatrix:
    techniques: List[str]
    matrix: List[List[float]]
    metric_type: str
    global_orthogonality_score: float
    global_mutual_information: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_separability_matrix(
    centroids_norm: Dict[str, Dict[str, float]],
    items: Optional[List[Any]] = None,
    keys: Optional[List[str]] = None,
) -> SeparabilityMatrix:
    """
    Computes pairwise separability distance matrix across techniques in normalized feature space
    using variance-weighted Mahalanobis distance.

    Outputs:
    - matrix: N x N pairwise Mahalanobis distance
    - global_mutual_information: average mutual information proxy exp(-0.5 * D_M)
    - global_orthogonality_score: 1.0 - global_mutual_information (in [0.0, 1.0])

    Guarantees:
    - Non-negativity: M[i][j] >= 0
    - Symmetry: M[i][j] == M[j][i]
    - Zero diagonal: M[i][i] == 0.0
    """
    techniques = sorted(centroids_norm.keys())
    n = len(techniques)
    if n == 0:
        return SeparabilityMatrix(
            techniques=[],
            matrix=[],
            metric_type="mahalanobis",
            global_orthogonality_score=0.0,
            global_mutual_information=0.0,
        )

    if keys is None:
        keys = list(centroids_norm[techniques[0]].keys())

    # Compute variance per feature dimension across items or centroids
    variances: Dict[str, float] = {}
    if items:
        valid_vecs = [
            extract_feature_vector(item.features, keys=keys)
            for item in items
            if getattr(item, "status", None) in (None, "SUCCESS") and getattr(item, "features", None) is not None
        ]
        norm_vecs, _ = normalize_feature_vectors(valid_vecs, keys=keys)
        for k in keys:
            vals = [v[k] for v in norm_vecs]
            mean_v = sum(vals) / len(vals) if vals else 0.0
            var_v = sum((x - mean_v) ** 2 for x in vals) / max(len(vals), 1)
            variances[k] = var_v if var_v > 1e-6 else 1.0
    else:
        for k in keys:
            vals = [centroids_norm[t][k] for t in techniques]
            mean_v = sum(vals) / len(vals) if vals else 0.0
            var_v = sum((x - mean_v) ** 2 for x in vals) / max(len(vals), 1)
            variances[k] = var_v if var_v > 1e-6 else 1.0

    matrix: List[List[float]] = [[0.0] * n for _ in range(n)]
    total_mi = 0.0
    pair_count = 0

    for i in range(n):
        for j in range(i, n):
            if i == j:
                matrix[i][j] = 0.0
            else:
                c1 = centroids_norm[techniques[i]]
                c2 = centroids_norm[techniques[j]]
                # Variance-weighted Mahalanobis distance
                dist_sq = sum(((c1[k] - c2[k]) ** 2) / variances[k] for k in keys)
                dist = round(math.sqrt(dist_sq), 4)
                matrix[i][j] = dist
                matrix[j][i] = dist

                # Mutual information proxy: exp(-0.5 * D_M)
                mi = math.exp(-0.5 * dist)
                total_mi += mi
                pair_count += 1

    avg_mi = round(total_mi / max(pair_count, 1), 4)
    global_ortho = round(max(0.0, min(1.0, 1.0 - avg_mi)), 4)

    return SeparabilityMatrix(
        techniques=techniques,
        matrix=matrix,
        metric_type="mahalanobis",
        global_orthogonality_score=global_ortho,
        global_mutual_information=avg_mi,
    )


@dataclass
class DistillationResult:
    version: str
    fingerprints: Dict[str, Dict[str, Any]]
    separability_matrix: Dict[str, Any]
    ground_truth_dataset: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "fingerprints": self.fingerprints,
            "separability_matrix": self.separability_matrix,
            "ground_truth_dataset": self.ground_truth_dataset,
        }

    def export_ground_truth(self, path: str, indent: int = 2) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(self.ground_truth_dataset, indent=indent, ensure_ascii=False))


def distill_benchmark_features(
    items: List[Any],
    keys: Optional[List[str]] = None,
    version: str = "1.0.0",
) -> DistillationResult:
    """
    Distills baseline fingerprints, dominant feature rankings, radar distribution profiles,
    purity champions, and variance-weighted Mahalanobis separability matrix for all techniques
    across the provided benchmark items, producing a standardized Ground Truth Distillation Dataset.
    """
    if keys is None:
        keys = DISTILLATION_FEATURE_KEYS

    centroids_raw, centroids_norm = compute_technique_centroids(items, keys=keys)
    importance = compute_feature_importance(centroids_raw, centroids_norm, top_k=3)
    sep_matrix = compute_separability_matrix(centroids_norm, items=items, keys=keys)

    fingerprints: Dict[str, Dict[str, Any]] = {}
    ground_truth_techniques: Dict[str, Any] = {}

    # Organize tier profiles per technique
    items_by_tech: Dict[str, List[Any]] = {}
    for item in items:
        status = getattr(item, "status", None)
        feat = getattr(item, "features", None)
        if (status is None or status == "SUCCESS") and feat is not None:
            items_by_tech.setdefault(item.technique, []).append(item)

    for tech in centroids_raw:
        tech_items = items_by_tech.get(tech, [])
        c_norm = centroids_norm[tech]

        # Determine Purity Champion (beatmap closest to centroid in cosine similarity)
        purity_champion = None
        best_purity_score = -1.0
        tier_profiles = {}

        for item in tech_items:
            tier = getattr(item, "tier", "unknown")
            vec = extract_feature_vector(item.features, keys=keys)
            purity = _calc_cosine_similarity(vec, centroids_raw[tech], keys=keys)

            if purity > best_purity_score:
                best_purity_score = purity
                purity_champion = {
                    "id": getattr(item, "id", None),
                    "song": getattr(item, "song", None),
                    "tier": tier,
                    "purity_score": purity,
                }

            tier_profiles[tier] = {
                "id": getattr(item, "id", None),
                "song": getattr(item, "song", None),
                "bpm": getattr(item, "bpm", None),
                "features": vec,
            }

        fp_data = {
            "technique": tech,
            "centroid_raw": centroids_raw[tech],
            "centroid_normalized": c_norm,
            "radar_profile": c_norm,
            "top_features": importance.get(tech, []),
            "purity_champion": purity_champion,
        }
        fingerprints[tech] = fp_data

        ground_truth_techniques[tech] = {
            "fingerprint": fp_data,
            "tier_profiles": tier_profiles,
        }

    ground_truth_dataset = {
        "version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_beatmaps": sum(len(tech_items) for tech_items in items_by_tech.values()),
        "feature_keys": keys,
        "techniques": ground_truth_techniques,
        "separability_matrix": sep_matrix.to_dict(),
    }

    return DistillationResult(
        version=version,
        fingerprints=fingerprints,
        separability_matrix=sep_matrix.to_dict(),
        ground_truth_dataset=ground_truth_dataset,
    )
