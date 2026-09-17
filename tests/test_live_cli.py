"""
Tests for proj7k.live.cli (Seam 3: CLI Entrypoint).

Verifies CLI argument parsing, --no-watch execution, --open behavior,
and graceful shutdown.
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from proj7k.live.cli import build_parser, main, run_live_service


def test_cli_help(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "--no-watch" in captured.out
    assert "--port" in captured.out
    assert "--host" in captured.out
    assert "--open" in captured.out


def test_cli_argument_defaults():
    parser = build_parser()
    args = parser.parse_args([])
    assert args.port == 7770
    assert args.host == "127.0.0.1"
    assert args.no_watch is False
    assert args.open is False


def test_cli_argument_custom():
    parser = build_parser()
    args = parser.parse_args([
        "--port", "8888",
        "--host", "0.0.0.0",
        "--no-watch",
        "--open",
        "--cache-dir", "/tmp/cache",
    ])
    assert args.port == 8888
    assert args.host == "0.0.0.0"
    assert args.no_watch is True
    assert args.open is True
    assert str(args.cache_dir) == "/tmp/cache"


def test_cli_run_no_watch():
    parser = build_parser()
    args = parser.parse_args(["--port", "7775", "--no-watch"])

    stop_event = asyncio.Event()

    async def _test():
        task = asyncio.create_task(run_live_service(args, stop_event=stop_event))
        await asyncio.sleep(0.1)
        stop_event.set()
        await task

    asyncio.run(_test())


def test_cli_run_with_watch_graceful_fallback(tmp_path: Path):
    parser = build_parser()
    lazer_dir = tmp_path / "lazer"
    lazer_dir.mkdir()
    args = parser.parse_args([
        "--port", "7778",
        "--lazer-dir", str(lazer_dir),
        "--cache-dir", str(tmp_path / "cache"),
    ])

    stop_event = asyncio.Event()

    async def _test():
        task = asyncio.create_task(run_live_service(args, stop_event=stop_event))
        await asyncio.sleep(0.1)
        stop_event.set()
        await task

    asyncio.run(_test())



def test_cli_main_success():
    with patch("proj7k.live.cli.run_live_service") as mock_run:
        mock_run.return_value = None
        code = main(["--no-watch", "--port", "7776"])
        assert code == 0
        mock_run.assert_called_once()


def test_cli_main_exception(capsys):
    with patch("proj7k.live.cli.run_live_service", side_effect=RuntimeError("bind failure")):
        code = main(["--no-watch"])
        assert code == 1
        captured = capsys.readouterr()
        assert "bind failure" in captured.err or "bind failure" in captured.out
