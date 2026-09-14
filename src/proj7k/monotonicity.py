import math
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict

TIER_ORDER: List[str] = [
    "1st", "2nd", "3rd", "4th", "5th", "6th", "7th",
    "8th", "9th", "10th", "Gamma", "Azimuth", "Zenith", "Stellium"
]


def compute_kendall_tau(y: List[float]) -> float:
    """
    Computes Kendall's rank correlation coefficient tau-b for sequence y against monotonic tier ranks.
    Returns value in [-1.0, 1.0].
    """
    n = len(y)
    if n < 2:
        return 1.0

    concordant = 0
    discordant = 0

    for i in range(n):
        for j in range(i + 1, n):
            diff = y[j] - y[i]
            if diff > 0:
                concordant += 1
            elif diff < 0:
                discordant += 1

    total_pairs = n * (n - 1) / 2.0
    denom = math.sqrt(total_pairs * (concordant + discordant))
    if denom == 0:
        return 0.0
    return (concordant - discordant) / denom


def _fractional_ranks(seq: List[float]) -> List[float]:
    n = len(seq)
    indexed = sorted(enumerate(seq), key=lambda x: x[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[indexed[k][0]] = avg_rank
        i = j
    return ranks


def compute_spearman_rho(y: List[float]) -> float:
    """
    Computes Spearman's rank correlation coefficient rho for sequence y against monotonic tier ranks.
    Returns value in [-1.0, 1.0].
    """
    n = len(y)
    if n < 2:
        return 1.0

    rx = list(range(1, n + 1))
    ry = _fractional_ranks(y)

    mean_rx = (n + 1) / 2.0
    mean_ry = sum(ry) / n

    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    var_x = sum((rx[i] - mean_rx) ** 2 for i in range(n))
    var_y = sum((ry[i] - mean_ry) ** 2 for i in range(n))

    denom = math.sqrt(var_x * var_y)
    if denom == 0:
        return 0.0
    return cov / denom


@dataclass
class MonotonicityStep:
    from_tier: str
    to_tier: str
    from_value: float
    to_value: float
    delta: float
    pct_change: Optional[float]
    curvature: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MonotonicityViolation:
    metric: str
    from_tier: str
    to_tier: str
    from_value: float
    to_value: float
    drop_magnitude: float
    advice: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiscontinuityWarning:
    metric: str
    from_tier: str
    to_tier: str
    delta: float
    threshold: float
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TierMonotonicityReport:
    technique: str
    metric: str
    kendall_tau: float
    spearman_rho: float
    is_monotonic: bool
    steps: List[MonotonicityStep]
    violations: List[MonotonicityViolation]
    warnings: List[DiscontinuityWarning]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "technique": self.technique,
            "metric": self.metric,
            "kendall_tau": self.kendall_tau,
            "spearman_rho": self.spearman_rho,
            "is_monotonic": self.is_monotonic,
            "steps": [s.to_dict() for s in self.steps],
            "violations": [v.to_dict() for v in self.violations],
            "warnings": [w.to_dict() for w in self.warnings],
        }


def evaluate_tier_sequence(
    tier_values: List[Tuple[str, float]],
    technique: str = "",
    metric: str = "",
    epsilon: float = 1e-4,
) -> TierMonotonicityReport:
    """
    Evaluates tier progression monotonicity for a given metric across tiers.
    """
    tier_indices = {t: i for i, t in enumerate(TIER_ORDER)}
    valid_items = [item for item in tier_values if item[0] in tier_indices]
    sorted_items = sorted(valid_items, key=lambda x: tier_indices[x[0]])

    if len(sorted_items) < 2:
        return TierMonotonicityReport(
            technique=technique,
            metric=metric,
            kendall_tau=1.0,
            spearman_rho=1.0,
            is_monotonic=True,
            steps=[],
            violations=[],
            warnings=[],
        )

    y_vals = [float(item[1]) for item in sorted_items]
    tau = round(compute_kendall_tau(y_vals), 4)
    rho = round(compute_spearman_rho(y_vals), 4)

    steps: List[MonotonicityStep] = []
    violations: List[MonotonicityViolation] = []

    for i in range(len(sorted_items) - 1):
        from_tier, from_val = sorted_items[i]
        to_tier, to_val = sorted_items[i + 1]
        delta = round(to_val - from_val, 4)
        pct_change = round((delta / from_val) * 100.0, 4) if from_val != 0 else None
        curvature = round(delta - steps[-1].delta, 4) if steps else None

        steps.append(
            MonotonicityStep(
                from_tier=from_tier,
                to_tier=to_tier,
                from_value=from_val,
                to_value=to_val,
                delta=delta,
                pct_change=pct_change,
                curvature=curvature,
            )
        )

        if delta < -epsilon:
            drop = round(abs(delta), 4)
            advice = (
                f"Monotonicity inversion on {metric} between {from_tier} ({from_val}) and {to_tier} ({to_val}): "
                f"drop of {drop}. Recommend inspecting {to_tier} beatmap slices for anomalous {metric} degradation."
            )
            violations.append(
                MonotonicityViolation(
                    metric=metric,
                    from_tier=from_tier,
                    to_tier=to_tier,
                    from_value=from_val,
                    to_value=to_val,
                    drop_magnitude=drop,
                    advice=advice,
                )
            )

    is_monotonic = (len(violations) == 0)

    # Discontinuity scan (cliff jump: delta > mean + 3 * std)
    warnings: List[DiscontinuityWarning] = []
    if len(steps) >= 3:
        deltas = [s.delta for s in steps]
        mean_delta = sum(deltas) / len(deltas)
        variance = sum((d - mean_delta) ** 2 for d in deltas) / (len(deltas) - 1)
        std_delta = math.sqrt(variance)

        if std_delta > 1e-6:
            threshold = mean_delta + 3.0 * std_delta
            for s in steps:
                if s.delta > threshold:
                    msg = (
                        f"Discontinuity warning on {metric} between {s.from_tier} and {s.to_tier}: "
                        f"delta {s.delta} exceeds 3-sigma threshold ({round(threshold, 4)})."
                    )
                    warnings.append(
                        DiscontinuityWarning(
                            metric=metric,
                            from_tier=s.from_tier,
                            to_tier=s.to_tier,
                            delta=s.delta,
                            threshold=round(threshold, 4),
                            message=msg,
                        )
                    )

    return TierMonotonicityReport(
        technique=technique,
        metric=metric,
        kendall_tau=tau,
        spearman_rho=rho,
        is_monotonic=is_monotonic,
        steps=steps,
        violations=violations,
        warnings=warnings,
    )


def evaluate_batch_monotonicity(
    results: List[Any],
    metrics: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Groups batch results by technique and evaluates monotonicity along canonical tiers
    for the specified feature metrics.
    """
    if metrics is None:
        metrics = ["avg_nps", "peak_4m_nps", "hold_pct", "mean_locked_fingers"]

    results_by_tech: Dict[str, List[Any]] = {}
    for r in results:
        if getattr(r, "status", None) == "SUCCESS" and getattr(r, "features", None) is not None:
            results_by_tech.setdefault(r.technique, []).append(r)

    reports_by_tech: Dict[str, Dict[str, Any]] = {}
    for tech, items in results_by_tech.items():
        if len(items) < 2:
            continue
        tech_reports: Dict[str, Any] = {}
        for metric in metrics:
            tier_vals = []
            for item in items:
                feat = getattr(item, "features", None)
                if feat is not None:
                    val = getattr(feat, metric, None)
                    if val is not None and isinstance(val, (int, float)):
                        tier_vals.append((item.tier, float(val)))
            if len(tier_vals) >= 2:
                report = evaluate_tier_sequence(tier_vals, technique=tech, metric=metric)
                tech_reports[metric] = report.to_dict()
        if tech_reports:
            reports_by_tech[tech] = tech_reports

    return reports_by_tech
