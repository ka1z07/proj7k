"""Fill the catalog website's Traditional Chinese dictionary from its English one (ADR-0038).

The catalog's pages are written in Simplified Chinese; `catalog/static/i18n.js` holds an English entry for every
string (EN) and a Traditional Chinese one (HANT). Run this after adding English entries: it converts each key with
OpenCC's Taiwan phrasing (s2twp), applies the overrides below, and rewrites the HANT block.

    pip install opencc
    python3 tools/catalog_hant.py
"""

import json
import re
from pathlib import Path

import opencc

I18N = Path(__file__).resolve().parents[1] / "src" / "proj7k" / "catalog" / "static" / "i18n.js"

#: Where OpenCC's phrasing reads wrong for 7K players.
OVERRIDES = {
    "倉庫": "儲存庫",
    "快捷方式": "捷徑",
    "標杆": "標竿",
}


def block(text: str, name: str) -> re.Match:
    return re.search(rf"/\* {name}-BEGIN \*/(.*?)/\* {name}-END \*/", text, re.S)


def main() -> None:
    text = I18N.read_text(encoding="utf-8")
    en = json.loads(block(text, "EN").group(1))
    convert = opencc.OpenCC("s2twp").convert
    hant = {}
    for zh in en:
        value = convert(zh)
        for a, b in OVERRIDES.items():
            value = value.replace(a, b)
        hant[zh] = value
    body = json.dumps(hant, ensure_ascii=False, indent=4).replace("\n}", "\n  }")
    m = block(text, "HANT")
    I18N.write_text(text[:m.start(1)] + body + text[m.end(1):], encoding="utf-8")
    print(f"{len(hant)} entries")


if __name__ == "__main__":
    main()
