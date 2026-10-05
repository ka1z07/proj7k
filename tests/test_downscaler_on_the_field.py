"""The downscaler on the engine's difficulty field (ADR-0021)."""

import dataclasses
import random

import numpy as np
import pytest
from scipy.stats import spearmanr

from proj7k.downscaler import DownscaleOptions, downscale_beatmap, removal_benefit
from proj7k.downscaler.mutation import apply_pure_deletion
from proj7k.downscaler.validator import DOMINANCE_TIE, DualGateValidator
from proj7k.engine.solver import loss
from proj7k.field import trace_beatmap
from proj7k.parser import dump_osu_7k, parse_osu_7k

CASES = [("Regular Stream", "Zenith", "7th"), ("Regular Jack", "Gamma", "5th"), ("LN Tech", "Gamma", "7th"), ("Regular Tech", "Azimuth", "8th")]


def _chart(manifest, corpus, pool, tier):
    return parse_osu_7k(corpus[int(manifest[pool][tier]["id"])])


def _note_benefit(bm, field, theta):
    b = removal_benefit(field, theta)
    out = []
    for ho in bm.hit_objects:
        i = field.press_index(ho.column, ho.time / 1000.0)
        r = field.release_index(i) if i is not None else None
        out.append((b[i] if i is not None else 0.0) + (b[r] if r is not None else 0.0))
    return np.array(out)


@pytest.mark.parametrize("pool,tier", [("Regular Stream", "8th"), ("Regular Jack", "8th"), ("LN Tech", "Gamma")])
def test_removal_benefit_ranks_notes_by_what_deleting_them_actually_buys(benchmark_manifest, benchmark_corpus, pool, tier):
    bm = _chart(benchmark_manifest, benchmark_corpus, pool, tier)
    f = trace_beatmap(bm)
    theta = f.total_D * 0.85
    L0 = loss(f.d, theta, f.params).sum()
    nb = _note_benefit(bm, f, theta)

    def drop(ks):
        g = trace_beatmap(apply_pure_deletion(bm, notes_to_remove=[int(k) for k in ks]))
        return L0 - loss(g.d, theta, g.params).sum()

    rnd = random.Random(5)
    sample = rnd.sample(range(len(nb)), 40)
    assert spearmanr([drop([k]) for k in sample], nb[sample])[0] > 0.6
    # the notes it ranks first are worth clearly more than the same number picked blindly
    assert drop(np.argsort(-nb)[:40]) > 1.5 * drop(rnd.sample(range(len(nb)), 40))
    assert np.all(removal_benefit(f, theta) >= 0)


@pytest.mark.parametrize("pool,tier,target", CASES)
def test_the_loop_lands_the_engines_star_on_the_target(benchmark_manifest, benchmark_corpus, pool, tier, target):
    bm = _chart(benchmark_manifest, benchmark_corpus, pool, tier)

    res = downscale_beatmap(bm, DownscaleOptions(target_dan=target))

    t = res.target.target_sr
    assert res.pruning_result.converged
    assert t * 0.9 <= res.downscaled_stars <= t * 1.05
    assert res.downscaled_stars == pytest.approx(trace_beatmap(res.downscaled_beatmap).profile.total_stars)
    assert res.validation.passed and res.validation.cosine_similarity >= 0.8
    assert 0 < res.notes_removed < 0.5 * len(bm.hit_objects)


def test_a_dominance_tie_is_not_a_change_of_technique(benchmark_manifest, benchmark_corpus):
    bm = _chart(benchmark_manifest, benchmark_corpus, "Regular Jack", "Gamma")
    original = DualGateValidator().profile_of(bm)
    skills = dict(original.skills)
    top = original.dominant_skill
    other = next(k for k in skills if k != top and k.startswith("rc"))

    def with_top(name, tie_gap):
        shares = {k: 0.0 for k in skills}
        shares[name] = 0.5
        shares[top if name != top else other] = 0.5 - tie_gap
        return dataclasses.replace(
            original, dominant_skill=name,
            skills={k: dataclasses.replace(v, dominance=shares[k]) for k, v in skills.items()},
        )

    class Fake(DualGateValidator):
        def __init__(self, replacement):
            super().__init__()
            self.replacement = replacement

        def profile_of(self, beatmap):
            return original if beatmap is bm else self.replacement

    copy_of_bm = parse_osu_7k(dump_osu_7k(bm))
    tied = Fake(with_top(other, DOMINANCE_TIE / 2)).validate(bm, copy_of_bm)
    shifted = Fake(with_top(other, DOMINANCE_TIE * 4)).validate(bm, copy_of_bm)

    assert tied.dominant_conserved is True
    assert shifted.dominant_conserved is False and shifted.gate1_passed is False


def test_cli_accepts_an_engine_level_as_the_target(benchmark_manifest, benchmark_corpus, tmp_path, capsys):
    from proj7k.downscaler.cli import main
    from proj7k.engine.scale import stars_of
    from proj7k.field import d_of_stars

    osu = tmp_path / "c.osu"
    osu.write_text(benchmark_corpus[int(benchmark_manifest["Regular Jack"]["Gamma"]["id"])], encoding="utf-8")

    assert main(["-i", str(osu), "--target-d", str(d_of_stars(6.0)), "--dry-run", "--json"]) == 0
    import json
    report = json.loads(capsys.readouterr().out)["results"][0]["report"]
    assert report["target"]["target_sr"] == pytest.approx(6.0, abs=1e-6)
    assert report["downscaled_star_rating"] <= 6.0 * 1.05
    assert main(["-i", str(osu)]) == 1                                     # a target is required
