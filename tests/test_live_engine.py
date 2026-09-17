"""
Tests for proj7k.live.engine (Seam 1: LiveEngine).

Verifies intrinsic difficulty evaluation, 8D technique radar formatting,
TwoLayerCache layer 1 & 2 integration, and bounded memory eviction.
"""

from pathlib import Path
import pytest

from proj7k.cache import TwoLayerCache
from proj7k.live.engine import LiveEngine


def _make_dummy_osu_content(title: str = "Test Song") -> str:
    lines = [
        "osu file format v14",
        "[General]",
        "Mode: 3",
        "[Metadata]",
        f"Title:{title}",
        "Artist:Test Artist",
        "Creator:Mapper",
        "Version:Hard",
        "[Difficulty]",
        "CircleSize:7",
        "[TimingPoints]",
        "0,400,4,2,0,50,1,0",
        "[HitObjects]",
    ]
    for i in range(50):
        col_x = [36, 109, 182, 256, 329, 402, 475][i % 7]
        t = i * 100
        lines.append(f"{col_x},192,{t},1,0,0:0:0:0:")
    return "\n".join(lines)


def test_live_engine_analyze_content_returns_contract(tmp_path: Path):
    cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=True)
    engine = LiveEngine(cache=cache)

    content = _make_dummy_osu_content()
    frame = engine.analyze_content(content)

    assert frame["type"] == "beatmap_update"
    assert frame["cached"] is False

    # Check metadata
    meta = frame["metadata"]
    assert meta["title"] == "Test Song"
    assert meta["artist"] == "Test Artist"
    assert meta["creator"] == "Mapper"
    assert meta["version"] == "Hard"
    assert meta["total_notes"] == 50

    # Check star rating and Jinjin Dan tier
    assert "star_rating" in frame
    assert frame["star_rating"] > 0.0
    assert "raw_star_rating" in frame
    assert frame["raw_star_rating"] > 0.0
    assert "dan_tier" in frame
    assert "dan_tier" in meta
    assert isinstance(meta["dan_tier"], str)

    # Check 8-dimension radar
    radar = frame["radar"]
    for tech in ("jack", "tech", "speed", "stream", "ln_general", "ln_tech", "ln_inverse", "ln_release"):
        assert tech in radar
        assert isinstance(radar[tech], (int, float))
    assert "dominant_technique" in radar
    assert "dominant_score" in radar

    # Check StarRatingSynthesis contract
    assert "synthesis" in frame
    synth = frame["synthesis"]
    assert "star_rating" in synth
    assert "uncompressed_rating" in synth
    assert "raw_strain_rating" in synth
    assert "dominant_technique" in synth
    assert "dominant_score" in synth
    assert "synergy_bonus" in synth

    # Verify TwoLayerCache Layer 1 AST and Layer 2 Features were cached
    h = TwoLayerCache.compute_content_hash(content)
    assert cache.get_ast(h) is not None
    assert cache.get_features(h) is not None


def test_live_engine_strain_profile_contract(tmp_path: Path):
    cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=False)
    engine = LiveEngine(cache=cache)

    content = _make_dummy_osu_content("Strain Test")
    frame = engine.analyze_content(content)

    assert "strain_profile" in frame
    strain = frame["strain_profile"]

    # Verify dual hand strain arrays
    assert "left_strains" in strain
    assert "right_strains" in strain
    assert "sample_times_ms" in strain
    assert len(strain["left_strains"]) == len(strain["sample_times_ms"])
    assert len(strain["right_strains"]) == len(strain["sample_times_ms"])
    assert len(strain["sample_times_ms"]) > 0

    # All sample times must be non-negative and monotonically increasing in ms
    assert strain["sample_times_ms"][0] >= 0.0
    for i in range(len(strain["sample_times_ms"]) - 1):
        assert strain["sample_times_ms"][i + 1] > strain["sample_times_ms"][i]

    # Verify P90 baseline and Top 5% threshold
    assert "p90_strain" in strain
    assert "top5_percent_strain" in strain
    assert strain["p90_strain"] >= 0.0
    assert strain["top5_percent_strain"] >= strain["p90_strain"]

    # Verify backward compatibility aliases
    assert "left_hand_strain" in strain
    assert "right_hand_strain" in strain
    assert "combined_strain" in strain
    assert "p95_strain" in strain



def test_live_engine_cache_hit_on_second_call(tmp_path: Path):
    cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=True)
    engine = LiveEngine(cache=cache)

    content = _make_dummy_osu_content(title="Cached Song")
    frame1 = engine.analyze_content(content)
    assert frame1["cached"] is False

    # Second call should hit cache
    frame2 = engine.analyze_content(content)
    assert frame2["cached"] is True
    assert frame2["metadata"]["title"] == "Cached Song"
    assert frame2["star_rating"] == frame1["star_rating"]
    assert frame2["radar"] == frame1["radar"]


def test_live_engine_bounded_memory_eviction(tmp_path: Path):
    cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=False)
    engine = LiveEngine(cache=cache, max_mem_entries=2)

    c1 = _make_dummy_osu_content(title="Song 1")
    c2 = _make_dummy_osu_content(title="Song 2")
    c3 = _make_dummy_osu_content(title="Song 3")

    engine.analyze_content(c1)
    engine.analyze_content(c2)
    assert len(engine._result_cache) == 2

    # Third song should evict Song 1 from memory cache
    engine.analyze_content(c3)
    assert len(engine._result_cache) == 2
    h1 = TwoLayerCache.compute_content_hash(c1)
    assert h1 not in engine._result_cache


def test_live_engine_analyze_file(tmp_path: Path):
    cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=True)
    engine = LiveEngine(cache=cache)

    osu_file = tmp_path / "song.osu"
    osu_file.write_text(_make_dummy_osu_content(title="File Song"), encoding="utf-8")

    frame = engine.analyze_file(osu_file)
    assert frame["type"] == "beatmap_update"
    assert frame["metadata"]["title"] == "File Song"
    assert frame["cached"] is False

    # Second call on same file
    frame2 = engine.analyze_file(osu_file)
    assert frame2["cached"] is True


def test_live_engine_includes_tech_breakdown(tmp_path: Path):
    cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=False)
    engine = LiveEngine(cache=cache)

    content = _make_dummy_osu_content("4D Tech Song")
    frame = engine.analyze_content(content)

    assert "tech_breakdown" in frame
    tech = frame["tech_breakdown"]
    for key in ("tortuosity", "bracket_shear", "spatial_entropy", "rhythm_irreg"):
        assert key in tech
        assert isinstance(tech[key], (int, float))

    # Also verify it is present in metadata for client convenience
    assert "tech_breakdown" in frame["metadata"]
    assert frame["metadata"]["tech_breakdown"] == tech
