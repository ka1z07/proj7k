import pytest
from pathlib import Path

from proj7k.downscaler.mapper import (
    TwoTierDanMapper,
    DanTarget,
    CANONICAL_DAN_SR,
    CANONICAL_DAN_TIERS,
    parse_dan_tier,
)


def test_parse_dan_tier():
    assert parse_dan_tier("7th") == "7th"
    assert parse_dan_tier("7th Dan") == "7th"
    assert parse_dan_tier("Dan 7") == "7th"
    assert parse_dan_tier("7") == "7th"
    assert parse_dan_tier("10th") == "10th"
    assert parse_dan_tier("Gamma") == "Gamma"
    assert parse_dan_tier("gamma") == "Gamma"
    assert parse_dan_tier("Azimuth") == "Azimuth"
    assert parse_dan_tier("Zenith") == "Zenith"
    assert parse_dan_tier("Stellium") == "Stellium"
    assert parse_dan_tier("stellium") == "Stellium"
    assert parse_dan_tier("[P-7th]") == "7th"
    assert parse_dan_tier("0th") == "0th"
    assert parse_dan_tier("1st") == "1st"

    with pytest.raises(ValueError, match="Unrecognized Dan tier"):
        parse_dan_tier("InvalidDan99")


def test_target_level_is_the_inverse_of_the_star_scale():
    from proj7k.engine.scale import stars_of

    mapper = TwoTierDanMapper()
    for sr in [3.5, 5.0, 6.1, 7.5, 9.0, 10.5]:
        target = mapper.resolve(target_sr=sr)
        assert stars_of(target.target_D) == pytest.approx(sr, rel=1e-9)
    # an explicit engine level is a star by the engine's own scale
    by_level = mapper.resolve(target_D=target.target_D)
    assert by_level.target_sr == pytest.approx(10.5, rel=1e-9)


def test_legacy_strain_law_inverse_still_round_trips():
    from proj7k.strain import compute_raw_strain_star_rating, star_rating_to_strain

    for sr in [3.5, 6.1, 10.5]:
        assert compute_raw_strain_star_rating(star_rating_to_strain(sr)) == pytest.approx(sr, rel=1e-3)


def test_canonical_dan_tiers_order_and_sr():
    assert len(CANONICAL_DAN_TIERS) == 15
    # Strict monotonicity of star ratings across canonical dan tiers
    srs = [CANONICAL_DAN_SR[tier] for tier in CANONICAL_DAN_TIERS]
    for i in range(len(srs) - 1):
        assert srs[i] < srs[i + 1]


def test_dan_mapper_tier1_lookup():
    mapper = TwoTierDanMapper()
    target = mapper.resolve(target_dan="7th", dominant_skill="jack")

    assert isinstance(target, DanTarget)
    assert target.target_dan == "7th"
    assert target.target_sr == CANONICAL_DAN_SR["7th"]
    assert target.target_D > 0.0
    assert target.dominant_skill == "jack"
    assert "peak_4m_nps" in target.features
    assert "hold_pct" in target.features
    assert target.features["hold_pct"] <= 0.05  # Jack should be low hold


def test_dan_mapper_tier2_continuous_sr_interpolation():
    mapper = TwoTierDanMapper()
    # 6.8 falls between 7th (6.55) and 8th (6.96)
    target = mapper.resolve(target_sr=6.8, dominant_skill="stream")

    assert isinstance(target, DanTarget)
    assert target.target_sr == 6.8
    assert target.target_D > 0.0
    # Should interpolate features between 7th and 8th
    assert "avg_nps" in target.features
    assert target.dominant_skill == "stream"


def test_dan_mapper_boundary_sr():
    mapper = TwoTierDanMapper()
    # Below the lowest dan
    target_low = mapper.resolve(target_sr=2.5)
    assert target_low.target_dan == "0th"
    assert target_low.target_sr == 2.5
    assert target_low.target_D > 0.0

    # Above the highest dan
    target_high = mapper.resolve(target_sr=12.5)
    assert target_high.target_dan == "Stellium"
    assert target_high.target_sr == 12.5


def test_resolve_from_beatmap_reads_the_dominant_skill_from_the_difficulty_engine():
    from proj7k.downscaler.mapper import TwoTierDanMapper
    from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint

    # A two-column fast jack: the engine's dominant skill is jack, which the mapper speaks as "jack".
    hit_objects = [
        HitObject(column=c, time=i * 120.0, note_type=NoteType.RICE) for i in range(80) for c in (0, 2)
    ]
    beatmap = Beatmap7K(
        title="t", artist="a", creator="c", version="v", hit_objects=hit_objects,
        timing_points=[TimingPoint(time=0.0, beat_length=500.0, meter=4, uninherited=True)],
    )

    target = TwoTierDanMapper().resolve_from_beatmap(beatmap, target_dan="7th")

    assert target.dominant_skill == "jack"
