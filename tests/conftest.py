"""
Shared fixtures for the frozen benchmark ladder.

`docs/research/structured_index.json` names the chart at each technique x Dan tier;
`tests/fixtures/benchmark_corpus.json.gz` carries those charts' raw `.osu` content. Both live
in the repository, so ladder diagnostics run everywhere — in CI as much as on a developer's
machine — instead of skipping silently wherever no osu! installation exists.
"""

import json
from pathlib import Path
from typing import Dict

import pytest

from proj7k.assets import load_corpus_fixture


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
