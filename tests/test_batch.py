import json
from pathlib import Path
import pytest
from proj7k.batch import (
    run_benchmark_pipeline,
    BenchmarkItem,
    BenchmarkBatchReport,
    main as batch_cli_main,
)


VALID_7K_OSU = """osu file format v14

[General]
AudioFilename: audio.mp3
Mode: 3

[Metadata]
Title: Valid 7K Song
Artist: Artist
Creator: Mapper
Version: 7K Hard

[Difficulty]
CircleSize: 7
OverallDifficulty: 8

[TimingPoints]
0,500,4,2,0,50,1,0

[HitObjects]
36,192,0,1,0,0:0:0:0:
109,192,500,1,0,0:0:0:0:
182,192,1000,1,0,0:0:0:0:
256,192,1500,128,0,2500:0:0:0:0:
"""

VALID_7K_OSU_2 = """osu file format v14

[General]
Mode: 3

[Metadata]
Title: Valid 7K Stream
Artist: Streamer
Creator: Mapper
Version: 7K Insane

[Difficulty]
CircleSize: 7
OverallDifficulty: 8

[TimingPoints]
0,250,4,2,0,50,1,0

[HitObjects]
36,192,0,1,0,0:0:0:0:
109,192,250,1,0,0:0:0:0:
182,192,500,1,0,0:0:0:0:
256,192,750,1,0,0:0:0:0:
329,192,1000,1,0,0:0:0:0:
"""

NON_7K_MODE0_OSU = """osu file format v14

[General]
Mode: 0

[Metadata]
Title: Standard Osu
Version: Normal

[Difficulty]
CircleSize: 4
"""

NON_7K_4K_OSU = """osu file format v14

[General]
Mode: 3

[Metadata]
Title: 4K Mania
Version: 4K Hard

[Difficulty]
CircleSize: 4
"""

CORRUPTED_OSU = """osu file format v14
[General
INVALID NOT A PROPER SECTION
[TimingPoints
broken,broken
"""


def test_batch_pipeline_happy_path():
    manifest = [
        BenchmarkItem(
            technique="Regular Jack",
            tier="1st",
            id=101,
            song="Valid 7K Song",
            content=VALID_7K_OSU,
        ),
        BenchmarkItem(
            technique="Regular Stream",
            tier="2nd",
            id=102,
            song="Valid 7K Stream",
            content=VALID_7K_OSU_2,
        ),
    ]

    report = run_benchmark_pipeline(manifest)

    assert isinstance(report, BenchmarkBatchReport)
    assert report.summary.total == 2
    assert report.summary.success == 2
    assert report.summary.failed == 0

    res1 = report.results[0]
    assert res1.technique == "Regular Jack"
    assert res1.tier == "1st"
    assert res1.status == "SUCCESS"
    assert res1.error is None
    assert res1.features is not None
    assert res1.features.total_notes == 4
    assert res1.features.rice_count == 3
    assert res1.features.ln_count == 1
    assert res1.features.hold_pct == 25.0
    assert res1.features.avg_nps > 0.0

    res2 = report.results[1]
    assert res2.technique == "Regular Stream"
    assert res2.tier == "2nd"
    assert res2.status == "SUCCESS"
    assert res2.features is not None
    assert res2.features.total_notes == 5


def test_batch_pipeline_fault_tolerance():
    manifest = [
        BenchmarkItem(
            technique="Regular Jack",
            tier="1st",
            id=101,
            content=VALID_7K_OSU,
        ),
        BenchmarkItem(
            technique="Regular Jack",
            tier="2nd",
            id=102,
            content=CORRUPTED_OSU,
        ),
        BenchmarkItem(
            technique="Regular Tech",
            tier="1st",
            id=103,
            content=NON_7K_MODE0_OSU,
        ),
        BenchmarkItem(
            technique="Regular Tech",
            tier="2nd",
            id=104,
            content=NON_7K_4K_OSU,
        ),
        BenchmarkItem(
            technique="LN Inverse",
            tier="1st",
            id=105,
            osu_path="/path/that/does/not/exist/ever.osu",
        ),
    ]

    # Pipeline MUST NOT crash, MUST gracefully record FAILED_INGESTION
    report = run_benchmark_pipeline(manifest)

    assert report.summary.total == 5
    assert report.summary.success == 1
    assert report.summary.failed == 4

    # Valid item
    assert report.results[0].status == "SUCCESS"
    assert report.results[0].features is not None

    # Corrupted / Non-7K items
    for res in report.results[1:]:
        assert res.status == "FAILED_INGESTION"
        assert res.features is None
        assert res.error is not None
        assert len(res.error) > 0


def test_batch_pipeline_json_export(tmp_path: Path):
    manifest = [
        BenchmarkItem(
            technique="Regular Jack",
            tier="1st",
            id=101,
            content=VALID_7K_OSU,
        ),
        BenchmarkItem(
            technique="Regular Jack",
            tier="2nd",
            id=102,
            content=NON_7K_4K_OSU,
        ),
    ]

    report = run_benchmark_pipeline(manifest)

    # 1. Serialization to string
    json_str = report.to_json()
    data = json.loads(json_str)
    assert data["summary"]["total"] == 2
    assert data["summary"]["success"] == 1
    assert data["summary"]["failed"] == 1
    assert len(data["results"]) == 2
    assert data["results"][0]["status"] == "SUCCESS"
    assert data["results"][0]["features"]["total_notes"] == 4
    assert data["results"][1]["status"] == "FAILED_INGESTION"

    # 2. Save to file
    out_file = tmp_path / "report.json"
    report.save_json(str(out_file))
    assert out_file.exists()
    loaded_data = json.loads(out_file.read_text(encoding="utf-8"))
    assert loaded_data == data


def test_batch_cli(tmp_path: Path):
    # Prepare manifest file
    manifest_data = [
        {
            "technique": "Regular Jack",
            "tier": "1st",
            "id": 101,
            "song": "Valid 7K Song",
            "content": VALID_7K_OSU,
        },
        {
            "technique": "Regular Jack",
            "tier": "2nd",
            "id": 102,
            "content": NON_7K_4K_OSU,
        },
    ]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    out_file = tmp_path / "cli_report.json"
    exit_code = batch_cli_main(["--manifest", str(manifest_path), "--output", str(out_file)])

    assert exit_code == 0
    assert out_file.exists()
    report_data = json.loads(out_file.read_text(encoding="utf-8"))
    assert report_data["summary"]["total"] == 2
    assert report_data["summary"]["success"] == 1
    assert report_data["summary"]["failed"] == 1


def test_batch_pipeline_hierarchical_manifest():
    manifest_dict = {
        "Regular Jack": {
            "1st": {
                "id": 101,
                "song": "Jack 1",
                "bpm": 120.0,
                "sr": 3.5,
                "content": VALID_7K_OSU,
            },
            "2nd": {
                "id": 102,
                "song": "Jack 2",
                "bpm": 140.0,
                "sr": 4.0,
                "content": NON_7K_4K_OSU,
            }
        },
        "LN Inverse": {
            "1st": {
                "id": 201,
                "song": "Inverse 1",
                "bpm": 160.0,
                "sr": 5.0,
                "content": VALID_7K_OSU_2,
            }
        }
    }

    report = run_benchmark_pipeline(manifest_dict)
    assert report.summary.total == 3
    assert report.summary.success == 2
    assert report.summary.failed == 1

    # Check results by technique
    jack_results = [r for r in report.results if r.technique == "Regular Jack"]
    assert len(jack_results) == 2
    assert jack_results[0].tier == "1st"
    assert jack_results[0].status == "SUCCESS"
    assert jack_results[1].tier == "2nd"
    assert jack_results[1].status == "FAILED_INGESTION"

    ln_results = [r for r in report.results if r.technique == "LN Inverse"]
    assert len(ln_results) == 1
    assert ln_results[0].tier == "1st"
    assert ln_results[0].status == "SUCCESS"


CONTROLLED_REGULAR_CHORD_OSU = """osu file format v14
[General]
Mode: 3

[Metadata]
Title: Controlled Regular Chord
Version: 1st

[Difficulty]
CircleSize: 7
OverallDifficulty: 8

[TimingPoints]
0,500,4,2,0,50,1,0

[HitObjects]
36,192,0,1,0,0:0:0:0:
182,192,0,1,0,0:0:0:0:
329,192,500,1,0,0:0:0:0:
402,192,500,1,0,0:0:0:0:
36,192,1000,1,0,0:0:0:0:
109,192,1000,1,0,0:0:0:0:
329,192,1000,1,0,0:0:0:0:
475,192,1000,1,0,0:0:0:0:
109,192,1500,1,0,0:0:0:0:
182,192,1500,1,0,0:0:0:0:
402,192,1500,1,0,0:0:0:0:
475,192,1500,1,0,0:0:0:0:
256,192,2000,1,0,0:0:0:0:
"""

CONTROLLED_LN_INVERSE_OSU = """osu file format v14
[General]
Mode: 3

[Metadata]
Title: Controlled LN Inverse
Version: 1st

[Difficulty]
CircleSize: 7
OverallDifficulty: 8

[TimingPoints]
0,500,4,2,0,50,1,0

[HitObjects]
36,192,0,128,0,2000:0:0:0:0:
109,192,0,128,0,2000:0:0:0:0:
329,192,0,128,0,2000:0:0:0:0:
402,192,0,128,0,2000:0:0:0:0:
256,192,500,1,0,0:0:0:0:
256,192,1000,1,0,0:0:0:0:
256,192,1500,1,0,0:0:0:0:
256,192,2000,1,0,0:0:0:0:
"""

CONTROLLED_LN_RELEASE_OSU = """osu file format v14
[General]
Mode: 3

[Metadata]
Title: Controlled LN Release
Version: 1st

[Difficulty]
CircleSize: 7
OverallDifficulty: 8

[TimingPoints]
0,500,4,2,0,50,1,0

[HitObjects]
36,192,0,128,0,1000:0:0:0:0:
109,192,0,128,0,1000:0:0:0:0:
182,192,1000,1,0,0:0:0:0:
329,192,1000,128,0,2000:0:0:0:0:
329,192,2000,1,0,0:0:0:0:
"""


def test_batch_pipeline_physiological_features(tmp_path: Path):
    manifest = [
        BenchmarkItem(
            technique="Regular Chord",
            tier="1st",
            id=101,
            song="Controlled Regular Chord",
            content=CONTROLLED_REGULAR_CHORD_OSU,
        ),
        BenchmarkItem(
            technique="LN Inverse",
            tier="1st",
            id=201,
            song="Controlled LN Inverse",
            content=CONTROLLED_LN_INVERSE_OSU,
        ),
        BenchmarkItem(
            technique="LN Release",
            tier="1st",
            id=301,
            song="Controlled LN Release",
            content=CONTROLLED_LN_RELEASE_OSU,
        ),
    ]

    report = run_benchmark_pipeline(manifest)
    assert report.summary.total == 3
    assert report.summary.success == 3
    assert report.summary.failed == 0

    # 1. Assert Regular Chord physiological metrics
    res_chord = report.results[0]
    f_chord = res_chord.features
    assert f_chord is not None
    assert f_chord.gap1_count == 2
    assert f_chord.gap1_density == 1.0
    assert f_chord.adj_count == 4
    assert f_chord.adj_density == 2.0
    assert f_chord.mean_locked_fingers == 0.0
    assert f_chord.lockout_profile[0] == 100.0
    assert f_chord.antiphase_count == 0
    assert f_chord.antiphase_rate == 0.0

    # 2. Assert LN Inverse degree-of-freedom suppression metrics
    res_inverse = report.results[1]
    f_inverse = res_inverse.features
    assert f_inverse is not None
    assert f_inverse.mean_locked_fingers >= 3.9
    assert f_inverse.lockout_profile[4] >= 99.0
    assert f_inverse.hold_pct == 50.0

    # 3. Assert LN Release antiphase articulation metrics
    res_release = report.results[2]
    f_release = res_release.features
    assert f_release is not None
    assert f_release.antiphase_count == 2
    assert f_release.antiphase_rate == 1.0

    # 4. Assert full serialization into JSON report
    report_dict = report.to_dict()
    for item in report_dict["results"]:
        feat = item["features"]
        assert "gap1_count" in feat
        assert "gap1_density" in feat
        assert "adj_count" in feat
        assert "adj_density" in feat
        assert "mean_locked_fingers" in feat
        assert "lockout_profile" in feat
        assert "antiphase_count" in feat
        assert "antiphase_rate" in feat

    out_json = tmp_path / "physio_report.json"
    report.save_json(str(out_json))
    assert out_json.exists()
    loaded_json = json.loads(out_json.read_text(encoding="utf-8"))
    assert loaded_json == report_dict


def _make_osu_with_notes(title: str, version: str, notes_per_sec: int) -> str:
    # 2-second beatmap with specified notes per second
    lines = [
        "osu file format v14",
        "[General]",
        "Mode: 3",
        "[Metadata]",
        f"Title: {title}",
        f"Version: {version}",
        "[Difficulty]",
        "CircleSize: 7",
        "OverallDifficulty: 8",
        "[TimingPoints]",
        "0,500,4,2,0,50,1,0",
        "[HitObjects]",
    ]
    step_ms = 1000.0 / notes_per_sec
    t = 0.0
    while t <= 2000.0:
        lines.append(f"36,192,{int(t)},1,0,0:0:0:0:")
        t += step_ms
    return "\n".join(lines) + "\n"


def test_batch_pipeline_monotonicity_evaluation(tmp_path: Path):
    manifest = [
        # Technique 1: Strictly monotonic progression (Jack 1st..4th)
        BenchmarkItem(technique="Regular Jack", tier="1st", content=_make_osu_with_notes("Jack", "1st", 2)),
        BenchmarkItem(technique="Regular Jack", tier="2nd", content=_make_osu_with_notes("Jack", "2nd", 4)),
        BenchmarkItem(technique="Regular Jack", tier="3rd", content=_make_osu_with_notes("Jack", "3rd", 6)),
        BenchmarkItem(technique="Regular Jack", tier="4th", content=_make_osu_with_notes("Jack", "4th", 8)),

        # Technique 2: Sequence with inversion (Stream 1st..4th, 3rd drops below 2nd!)
        BenchmarkItem(technique="Regular Stream", tier="1st", content=_make_osu_with_notes("Stream", "1st", 2)),
        BenchmarkItem(technique="Regular Stream", tier="2nd", content=_make_osu_with_notes("Stream", "2nd", 6)),
        BenchmarkItem(technique="Regular Stream", tier="3rd", content=_make_osu_with_notes("Stream", "3rd", 3)),
        BenchmarkItem(technique="Regular Stream", tier="4th", content=_make_osu_with_notes("Stream", "4th", 8)),
    ]

    report = run_benchmark_pipeline(manifest)
    assert report.summary.total == 8
    assert report.summary.success == 8
    assert report.summary.failed == 0

    # Assert top-level report contains monotonicity evaluation
    assert report.monotonicity is not None
    assert "Regular Jack" in report.monotonicity
    assert "Regular Stream" in report.monotonicity

    # 1. Assert Regular Jack is monotonic
    jack_mono = report.monotonicity["Regular Jack"]["avg_nps"]
    assert jack_mono["is_monotonic"] is True
    assert jack_mono["kendall_tau"] == 1.0
    assert jack_mono["spearman_rho"] == 1.0
    assert len(jack_mono["violations"]) == 0
    assert len(jack_mono["steps"]) == 3

    # 2. Assert Regular Stream captures the inversion violation at 2nd -> 3rd
    stream_mono = report.monotonicity["Regular Stream"]["avg_nps"]
    assert stream_mono["is_monotonic"] is False
    assert stream_mono["kendall_tau"] < 1.0
    assert len(stream_mono["violations"]) == 1

    violation = stream_mono["violations"][0]
    assert violation["metric"] == "avg_nps"
    assert violation["from_tier"] == "2nd"
    assert violation["to_tier"] == "3rd"
    assert violation["drop_magnitude"] > 0
    assert "2nd" in violation["advice"] and "3rd" in violation["advice"]

    # 3. Assert full JSON serialization of monotonicity chapter
    report_dict = report.to_dict()
    assert "monotonicity" in report_dict
    assert "Regular Jack" in report_dict["monotonicity"]
    assert "Regular Stream" in report_dict["monotonicity"]

    out_file = tmp_path / "mono_report.json"
    report.save_json(str(out_file))
    assert out_file.exists()
    loaded_data = json.loads(out_file.read_text(encoding="utf-8"))
    assert loaded_data["monotonicity"] == report_dict["monotonicity"]


def test_batch_pipeline_inverse_bpm_scaling_top_level_seam(tmp_path: Path):
    # Highest testing seam: test end-to-end benchmark pipeline with Inverse BPM scaling law.
    # Chart 1: 7th Dan, Low-speed 30 BPM, 6 LNs locked simultaneously (mean locked ~ 5.8)
    # Chart 2: Stellium, High-speed 250 BPM, 4 LNs locked simultaneously (mean locked ~ 3.9)
    #
    # Without scaling: 7th (5.8) > Stellium (3.9) -> Pseudo Inversion!
    # With scaling: Gating operator cuts 7th Dan into the low-speed truncation regime
    #               and applies exponential penalty to the 250 BPM Stellium.
    # Monotonicity is preserved and Kendall's tau reaches 1.0.
    #
    # Both charts are annotated in 1/16 (their notes sit a quarter of a beat apart), so the
    # notation-normalized tempo the engine reads equals the tempo stated here — see ADR-0006
    # revision 1. Under the pre-#51 code the tempi below were 130 and 220, which put neither
    # chart where its own note spacing says it belongs.

    low_speed_osu = """osu file format v14
[General]
Mode: 3
[Metadata]
Title: Low Speed High Lock
Version: 7th
[Difficulty]
CircleSize: 7
OverallDifficulty: 8
[TimingPoints]
0,2000,4,2,0,50,1,0
[HitObjects]
36,192,0,128,0,2000:0:0:0:0:
109,192,0,128,0,2000:0:0:0:0:
182,192,0,128,0,2000:0:0:0:0:
329,192,0,128,0,2000:0:0:0:0:
402,192,0,128,0,2000:0:0:0:0:
475,192,0,128,0,2000:0:0:0:0:
256,192,500,1,0,0:0:0:0:
256,192,1000,1,0,0:0:0:0:
256,192,1500,1,0,0:0:0:0:
"""

    high_speed_osu = """osu file format v14
[General]
Mode: 3
[Metadata]
Title: High Speed Modest Lock
Version: Stellium
[Difficulty]
CircleSize: 7
OverallDifficulty: 8
[TimingPoints]
0,240,4,2,0,50,1,0
[HitObjects]
36,192,0,128,0,2000:0:0:0:0:
109,192,0,128,0,2000:0:0:0:0:
402,192,0,128,0,2000:0:0:0:0:
475,192,0,128,0,2000:0:0:0:0:
256,192,60,1,0,0:0:0:0:
256,192,120,1,0,0:0:0:0:
256,192,180,1,0,0:0:0:0:
256,192,240,1,0,0:0:0:0:
256,192,300,1,0,0:0:0:0:
256,192,360,1,0,0:0:0:0:
256,192,420,1,0,0:0:0:0:
256,192,480,1,0,0:0:0:0:
256,192,540,1,0,0:0:0:0:
256,192,600,1,0,0:0:0:0:
256,192,660,1,0,0:0:0:0:
256,192,720,1,0,0:0:0:0:
256,192,780,1,0,0:0:0:0:
256,192,840,1,0,0:0:0:0:
256,192,900,1,0,0:0:0:0:
256,192,960,1,0,0:0:0:0:
256,192,1020,1,0,0:0:0:0:
256,192,1080,1,0,0:0:0:0:
256,192,1140,1,0,0:0:0:0:
256,192,1200,1,0,0:0:0:0:
"""

    manifest = [
        BenchmarkItem(
            technique="LN Inverse",
            tier="7th",
            id=701,
            song="Low Speed High Lock",
            bpm=30.0,
            content=low_speed_osu,
        ),
        BenchmarkItem(
            technique="LN Inverse",
            tier="Stellium",
            id=1401,
            song="High Speed Modest Lock",
            bpm=250.0,
            content=high_speed_osu,
        ),
    ]

    # 1. Run pipeline with scaling enabled (default)
    report = run_benchmark_pipeline(manifest, monotonicity_metrics=["mean_locked_fingers"])

    assert report.summary.total == 2
    assert report.summary.success == 2
    assert report.summary.failed == 0

    # 2. Check item-level results
    res_7th = report.results[0]
    res_stellium = report.results[1]
    assert res_7th.bpm == 30.0
    assert res_7th.features.mean_locked_fingers >= 5.0
    # The action clock is the chart's own note spacing (1/16 of the stated tempo): 500 ms.
    assert res_7th.features.delta_t_action == pytest.approx(500.0, abs=1e-2)

    assert res_stellium.bpm == 250.0
    assert res_stellium.features.mean_locked_fingers < res_7th.features.mean_locked_fingers
    assert res_stellium.features.delta_t_action == pytest.approx(60.0, abs=1e-2)

    # 3. Check monotonicity evaluation
    assert report.monotonicity is not None
    assert "LN Inverse" in report.monotonicity
    inverse_mono = report.monotonicity["LN Inverse"]["mean_locked_fingers"]

    # Calibrated evaluation must eliminate pseudo-inversion!
    assert inverse_mono["is_monotonic"] is True
    assert inverse_mono["kendall_tau"] == 1.0
    assert len(inverse_mono["violations"]) == 0

    # 4. Check calibration chapter with before/after comparison
    calib = inverse_mono["scaling_calibration"]
    assert calib is not None
    assert calib["before"]["is_monotonic"] is False
    assert calib["before"]["kendall_tau"] == -1.0
    assert calib["before"]["violations_count"] == 1
    assert calib["after"]["is_monotonic"] is True
    assert calib["after"]["kendall_tau"] == 1.0
    assert calib["after"]["violations_count"] == 0

    # 5. Check action clock window distribution
    dist = calib["window_distribution"]
    assert len(dist) == 2
    assert dist[0]["tier"] == "7th"
    assert dist[0]["regime"] == "LOW_SPEED_TRUNCATION"
    assert dist[0]["calibrated_value"] <= 5.0

    assert dist[1]["tier"] == "Stellium"
    assert dist[1]["regime"] == "EXPONENTIAL_PENALTY"
    assert dist[1]["scaling_factor"] > 1.0
    assert dist[1]["calibrated_value"] > dist[0]["calibrated_value"]

    # 6. Check full JSON export roundtrip
    out_file = tmp_path / "top_level_seam_report.json"
    report.save_json(str(out_file))
    assert out_file.exists()
    loaded_data = json.loads(out_file.read_text(encoding="utf-8"))
    assert loaded_data["monotonicity"]["LN Inverse"]["mean_locked_fingers"]["is_monotonic"] is True


def test_batch_pipeline_feature_distillation_and_orthogonality(tmp_path: Path):
    # Highest testing seam: verify 8-technique feature distillation, baseline fingerprints,
    # top-3 feature rankings, separability matrix non-negativity, and ground-truth JSON export.
    # We construct controlled beatmaps representing distinct techniques:
    # 1. Regular Jack: burst chord/jack density (cols 0, 1)
    # 2. Regular Stream: high NPS with gap:1 (cols 0, 2)
    # 3. LN Inverse: full LN lock (5 columns held)
    # 4. LN Release: rapid antiphase releases
    manifest = [
        BenchmarkItem(
            technique="Regular Jack",
            tier="1st",
            id=101,
            bpm=150.0,
            content="""osu file format v14
[General]
Mode: 3
[Difficulty]
CircleSize: 7
[TimingPoints]
0,400,4,2,0,50,1,0
[HitObjects]
36,192,0,1,0,0:0:0:0:
109,192,0,1,0,0:0:0:0:
36,192,200,1,0,0:0:0:0:
109,192,200,1,0,0:0:0:0:
""",
        ),
        BenchmarkItem(
            technique="Regular Stream",
            tier="1st",
            id=201,
            bpm=180.0,
            content="""osu file format v14
[General]
Mode: 3
[Difficulty]
CircleSize: 7
[TimingPoints]
0,333.33,4,2,0,50,1,0
[HitObjects]
36,192,0,1,0,0:0:0:0:
182,192,0,1,0,0:0:0:0:
329,192,100,1,0,0:0:0:0:
475,192,100,1,0,0:0:0:0:
""",
        ),
        BenchmarkItem(
            technique="LN Inverse",
            tier="1st",
            id=301,
            bpm=140.0,
            content="""osu file format v14
[General]
Mode: 3
[Difficulty]
CircleSize: 7
[TimingPoints]
0,428.57,4,2,0,50,1,0
[HitObjects]
36,192,0,128,0,1000:0:0:0:0:
109,192,0,128,0,1000:0:0:0:0:
182,192,0,128,0,1000:0:0:0:0:
329,192,0,128,0,1000:0:0:0:0:
402,192,0,128,0,1000:0:0:0:0:
""",
        ),
        BenchmarkItem(
            technique="LN Release",
            tier="1st",
            id=401,
            bpm=140.0,
            content="""osu file format v14
[General]
Mode: 3
[Difficulty]
CircleSize: 7
[TimingPoints]
0,428.57,4,2,0,50,1,0
[HitObjects]
36,192,0,128,0,500:0:0:0:0:
109,192,0,128,0,500:0:0:0:0:
182,192,500,1,0,0:0:0:0:
329,192,500,128,0,1000:0:0:0:0:
""",
        ),
    ]

    gt_file = tmp_path / "ground_truth_test.json"
    report = run_benchmark_pipeline(manifest, ground_truth_output=str(gt_file))

    assert report.summary.total == 4
    assert report.summary.success == 4
    assert report.summary.failed == 0

    # 1. Distillation chapter present in report
    assert report.distillation is not None
    fps = report.distillation["fingerprints"]
    assert len(fps) == 4
    for tech in ["Regular Jack", "Regular Stream", "LN Inverse", "LN Release"]:
        assert tech in fps
        fp = fps[tech]
        assert "centroid_raw" in fp
        assert "centroid_normalized" in fp
        assert "top_features" in fp
        assert len(fp["top_features"]) == 3
        # Assert top features are ranked
        ranks = [f["rank"] for f in fp["top_features"]]
        assert ranks == [1, 2, 3]

    # 2. Separability matrix non-negativity and symmetry
    sep = report.distillation["separability_matrix"]
    matrix = sep["matrix"]
    techs = sep["techniques"]
    assert len(techs) == 4
    for i in range(len(techs)):
        for j in range(len(techs)):
            # Strictly non-negative
            assert matrix[i][j] >= 0.0
            # Strictly symmetric
            assert matrix[i][j] == pytest.approx(matrix[j][i], abs=1e-4)
            if i == j:
                assert matrix[i][j] == 0.0
            else:
                assert matrix[i][j] > 0.0
    assert sep["global_orthogonality_score"] > 0.0

    # 3. Ground truth JSON file exported and valid
    assert gt_file.exists()
    gt_data = json.loads(gt_file.read_text(encoding="utf-8"))
    assert gt_data["version"] == "1.0.0"
    assert "techniques" in gt_data
    assert "separability_matrix" in gt_data
    assert len(gt_data["techniques"]) == 4

    # 4. Full report JSON serialization roundtrip
    report_dict = report.to_dict()
    assert "distillation" in report_dict
    assert report_dict["distillation"]["separability_matrix"] == sep



