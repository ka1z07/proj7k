"""
Tests for proj7k.live.engine (Seam 1: LiveEngine).

Verifies intrinsic difficulty evaluation, 8D technique radar formatting,
TwoLayerCache layer 1 & 2 integration, and bounded memory eviction.
"""

from pathlib import Path
import pytest

from proj7k.cache import TwoLayerCache
from proj7k.dan import estimate_canonical_dan
from proj7k.field import trace_osu
from proj7k.live.engine import TIMELINE_BIN_S, LiveEngine


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

    # Stars, tier, radar and timeline all come from the difficulty engine; nothing in the frame is the legacy engine's.
    profile = frame["profile"]
    assert profile["total"]["stars"] == frame["star_rating"]
    assert set(profile["skills"]) == {
        "rc_jack", "rc_tech", "rc_speed", "rc_stamina", "ln_general", "ln_tech", "ln_inverse", "ln_release",
    }
    assert frame["dan_tier"] == estimate_canonical_dan(frame["star_rating"])
    assert radar["dominant_technique"] in {"jack", "tech", "speed", "stream", "ln_general", "ln_tech", "ln_inverse", "ln_release"}
    assert radar["dominant_score"] == radar[radar["dominant_technique"]]
    assert "legacy" not in frame

    # Verify TwoLayerCache Layer 1 AST and Layer 2 Features were cached
    h = TwoLayerCache.compute_content_hash(content)
    assert cache.get_ast(h) is not None
    assert cache.get_features(h) is not None


def test_live_engine_timeline_contract(tmp_path: Path):
    cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=False)
    engine = LiveEngine(cache=cache)

    content = _make_dummy_osu_content("Timeline Test")
    frame = engine.analyze_content(content)

    tl = frame["timeline"]
    # the difficulty field's curve in chart time from 0, one bin per TIMELINE_BIN_S
    assert tl["start_s"] == 0.0
    assert tl["bin_s"] == TIMELINE_BIN_S
    bins = len(tl["load"])
    assert bins > 0
    assert len(tl["risk"]) == len(tl["skill"]) == len(tl["events"]) == bins
    assert tl["skills"] == ["jack", "tech", "speed", "stream", "ln_general", "ln_tech", "ln_inverse", "ln_release"]
    assert all(-1 <= k < len(tl["skills"]) for k in tl["skill"])
    assert all(x >= 0.0 for x in tl["load"]) and all(x >= 0.0 for x in tl["risk"])
    # the bins hold every event
    assert sum(tl["events"]) > 0

    # it is the field's own curve, with the same profile the frame reports
    field = trace_osu(content)
    assert field.profile.total_stars == frame["star_rating"]
    assert tl["load"] == field.curve(bin_s=TIMELINE_BIN_S, start_s=0.0).to_dict()["load"]


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
