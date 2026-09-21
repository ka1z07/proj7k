import json
from pathlib import Path
import pytest
from proj7k.parser import parse_osu_7k, Beatmap7K
from proj7k.features import extract_beatmap_features, BeatmapFeatures
from proj7k.batch import run_benchmark_pipeline, BenchmarkItem
from proj7k.cache import TwoLayerCache, ALGORITHM_VERSION

SAMPLE_OSU = """osu file format v14

[General]
Mode: 3

[Metadata]
Title: Cache Test
Artist: Artist
Creator: Jinjin
Version: Hard

[Difficulty]
CircleSize: 7

[TimingPoints]
0,500,4,2,0,50,1,0

[HitObjects]
36,192,0,1,0,0:0:0:0:
109,192,500,1,0,0:0:0:0:
182,192,1000,1,0,0:0:0:0:
"""


def test_two_layer_cache_ast_and_features(tmp_path: Path):
    cache = TwoLayerCache(cache_dir=tmp_path / ".cache")
    content_hash = cache.compute_content_hash(SAMPLE_OSU)

    # Initially empty
    assert cache.get_ast(content_hash) is None
    assert cache.get_features(content_hash, bpm=120.0) is None

    # Parse and put AST
    bm = parse_osu_7k(SAMPLE_OSU)
    cache.put_ast(content_hash, bm)

    # AST retrieved from cache
    cached_bm = cache.get_ast(content_hash)
    assert cached_bm is not None
    assert cached_bm.title == "Cache Test"
    assert len(cached_bm.hit_objects) == 3

    # Extract and put features
    features = extract_beatmap_features(cached_bm, bpm=120.0)
    cache.put_features(content_hash, bpm=120.0, features=features)

    # Features retrieved from cache
    cached_feat = cache.get_features(content_hash, bpm=120.0)
    assert cached_feat is not None
    assert cached_feat.total_notes == 3
    assert cached_feat.avg_nps == features.avg_nps

    # Different algorithm version invalidates feature cache
    diff_ver_cache = TwoLayerCache(cache_dir=tmp_path / ".cache", algorithm_version="2.0.0")
    # AST remains valid
    assert diff_ver_cache.get_ast(content_hash) is not None
    # Features cache is invalidated by new algorithm version
    assert diff_ver_cache.get_features(content_hash, bpm=120.0) is None


def test_feature_cache_round_trips_every_field_through_disk(tmp_path: Path):
    """
    Layer-2 features must survive the disk round trip whole.

    The in-memory dictionary answers a second lookup from the same instance, so a partial
    reconstruction on the disk path is invisible unless a fresh cache reads the file back.
    That is not a cosmetic defect: the batch pipeline feeds these features straight into the
    engine's star rating, so a field the reconstruction forgets silently changes the rating of
    every chart whose features came from a warm cache.
    """
    # A sparse synthetic chart has zero spatial/rhythm entropy, which would let a lossy
    # reconstruction pass unnoticed; a real 7K chart exercises every field.
    chart = (Path(__file__).resolve().parents[1] / "docs" / "sample_7k.osu").read_text(encoding="utf-8")
    content_hash = TwoLayerCache.compute_content_hash(chart)

    writer = TwoLayerCache(cache_dir=tmp_path / ".cache")
    features = extract_beatmap_features(parse_osu_7k(chart))
    assert features.spatial_entropy > 0.0 and features.rhythm_irreg > 0.0
    writer.put_features(content_hash, bpm=180.0, features=features)

    # A fresh instance has no in-memory entry, so this reads the JSON the writer left behind.
    restored = TwoLayerCache(cache_dir=tmp_path / ".cache").get_features(content_hash, bpm=180.0)

    assert restored is not None
    assert restored.to_dict() == features.to_dict()


def test_pipeline_uses_cache_and_skips_recomputation(tmp_path: Path, monkeypatch):
    cache_dir = tmp_path / "cache_store"
    manifest = [
        BenchmarkItem(
            technique="Regular Jack",
            tier="1st",
            id=101,
            song="Cache Test",
            content=SAMPLE_OSU,
            bpm=120.0,
        )
    ]

    # First run: cache miss, populates cache
    cache = TwoLayerCache(cache_dir=cache_dir)
    rep1 = run_benchmark_pipeline(manifest, cache=cache)
    assert rep1.summary.success == 1
    assert cache.stats["feature_hits"] == 0
    assert cache.stats["feature_misses"] == 1

    # Second run: cache hit on features!
    cache2 = TwoLayerCache(cache_dir=cache_dir)
    rep2 = run_benchmark_pipeline(manifest, cache=cache2)
    assert rep2.summary.success == 1
    assert cache2.stats["feature_hits"] == 1
    assert cache2.stats["feature_misses"] == 0
    assert rep2.results[0].features.total_notes == 3


def test_cache_memory_residency_bounds(tmp_path: Path):
    # Set max_mem_entries to 2 to test eviction
    cache = TwoLayerCache(cache_dir=tmp_path / "lru_cache", max_mem_entries=2)
    bm = parse_osu_7k(SAMPLE_OSU)

    cache.put_ast("hash1", bm)
    cache.put_ast("hash2", bm)
    assert len(cache._ast_mem_cache) == 2

    # Inserting 3rd entry evicts the oldest from memory
    cache.put_ast("hash3", bm)
    assert len(cache._ast_mem_cache) == 2
    assert "hash1" not in cache._ast_mem_cache
    assert "hash2" in cache._ast_mem_cache
    assert "hash3" in cache._ast_mem_cache

    # However, hash1 is still persistent on disk!
    reloaded_bm = cache.get_ast("hash1")
    assert reloaded_bm is not None
    assert reloaded_bm.title == "Cache Test"
