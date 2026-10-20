"""The dashboard's English translation (ADR-0035): every Chinese string a page shows has an English entry.

Pages are written in Chinese. Script text goes through `tr("中文 {x}", {x})`, which looks the Chinese up in
`static/i18n.js`'s dictionary; static HTML text and the title / placeholder attributes are looked up the same way
when the page loads. These tests read every page and fail on Chinese the dictionary would leave untranslated.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from proj7k import i18n

STATIC = Path(__file__).resolve().parents[1] / "src" / "proj7k" / "dashboard" / "static"
CJK = re.compile(r"[　-〿一-鿿＀-￯]")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
TRANSLATED_ATTRS = ("title", "placeholder", "alt", "aria-label")


def dictionary() -> dict:
    text = (STATIC / "i18n.js").read_text(encoding="utf-8")
    body = text.split("/* EN-BEGIN */", 1)[1].split("/* EN-END */", 1)[0]
    return json.loads(body)


def js_strings(source: str):
    """(literal, kind, text before it) for each string literal in a script; kind is the quote character.

    A small lexer: comments are skipped, template literals yield their literal parts (the `${}` holes are lexed as
    code), and a `/` after an operator starts a regular expression.
    """
    out = []
    i, n = 0, len(source)
    stack = []  # brace depth of each open template's ${ hole
    depth = 0
    last = ""   # last significant character, to tell a regex from a division
    while i < n:
        c = source[i]
        if c == "/" and source.startswith("//", i):
            i = source.find("\n", i)
            i = n if i < 0 else i
            continue
        if c == "/" and source.startswith("/*", i):
            i = source.index("*/", i) + 2
            continue
        if c in "'\"":
            j = i + 1
            while source[j] != c:
                j += 2 if source[j] == "\\" else 1
            out.append((source[i + 1:j], c, source[max(0, i - 40):i]))
            i, last = j + 1, c
            continue
        if c == "`" or (c == "}" and stack and stack[-1] == depth):
            if c == "}":
                stack.pop()
            j = i + 1
            while True:
                if source[j] == "\\":
                    j += 2
                elif source[j] == "`":
                    out.append((source[i + 1:j], "`", source[max(0, i - 40):i]))
                    i, last = j + 1, "`"
                    break
                elif source.startswith("${", j):
                    out.append((source[i + 1:j], "`", source[max(0, i - 40):i]))
                    stack.append(depth)
                    i, last = j + 2, "{"
                    break
                else:
                    j += 1
            continue
        if c == "/" and (last in "(,=:[!&|?{};+-*%<>~^" or last == ""):
            j = i + 1
            in_class = False
            while source[j] != "/" or in_class:
                if source[j] == "\\":
                    j += 1
                elif source[j] == "[":
                    in_class = True
                elif source[j] == "]":
                    in_class = False
                j += 1
            i, last = j + 1, "/"
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        if not c.isspace():
            last = c if not (c.isalnum() or c in "_$") else "a"
        i += 1
    return out


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts, self.scripts, self._in = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ("br", "img", "input", "meta", "link", "hr"):
            self._in.append(tag)
        for k, v in attrs:
            if k in TRANSLATED_ATTRS and v:
                self.texts.append(" ".join(v.split()))

    def handle_endtag(self, tag):
        while self._in and self._in.pop() != tag:
            pass

    def handle_data(self, data):
        where = self._in[-1] if self._in else ""
        if where == "script":
            self.scripts.append(data)
        elif where != "style" and data.strip():
            self.texts.append(" ".join(data.split()))


def pages():
    return sorted(STATIC.glob("*.html"))


def page_parts(path: Path):
    p = _Page()
    p.feed(path.read_text(encoding="utf-8"))
    return p.texts, p.scripts


def scripts():
    yield "app.js", (STATIC / "app.js").read_text(encoding="utf-8")
    for path in pages():
        for k, s in enumerate(page_parts(path)[1]):
            yield f"{path.name}<script {k}>", s


def test_dictionary_is_english_with_matching_placeholders():
    en = dictionary()
    assert len(en) > 100
    for zh, text in en.items():
        assert CJK.search(zh), zh
        assert not CJK.search(text), (zh, text)
        assert set(PLACEHOLDER.findall(zh)) == set(PLACEHOLDER.findall(text)), (zh, text)


@pytest.mark.parametrize("path", pages(), ids=lambda p: p.name)
def test_html_text_has_english(path):
    en = dictionary()
    texts, _ = page_parts(path)
    missing = [t for t in texts if CJK.search(t) and t not in en]
    assert not missing, missing


def test_script_chinese_goes_through_tr_with_an_entry():
    en = dictionary()
    problems = []
    for name, source in scripts():
        for literal, kind, before in js_strings(source):
            if not CJK.search(literal):
                continue
            if kind == "`":
                problems.append(f"{name}: Chinese in a template literal: {literal!r}")
            elif not re.search(r"(?<![\w$.])tr\(\s*$", before):
                problems.append(f"{name}: Chinese outside tr(): {literal!r}")
            elif json.loads(f'"{literal}"' if kind == '"' else json.dumps(literal)) not in en:
                problems.append(f"{name}: no English for {literal!r}")
    assert not problems, "\n".join(problems)


def test_every_page_loads_the_dictionary_first():
    for path in pages():
        html = path.read_text(encoding="utf-8")
        assert html.index("/static/i18n.js") < html.index("/static/app.js"), path.name


def test_server_language_comes_from_the_cookie_and_defaults_to_chinese():
    assert i18n.from_cookie("p7lang=en; other=1") == "en"
    assert i18n.from_cookie("other=1; p7lang=zh") == "zh"
    assert i18n.from_cookie("p7lang=fr") == i18n.from_cookie("") == i18n.from_cookie(None) == "zh"
    assert i18n.tr("中文", "English") == "中文"
    with i18n.language("en"):
        assert i18n.tr("中文", "English") == "English"
    assert i18n.current() == "zh"


def test_system_language(monkeypatch):
    for k in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: ("Chinese (Simplified)_China", "936"))
    assert i18n.system_language() == "zh"
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert i18n.system_language() == "en"
