"""
What the catalog tells search engines (ADR-0042): each page's own title, description, canonical address and
link preview, a plain-HTML summary of a set or mapper before the script runs, and the sitemap.

The pages are otherwise drawn by `static/catalog.js` from the JSON API. Google runs that script, slowly and
not for every page; Bing, Baidu and link previews (Discord, QQ, X) mostly do not. So the server writes what
identifies a page into the HTML itself, and the script removes the summary once it has drawn the real page.
"""

import html
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote

SITE_NAME = "proj7k 7K 谱面库"
#: Most URLs a sitemap file may list is 50,000; stay well under it so a growing catalog never crosses it.
SITEMAP_CHUNK = 40_000

_TITLE = re.compile(r"<title>.*?</title>", re.S)
_SCRIPTS = '<script src="/static/i18n.js'   # the pages' first script (its link carries `?v=` when served)

#: The fixed pages: their address, title (None keeps the file's own) and description.
STATIC_PAGES: Dict[str, Tuple[Optional[str], str]] = {
    "/": (None, "proj7k：osu!mania 7K 的难度评级、降阶练习、谱面生成、回放分析工具，以及收录全部 7K 谱面的"
                "在线谱面库（官方星级与按 Jinjin 7K 段位标定的引擎星级）。"),
    "/beatmapsets": ("osu!mania 7K 谱面搜索 · " + SITE_NAME,
                     "搜索全部 osu!mania 7K 谱面：按官方星级、引擎星级、段位、技能（速度、叠键、长条等）、"
                     "谱师和 Ranked/Loved 状态筛选。"),
    "/mappers": ("osu!mania 7K 谱师 · " + SITE_NAME,
                 "osu!mania 7K 谱师一览：每位谱师的谱面数量、难度分布、长条比例与写法风格标签。"),
}


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _stars(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.2f}★"


def _range(values: Sequence[float]) -> str:
    lo, hi = min(values), max(values)
    return f"{lo:.2f}★" if round(lo, 2) == round(hi, 2) else f"{lo:.2f}–{hi:.2f}★"


def _ld(data: Dict[str, Any]) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/ld+json">{text}</script>'


def _breadcrumbs(base: str, trail: List[Tuple[str, str]]) -> str:
    return _ld({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": name, "item": base + path}
        for i, (name, path) in enumerate(trail)]})


def head(base: str, path: str, title: str, description: str, image: Optional[str] = None,
         extra: Iterable[str] = (), index: bool = True) -> str:
    """The `<head>` tags that replace a page's `<title>`."""
    url = base + path
    tags = [
        f"<title>{_e(title)}</title>",
        f'<meta name="description" content="{_e(description)}">',
        f'<link rel="canonical" href="{_e(url)}">',
        '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">',
        f'<meta property="og:site_name" content="{_e(SITE_NAME)}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{_e(title)}">',
        f'<meta property="og:description" content="{_e(description)}">',
        f'<meta property="og:url" content="{_e(url)}">',
        '<meta property="og:locale" content="zh_CN">',
    ]
    if image:
        tags += [f'<meta property="og:image" content="{_e(image)}">',
                 '<meta name="twitter:card" content="summary_large_image">']
    else:
        tags.append('<meta name="twitter:card" content="summary">')
    if not index:
        tags.append('<meta name="robots" content="noindex">')
    tags.extend(extra)
    return "\n".join(tags)


def render(page: str, head_html: str, summary: str = "") -> str:
    """`page` (a file of `static/`) with its `<title>` replaced by `head_html` and `summary` placed before
    the scripts, where `catalog.js` finds and removes it (`#ssr`)."""
    page = _TITLE.sub(lambda _: head_html, page, count=1)
    if summary:
        page = page.replace(_SCRIPTS, f'<section class="ssr container" id="ssr">\n{summary}\n</section>\n{_SCRIPTS}', 1)
    return page


def static_head(base: str, path: str, page: str) -> str:
    title, description = STATIC_PAGES[path]
    if title is None:
        found = _TITLE.search(page)
        title = html.unescape(found.group(0)[7:-8]) if found else SITE_NAME
    extra = []
    if path == "/":
        extra.append(_ld({"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME,
                          "alternateName": ["proj7k", "7K 谱面库"], "url": base + "/"}))
    return head(base, path, title, description, extra=extra)


def set_page(base: str, s: Dict[str, Any]) -> Tuple[str, str]:
    """(head, summary) of a set's page; `s` is `CatalogStore.beatmapset`."""
    maps = s["beatmaps"]
    name = f"{s['artist']} - {s['title']}"
    shown = f"{s['artist_unicode'] or s['artist']} - {s['title_unicode'] or s['title']}"
    title = f"{name} ({s['creator']}) · osu!mania 7K · {SITE_NAME}"
    official = [b["official_sr"] for b in maps if b["official_sr"] is not None]
    engine = [b["engine_sr"] for b in maps]
    dans = list(dict.fromkeys(b["dan"] for b in maps))
    status = f"，{s['status'].capitalize()}" if s["status"] != "unknown" else ""
    parts = [f"{shown}（谱师 {s['creator']}{status}）的 osu!mania 7K 谱面，{len(maps)} 个难度"]
    if official:
        parts.append(f"官方星级 {_range(official)}")
    if engine:
        parts.append(f"引擎星级 {_range(engine)}")
    if dans:
        parts.append(f"段位 {'、'.join(dans[:4])}")
    description = "，".join(parts) + "。"
    if shown != name:
        description += f" {name}."
    image = f"https://assets.ppy.sh/beatmaps/{s['id']}/covers/cover@2x.jpg" if s["id"] > 0 else None
    path = f"/beatmapsets/{s['id']}"
    crumbs = _breadcrumbs(base, [("谱面", "/beatmapsets"), (name, path)])

    mapper = f"/mappers/{s['creator_id']}" if s.get("creator_id") else f"/mappers/{quote('@' + s['creator'], safe='@')}"
    rows = "\n".join(
        f"<tr><td>{_e(b['version'])}</td><td>{_stars(b['official_sr'])}</td><td>{_stars(b['engine_sr'])}</td>"
        f"<td>{_e(b['dan'])}</td><td>{_e(b.get('mapper_name') or s['creator'])}</td></tr>" for b in maps)
    osu = f' · <a href="https://osu.ppy.sh/beatmapsets/{s["id"]}">osu!</a>' if s["id"] > 0 else ""
    summary = (
        f"<h1>{_e(shown)}</h1>\n"
        f'<p>谱师 <a href="{mapper}">{_e(s["creator"])}</a> · {_e(s["status"])}{osu}</p>\n'
        f"<p>{_e(description)}</p>\n"
        "<table><tr><th>难度</th><th>官方</th><th>引擎</th><th>段位</th><th>谱师</th></tr>\n"
        f"{rows}\n</table>")
    return head(base, path, title, description, image, [crumbs]), summary


def mapper_page(base: str, key: str, m: Dict[str, Any]) -> Tuple[str, str]:
    """(head, summary) of a mapper's page; `m` is `MapperIndex.get`."""
    title = f"{m['name']} · osu!mania 7K 谱师 · {SITE_NAME}"
    parts = [f"osu!mania 7K 谱师 {m['name']}：{m['sets']} 组谱面、{m['diffs']} 个 7K 难度"]
    if m.get("engine_median") is not None:
        parts.append(f"引擎星级中位数 {m['engine_median']:.2f}★")
    if m.get("ln_ratio") is not None:
        parts.append(f"长条占比 {m['ln_ratio']:.0%}")
    labels = [t["label"] for t in m.get("tags", [])[:4]]
    if labels:
        parts.append(f"风格：{'、'.join(labels)}")
    description = "，".join(parts) + "。"
    path = f"/mappers/{quote(key, safe='@')}"
    image = f"https://a.ppy.sh/{m['user_id']}" if m.get("user_id") else None
    crumbs = _breadcrumbs(base, [("谱师", "/mappers"), (m["name"], path)])
    tags = "".join(f"<li>{_e(t['label'])}：{_e(t['why'])}</li>" for t in m.get("tags", []))
    summary = (f"<h1>{_e(m['name'])}</h1>\n<p>{_e(description)}</p>\n" + (f"<ul>{tags}</ul>" if tags else ""))
    return head(base, path, title, description, image, [crumbs]), summary


def robots(base: str) -> str:
    # The API stays crawlable: Google draws the pages with it, and a blocked fetch would leave it the
    # script's "not found" message instead of the page.
    return f"User-agent: *\nDisallow: /oauth/\n\nSitemap: {base}/sitemap.xml\n"


def sitemap_index(base: str, set_chunks: int, lastmod: Optional[str]) -> str:
    names = ["pages", "mappers"] + [f"sets-{i + 1}" for i in range(set_chunks)]
    mod = f"<lastmod>{_e(lastmod)}</lastmod>" if lastmod else ""
    items = "".join(f"<sitemap><loc>{_e(base)}/sitemaps/{n}.xml</loc>{mod}</sitemap>" for n in names)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</sitemapindex>\n')


def urlset(base: str, entries: Iterable[Tuple[str, Optional[str]]]) -> str:
    """`entries`: (path, last modified date or None)."""
    items = "".join(
        f"<url><loc>{_e(base + path)}</loc>{f'<lastmod>{_e(mod)}</lastmod>' if mod else ''}</url>"
        for path, mod in entries)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>\n')
