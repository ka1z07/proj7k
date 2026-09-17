"""
CLI Entrypoint and runner for proj7k.live real-time radar dashboard.

Usage:
    python3 -m proj7k.live [--no-watch] [--port 7770] [--host 127.0.0.1] [--open]

SPEC-P2.4-01 / ADR-0010.
"""

import argparse
import asyncio
import logging
from pathlib import Path
import signal
import sys
from typing import Optional, Sequence
import webbrowser

from proj7k.cache import DEFAULT_CACHE_DIR, TwoLayerCache
from proj7k.live.coordinator import LiveSessionCoordinator
from proj7k.live.engine import LiveEngine
from proj7k.live.index import LazerRealmIndex
from proj7k.live.server import LiveServer
from proj7k.live.watcher import LazerLogWatcher

logger = logging.getLogger("proj7k.live")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m proj7k.live",
        description="proj7k - Real-time 8-dimension technique radar profile and stream dashboard.",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=7770,
        help="Port for HTTP dashboard and WebSocket stream (default: 7770).",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host address to bind (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Automatically open dashboard in default browser upon startup.",
    )
    parser.add_argument(
        "--no-watch",
        action="store_true",
        help="Disable automatic lazer runtime log monitoring (manual mode only).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"Path to proj7k cache directory (default: {DEFAULT_CACHE_DIR}).",
    )
    parser.add_argument(
        "--lazer-dir",
        type=Path,
        default=None,
        help="Custom osu!lazer directory (default: system standard path).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose / debug log output.",
    )

    return parser


async def run_live_service(
    args: argparse.Namespace,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Runs the live service until stop_event is set."""
    cache = TwoLayerCache(cache_dir=args.cache_dir, enabled=True)
    engine = LiveEngine(cache=cache)
    server = LiveServer(
        host=args.host,
        port=args.port,
        engine=engine,
        no_watch=args.no_watch,
    )

    await server.start()
    url = f"http://{args.host}:{args.port}/"
    logger.info(f"Dashboard available at {url}")
    print(f"proj7k live dashboard running at {url}")

    coordinator: Optional[LiveSessionCoordinator] = None
    if not args.no_watch:
        lazer_dir = args.lazer_dir
        realm_path = (lazer_dir / "client.realm") if lazer_dir else None
        files_dir = (lazer_dir / "files") if lazer_dir else None
        logs_dir = (lazer_dir / "logs") if lazer_dir else None

        index = LazerRealmIndex(realm_path=realm_path, files_dir=files_dir)
        watcher = LazerLogWatcher(logs_dir=logs_dir)
        coordinator = LiveSessionCoordinator(
            server=server,
            engine=engine,
            index=index,
            watcher=watcher,
        )
        await coordinator.start()

    if args.open:
        try:
            webbrowser.open(url)
        except Exception as e:
            logger.warning(f"Failed to open browser automatically: {e}")

    if stop_event is None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _signal_handler():
            logger.info("Shutdown signal received.")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except (NotImplementedError, RuntimeError):
                pass

    try:
        await stop_event.wait()
    finally:
        logger.info("Stopping proj7k.live server...")
        if coordinator is not None:
            await coordinator.stop()
        await server.stop()
        logger.info("Server stopped.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(run_live_service(args))
        return 0
    except (KeyboardInterrupt, SystemExit):
        return 0
    except Exception as e:
        logger.error(f"Fatal error in proj7k.live: {e}", exc_info=True)
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
