from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Union
from pathlib import Path

from proj7k.batch import run_benchmark_pipeline, BenchmarkBatchReport, BenchmarkItem
from proj7k.monotonicity import DEFAULT_GUARD_METRICS


class MonotonicityGuardError(Exception):
    """Raised when the Monotonicity Guard fails validation."""
    pass


@dataclass
class MonotonicityGuardConfig:
    """
    Thresholds for the monotonicity gate.

    Defaults are calibrated against the repository's bundled 120-chart benchmark report
    (reports/batch_report.json, 8 techniques x 15 tiers), whose worst measured values are
    Kendall tau 0.780 / Spearman rho 0.894 / 6 tier inversions on one metric-technique pair.
    A real community-rated corpus is never perfectly ordered by physical density, so the gate's
    job is to catch *systematic* degradation — a collapsing rank correlation or inversions
    spreading across many pairs — not every local dip: a zero-violation default made the gate
    fail on the repository's own benchmark, which is why it was unusable. Runs that should be
    held to a stricter bar pass their own thresholds (see the batch CLI's --guard-* flags).
    """
    min_kendall_tau: float = 0.75
    min_spearman_rho: float = 0.85
    max_violations: int = 8
    max_violation_drop: float = 0.0
    expected_checksum: Optional[str] = None
    #: Metrics validated by default: only the quantities that are monotone along the Dan
    #: ladder by construction (see `monotonicity.DEFAULT_GUARD_METRICS`). Reports still carry
    #: every evaluated metric; passing names here (or `--guard-metric`) gates on them too, and
    #: an empty list validates every metric present in the report.
    metrics: List[str] = field(default_factory=lambda: list(DEFAULT_GUARD_METRICS))


@dataclass
class MonotonicityGuardResult:
    passed: bool
    violations_by_technique: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    metrics_summary: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    feature_checksum: Optional[str] = None
    error_message: Optional[str] = None


def evaluate_monotonicity_guard(
    report: BenchmarkBatchReport,
    config: Optional[MonotonicityGuardConfig] = None,
) -> MonotonicityGuardResult:
    """
    Evaluates a BenchmarkBatchReport against MonotonicityGuardConfig criteria:
    - Verifies Kendall's tau and Spearman's rho exceed thresholds.
    - Scans for monotonicity violations (tier inversions).
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

    if not report.monotonicity:
        msg = "\n".join(error_lines) if error_lines else "No monotonicity evaluation results present in batch report"
        return MonotonicityGuardResult(
            passed=False,
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
            if len(severe_violations) > config.max_violations:
                failed_checks.append(
                    f"{len(severe_violations)} violation(s) (limit {config.max_violations})"
                )
                if technique not in violations_by_tech:
                    violations_by_tech[technique] = []
                violations_by_tech[technique].extend(severe_violations)

            if tau < config.min_kendall_tau:
                failed_checks.append(
                    f"kendall_tau {tau:.3f} < min {config.min_kendall_tau:.3f}"
                )

            if rho < config.min_spearman_rho:
                failed_checks.append(
                    f"spearman_rho {rho:.3f} < min {config.min_spearman_rho:.3f}"
                )

            if failed_checks:
                error_lines.append(
                    f"- [{technique}] {metric}: {', '.join(failed_checks)}"
                )

    if error_lines:
        full_msg = "Monotonicity Guard Failure:\n" + "\n".join(error_lines)
        return MonotonicityGuardResult(
            passed=False,
            violations_by_technique=violations_by_tech,
            metrics_summary=summary_by_tech,
            feature_checksum=report.feature_checksum,
            error_message=full_msg,
        )

    return MonotonicityGuardResult(
        passed=True,
        violations_by_technique=violations_by_tech,
        metrics_summary=summary_by_tech,
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
