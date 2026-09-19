"""
Unified CLI Entrypoint for osu!lazer database synchronization and daemon.

Usage:
    python3 -m proj7k.sync [--once | --daemon | --revert | --setup] [OPTIONS]

SPEC-P2.3-03 / ADR-0009.
"""

import argparse
import logging
from pathlib import Path
import signal
import sys
import threading
from typing import Optional, Sequence

from proj7k.lazer.backup import DEFAULT_CACHE_DIR
from proj7k.lazer.bridge import DEFAULT_REALM_PATH, RealmBridgeClient
from proj7k.lazer.daemon import (
    DEFAULT_FILES_DIR,
    DEFAULT_LOCK_PATH,
    LazerDaemon,
    LazerSyncManager,
    SyncOptions,
    run_daemon,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m proj7k.sync",
        description="proj7k - osu!lazer database synchronization and background daemon.",
    )

    parser.add_argument(
        "action",
        nargs="?",
        choices=["once", "daemon", "revert", "setup"],
        default=None,
        help="Optional positional action: once (default), daemon, revert, setup.",
    )

    action_group = parser.add_argument_group("Actions (default: --once)")
    action_group.add_argument(
        "--once",
        action="store_true",
        help="Perform a single incremental synchronization pass and exit (default).",
    )
    action_group.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously as a background daemon monitoring the safe flush window.",
    )
    action_group.add_argument(
        "--revert",
        action="store_true",
        help="Roll back all proj7k database modifications and restore original attributes.",
    )
    action_group.add_argument(
        "--setup",
        action="store_true",
        help="Initialize and verify the Node.js Realm companion bridge environment.",
    )

    path_group = parser.add_argument_group("Path Configuration")
    path_group.add_argument(
        "--realm",
        type=Path,
        default=DEFAULT_REALM_PATH,
        help=f"Path to osu!lazer client.realm database (default: {DEFAULT_REALM_PATH}).",
    )
    path_group.add_argument(
        "--files-dir",
        type=Path,
        default=DEFAULT_FILES_DIR,
        help=f"Path to osu!lazer files storage directory (default: {DEFAULT_FILES_DIR}).",
    )
    path_group.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"Path to proj7k cache directory (default: {DEFAULT_CACHE_DIR}).",
    )
    path_group.add_argument(
        "--lock-path",
        type=Path,
        default=None,
        help="Path to client.realm.lock file (default: client.realm.lock next to client.realm).",
    )

    opts_group = parser.add_argument_group("Execution Options")
    opts_group.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds when running in daemon mode (default: 5.0s).",
    )
    opts_group.add_argument(
        "--wait",
        action="store_true",
        help="When running with --once, wait up to 60s for the safe flush window to open.",
    )
    opts_group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose / debug log output.",
    )

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    realm_path = args.realm
    files_dir = args.files_dir
    cache_dir = args.cache_dir
    lock_path = args.lock_path

    pos_action = args.action
    is_setup = args.setup or (pos_action == "setup")
    is_revert = args.revert or (pos_action == "revert")
    is_daemon = args.daemon or (pos_action == "daemon")
    is_once = args.once or (pos_action == "once") or (not is_setup and not is_revert and not is_daemon)

    # 1. Action: setup
    if is_setup:
        logging.info("Setting up Node.js Realm companion bridge environment...")
        bridge = RealmBridgeClient(default_realm_path=realm_path)
        try:
            bridge.ensure_installed()
            msg = "Setup completed successfully."
            logging.info(msg)
            print(msg)
            return 0
        except Exception as e:
            err = f"Setup failed: {e}"
            logging.error(err)
            print(err, file=sys.stderr)
            return 1

    options = SyncOptions(
        realm_path=realm_path,
        files_dir=files_dir,
        cache_dir=cache_dir,
        lock_path=lock_path,
    )
    manager = LazerSyncManager(options=options)

    # 2. Action: revert
    if is_revert:
        logging.info(f"Reverting all proj7k modifications on '{realm_path}'...")
        res = manager.revert_all()
        if res.success:
            msg = f"Revert completed successfully ({res.updated_count} beatmaps restored)."
            logging.info(msg)
            print(msg)
            return 0
        else:
            err = f"Revert failed: {res.error}"
            logging.error(err)
            print(err, file=sys.stderr)
            return 1

    # 3. Action: daemon
    if is_daemon:
        stop_event = threading.Event()

        def _handle_signal(signum, frame):
            logging.info(f"Received signal {signum}, stopping daemon...")
            stop_event.set()

        try:
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
        except (ValueError, AttributeError):
            # Non-main thread or unsupported platform
            pass

        run_daemon(manager=manager, interval_s=args.interval, stop_event=stop_event)
        return 0

    # 4. Action: once (default)
    logging.info(f"Synchronizing osu!lazer database at '{realm_path}'...")
    summary = manager.sync_once(wait_for_lock=args.wait)
    if summary.success:
        msg = (
            f"Sync completed successfully: Total 7K: {summary.total_7k}, "
            f"Evaluated: {summary.evaluated_count}, Updated: {summary.updated_count}, "
            f"Skipped: {summary.skipped_count}, Failed: {summary.failed_count}"
        )
        logging.info(msg)
        print(msg)
        if summary.snapshot_path:
            snap_msg = f"Backup snapshot created at: {summary.snapshot_path}"
            logging.info(snap_msg)
            print(snap_msg)
        return 0
    else:
        err = f"Sync failed: {summary.error}"
        logging.error(err)
        print(err, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
