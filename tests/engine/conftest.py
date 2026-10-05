"""
Session fixtures for the engine's acceptance tests: the 120 frozen benchmark charts as notes,
and one evaluation of each, shared by every test in this directory.
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from engine_support import POOL_OF, TIERS, Notes
from proj7k.engine import DifficultyProfile, evaluate_notes
from proj7k.engine.events import notes_from_osu

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "engine_golden_v02.json"


@pytest.fixture(scope="session")
def golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def engine_charts(benchmark_manifest, benchmark_corpus) -> Dict[Tuple[str, str], Notes]:
    return {
        (pool, tier): notes_from_osu(benchmark_corpus[int(benchmark_manifest[pool][tier]["id"])])
        for pool in POOL_OF
        for tier in TIERS
    }


@pytest.fixture(scope="session")
def engine_profiles(engine_charts) -> Dict[Tuple[str, str], DifficultyProfile]:
    return {key: evaluate_notes(notes) for key, notes in engine_charts.items()}


@pytest.fixture(scope="session")
def chart_keys() -> List[Tuple[str, str]]:
    return [(pool, tier) for pool in POOL_OF for tier in TIERS]
