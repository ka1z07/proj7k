"""
Notation-scale invariance of the action clock (issue #51, ADR-0006 revision 1).

A chart's timing point states a tempo together with the note value the chart is written in, and
charters do not agree on the latter. Two annotations of one physical chart — the same hit objects,
one written in 1/4 and one in 1/8 — must therefore drive the engine to the same place. These two
tests pin that from both ends: a constructed twin pair, and the whole 120-chart ladder.
"""

import json
import statistics
from pathlib import Path

import pytest

from proj7k.assets import load_corpus_fixture
from proj7k.features import extract_beatmap_features
from proj7k.parser import dominant_bpm, notation_normalized_bpm, observed_note_value, parse_osu_7k
from proj7k.strain import compute_dual_hand_strain

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "docs" / "research" / "structured_index.json"
CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "benchmark_corpus.json.gz"

#: One physical chart: four long notes held from the start, then a single-lane run every 250 ms.
#: The hit objects are identical in both notations below; only the timing point differs.
_TWIN_NOTES = "\n".join(
    [
        "36,192,0,128,0,3000:0:0:0:0:",
        "109,192,0,128,0,3000:0:0:0:0:",
        "329,192,0,128,0,3000:0:0:0:0:",
        "402,192,0,128,0,3000:0:0:0:0:",
    ]
    + [f"256,192,{t},1,0,0:0:0:0:" for t in range(250, 2251, 250)]
)


def _chart_with_beat_length(beat_length_ms: float) -> str:
    return f"""osu file format v14
[General]
Mode: 3
[Metadata]
Title: Notation Scale Twin
Version: Test
[Difficulty]
CircleSize: 7
OverallDifficulty: 8
[TimingPoints]
0,{beat_length_ms},4,2,0,50,1,0
[HitObjects]
{_TWIN_NOTES}
"""


def _median_onset_interval_ms(content: str) -> float:
    """The chart's median gap between distinct onset times, measured without the engine."""
    onsets = sorted({ho.time for ho in parse_osu_7k(content).hit_objects})
    return statistics.median([b - a for a, b in zip(onsets, onsets[1:])])


def test_two_notations_of_one_chart_drive_the_engine_to_the_same_place():
    # 1/16 notation: a 1000 ms beat puts the 250 ms run a quarter of a beat apart.
    sixteenth = _chart_with_beat_length(1000.0)
    # 1/8 notation of the same hit objects: half the beat, twice the note value.
    eighth = _chart_with_beat_length(500.0)

    bm_sixteenth = parse_osu_7k(sixteenth)
    bm_eighth = parse_osu_7k(eighth)

    # The annotations really do disagree — otherwise the test proves nothing.
    assert dominant_bpm(bm_sixteenth) == pytest.approx(60.0)
    assert dominant_bpm(bm_eighth) == pytest.approx(120.0)
    assert observed_note_value(bm_sixteenth) == pytest.approx(0.25)
    assert observed_note_value(bm_eighth) == pytest.approx(0.5)

    # ... and the normalized tempo is the same number for both.
    assert notation_normalized_bpm(bm_sixteenth) == pytest.approx(60.0)
    assert notation_normalized_bpm(bm_eighth) == pytest.approx(60.0)


def test_two_notations_agree_on_the_action_clock_the_inverse_score_and_the_strain():
    sixteenth = _chart_with_beat_length(1000.0)
    eighth = _chart_with_beat_length(500.0)

    feat_sixteenth = extract_beatmap_features(parse_osu_7k(sixteenth))
    feat_eighth = extract_beatmap_features(parse_osu_7k(eighth))

    assert feat_sixteenth.delta_t_action == feat_eighth.delta_t_action
    assert feat_sixteenth.inverse_score == feat_eighth.inverse_score

    strain_sixteenth = compute_dual_hand_strain(parse_osu_7k(sixteenth))
    strain_eighth = compute_dual_hand_strain(parse_osu_7k(eighth))

    assert strain_sixteenth.p90_strain == strain_eighth.p90_strain
    assert strain_sixteenth.peak_strain == strain_eighth.peak_strain


def test_action_clock_is_the_charts_own_note_spacing_across_the_benchmark_ladder():
    """
    The whole point of the normalization: `delta_t_action` describes the chart, not the
    convention it was written in. Before #51 the 120 charts' written note value spanned 1/12 to
    1/1 of their own beat, so a chart annotated in 1/8 read as four times as fast as the
    identical chart annotated in 1/4.

    The tolerance is not zero because the benchmark manifest carries its own `bpm` annotation
    and one chart disagrees with the chart's own tempo (LN Inverse 6th, 78.96 vs 78.85 — see
    `docs/methodology/calibration-sensitivity-and-sandbox.md` §9.3). That single annotation is
    the whole residual.
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    corpus = load_corpus_fixture(CORPUS_PATH)

    ratios = []
    for technique, tiers in manifest.items():
        for tier, entry in tiers.items():
            content = corpus[int(entry["id"])]
            beatmap = parse_osu_7k(content)
            features = extract_beatmap_features(beatmap, bpm=entry.get("bpm"))
            ratios.append(
                (
                    f"{technique}/{tier}",
                    features.delta_t_action / _median_onset_interval_ms(content),
                )
            )

    assert len(ratios) == 120
    worst = max(ratios, key=lambda item: abs(item[1] - 1.0))
    assert worst[1] == pytest.approx(1.0, abs=2e-3), f"worst off-anchor chart: {worst}"
