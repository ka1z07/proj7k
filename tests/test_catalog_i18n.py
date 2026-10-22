"""The catalog website's languages (ADR-0038): every Chinese string a page shows has English and Traditional Chinese.

Pages are written in Simplified Chinese. Script text goes through `tr("中文 {x}", {x})`, static HTML text and the
title / placeholder / description attributes are looked up when the page loads, and a mapper's style tags carry
their Chinese templates (`mappers.TAG_TEXT`). These tests fail on Chinese the dictionaries would leave untranslated.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from proj7k.catalog.mappers import TAG_TEXT
from test_dashboard_i18n import CJK, PLACEHOLDER, _Page, js_strings

STATIC = Path(__file__).resolve().parents[1] / "src" / "proj7k" / "catalog" / "static"
SIMPLIFIED_ONLY = re.compile("[谱难级键条师组库语简选设页这么们个为说开关应换显图来对则还数进实时]")


def dictionary(name: str) -> dict:
    text = (STATIC / "i18n.js").read_text(encoding="utf-8")
    body = text.split(f"/* {name}-BEGIN */", 1)[1].split(f"/* {name}-END */", 1)[0]
    return json.loads(body)


def pages():
    return sorted(STATIC.glob("*.html"))


def page_texts(path: Path):
    p = _Page()
    p.feed(path.read_text(encoding="utf-8"))
    texts = list(p.texts)
    texts += [" ".join(m.split()) for m in re.findall(r'<meta name="description" content="([^"]*)"', path.read_text(encoding="utf-8"))]
    texts += [" ".join(m.split()) for m in re.findall(r"<title>([^<]*)</title>", path.read_text(encoding="utf-8"))]
    return texts, p.scripts


def script_literals():
    yield "catalog.js", (STATIC / "catalog.js").read_text(encoding="utf-8")
    for path in pages():
        for k, s in enumerate(page_texts(path)[1]):
            yield f"{path.name}<script {k}>", s


def wanted_keys() -> set:
    keys = set()
    for path in pages():
        keys |= {t for t in page_texts(path)[0] if CJK.search(t)}
    for _, source in script_literals():
        for literal, kind, before in js_strings(source):
            if CJK.search(literal) and kind != "`" and re.search(r"(?<![\w$.])tr\(\s*$", before):
                keys.add(json.loads(f'"{literal}"') if kind == '"' else literal)
    for label, why in TAG_TEXT.values():
        keys |= {label, why}
    return keys


def test_both_dictionaries_cover_every_key_and_keep_placeholders():
    en, hant = dictionary("EN"), dictionary("HANT")
    assert set(en) == set(hant)
    missing = sorted(wanted_keys() - set(en))
    assert not missing, missing
    for zh in en:
        assert CJK.search(zh), zh
        assert not CJK.search(en[zh]), (zh, en[zh])
        assert set(PLACEHOLDER.findall(zh)) == set(PLACEHOLDER.findall(en[zh])) == set(PLACEHOLDER.findall(hant[zh])), zh
        assert not SIMPLIFIED_ONLY.search(hant[zh]), (zh, hant[zh])


def test_script_chinese_goes_through_tr():
    problems = []
    for name, source in script_literals():
        for literal, kind, before in js_strings(source):
            if not CJK.search(literal):
                continue
            if kind == "`":
                problems.append(f"{name}: Chinese in a template literal: {literal!r}")
            elif not re.search(r"(?<![\w$.])tr\(\s*$", before):
                problems.append(f"{name}: Chinese outside tr(): {literal!r}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("path", pages(), ids=lambda p: p.name)
def test_every_page_loads_the_dictionary_first_and_has_the_switcher(path):
    html = path.read_text(encoding="utf-8")
    assert html.index("/static/i18n.js") < html.index("/static/catalog.js")
    assert 'id="lang"' in html
