"""
Shared fixtures for the frozen benchmark ladder.

`docs/research/structured_index.json` names the chart at each technique x Dan tier;
`tests/fixtures/benchmark_corpus.json.gz` carries those charts' raw `.osu` content. Both live
in the repository, so ladder diagnostics run everywhere — in CI as much as on a developer's
machine — instead of skipping silently wherever no osu! installation exists.
"""

import json
from pathlib import Path
from typing import Callable, Dict

import pytest

from proj7k.assets import load_corpus_fixture
from proj7k.difficulty import IntrinsicDifficultyResult, evaluate_intrinsic_difficulty
from proj7k.radar import TechniqueRadar


REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_MANIFEST_PATH = REPO_ROOT / "docs" / "research" / "structured_index.json"
BENCHMARK_CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "benchmark_corpus.json.gz"


@pytest.fixture(scope="session")
def benchmark_manifest_path() -> Path:
    return BENCHMARK_MANIFEST_PATH


@pytest.fixture(scope="session")
def benchmark_corpus_path() -> Path:
    return BENCHMARK_CORPUS_PATH


@pytest.fixture(scope="session")
def benchmark_manifest() -> Dict[str, Dict[str, dict]]:
    return json.loads(BENCHMARK_MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def benchmark_corpus() -> Dict[int, str]:
    return load_corpus_fixture(BENCHMARK_CORPUS_PATH)


@pytest.fixture(scope="session")
def benchmark_ladder(
    benchmark_manifest: Dict[str, Dict[str, dict]],
) -> Callable[[str, str], IntrinsicDifficultyResult]:
    """
    Evaluates one benchmark chart by technique and tier, memoized for the whole session so the
    diagnostics scattered across test modules share one evaluation per chart.
    """
    corpus = load_corpus_fixture(BENCHMARK_CORPUS_PATH)
    evaluated: Dict[tuple, IntrinsicDifficultyResult] = {}

    def _evaluate(technique: str, tier: str) -> IntrinsicDifficultyResult:
        key = (technique, tier)
        if key not in evaluated:
            entry = benchmark_manifest[technique][tier]
            content = corpus.get(int(entry["id"]))
            assert content is not None, (
                f"{technique} {tier} ({entry['song']}) is missing from the frozen corpus — "
                f"regenerate it with tools/export_benchmark_corpus.py"
            )
            evaluated[key] = evaluate_intrinsic_difficulty(content)
        return evaluated[key]

    return _evaluate


@pytest.fixture(scope="session")
def benchmark_radar(
    benchmark_ladder: Callable[[str, str], IntrinsicDifficultyResult],
) -> Callable[[str, str], TechniqueRadar]:
    """The 8-dimension radar of one benchmark chart, by technique and tier."""
    return lambda technique, tier: benchmark_ladder(technique, tier).radar
