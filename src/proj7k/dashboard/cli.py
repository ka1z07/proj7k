"""
CLI entrypoint for the proj7k dashboard: live radar, lazer sync, downscaler and profiler on one local page.

Usage:
    python3 -m proj7k.dashboard [--port 7770] [--no-open] [--no-watch] [--lazer-dir DIR] [--db PATH] [--output-dir DIR]

ADR-0024 (built on the live server of ADR-0010).
"""

import argparse
import asyncio
import logging
from pathlib import Path
import sys
from typing import Optional, Sequence

from proj7k.dashboard.actions import DEFAULT_DASHBOARD_DIR, DashboardConfig
from proj7k.dashboard.server import DashboardServer
from proj7k.lazer.bridge import DEFAULT_REALM_PATH
from proj7k.live import cli as live_cli

logger = logging.getLogger("proj7k.dashboard")


def build_parser() -> argparse.ArgumentParser:
    parser = live_cli.build_parser()
    parser.prog = "python3 -m proj7k.dashboard"
    parser.description = (
        "proj7k - one local web dashboard for the live radar, osu!lazer library sync, downscaler and profiler."
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the dashboard in the default browser on startup (it opens by default).",
    )
    parser.add_argument(
        "--realm",
        type=Path,
        default=None,
        help=f"Path to osu!lazer client.realm (default: <lazer-dir>/client.realm, else {DEFAULT_REALM_PATH}).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Profiler SQLite database (default: the profiler's own, ~/.proj7k/profiler.db).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DASHBOARD_DIR,
        help=f"Where practice maps, bundles and replay views are written (default: {DEFAULT_DASHBOARD_DIR}).",
    )
    return parser


def build_config(args: argparse.Namespace) -> DashboardConfig:
    realm = args.realm or ((args.lazer_dir / "client.realm") if args.lazer_dir else DEFAULT_REALM_PATH)
    return DashboardConfig(
        realm_path=Path(realm),
        files_dir=(args.lazer_dir / "files") if args.lazer_dir else None,
        cache_dir=args.cache_dir,
        db_path=args.db,
        output_dir=args.output_dir,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.open = args.open or not args.no_open

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "Binding to %s: anyone who can reach this address can sync or revert your osu!lazer library.", args.host
        )

    config = build_config(args)

    def factory(engine):
        return DashboardServer(host=args.host, port=args.port, engine=engine, no_watch=args.no_watch, config=config)

    try:
        asyncio.run(live_cli.run_live_service(args, server_factory=factory, banner="proj7k dashboard"))
        return 0
    except (KeyboardInterrupt, SystemExit):
        return 0
    except Exception as e:
        logger.error(f"Fatal error in proj7k.dashboard: {e}", exc_info=True)
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
