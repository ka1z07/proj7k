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
    assert f_release.antiphase_count == 4
    assert f_release.antiphase_rate == 2.0

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

