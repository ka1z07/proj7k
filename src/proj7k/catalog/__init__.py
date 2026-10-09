"""
The online 7K chart catalog (ADR-0026): a searchable database of 7K osu!mania charts that shows, for
every difficulty, the official star rating next to the engine's (`proj7k.engine`), with a website laid
out like osu!'s own beatmap listing.

    python3 -m proj7k.catalog build --db catalog.sqlite3 --corpus tests/fixtures/benchmark_corpus.json.gz \
        --manifest docs/research/structured_index.json
    python3 -m proj7k.catalog serve --db catalog.sqlite3

Modules: `charts` (one `.osu` to a catalog row), `store` (the SQLite database), `query` (the osu!-style
search syntax), `ingest` (where charts come from), `server` (the website and its JSON API), `cli`.
"""

from proj7k.catalog.charts import ChartRow, chart_row
from proj7k.catalog.query import SearchQuery, parse_query
from proj7k.catalog.store import CatalogStore

__all__ = ["CatalogStore", "ChartRow", "SearchQuery", "chart_row", "parse_query"]
