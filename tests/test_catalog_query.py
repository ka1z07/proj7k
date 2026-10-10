"""The catalog's osu!-style search syntax (ADR-0026)."""

import pytest

from proj7k.catalog.query import parse_query


def test_free_words_are_casefolded_and_quotes_keep_phrases():
    q = parse_query('Camellia "Black Lotus"')
    assert q.words == ["camellia", "black lotus"]
    assert q.conditions == []


@pytest.mark.parametrize("alias", ["stars", "sr", "official", "STARS"])
def test_official_star_aliases(alias):
    q = parse_query(f"{alias}>5")
    assert q.conditions == [("b.official_sr > ?", (5.0,))]


def test_engine_and_delta_keys():
    assert parse_query("engine>=7").conditions == [("b.engine_sr >= ?", (7.0,))]
    assert parse_query("esr<3").conditions == [("b.engine_sr < ?", (3.0,))]
    assert parse_query("delta>1").conditions == [("(b.engine_sr - b.official_sr) > ?", (1.0,))]


def test_equality_matches_the_written_precision():
    (sql, (lo, hi)), = parse_query("stars=5").conditions
    assert (lo, hi) == (5.0, 6.0)
    (sql, (lo, hi)), = parse_query("engine=6.5").conditions
    assert lo == 6.5 and hi == pytest.approx(6.6)


def test_length_accepts_minutes_and_seconds():
    assert parse_query("length<2:30").conditions == [("b.length_s < ?", (150.0,))]
    assert parse_query("length>3m").conditions == [("b.length_s > ?", (180.0,))]
    assert parse_query("length<=90s").conditions == [("b.length_s <= ?", (90.0,))]


def test_dan_compares_on_the_ladder():
    assert parse_query("dan>=5th").conditions == [("b.dan_index >= ?", (5,))]
    assert parse_query("dan=gamma").conditions == [("b.dan_index = ?", (11,))]


@pytest.mark.parametrize("raw, skill", [("ln_inverse", "ln_inverse"), ("inverse", "ln_inverse"),
                                        ("jack", "rc_jack"), ("stream", "rc_stamina"), ("rc_tech", "rc_tech")])
def test_skill_aliases(raw, skill):
    assert parse_query(f"skill={raw}").conditions == [("b.dominant_skill = ?", (skill,))]


def test_text_filters_are_substrings():
    assert parse_query('creator="Komeiji Dove"').conditions == [("instr(s.creator_search, ?) > 0", ("komeiji dove",))]


def test_unknown_or_malformed_filters_fall_back_to_words():
    q = parse_query("foo=bar stars>abc status=nope")
    assert q.conditions == []
    assert q.words == ["foo=bar", "stars>abc", "status=nope"]


def test_where_combines_words_and_filters():
    sql, params = parse_query("lotus engine>9").where()
    assert "instr(s.search_text, ?)" in sql and "b.engine_sr > ?" in sql
    assert params == ("lotus", "lotus", 9.0)
    assert parse_query("").where() == ("1", ())
