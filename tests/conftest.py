"""
Shared fixtures for the frozen benchmark ladder.

`docs/research/structured_index.json` names the chart at each technique x Dan tier;
`tests/fixtures/benchmark_corpus.json.gz` carries those charts' raw `.osu` content. Both live
in the repository, so ladder diagnostics run everywhere — in CI as much as on a developer's
machine — instead of skipping silently wherever no osu! installation exists.
"""

import importlib.util
import json
from pathlib import Path
from typing import Callable, Dict

import pytest

from proj7k.assets import load_corpus_fixture
from proj7k.difficulty import IntrinsicDifficultyResult, evaluate_intrinsic_difficulty
from proj7k.features import BeatmapFeatures, extract_beatmap_features
from proj7k.parser import parse_osu_7k
from proj7k.radar import TechniqueRadar, compute_raw_technique_drivers


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


@pytest.fixture(scope="session")
def benchmark_features(
    benchmark_manifest: Dict[str, Dict[str, dict]],
    benchmark_corpus: Dict[int, str],
) -> Callable[[str, str], BeatmapFeatures]:
    """The feature tensor of one benchmark chart, by technique and tier, extracted once."""
    extracted: Dict[tuple, BeatmapFeatures] = {}

    def _features(technique: str, tier: str) -> BeatmapFeatures:
        key = (technique, tier)
        if key not in extracted:
            content = benchmark_corpus[int(benchmark_manifest[technique][tier]["id"])]
            extracted[key] = extract_beatmap_features(parse_osu_7k(content))
        return extracted[key]

    return _features


@pytest.fixture(scope="session")
def benchmark_drivers(
    benchmark_manifest: Dict[str, Dict[str, dict]],
    benchmark_corpus: Dict[int, str],
) -> Callable[[str, str], Dict[str, float]]:
    """
    The raw 8-technique driver vector of one benchmark chart, by technique and tier.

    The drivers are what the per-technique ladder gates are stated on (`guard`), so the ladder
    diagnostics measure them directly rather than going through the star mapping — which is a
    monotone per-chart rescaling and therefore hides which axis moved.
    """
    computed: Dict[tuple, Dict[str, float]] = {}

    def _drivers(technique: str, tier: str) -> Dict[str, float]:
        key = (technique, tier)
        if key not in computed:
            content = benchmark_corpus[int(benchmark_manifest[technique][tier]["id"])]
            beatmap = parse_osu_7k(content)
            features = extract_beatmap_features(beatmap)
            computed[key] = compute_raw_technique_drivers(beatmap, features=features).to_dict()
        return computed[key]

    return _drivers


@pytest.fixture(scope="session")
def orthogonality_report():
    """
    `tools/radar_orthogonality_report.py`, loaded by path.

    `tools/` is deliberately not a package, so the thresholds and the helpers the CI assertions
    are stated in terms of are imported from the tool itself rather than retyped in the test —
    the ticket's complaint about that file was exactly that its thresholds had no force because
    nothing read them.
    """
    path = REPO_ROOT / "tools" / "radar_orthogonality_report.py"
    spec = importlib.util.spec_from_file_location("radar_orthogonality_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
