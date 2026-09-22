"""
The frozen benchmark core: the 120-chart ladder evaluated once, up to but not including the
stars.

The engine's rating is a composition of two very different things — everything *upstream* of
the star scale (feature extraction, the dual-hand strain accumulator, the 8 raw technique
drivers) and the arithmetic that carries those numbers onto the star scale. Only the second
half reads the star-scale constants, so the first half can be computed once over the frozen
corpus and frozen, after which a calibration question costs arithmetic alone instead of a
twenty-minute re-ingestion.

That is what makes the calibration workflow cheap, and it is the one place the freeze lives:
`tools/calibration_sandbox.py` evaluates a calibration *forwards* ("what would this calibration
do to the ladder"), `tools/technique_star_fit.py` searches for one *backwards* ("which
constants put the ladder where it belongs"). Both need the same frozen input, and a second copy
of the freeze would be a second thing to keep in step.

The cache is keyed by the digest of the engine source it was built from (`source_digest`), not
by the methodology fingerprint: a sweep exists precisely to move constants the fingerprint
tracks, while the upstream maths has literals the fingerprint does not track at all. Hashing
the source catches both, so a core built before an upstream edit is rebuilt rather than
silently supplying stale numbers.

A record carries what the star scale does *not* touch: the feature tensor's derived quantities
the operators read, the P90 strain, the 8 raw drivers, and the engine's own rating of the chart
for the caller's self-check.
"""

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any, Dict, List

from proj7k.assets import load_corpus_fixture
from proj7k.difficulty import evaluate_intrinsic_difficulty
from proj7k.features import extract_beatmap_features
from proj7k.monotonicity import TIER_ORDER
from proj7k.parser import parse_osu_7k
from proj7k.radar import compute_raw_technique_drivers
from proj7k.strain import compute_dual_hand_strain

#: Repository root, from this file's own location (`src/proj7k/benchmark_core.py`). The frozen
#: corpus and manifest are repository artifacts, not package data: they are the ladder the
#: calibration tools are accountable to, and the calibration is a maintainer's workflow.
REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "docs" / "research" / "structured_index.json"
CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "benchmark_corpus.json.gz"
CACHE_PATH = REPO_ROOT / ".cache" / "proj7k-sandbox" / "core.pkl"


def source_digest() -> str:
    """Digest of every engine module's source — the frozen core's cache key."""
    digest = hashlib.sha256()
    for path in sorted((REPO_ROOT / "src" / "proj7k").rglob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_core(force: bool = False) -> List[Dict[str, Any]]:
    """
    Evaluates every benchmark chart in full and freezes everything upstream of the stars.

    Each record is `{technique, tier, song, p90, drivers, reference_star}`: the two quantities
    the star mapping consumes (`p90` for the strain anchor law, `drivers` for the technique
    anchors), the ladder coordinates, and the engine's own rating of the chart as a reference.
    """
    digest = source_digest()
    if CACHE_PATH.exists() and not force:
        cached = pickle.loads(CACHE_PATH.read_bytes())
        if cached.get("digest") == digest:
            return cached["records"]

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    corpus = load_corpus_fixture(CORPUS_PATH)

    records: List[Dict[str, Any]] = []
    for technique, tiers in manifest.items():
        for tier, entry in tiers.items():
            content = corpus[int(entry["id"])]
            beatmap = parse_osu_7k(content)
            features = extract_beatmap_features(beatmap)
            strain = compute_dual_hand_strain(beatmap)
            records.append(
                {
                    "technique": technique,
                    "tier": tier,
                    "song": entry["song"],
                    "p90": strain.p90_strain,
                    "drivers": compute_raw_technique_drivers(
                        beatmap, features=features
                    ).to_dict(),
                    "reference_star": evaluate_intrinsic_difficulty(content).star_rating,
                }
            )

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_bytes(pickle.dumps({"digest": digest, "records": records}))
    return records


def ladder_records(records: List[Dict[str, Any]], group: str) -> List[Dict[str, Any]]:
    """One technique's own 15-tier ladder, in canonical tier order."""
    rows = [r for r in records if r["technique"] == group]
    rows.sort(key=lambda r: TIER_ORDER.index(r["tier"]))
    return rows
