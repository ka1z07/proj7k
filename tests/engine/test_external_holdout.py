"""
External held-out check of the engine (spec v0.2, Q3 acceptance condition 2).

`practice_maps/` holds whole songs the owner labelled `[P-<tier> <skill>]` with the dan slot the song
belongs to. None of them is among the 120 benchmark charts and nothing is fitted on them; they are frozen
into `tests/fixtures/practice_corpus.json.gz` by `tools/export_practice_corpus.py` and only ever read
here, to ask whether the engine's total stars generalise off the benchmark cuts.
"""

import gzip
import importlib.util
import json
import math
from pathlib import Path
from statistics import geometric_mean, median
from typing import Dict, List

import pytest

from engine_support import POOL_OF, SKILLS_LN, TIERS
from proj7k.engine import evaluate_osu

REPO_ROOT = Path(__file__).resolve().parents[2]
PRACTICE_CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "practice_corpus.json.gz"

#: How many of the held-out charts must read within one dan tier of their label, and how many there are.
CHARTS = 19
WITHIN_ONE_TIER = 16

#: The label's skill word against the engine's skill name.
SKILL_OF_LABEL = {
    "jack": "rc_jack", "tech": "rc_tech", "speed": "rc_speed", "stream": "rc_stamina",
    "ln_general": "ln_general", "ln_tech": "ln_tech", "ln_inverse": "ln_inverse", "ln_release": "ln_release",
}


@pytest.fixture(scope="session")
def exporter():
    """`tools/export_practice_corpus.py`, loaded by path (`tools/` is not a package)."""
    path = REPO_ROOT / "tools" / "export_practice_corpus.py"
    spec = importlib.util.spec_from_file_location("export_practice_corpus", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _osu(n_notes: int) -> str:
    """A 7K chart of `n_notes` rice, one every 100 ms, walking across the columns."""
    header = (
        "osu file format v14\n\n[General]\nMode: 3\n\n[Metadata]\nTitle:t\nArtist:a\nVersion:v\n\n"
        "[Difficulty]\nCircleSize:7\nOverallDifficulty:8\n\n[TimingPoints]\n0,500,4,2,1,60,1,0\n\n[HitObjects]\n"
    )
    rows = [f"{int((k % 7 + 0.5) * 512 / 7)},192,{1000 + 100 * k},1,0,0:0:0:0:\n" for k in range(n_notes)]
    return header + "".join(rows)


def test_a_labelled_full_length_chart_is_collected_with_its_tier_skill_and_content(exporter, tmp_path):
    name = "Artist - Song (Mapper) [[P-7th stream] Expert].osu"
    (tmp_path / name).write_text(_osu(300), encoding="utf-8")

    corpus = exporter.collect_practice_corpus(tmp_path)

    assert corpus == {name: {"tier": "7th", "skill": "stream", "osu": _osu(300)}}


def test_a_short_test_fixture_is_left_out_however_it_is_labelled(exporter, tmp_path):
    (tmp_path / "test [[P-7th stream] Expert].osu").write_text(_osu(88), encoding="utf-8")
    (tmp_path / "Artist - Song (Mapper) [[P-7th stream] Expert].osu").write_text(_osu(200), encoding="utf-8")

    corpus = exporter.collect_practice_corpus(tmp_path)

    assert list(corpus) == ["Artist - Song (Mapper) [[P-7th stream] Expert].osu"]


def test_unlabelled_charts_and_packaged_copies_are_ignored(exporter, tmp_path):
    (tmp_path / "Artist - Song (Mapper) [Synthetic Mind Break].osu").write_text(_osu(300), encoding="utf-8")
    (tmp_path / "Artist - Song (Mapper) [[P-7th stream] Expert].osz").write_bytes(b"PK")
    (tmp_path / "audio.mp3").write_bytes(b"\x00")

    assert exporter.collect_practice_corpus(tmp_path) == {}


@pytest.fixture(scope="module")
def practice_corpus() -> Dict[str, Dict[str, str]]:
    with gzip.open(PRACTICE_CORPUS_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def tier_ladders(engine_profiles) -> Dict[str, Dict[str, float]]:
    """The benchmark's stars at each tier, per chart family: RC is the median of the four RC pools,
    LN the geometric mean of the four LN pools."""
    rc_pools = [pool for pool, skill in POOL_OF.items() if skill not in SKILLS_LN]
    ln_pools = [pool for pool, skill in POOL_OF.items() if skill in SKILLS_LN]
    return {
        "rc": {t: median(engine_profiles[(p, t)].total_stars for p in rc_pools) for t in TIERS},
        "ln": {t: geometric_mean([engine_profiles[(p, t)].total_stars for p in ln_pools]) for t in TIERS},
    }


def _tier_read_as(stars: float, ladder: Dict[str, float]) -> str:
    """The tier whose benchmark stars are nearest, by ratio."""
    return min(TIERS, key=lambda t: abs(math.log(ladder[t] / stars)))


def test_the_engine_reads_most_held_out_songs_within_one_tier_of_their_label(practice_corpus, tier_ladders):
    errors: List[int] = []
    for name, chart in practice_corpus.items():
        family = "ln" if SKILL_OF_LABEL[chart["skill"]] in SKILLS_LN else "rc"
        stars = evaluate_osu(chart["osu"]).total_stars
        errors.append(TIERS.index(_tier_read_as(stars, tier_ladders[family])) - TIERS.index(chart["tier"]))

    assert len(errors) == CHARTS
    assert sum(abs(e) <= 1 for e in errors) >= WITHIN_ONE_TIER, errors
