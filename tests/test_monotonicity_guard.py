import json
from pathlib import Path
import pytest
from proj7k.batch import (
    BatchSummary,
    BenchmarkBatchReport,
    BenchmarkItem,
    run_benchmark_pipeline,
    main as batch_cli_main,
)
from proj7k.guard import (
    run_monotonicity_guard,
    evaluate_monotonicity_guard,
    MonotonicityGuardConfig,
    MonotonicityGuardError,
    MonotonicityGuardResult,
)


def _create_osu(notes_count: int, bpm: float = 120.0) -> str:
    """Helper to generate a valid 7K osu file with given number of notes spaced over time."""
    beat_length = 60000.0 / bpm
    interval = max(50.0, beat_length / 2.0)
    hit_objects = []
    for i in range(notes_count):
        col_x = [36, 109, 182, 256, 329, 402, 475][i % 7]
        t = int(round(i * interval))
        hit_objects.append(f"{col_x},192,{t},1,0,0:0:0:0:")
    
    ho_text = "\n".join(hit_objects)
    beat_length = 60000.0 / bpm
    return f"""osu file format v14
[General]
Mode: 3
[Difficulty]
CircleSize: 7
[TimingPoints]
0,{beat_length},4,2,0,50,1,0
[HitObjects]
{ho_text}
"""


def test_monotonicity_guard_passes_on_monotonic_progression():
    # Progressively strictly increasing BPM & NPS across tiers
    tiers = ["1st", "2nd", "3rd", "4th", "5th"]
    manifest = [
        BenchmarkItem(
            technique="Regular Stream",
            tier=tier,
            id=100 + i,
            content=_create_osu(notes_count=20 + i * 10, bpm=120.0 + i * 25),
            bpm=120.0 + i * 25,
        )
        for i, tier in enumerate(tiers)
    ]

    report = run_benchmark_pipeline(manifest)
    config = MonotonicityGuardConfig(
        min_kendall_tau=0.8,
        min_spearman_rho=0.8,
        max_violations=0,
    )
    guard_res = evaluate_monotonicity_guard(report, config=config)

    assert isinstance(guard_res, MonotonicityGuardResult)
    assert guard_res.passed is True
    assert guard_res.error_message is None
    assert len(guard_res.violations_by_technique) == 0


def test_monotonicity_guard_fails_and_blocks_on_inversion():
    # Inverted tiers: 2nd tier is 200 BPM, 3rd tier drops to 130 BPM
    tiers = ["1st", "2nd", "3rd", "4th"]
    bpms = [120.0, 200.0, 130.0, 240.0]  # 2nd -> 3rd drops significantly!
    manifest = [
        BenchmarkItem(
            technique="Regular Jack",
            tier=tier,
            id=200 + i,
            content=_create_osu(notes_count=30, bpm=bpms[i]),
            bpm=bpms[i],
        )
        for i, tier in enumerate(tiers)
    ]

    report = run_benchmark_pipeline(manifest)
    config = MonotonicityGuardConfig(
        min_kendall_tau=0.8,
        min_spearman_rho=0.8,
        max_violations=0,
    )
    guard_res = evaluate_monotonicity_guard(report, config=config)

    assert guard_res.passed is False
    assert guard_res.error_message is not None
    assert "Regular Jack" in guard_res.violations_by_technique

    # Calling run_monotonicity_guard should raise MonotonicityGuardError
    with pytest.raises(MonotonicityGuardError) as exc_info:
        run_monotonicity_guard(manifest, config=config)
    assert "Monotonicity Guard Failure" in str(exc_info.value)
    assert "Regular Jack" in str(exc_info.value)


def test_monotonicity_guard_detects_checksum_mismatch():
    manifest = [
        BenchmarkItem(
            technique="Regular Stream",
            tier="1st",
            id=301,
            content=_create_osu(notes_count=20, bpm=120.0),
            bpm=120.0,
        )
    ]
    report = run_benchmark_pipeline(manifest)
    wrong_checksum = "sha256:0000000000000000000000000000000000000000000000000000000000000000"

    config = MonotonicityGuardConfig(expected_checksum=wrong_checksum)
    guard_res = evaluate_monotonicity_guard(report, config=config)

    assert guard_res.passed is False
    assert "Checksum mismatch" in guard_res.error_message


def test_batch_cli_with_guard_flag(tmp_path: Path):
    # Manifest with intentional inversion: 1st tier (200 BPM) > 2nd tier (100 BPM)
    manifest_data = [
        {
            "technique": "Regular Jack",
            "tier": "1st",
            "id": 1,
            "bpm": 200.0,
            "content": _create_osu(30, bpm=200.0),
        },
        {
            "technique": "Regular Jack",
            "tier": "2nd",
            "id": 2,
            "bpm": 100.0,
            "content": _create_osu(30, bpm=100.0),  # Inverted!
        },
    ]
    manifest_file = tmp_path / "inversion_manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    # CLI with --guard must fail with exit code 1 to block CI merge
    exit_code = batch_cli_main([
        "--manifest", str(manifest_file),
        "--guard",
    ])
    assert exit_code == 1

    # With max-violations tolerance >= 1, it passes
    exit_code_tolerated = batch_cli_main([
        "--manifest", str(manifest_file),
        "--guard",
        "--guard-max-violations", "1",
        "--guard-min-tau", "-1.0",
        "--guard-min-rho", "-1.0",
    ])
    assert exit_code_tolerated == 0


def test_guard_default_config_passes_on_bundled_benchmark_report():
    """
    The default gate must be usable on the repository's own 120-chart benchmark:
    - hold_pct / mean_locked_fingers are not monotone along the tier ladder at all (pure rice
      charts are hold-poor, LN charts lock many fingers) and must not be validated by default,
      while remaining available on explicit request;
    - the thresholds must tolerate the local tier inversions a real community-rated corpus
      always has, instead of failing on every technique.
    """
    report_path = Path("reports/batch_report.json")
    if not report_path.exists():
        pytest.skip("Bundled benchmark report not found.")

    with open(report_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    report = BenchmarkBatchReport(
        summary=BatchSummary(**data["summary"]),
        results=[],
        monotonicity=data.get("monotonicity"),
        feature_checksum=data.get("feature_checksum"),
    )
    assert "hold_pct" in report.monotonicity["Regular Speed"]

    default_res = evaluate_monotonicity_guard(report)
    default_msg = default_res.error_message or ""
    assert default_res.passed is True, default_msg
    assert "hold_pct" not in default_msg
    assert "mean_locked_fingers" not in default_msg
    assert set(default_res.metrics_summary["Regular Jack"]) == {"avg_nps", "peak_4m_nps"}

    requested_res = evaluate_monotonicity_guard(
        report, config=MonotonicityGuardConfig(metrics=["hold_pct"])
    )
    assert requested_res.passed is False
    assert "hold_pct" in requested_res.error_message

    # Stricter-than-default runs still fail on the corpus' genuine local inversions.
    strict_res = evaluate_monotonicity_guard(
        report, config=MonotonicityGuardConfig(max_violations=0, min_kendall_tau=0.80)
    )
    assert strict_res.passed is False
    assert "avg_nps" in strict_res.error_message


def test_monotonicity_guard_blocks_on_ingestion_failure():
    manifest = [
        BenchmarkItem(
            technique="Regular Stream",
            tier="1st",
            id=401,
            osu_path="/path/does/not/exist.osu",
        )
    ]
    report = run_benchmark_pipeline(manifest)
    assert report.summary.failed == 1

    guard_res = evaluate_monotonicity_guard(report)
    assert guard_res.passed is False
    assert "Ingestion incomplete" in guard_res.error_message
