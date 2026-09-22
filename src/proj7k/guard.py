from dataclasses import dataclass, field
import statistics
from typing import Dict, List, Optional, Tuple, Any, Union
from pathlib import Path

from proj7k.batch import run_benchmark_pipeline, BenchmarkBatchReport, BenchmarkItem
from proj7k.dan import CANONICAL_DAN_SR_BANDS
from proj7k.monotonicity import DEFAULT_GUARD_METRICS


class MonotonicityGuardError(Exception):
    """Raised when the Monotonicity Guard fails validation."""
    pass


@dataclass(frozen=True)
class MetricGate:
    """Acceptance thresholds for one ladder metric."""
    min_kendall_tau: float
    min_spearman_rho: float
    max_violations: int


#: Thresholds per metric, each calibrated against the worst value the repository's bundled
#: 120-chart benchmark actually produces for that metric (8 techniques x 15 tiers). A real
#: community-rated corpus is never perfectly ordered, so a gate's job is to catch
#: *systematic* degradation — a collapsing rank correlation or inversions spreading across
#: many pairs — not every local dip: a zero-violation default made the gate fail on the
#: repository's own benchmark, which is why it was unusable.
#:
#: The star rating is the engine's artifact and is held to the tighter bar; raw density
#: features are inputs to it and are gated only when a caller asks for them.
#:
#: These are per-technique floors — "no single technique's ladder has collapsed" — and sit
#: below the Phase 2 specification's *ladder-level* acceptance bar (mean rho >= 0.98,
#: mean tau >= 0.94, <= 20 inversions over all 8 techniques), which is a claim about the whole
#: ladder rather than about any one technique and is asserted end-to-end alongside these
#: (`tests/test_120_benchmark_guard.py`). Splitting them this way keeps the library gate
#: meaningful for partial manifests, where a cross-technique mean would say nothing.
CALIBRATED_METRIC_GATES: Dict[str, MetricGate] = {
    # Measured worst over the benchmark corpus: tau 0.905 / rho 0.964 / 3 inversions.
    "star_rating": MetricGate(min_kendall_tau=0.88, min_spearman_rho=0.95, max_violations=4),
    # --- The raw technique drivers (issue #52) -------------------------------------------------
    #
    # One gate per technique, on the technique's *own* ladder (`monotonicity.DRIVER_METRIC_
    # PREFIX` reads them). Without these entries a driver ladder fell back to
    # `DEFAULT_METRIC_GATE`, which is how issue #52's starting evidence could be read as "5 of 8
    # fail" or "3 of 8 fail" depending on which gate the reader assumed — the same table, two
    # answers.
    #
    # The thresholds are the ticket's **red line**, not a fitted floor: they are the bar a
    # driver has to clear to count as ordering its own ladder at all, and the ticket's bullseye
    # (tau >= 0.93 / rho >= 0.98) sits above them deliberately — `docs/adr/0015` records that
    # the official Jinjin ladder's own ratings order at tau 0.886 on Regular Tech, so the
    # bullseye is a target rather than a property of the ground truth. Measured at registration
    # (HEAD of issue #52's branch): ln_general 0.905/0.971, ln_tech 0.886/0.961, ln_release
    # 0.943/0.986 clear the line; jack 0.829/0.943, stream 0.790/0.904, ln_inverse 0.790/0.900,
    # speed 0.714/0.875, tech 0.581/0.729 do not. A caller that evaluates a driver ladder
    # therefore gets the shortfall reported, which is the point: the line is where the driver
    # layer has to arrive, not where it is.
    "driver_jack": MetricGate(min_kendall_tau=0.88, min_spearman_rho=0.95, max_violations=4),
    "driver_tech": MetricGate(min_kendall_tau=0.88, min_spearman_rho=0.95, max_violations=4),
    "driver_speed": MetricGate(min_kendall_tau=0.88, min_spearman_rho=0.95, max_violations=4),
    "driver_stream": MetricGate(min_kendall_tau=0.88, min_spearman_rho=0.95, max_violations=4),
    "driver_ln_general": MetricGate(min_kendall_tau=0.88, min_spearman_rho=0.95, max_violations=4),
    "driver_ln_tech": MetricGate(min_kendall_tau=0.88, min_spearman_rho=0.95, max_violations=4),
    "driver_ln_inverse": MetricGate(min_kendall_tau=0.88, min_spearman_rho=0.95, max_violations=4),
    "driver_ln_release": MetricGate(min_kendall_tau=0.88, min_spearman_rho=0.95, max_violations=4),
}
#: Applied to any metric without its own calibration entry.
#: Measured worst for the raw density metrics: tau 0.780 / rho 0.894 / 6 inversions.
DEFAULT_METRIC_GATE = MetricGate(min_kendall_tau=0.75, min_spearman_rho=0.85, max_violations=8)

#: Charts a tier needs before its star-rating median is treated as that tier's centre. A band
#: is a claim about the ladder's central tendency, so a lone chart (a fragment manifest's
#: synthetic entry) cannot carry one; the benchmark's full technique roster can.
DEFAULT_MIN_ANCHOR_SAMPLES: int = 4


@dataclass
class MonotonicityGuardConfig:
    """
    Thresholds for the monotonicity gate.

    `min_kendall_tau` / `min_spearman_rho` / `max_violations` left as None defer to the
    per-metric calibrated gate (`CALIBRATED_METRIC_GATES`); setting any of them overrides the
    calibrated value for every metric being validated (see the batch CLI's --guard-* flags).
    """
    min_kendall_tau: Optional[float] = None
    min_spearman_rho: Optional[float] = None
    max_violations: Optional[int] = None
    max_violation_drop: float = 0.0
    expected_checksum: Optional[str] = None
    #: Metrics validated by default: the engine's own artifact, the star rating (see
    #: `monotonicity.DEFAULT_GUARD_METRICS`). Reports still carry every evaluated metric;
    #: passing names here (or `--guard-metric`) gates on them too, and an empty list validates
    #: every metric present in the report.
    metrics: List[str] = field(default_factory=lambda: list(DEFAULT_GUARD_METRICS))
    #: Star-rating median band per tier (see `dan.CANONICAL_DAN_SR_BANDS`). Empty disables the
    #: anchor assertions; tiers with fewer than `min_anchor_samples` charts are skipped.
    anchor_bands: Dict[str, Tuple[float, float]] = field(
        default_factory=lambda: dict(CANONICAL_DAN_SR_BANDS)
    )
    min_anchor_samples: int = DEFAULT_MIN_ANCHOR_SAMPLES

    def gate_for(self, metric: str) -> MetricGate:
        """Resolves the thresholds to apply to one metric: explicit override, else calibrated."""
        base = CALIBRATED_METRIC_GATES.get(metric, DEFAULT_METRIC_GATE)
        return MetricGate(
            min_kendall_tau=self.min_kendall_tau if self.min_kendall_tau is not None else base.min_kendall_tau,
            min_spearman_rho=self.min_spearman_rho if self.min_spearman_rho is not None else base.min_spearman_rho,
            max_violations=self.max_violations if self.max_violations is not None else base.max_violations,
        )


@dataclass
class MonotonicityGuardResult:
    passed: bool
    violations_by_technique: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    metrics_summary: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Per tier: the measured star-rating median, its band, the sample count, and the verdict
    #: (`passed` is None when the tier had too few charts to carry a median).
    anchor_summary: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    feature_checksum: Optional[str] = None
    error_message: Optional[str] = None


def _evaluate_anchor_bands(
    report: BenchmarkBatchReport,
    config: MonotonicityGuardConfig,
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """
    Checks each anchor tier's median star rating against its acceptance band.

    Returns the per-tier summary and one error line per out-of-band tier.
    """
    by_tier: Dict[str, List[float]] = {tier: [] for tier in config.anchor_bands}
    for result in report.results:
        tier = getattr(result, "tier", None)
        star_rating = getattr(result, "star_rating", None)
        if getattr(result, "status", None) == "SUCCESS" and tier in by_tier and star_rating is not None:
            by_tier[tier].append(float(star_rating))

    summary: Dict[str, Dict[str, Any]] = {}
    error_lines: List[str] = []

    for tier, (low, high) in config.anchor_bands.items():
        samples = by_tier[tier]
        if len(samples) < config.min_anchor_samples:
            summary[tier] = {
                "median": round(statistics.median(samples), 4) if samples else None,
                "band": [low, high],
                "samples": len(samples),
                "passed": None,
            }
            continue

        median = statistics.median(samples)
        passed = low <= median <= high
        summary[tier] = {
            "median": round(median, 4),
            "band": [low, high],
            "samples": len(samples),
            "passed": passed,
        }
        if not passed:
            error_lines.append(
                f"- anchor {tier}: median star rating {median:.3f} outside band [{low}, {high}] "
                f"({len(samples)} chart(s) at this tier)"
            )

    return summary, error_lines


def evaluate_monotonicity_guard(
    report: BenchmarkBatchReport,
    config: Optional[MonotonicityGuardConfig] = None,
) -> MonotonicityGuardResult:
    """
    Evaluates a BenchmarkBatchReport against MonotonicityGuardConfig criteria:
    - Verifies Kendall's tau and Spearman's rho exceed thresholds.
    - Scans for monotonicity violations (tier inversions).
    - Checks each anchor tier's median star rating against its acceptance band.
    - Validates deterministic feature checksum matches expected golden checksum.
    """
    if config is None:
        config = MonotonicityGuardConfig()

    # 1. Verify Checksum if expected
    if config.expected_checksum and report.feature_checksum != config.expected_checksum:
        err = (
            f"Checksum mismatch: expected '{config.expected_checksum}', "
            f"got '{report.feature_checksum}'"
        )
        return MonotonicityGuardResult(
            passed=False,
            feature_checksum=report.feature_checksum,
            error_message=err,
        )

    error_lines: List[str] = []
    if report.summary.failed > 0:
        error_lines.append(f"Ingestion incomplete: {report.summary.failed} benchmark beatmap(s) failed ingestion")

    anchor_summary, anchor_errors = _evaluate_anchor_bands(report, config)
    error_lines.extend(anchor_errors)

    if not report.monotonicity:
        msg = "\n".join(error_lines) if error_lines else "No monotonicity evaluation results present in batch report"
        return MonotonicityGuardResult(
            passed=False,
            anchor_summary=anchor_summary,
            feature_checksum=report.feature_checksum,
            error_message=msg,
        )

    violations_by_tech: Dict[str, List[Dict[str, Any]]] = {}
    summary_by_tech: Dict[str, Dict[str, Any]] = {}

    for technique, metrics_dict in report.monotonicity.items():
        summary_by_tech[technique] = {}
        for metric, rep in metrics_dict.items():
            if metric == "scaling_calibration" or not isinstance(rep, dict) or "kendall_tau" not in rep:
                continue

            if config.metrics and metric not in config.metrics:
                continue

            gate = config.gate_for(metric)
            tau = rep.get("kendall_tau", 0.0)
            rho = rep.get("spearman_rho", 0.0)
            violations = rep.get("violations", [])
            steps = rep.get("steps", [])

            # If all values are 0 across all tiers (inactive metric for this technique), skip
            if steps and all(s.get("from_value", 0.0) == 0.0 and s.get("to_value", 0.0) == 0.0 for s in steps):
                continue

            # Filter violations exceeding tolerance drop
            severe_violations = [
                v for v in violations if v.get("drop_magnitude", 0.0) > config.max_violation_drop
            ]

            summary_by_tech[technique][metric] = {
                "kendall_tau": tau,
                "spearman_rho": rho,
                "violations_count": len(severe_violations),
            }

            failed_checks = []
            if len(severe_violations) > gate.max_violations:
                failed_checks.append(
                    f"{len(severe_violations)} violation(s) (limit {gate.max_violations})"
                )
                if technique not in violations_by_tech:
                    violations_by_tech[technique] = []
                violations_by_tech[technique].extend(severe_violations)

            if tau < gate.min_kendall_tau:
                failed_checks.append(
                    f"kendall_tau {tau:.3f} < min {gate.min_kendall_tau:.3f}"
                )

            if rho < gate.min_spearman_rho:
                failed_checks.append(
                    f"spearman_rho {rho:.3f} < min {gate.min_spearman_rho:.3f}"
                )

            if failed_checks:
                error_lines.append(
                    f"- [{technique}] {metric}: {', '.join(failed_checks)}"
                )

    # A gate that checked nothing reads exactly like a gate that passed: a report evaluated
    # without the rated stage carries no star ladder, and a mistyped metric name matches
    # nothing at all. Both must fail loudly instead of returning green.
    if not any(summary_by_tech.values()):
        error_lines.append(
            "Nothing was validated: no ladder was found for the configured metric(s) "
            f"({', '.join(config.metrics) if config.metrics else 'any metric'})"
        )

    if error_lines:
        full_msg = "Monotonicity Guard Failure:\n" + "\n".join(error_lines)
        return MonotonicityGuardResult(
            passed=False,
            violations_by_technique=violations_by_tech,
            metrics_summary=summary_by_tech,
            anchor_summary=anchor_summary,
            feature_checksum=report.feature_checksum,
            error_message=full_msg,
        )

    return MonotonicityGuardResult(
        passed=True,
        violations_by_technique=violations_by_tech,
        metrics_summary=summary_by_tech,
        anchor_summary=anchor_summary,
        feature_checksum=report.feature_checksum,
        error_message=None,
    )


def run_monotonicity_guard(
    manifest: Union[str, Path, List[Union[BenchmarkItem, Dict[str, Any]]], Dict[str, Any]],
    config: Optional[MonotonicityGuardConfig] = None,
    raise_on_failure: bool = True,
    **pipeline_kwargs: Any,
) -> MonotonicityGuardResult:
    """
    Executes the benchmark batch pipeline and validates monotonicity guard constraints.
    Raises MonotonicityGuardError if validation fails and raise_on_failure is True.
    """
    report = run_benchmark_pipeline(
        manifest,
        evaluate_monotonicity=True,
        **pipeline_kwargs,
    )
    result = evaluate_monotonicity_guard(report, config=config)

    if not result.passed and raise_on_failure:
        raise MonotonicityGuardError(result.error_message)

    return result
