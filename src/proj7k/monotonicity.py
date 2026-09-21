import math
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from proj7k.dan import CANONICAL_DAN_TIERS
from proj7k.scaling import (
    compute_action_window,
    apply_inverse_bpm_scaling,
    ClockWindowRecord,
)

#: Canonical Dan progression ladder, referenced from its single authoritative definition.
#: Every tier — including the lowest one, 0th — must be covered by monotonicity evaluation;
#: a private copy here previously dropped 0th silently.
TIER_ORDER: List[str] = list(CANONICAL_DAN_TIERS)

#: Metrics gated on by default: the engine's own artifact, the star rating. The raw density
#: features (`avg_nps`, `peak_4m_nps`) climb with the ladder too, but they are inputs to the
#: rating rather than its output — a change to the rating formula that leaves the features
#: untouched would slip past a gate that only watched them. `hold_pct` and
#: `mean_locked_fingers` are deliberately absent even as inputs: they describe the technique
#: regime rather than the tier (pure rice charts are hold-poor, LN charts lock many fingers),
#: so they are not monotone across tiers at all. Batch reports still evaluate every metric;
#: this is the set the Monotonicity Guard validates unless asked for others explicitly.
DEFAULT_GUARD_METRICS: Tuple[str, ...] = ("star_rating",)

#: Metrics evaluated for diagnosis by default, in addition to `DEFAULT_GUARD_METRICS`.
DIAGNOSTIC_METRICS: Tuple[str, ...] = ("avg_nps", "peak_4m_nps")


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
    scaling_calibration: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "technique": self.technique,
            "metric": self.metric,
            "kendall_tau": self.kendall_tau,
            "spearman_rho": self.spearman_rho,
            "is_monotonic": self.is_monotonic,
            "steps": [s.to_dict() for s in self.steps],
            "violations": [v.to_dict() for v in self.violations],
            "warnings": [w.to_dict() for w in self.warnings],
        }
        if self.scaling_calibration is not None:
            d["scaling_calibration"] = self.scaling_calibration
        return d


def read_ladder_metric(result: Any, metric: str) -> Optional[float]:
    """
    Reads one ladder metric off a batch result.

    Physical quantities live on the feature tensor; the engine's star rating is carried by the
    result itself. Consulting the features first and the result second lets a single metric
    name work regardless of which of the two owns it, without either side knowing the other.
    """
    for source in (getattr(result, "features", None), result):
        if source is None:
            continue
        value = getattr(source, metric, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def evaluate_tier_sequence(
    tier_values: List[Tuple[str, float]],
    technique: str = "",
    metric: str = "",
    epsilon: float = 1e-4,
    scaling_calibration: Optional[Dict[str, Any]] = None,
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
            scaling_calibration=scaling_calibration,
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
        scaling_calibration=scaling_calibration,
    )


def evaluate_batch_monotonicity(
    results: List[Any],
    metrics: Optional[List[str]] = None,
    apply_scaling: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """
    Groups batch results by technique and evaluates monotonicity along canonical tiers.

    Each metric is read from the result's feature tensor when it has one there (physical
    quantities), and from the result itself otherwise — which is how the engine's star rating,
    carried by the result rather than by any feature, joins the ladder. Evaluates every listed
    metric; when none are listed, the gated star rating plus the baseline density metrics are
    evaluated. Note that `hold_pct` and `mean_locked_fingers` are not monotone across tiers by
    nature (pure rice charts are hold-poor, LN charts lock many fingers), so they are reported
    for diagnosis but are excluded from the Monotonicity Guard's default gate — see
    `DEFAULT_GUARD_METRICS`.

    If apply_scaling is True, pre-applies the Inverse BPM Scaling Law gating operator
    for LN Inverse on mean_locked_fingers to eliminate pseudo-inversions caused by low-speed charts,
    and records full before/after metrics and action clock window distribution.
    """
    if metrics is None:
        metrics = [
            *DEFAULT_GUARD_METRICS,
            *DIAGNOSTIC_METRICS,
            "hold_pct",
            "mean_locked_fingers",
        ]

    tier_indices = {t: i for i, t in enumerate(TIER_ORDER)}

    results_by_tech: Dict[str, List[Any]] = {}
    for r in results:
        # Every ingested chart joins the ladder; a metric that a given chart cannot supply is
        # simply absent for its tier, rather than dropping the chart from every other metric.
        if getattr(r, "status", None) == "SUCCESS":
            results_by_tech.setdefault(r.technique, []).append(r)

    reports_by_tech: Dict[str, Dict[str, Any]] = {}
    for tech, raw_items in results_by_tech.items():
        valid_items = [item for item in raw_items if getattr(item, "tier", None) in tier_indices]
        items = sorted(valid_items, key=lambda x: tier_indices[x.tier])
        if len(items) < 2:
            continue

        tech_norm = tech.lower().replace("_", " ").strip()
        is_inverse = tech_norm in ("ln inverse", "inverse")

        tech_reports: Dict[str, Any] = {}
        for metric in metrics:
            if is_inverse and metric == "mean_locked_fingers" and apply_scaling:
                # 1. Evaluate uncalibrated raw baseline
                tier_vals_raw = []
                for item in items:
                    feat = getattr(item, "features", None)
                    if feat is not None:
                        val = getattr(feat, "mean_locked_fingers", None)
                        if val is not None and isinstance(val, (int, float)):
                            tier_vals_raw.append((item.tier, float(val)))

                report_raw = evaluate_tier_sequence(tier_vals_raw, technique=tech, metric=metric)

                # 2. Pre-apply Inverse BPM Scaling Law non-linear gating operator
                tier_vals_calibrated = []
                window_distribution = []
                for item in items:
                    feat = getattr(item, "features", None)
                    if feat is not None:
                        raw_val = float(getattr(feat, "mean_locked_fingers", 0.0))
                        bpm = float(getattr(item, "bpm", None) or 150.0)
                        calibrated_val, factor, regime = apply_inverse_bpm_scaling(raw_val, bpm=bpm)
                        delta_t = compute_action_window(bpm)

                        rec = ClockWindowRecord(
                            tier=item.tier,
                            bpm=bpm,
                            delta_t_ms=delta_t,
                            raw_value=raw_val,
                            calibrated_value=calibrated_val,
                            scaling_factor=factor,
                            regime=regime,
                            id=getattr(item, "id", None),
                            song=getattr(item, "song", None),
                        )
                        window_distribution.append(rec.to_dict())
                        tier_vals_calibrated.append((item.tier, calibrated_val))

                # 3. Evaluate calibrated sequence
                report_calibrated = evaluate_tier_sequence(tier_vals_calibrated, technique=tech, metric=metric)

                # 4. Construct calibration comparison and window distribution
                scaling_calibration = {
                    "enabled": True,
                    "technique": tech,
                    "target_metric": metric,
                    "before": {
                        "kendall_tau": report_raw.kendall_tau,
                        "spearman_rho": report_raw.spearman_rho,
                        "is_monotonic": report_raw.is_monotonic,
                        "violations_count": len(report_raw.violations),
                        "violations": [v.to_dict() for v in report_raw.violations],
                    },
                    "after": {
                        "kendall_tau": report_calibrated.kendall_tau,
                        "spearman_rho": report_calibrated.spearman_rho,
                        "is_monotonic": report_calibrated.is_monotonic,
                        "violations_count": len(report_calibrated.violations),
                        "violations": [v.to_dict() for v in report_calibrated.violations],
                    },
                    "window_distribution": window_distribution,
                }
                report_calibrated.scaling_calibration = scaling_calibration
                tech_reports[metric] = report_calibrated.to_dict()
                tech_reports["scaling_calibration"] = scaling_calibration
            else:
                tier_vals = []
                for item in items:
                    val = read_ladder_metric(item, metric)
                    if val is not None:
                        tier_vals.append((item.tier, val))
                if len(tier_vals) >= 2:
                    report = evaluate_tier_sequence(tier_vals, technique=tech, metric=metric)
                    tech_reports[metric] = report.to_dict()

        if tech_reports:
            reports_by_tech[tech] = tech_reports

    return reports_by_tech
