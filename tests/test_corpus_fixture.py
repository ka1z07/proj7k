import gzip
import json
from pathlib import Path
import pytest

from proj7k.assets import (
    bind_manifest_to_corpus,
    load_corpus_fixture,
)
from proj7k.batch import BenchmarkItem, load_manifest


OSU_A = "osu file format v14\n[Metadata]\nBeatmapID:11\n\n[HitObjects]\n36,192,0,1,0,0:0:0:0:\n"
OSU_B = "osu file format v14\n[Metadata]\nBeatmapID:22\n\n[HitObjects]\n109,192,0,1,0,0:0:0:0:\n"


def _write_corpus(path: Path, corpus: dict) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(corpus, f)
    return path


def test_load_corpus_fixture_keys_by_beatmap_id(tmp_path: Path):
    corpus_path = _write_corpus(tmp_path / "corpus.json.gz", {"11": OSU_A, "22": OSU_B})

    corpus = load_corpus_fixture(corpus_path)

    assert corpus == {11: OSU_A, 22: OSU_B}
    assert all(isinstance(k, int) for k in corpus)


def test_bind_manifest_to_corpus_fills_content_for_matching_ids():
    items = [
        BenchmarkItem(technique="Regular Jack", tier="0th", id=11),
        BenchmarkItem(technique="Regular Jack", tier="1st", id=22),
    ]

    bound = bind_manifest_to_corpus(items, {11: OSU_A, 22: OSU_B})

    assert [i.content for i in bound] == [OSU_A, OSU_B]


def test_bind_manifest_to_corpus_leaves_unknown_ids_unbound():
    """
    A chart missing from the frozen corpus must stay unbound rather than pick up a
    neighbour's notes: the batch pipeline turns such an item into a FAILED_INGESTION
    result, which the guard already blocks on.
    """
    items = [
        BenchmarkItem(technique="Regular Jack", tier="0th", id=11),
        BenchmarkItem(technique="Regular Jack", tier="1st", id=999),
    ]

    bound = bind_manifest_to_corpus(items, {11: OSU_A})

    assert bound[0].content == OSU_A
    assert bound[1].content is None


def test_bind_manifest_to_corpus_preserves_explicit_content():
    items = [BenchmarkItem(technique="Regular Jack", tier="0th", id=11, content=OSU_B)]

    bound = bind_manifest_to_corpus(items, {11: OSU_A})

    assert bound[0].content == OSU_B


def test_checked_in_corpus_covers_the_benchmark_manifest_exactly(
    benchmark_corpus_path, benchmark_manifest_path
):
    """
    The frozen corpus and the benchmark manifest are the two halves of the same ground truth:
    every manifest chart must resolve to raw `.osu` content, and the corpus must carry nothing
    else. A drift between them would leave the ladder gates validating fewer charts than the
    ladder actually claims.
    """
    manifest_ids = {
        meta["id"]
        for tiers in json.loads(benchmark_manifest_path.read_text(encoding="utf-8")).values()
        for meta in tiers.values()
    }

    corpus = load_corpus_fixture(benchmark_corpus_path)

    assert len(manifest_ids) == 120
    assert set(corpus) == manifest_ids
    assert all(content.startswith("osu file format") for content in corpus.values())


def test_load_manifest_with_corpus_path(tmp_path: Path):
    corpus_path = _write_corpus(tmp_path / "corpus.json.gz", {"11": OSU_A})
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"Regular Jack": {"0th": {"id": 11, "song": "A - B [C]"}}}),
        encoding="utf-8",
    )

    items = load_manifest(manifest_path, corpus=corpus_path)

    assert len(items) == 1
    assert items[0].content == OSU_A
    assert items[0].technique == "Regular Jack"
    assert items[0].tier == "0th"
