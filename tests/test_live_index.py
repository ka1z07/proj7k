"""
Tests for proj7k.live.index (Seam 2: LazerRealmIndex).

Verifies in-memory inverted index construction, suffix stripping,
O(1) dictionary lookup, fuzzy fallback matching, and physical file resolution.
"""

from pathlib import Path
import time
import pytest

from proj7k.lazer.bridge import LazerBeatmapRecord
from proj7k.live.index import LazerRealmIndex


def _make_dummy_record(
    id: str,
    title: str,
    artist: str,
    diff: str,
    file_hash: str = "abc12345",
) -> LazerBeatmapRecord:
    return LazerBeatmapRecord(
        id=id,
        hash=file_hash,
        md5_hash=file_hash,
        file_hash=file_hash,
        star_rating=5.0,
        difficulty_name=diff,
        tags="",
        title=title,
        artist=artist,
        ruleset_id=3,
        circle_size=7.0,
    )


def test_exact_lookup_and_suffix_cleaning(tmp_path: Path):
    r1 = _make_dummy_record("1", "crystallized", "Camellia", "7K Hyper")
    r2 = _make_dummy_record("2", "C18H27NO3", "Team Grimoire", "14 (10.74★ Tech)")
    r3 = _make_dummy_record("3", "FREEDOM DiVE", "xi", "[FOUR DIMENSIONS]")

    index = LazerRealmIndex(records=[r1, r2, r3], files_dir=tmp_path)
    index.warmup()

    # Exact lookup with pristine diff name
    found1 = index.lookup(title="crystallized", difficulty="7K Hyper", artist="Camellia")
    assert found1 is not None
    assert found1.id == "1"

    # Query with injected suffix in query or in record
    found2 = index.lookup(title="C18H27NO3", difficulty="14 (10.74★ Tech)", artist="Team Grimoire")
    assert found2 is not None
    assert found2.id == "2"

    found2_clean_query = index.lookup(title="C18H27NO3", difficulty="14", artist="Team Grimoire")
    assert found2_clean_query is not None
    assert found2_clean_query.id == "2"

    # Case insensitivity and whitespace trimming
    found3 = index.lookup(title=" freedom dive ", difficulty="[four dimensions]", artist="xi")
    assert found3 is not None
    assert found3.id == "3"


def test_warmup_performance_10k_records(tmp_path: Path):
    # Benchmark building index with 10,000 records
    records = [
        _make_dummy_record(
            id=str(i),
            title=f"Song Title {i}",
            artist=f"Artist {i % 100}",
            diff=f"Difficulty {i % 5} (7.50★ Stream)",
            file_hash=f"hash{i:06d}",
        )
        for i in range(10000)
    ]

    index = LazerRealmIndex(records=records, files_dir=tmp_path)

    t0 = time.perf_counter()
    count = index.warmup()
    t_elapsed = time.perf_counter() - t0

    assert count == 10000
    assert t_elapsed < 0.5, f"Warmup took too long: {t_elapsed:.4f}s >= 0.5s"

    # O(1) lookup
    t_look_0 = time.perf_counter()
    found = index.lookup(title="Song Title 5432", difficulty="Difficulty 2")
    t_look_elapsed = time.perf_counter() - t_look_0

    assert found is not None
    assert found.id == "5432"
    assert t_look_elapsed < 0.001, f"Lookup took too long: {t_look_elapsed:.6f}s"


def test_fuzzy_fallback_lookup(tmp_path: Path):
    r1 = _make_dummy_record("1", "crystallized", "Camellia", "7K Hyper")
    r2 = _make_dummy_record("2", "FREEDOM DiVE -Another-", "xi", "FOUR DIMENSIONS")

    index = LazerRealmIndex(records=[r1, r2], files_dir=tmp_path)
    index.warmup()

    # Exact lookup fails due to minor typo "7K Hypr"
    exact_miss = index.lookup_exact("crystallized", "7K Hypr")
    assert exact_miss is None

    # Full lookup with fallback should find it
    fuzzy_hit = index.lookup(title="crystallized", difficulty="7K Hypr", artist="Camellia")
    assert fuzzy_hit is not None
    assert fuzzy_hit.id == "1"

    # Query with omitted suffix in title
    fuzzy_hit2 = index.lookup(title="FREEDOM DiVE", difficulty="FOUR DIMENSIONS", artist="xi")
    assert fuzzy_hit2 is not None
    assert fuzzy_hit2.id == "2"

    # Completely non-matching query returns None
    assert index.lookup(title="NonExistent", difficulty="Unknown", artist="Nobody") is None


def test_resolve_beatmap_file(tmp_path: Path):
    files_dir = tmp_path / "files"
    files_dir.mkdir()

    # Create sharded file: files/a/ab/abcdef123456
    file_hash = "abcdef123456"
    shard_dir = files_dir / file_hash[:1] / file_hash[:2]
    shard_dir.mkdir(parents=True)
    osu_file = shard_dir / file_hash
    osu_file.write_text("osu file content", encoding="utf-8")

    rec = _make_dummy_record("1", "Song", "Artist", "Hard", file_hash=file_hash)
    index = LazerRealmIndex(records=[rec], files_dir=files_dir)

    resolved = index.resolve_file(rec)
    assert resolved is not None
    assert resolved == osu_file
    assert resolved.exists()

    # Non-existent file
    missing_rec = _make_dummy_record("2", "Song 2", "Artist 2", "Insane", file_hash="missinghash99")
    assert index.resolve_file(missing_rec) is None
