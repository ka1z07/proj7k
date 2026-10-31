"""
Cancelling a running dashboard job (ADR-0049): it stops at its next safe point, a child process it waits on is
killed at once, the `except Exception` that skips one bad chart or replay doesn't swallow the stop, and the work
stops between whole steps (model runs, replays) rather than in the middle of one.
"""

import os
import sys
import threading
import time

import pytest

from proj7k import cancel
from proj7k.dashboard import JobManager
from proj7k.dashboard.jobs import JobCancelled

from test_generator_mapperatorinator import song, stream_runner  # noqa: F401  (song is a fixture)


def _wait(job, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if job.finished:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job.id} did not finish: {job.status}")


def _until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


# --- the job manager -----------------------------------------------------------------------------------------


def test_a_running_job_stops_at_its_next_safe_point_and_frees_its_resource(tmp_path):
    entered, steps, frames = threading.Event(), [], []

    def loop(params, ctx):
        entered.set()
        for i in range(500):
            cancel.checkpoint()
            steps.append(i)
            time.sleep(0.01)
        return {"finished": True}

    manager = JobManager({"loop": loop, "next": lambda p, c: {"ran": True}}, tmp_path / "jobs", notify=frames.append)
    try:
        job = manager.submit("loop")
        after = manager.submit("next")  # same (serial) resource: waits for the first
        assert entered.wait(5)
        assert manager.cancel(job.id)
        assert _wait(job).status == "cancelled" and job.result is None
        assert len(steps) < 500
        assert any(f["type"] == "job_update" and f["job"]["id"] == job.id and f["job"]["stopping"] for f in frames)
        assert job.to_dict()["stopping"] is False  # finished, no longer stopping
        assert any("取消" in line for line in job.logs)
        assert _wait(after).status == "done" and after.result == {"ran": True}
    finally:
        manager.shutdown()


def test_a_job_with_no_safe_point_left_finishes(tmp_path):
    gate, entered = threading.Event(), threading.Event()

    def work(params, ctx):
        entered.set()
        gate.wait(5)
        return {"wrote": True}

    manager = JobManager({"work": work}, tmp_path / "jobs")
    try:
        job = manager.submit("work")
        assert entered.wait(5)
        assert manager.cancel(job.id)
        assert job.to_dict()["stopping"] is True
        assert manager.cancel(job.id)  # asking again changes nothing
        gate.set()
        assert _wait(job).status == "done" and job.result == {"wrote": True}
        assert not manager.cancel(job.id)
    finally:
        manager.shutdown()


def test_skipping_one_bad_item_does_not_swallow_the_stop(tmp_path):
    entered, tried = threading.Event(), []

    def per_item(params, ctx):
        entered.set()
        for i in range(500):
            try:
                cancel.checkpoint()
                tried.append(i)
                time.sleep(0.01)
            except Exception:  # what run_downscale and run_replay_import do for one bad chart or replay
                pass
        return {}

    manager = JobManager({"items": per_item}, tmp_path / "jobs")
    try:
        job = manager.submit("items")
        assert entered.wait(5)
        manager.cancel(job.id)
        assert _wait(job).status == "cancelled" and len(tried) < 500
    finally:
        manager.shutdown()


# --- safe points and child processes ---------------------------------------------------------------------------


def test_off_a_job_thread_nothing_stops():
    cancel.checkpoint()
    assert not cancel.requested()
    proc = cancel.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
    assert proc.returncode == 0 and proc.stdout.strip() == "hi"


def test_run_returns_what_the_child_wrote_on_a_job_thread():
    with cancel.watching(threading.Event()):
        proc = cancel.run([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
                          capture_output=True, text=True)
    assert (proc.returncode, proc.stdout.strip(), proc.stderr.strip()) == (3, "out", "err")


@pytest.mark.skipif(os.name == "nt", reason="checks the grandchild through its pid on POSIX")
def test_run_kills_the_child_and_its_children_when_the_job_is_cancelled(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    # The child starts a grandchild (as the model's Python starts workers), then both sleep.
    script = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(g.pid))\n"
        "time.sleep(60)\n"
    )
    event = threading.Event()
    threading.Timer(1.0, event.set).start()
    started = time.monotonic()
    with cancel.watching(event), pytest.raises(cancel.Cancelled):
        cancel.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert time.monotonic() - started < 10
    grandchild = int(pid_file.read_text())

    def gone():
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            return True
        # A killed child not yet reaped by its (also killed) parent is a zombie: gone for our purposes.
        try:
            with open(f"/proc/{grandchild}/stat") as f:
                return f.read().split(")")[-1].split()[0] == "Z"
        except OSError:
            return True
    _until(gone)


def test_a_stop_before_the_child_starts_never_starts_it(tmp_path):
    marker = tmp_path / "ran"
    event = threading.Event()
    event.set()
    with cancel.watching(event), pytest.raises(cancel.Cancelled):
        cancel.run([sys.executable, "-c", f"open({str(marker)!r}, 'w')"])
    assert not marker.exists()


def test_cancelled_is_the_job_managers_stop():
    assert JobCancelled is cancel.Cancelled
    assert not issubclass(cancel.Cancelled, Exception)


# --- where the tools stop --------------------------------------------------------------------------------------


def test_the_generator_stops_between_model_runs(song, tmp_path):  # noqa: F811
    from proj7k.generator.generate import GeneratorOptions, generate_from_file

    calls, event = [], threading.Event()
    inner = stream_runner(calls)

    def runner(*args):
        event.set()  # cancelled while the first run is being written
        return inner(*args)

    # With a technique mix every run is used, so without the stop it would make five.
    options = GeneratorOptions(target_stars=3.0, model_runner=runner, model_runs=5, pool_mix={"jack": 1.0})
    with cancel.watching(event), pytest.raises(cancel.Cancelled):
        generate_from_file(song, options)
    assert len(calls) == 1


def test_the_replay_import_stops_between_replays(tmp_path, monkeypatch):
    files = tmp_path / "files"
    realm = tmp_path / "client.realm"
    realm.write_bytes(b"")
    scores = []
    for i in range(3):
        b, r = f"b{i}" * 8, f"r{i}" * 8
        for h in (b, r):
            (files / h[0] / h[:2]).mkdir(parents=True, exist_ok=True)
            (files / h[0] / h[:2] / h).write_bytes(b"x")
        scores.append({"player_name": "P", "beatmap_file_hash": b, "replay_file_hash": r})

    class FakeBridge:
        def __init__(self, **_):
            pass

        def dump_7k_scores(self, realm_path=None, user=None):
            return scores

    event, read = threading.Event(), []

    def ingest(replay, beatmap):
        read.append(replay)
        event.set()  # cancelled while the first replay is read
        raise ValueError("not a replay")

    monkeypatch.setattr("proj7k.lazer.bridge.RealmBridgeClient", FakeBridge)
    monkeypatch.setattr("proj7k.profiler.cli.run_ingestion", ingest)
    from proj7k.profiler.cli import run_replay_import

    with cancel.watching(event), pytest.raises(cancel.Cancelled):
        run_replay_import("P", realm_path=realm, files_dir=files, db_path=tmp_path / "p.db")
    assert len(read) == 1
