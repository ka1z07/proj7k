"""
Command line of the catalog.

    python3 -m proj7k.catalog build   --db catalog.sqlite3 --corpus tests/fixtures/benchmark_corpus.json.gz \
                                      --manifest docs/research/structured_index.json
    python3 -m proj7k.catalog add     --db catalog.sqlite3 PATH...          (.osu files or folders)
    python3 -m proj7k.catalog lazer   --db catalog.sqlite3 [--realm ... --files-dir ...]
    python3 -m proj7k.catalog crawl   --db catalog.sqlite3                  (osu! API; OSU_CLIENT_ID / OSU_CLIENT_SECRET)
    python3 -m proj7k.catalog refresh --db catalog.sqlite3                  (re-evaluate after an engine change)
    python3 -m proj7k.catalog serve   --db catalog.sqlite3 [--host 127.0.0.1 --port 7780]
"""

import argparse
import logging
import os
import sys
import webbrowser
from pathlib import Path
from typing import Optional, Sequence

from proj7k.catalog.store import CatalogStore

DEFAULT_DB = Path.home() / ".cache" / "proj7k" / "catalog.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m proj7k.catalog",
                                     description="proj7k - the 7K chart catalog (database and website).")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"catalog database (default: {DEFAULT_DB})")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="add the frozen benchmark corpus (with the manifest's official ratings)")
    build.add_argument("--corpus", type=Path, default=Path("tests/fixtures/benchmark_corpus.json.gz"))
    build.add_argument("--manifest", type=Path, default=Path("docs/research/structured_index.json"))

    add = sub.add_parser("add", help="add .osu files or folders of them")
    add.add_argument("paths", type=Path, nargs="+")

    lazer = sub.add_parser("lazer", help="add the 7K charts of the local osu!lazer library (read-only)")
    lazer.add_argument("--realm", type=Path, default=None)
    lazer.add_argument("--files-dir", type=Path, default=None)
    lazer.add_argument("--cache-dir", type=Path, default=None, help="proj7k sync's cache (its backup holds the "
                                                                    "official ratings sync rewrote)")

    crawl = sub.add_parser("crawl", help="crawl osu!'s ranked and loved 7K sets through the API v2")
    crawl.add_argument("--status", nargs="+", default=["ranked", "loved"])
    crawl.add_argument("--max-pages", type=int, default=None, help="stop after this many search pages per status")

    sub.add_parser("refresh", help="re-evaluate every difficulty the running engine version has not evaluated")

    serve = sub.add_parser("serve", help="run the website")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=7780)
    serve.add_argument("--open", action="store_true", help="open the browser")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    if args.command != "serve":
        args.db.parent.mkdir(parents=True, exist_ok=True)
    elif not args.db.exists():
        print(f"no catalog at {args.db}; run `python3 -m proj7k.catalog build` first", file=sys.stderr)
        return 2
    store = CatalogStore(args.db)

    from proj7k.catalog import ingest

    if args.command == "build":
        print(ingest.ingest_corpus(store, args.corpus, args.manifest))
    elif args.command == "add":
        print(ingest.ingest_osu_files(store, args.paths))
    elif args.command == "lazer":
        print(ingest.ingest_lazer(store, args.realm, args.files_dir, args.cache_dir))
    elif args.command == "crawl":
        client_id, secret = os.environ.get("OSU_CLIENT_ID"), os.environ.get("OSU_CLIENT_SECRET")
        if not client_id or not secret:
            print("set OSU_CLIENT_ID and OSU_CLIENT_SECRET (an OAuth application at "
                  "https://osu.ppy.sh/home/account/edit#oauth)", file=sys.stderr)
            return 2
        summary = ingest.ingest_osu_api(store, ingest.OsuApiClient(client_id, secret), args.status, args.max_pages)
        print(summary)
    elif args.command == "refresh":
        print(f"re-evaluated {store.reevaluate_stale()}")
    elif args.command == "serve":
        from proj7k.catalog.server import make_server

        server = make_server(store, args.host, args.port)
        url = f"http://{args.host}:{args.port}/beatmapsets"
        stats = store.stats()
        print(f"{stats['beatmapsets']} sets, {stats['beatmaps']} difficulties; serving {url}")
        if stats["stale"]:
            print(f"warning: {stats['stale']} difficulties were evaluated by another engine version; "
                  "run `refresh`", file=sys.stderr)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    if args.command != "serve":
        print(store.stats())
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
