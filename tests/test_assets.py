from pathlib import Path
import json
import pytest
from proj7k.batch import BenchmarkItem, load_manifest
from proj7k.assets import (
    scan_local_asset_library,
    bind_manifest_to_library,
    AssetLibraryIndex,
)


SAMPLE_OSU_WITH_BEATMAP_ID = """osu file format v14

[General]
Mode: 3

[Metadata]
Title: Anemone
Artist: S-C-U feat. Qrispy Joybox
Creator: Jinjin
Version: Another (JJ Edit)
BeatmapID: 3864745
BeatmapSetID: 1900000

[Difficulty]
CircleSize: 7

[TimingPoints]
0,300,4,2,0,50,1,0

[HitObjects]
36,192,0,1,0,0:0:0:0:
"""

SAMPLE_OSU_FILENAME_ID = """osu file format v14

[General]
Mode: 3

[Metadata]
Title: SAMBA LAND
Artist: SAMBA MASTER Satou
Creator: Jinjin
Version: SAMBAJACK

[Difficulty]
CircleSize: 7

[TimingPoints]
0,454.55,4,2,0,50,1,0

[HitObjects]
36,192,0,1,0,0:0:0:0:
"""


def test_scan_local_asset_library(tmp_path: Path):
    # Setup nested library directory structure mimicking osu! Songs folder
    song_dir1 = tmp_path / "1900000 S-C-U - Anemone"
    song_dir1.mkdir(parents=True)
    osu_file1 = song_dir1 / "S-C-U feat. Qrispy Joybox - Anemone [Another].osu"
    osu_file1.write_text(SAMPLE_OSU_WITH_BEATMAP_ID, encoding="utf-8")

    song_dir2 = tmp_path / "1900001 SAMBA MASTER - SAMBA LAND"
    song_dir2.mkdir(parents=True)
    # Filename directly has beatmap ID
    osu_file2 = song_dir2 / "3864746.osu"
    osu_file2.write_text(SAMPLE_OSU_FILENAME_ID, encoding="utf-8")

    index = scan_local_asset_library(tmp_path)
    assert isinstance(index, AssetLibraryIndex)
    assert 3864745 in index.by_id
    assert index.by_id[3864745] == osu_file1
    assert 3864746 in index.by_id
    assert index.by_id[3864746] == osu_file2


def test_bind_manifest_to_library(tmp_path: Path):
    song_dir = tmp_path / "Songs" / "Anemone"
    song_dir.mkdir(parents=True)
    osu_file = song_dir / "anemone.osu"
    osu_file.write_text(SAMPLE_OSU_WITH_BEATMAP_ID, encoding="utf-8")

    items = [
        BenchmarkItem(
            technique="Regular Jack",
            tier="0th",
            id=3864745,
            song="S-C-U feat. Qrispy Joybox - Anemone [Another] (JJ Edit)",
        ),
        BenchmarkItem(
            technique="Regular Jack",
            tier="10th",
            id=9999999,  # Unmatched
            song="Unknown Song",
        ),
    ]

    bound_items = bind_manifest_to_library(items, tmp_path)
    assert bound_items[0].osu_path == str(osu_file)
    assert bound_items[1].osu_path is None


def test_load_manifest_with_library_dir(tmp_path: Path):
    song_dir = tmp_path / "assets"
    song_dir.mkdir()
    osu_file = song_dir / "3864745.osu"
    osu_file.write_text(SAMPLE_OSU_WITH_BEATMAP_ID, encoding="utf-8")

    manifest_dict = {
        "Regular Jack": {
            "0th": {
                "id": 3864745,
                "song": "S-C-U feat. Qrispy Joybox - Anemone [Another] (JJ Edit)",
                "bpm": 200,
                "sr": 3.83649,
            }
        }
    }

    items = load_manifest(manifest_dict, library_dir=tmp_path / "assets")
    assert len(items) == 1
    assert items[0].osu_path == str(osu_file)


def test_scan_local_asset_library_lazer_content_addressed(tmp_path: Path):
    # Setup lazer-style hashed filename (64-char hex, no .osu extension)
    lazer_dir = tmp_path / "files" / "5" / "5a"
    lazer_dir.mkdir(parents=True)
    hash_filename = "5abd6258a0842a67ecf98e8a6babfc29bea4ad021ccdd090124b919b388db0c7"
    lazer_file = lazer_dir / hash_filename
    lazer_file.write_text(SAMPLE_OSU_WITH_BEATMAP_ID, encoding="utf-8")

    index = scan_local_asset_library(tmp_path / "files")
    assert 3864745 in index.by_id
    assert index.by_id[3864745] == lazer_file

