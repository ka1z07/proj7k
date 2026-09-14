from pathlib import Path
import pytest
from proj7k.batch import (
    run_benchmark_pipeline,
    BenchmarkItem,
    main as batch_cli_main,
)
from proj7k.cache import TwoLayerCache

SAMPLE_OSU_TEMPLATE = """osu file format v14

[General]
Mode: 3

[Metadata]
Title: Song {idx}
Artist: Artist
Creator: Jinjin
Version: Tier {idx}

[Difficulty]
CircleSize: 7

[TimingPoints]
0,500,4,2,0,50,1,0

[HitObjects]
36,192,0,1,0,0:0:0:0:
109,192,500,1,0,0:0:0:0:
182,192,1000,1,0,0:0:0:0:
"""


def test_parallel_batch_execution(tmp_path: Path):
    manifest = [
        BenchmarkItem(
            technique="Regular Jack",
            tier=f"{i}st",
            id=1000 + i,
            song=f"Song {i}",
            content=SAMPLE_OSU_TEMPLATE.format(idx=i),
            bpm=120.0,
        )
        for i in range(1, 9)
    ]

    cache_dir = tmp_path / "parallel_cache"
    # Execute with 2 workers
    report = run_benchmark_pipeline(
        manifest,
        workers=2,
        cache_dir=cache_dir,
    )

    assert report.summary.total == 8
    assert report.summary.success == 8
    assert report.summary.failed == 0
    assert len(report.results) == 8
    assert report.feature_checksum is not None

    # Results order should remain consistent with manifest input order
    for i, res in enumerate(report.results):
        assert res.tier == f"{i+1}st"
        assert res.id == 1000 + (i + 1)
        assert res.status == "SUCCESS"
        assert res.features is not None
        assert res.features.total_notes == 3


def test_parallel_batch_cli(tmp_path: Path):
    manifest_file = tmp_path / "parallel_manifest.json"
    items_json = [
        {
            "technique": "Regular Jack",
            "tier": "1st",
            "id": 101,
            "content": SAMPLE_OSU_TEMPLATE.format(idx=1),
        },
        {
            "technique": "Regular Jack",
            "tier": "2nd",
            "id": 102,
            "content": SAMPLE_OSU_TEMPLATE.format(idx=2),
        },
    ]
    manifest_file.write_text(__import__("json").dumps(items_json), encoding="utf-8")
    out_file = tmp_path / "out.json"

    exit_code = batch_cli_main([
        "--manifest", str(manifest_file),
        "--output", str(out_file),
        "--workers", "2",
    ])
    assert exit_code == 0
    assert out_file.exists()
