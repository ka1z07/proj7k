import pytest
from pathlib import Path

from proj7k.downscaler.mapper import (
    TwoTierDanMapper,
    DanTarget,
    CANONICAL_DAN_SR,
    CANONICAL_DAN_TIERS,
    parse_dan_tier,
    star_rating_to_strain,
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


def test_star_rating_to_strain_roundtrip():
    # SR_raw = a * S^0.65 + b with a=0.268980, b=0.129915
    from proj7k.strain import compute_raw_strain_star_rating

    for sr in [3.5, 5.0, 6.1, 7.5, 9.0, 10.5]:
        s = star_rating_to_strain(sr)
        sr_recomputed = compute_raw_strain_star_rating(s)
        assert pytest.approx(sr, rel=1e-3) == sr_recomputed


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
    assert pytest.approx(target.target_sr, abs=0.2) == 6.1
    assert target.target_strain > 0.0
    assert target.dominant_skill == "jack"
    assert "peak_4m_nps" in target.features
    assert "hold_pct" in target.features
    assert target.features["hold_pct"] <= 0.05  # Jack should be low hold


def test_dan_mapper_tier2_continuous_sr_interpolation():
    mapper = TwoTierDanMapper()
    # 6.3 falls between 7th (6.1) and 8th (6.5)
    target = mapper.resolve(target_sr=6.3, dominant_skill="stream")

    assert isinstance(target, DanTarget)
    assert target.target_sr == 6.3
    assert target.target_strain > 0.0
    # Should interpolate features between 7th and 8th
    assert "avg_nps" in target.features
    assert target.dominant_skill == "stream"


def test_dan_mapper_boundary_sr():
    mapper = TwoTierDanMapper()
    # Below lowest dan (3.2)
    target_low = mapper.resolve(target_sr=2.5)
    assert target_low.target_dan == "0th"
    assert target_low.target_sr == 2.5
    assert target_low.target_strain > 0.0

    # Above highest dan (10.5)
    target_high = mapper.resolve(target_sr=11.5)
    assert target_high.target_dan == "Stellium"
    assert target_high.target_sr == 11.5
