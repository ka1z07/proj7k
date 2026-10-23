"""
Tests for the dashboard's parallel jobs (ADR-0043): the scheduler checked over every state it can reach (within a
bound), each job kind's claim, the write claims enforced where the writes happen, audits of every place that writes
osu!lazer's library or the profiler database, and the job manager under real threads.

ADR-0043 states the theorem these tests carry: two jobs that write the same store never run at the same time.
"""

import ast
from collections import deque
import itertools
import os
from pathlib import Path
import random
import re
import threading
import time

import pytest

from proj7k import claims
from proj7k.dashboard import JobManager, actions
from proj7k.dashboard.actions import DashboardConfig, JOB_RESOURCES, build_actions, job_resources
from proj7k.dashboard.jobs import SERIAL, Scheduler

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "proj7k"


def _wait(job, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if job.finished:
            return job
        time.sleep(0.005)
    raise AssertionError(f"job {job.id} did not finish: {job.status}")


# --- the scheduler, over every reachable state -------------------------------------------------------------------

#: Three resources and up to four waiting jobs: about 30,000 states, a few seconds. PROJ7K_DEEP_MODEL_CHECK=1 checks
#: four resources (0.8–2.3 million states, a few minutes); ADR-0043 records both runs.
RES = ("a", "b", "c", "d") if os.environ.get("PROJ7K_DEEP_MODEL_CHECK") else ("a", "b", "c")
SETS = [frozenset(c) for n in range(1, len(RES) + 1) for c in itertools.combinations(RES, n)]
QUEUE_BOUND = 4

State = tuple  # (queued resource sets in order, running resource sets sorted)


def _key(s: frozenset) -> tuple:
    return tuple(sorted(s))


def _build(state: State, max_running: int) -> Scheduler:
    queued, running = state
    s = Scheduler(max_running)
    s.queue = [(f"q{i}", r) for i, r in enumerate(queued)]
    s.running = {f"r{i}": r for i, r in enumerate(running)}
    return s


def _canon(s: Scheduler) -> State:
    return tuple(r for _, r in s.queue), tuple(sorted(s.running.values(), key=_key))


def _step(s: Scheduler, max_running: int) -> None:
    """`start_ready`, as the manager calls it after every change, with the safety and order checks on it."""
    before = list(s.queue)
    started = s.start_ready()
    assert started == [j for j, _ in before if j in started], "started out of queue order"
    for i, (job, resources) in enumerate(before):
        if job in started:
            # Order: a job starts only when no job submitted before it that shares a resource is still waiting.
            assert not any(resources & r for j, r in before[:i] if j not in started)
    _check_invariants(s, max_running)


def _check_invariants(s: Scheduler, max_running: int) -> None:
    running = list(s.running.values())
    # Safety: running jobs hold pairwise disjoint resources.
    for x, y in itertools.combinations(running, 2):
        assert not (x & y), f"two running jobs share {sorted(x & y)}"
    # Capacity.
    assert len(running) <= max_running
    # Work-conserving: every waiting job is held back by capacity, a running job, or an earlier waiting job.
    busy = set(s.held())
    for _, r in s.queue:
        assert len(running) == max_running or (r & busy)
        busy |= r
    # No deadlock: something waits only while something runs.
    assert not s.queue or running


def _successors(state: State, max_running: int):
    queued, running = state
    if len(queued) < QUEUE_BOUND:
        for r in SETS:  # submit
            s = _build(state, max_running)
            s.enqueue("new", r)
            yield s
    for i in range(len(running)):  # a running job ends
        s = _build(state, max_running)
        s.finish(f"r{i}")
        yield s
    for i in range(len(queued)):  # a queued job is cancelled
        s = _build(state, max_running)
        assert s.remove(f"q{i}")
        yield s


def _drains(state: State, max_running: int) -> bool:
    """With no new jobs, ending the running jobs one by one runs every queued job (liveness)."""
    s = _build(state, max_running)
    for _ in range(len(state[0]) + len(state[1]) + 1):
        if not s.running and not s.queue:
            return True
        s.finish(next(iter(s.running)))
        s.start_ready()
    return not s.running and not s.queue


@pytest.mark.parametrize("max_running", [1, 2, 3])
def test_scheduler_is_safe_ordered_and_live_in_every_reachable_state(max_running):
    start: State = ((), ())
    seen = {start}
    todo = deque([start])
    while todo:
        state = todo.popleft()
        assert _drains(state, max_running)
        for s in _successors(state, max_running):
            _step(s, max_running)
            nxt = _canon(s)
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    # Every queue of up to QUEUE_BOUND jobs was reached (so the search is not stuck in a corner).
    assert sum(1 for q, _ in seen if len(q) == QUEUE_BOUND) > 0
    assert len(seen) > 100


def test_scheduler_basics():
    s = Scheduler(4)
    s.enqueue("gen", frozenset({"generator"}))
    s.enqueue("down", frozenset({"downscaler"}))
    s.enqueue("sync", frozenset({"lazer"}))
    s.enqueue("down+lazer", frozenset({"downscaler", "lazer"}))
    s.enqueue("prof", frozenset({"profiler"}))
    # The fourth waits for both the downscale and the sync; the profiler job is not held back by it.
    assert s.start_ready() == ["gen", "down", "sync", "prof"]
    s.finish("down")
    assert s.start_ready() == []
    s.finish("sync")
    assert s.start_ready() == ["down+lazer"]
    with pytest.raises(ValueError):
        s.enqueue("nothing", frozenset())
    with pytest.raises(ValueError):
        s.enqueue("gen", frozenset({"x"}))
    with pytest.raises(ValueError):
        Scheduler(0)


def test_a_waiting_job_is_not_overtaken_on_its_resources():
    s = Scheduler(4)
    s.enqueue("sync", frozenset({"lazer"}))
    assert s.start_ready() == ["sync"]
    s.enqueue("down+lazer", frozenset({"downscaler", "lazer"}))
    s.enqueue("down", frozenset({"downscaler"}))
    # The plain downscale would fit now, but the earlier one claimed the folder first.
    assert s.start_ready() == []
    s.finish("sync")
    assert s.start_ready() == ["down+lazer"]
    s.finish("down+lazer")
    assert s.start_ready() == ["down"]


# --- what each job kind claims -----------------------------------------------------------------------------------


def test_every_job_kind_has_a_claim():
    kinds = set(build_actions(DashboardConfig()))
    assert kinds == set(JOB_RESOURCES)
    assert job_resources("something_new", {}) == claims.ALL


@pytest.mark.parametrize("kind", ["sync_setup", "sync_once", "sync_revert", "lazer_control"])
def test_library_jobs_claim_the_library(kind):
    assert claims.LAZER in job_resources(kind, {})
    assert claims.LAZER in job_resources(kind, {"dry_run": True})


def test_profiler_jobs_claim_the_profiler_database():
    for kind in ("profile_replay", "profile_player", "profile_habits", "import_replays"):
        assert job_resources(kind, {}) == {claims.PROFILER}
        assert job_resources(kind, {"save": False}) == {claims.PROFILER}


def test_a_downscale_claims_the_library_only_when_it_writes_it():
    assert job_resources("downscale", {}) == {claims.DOWNSCALER}
    for off in (False, "false", "0", "", "no", None):
        assert job_resources("downscale", {"sync_lazer": off}) == {claims.DOWNSCALER}
    for on in (True, "true", "1", "yes", "on", " TRUE "):
        assert job_resources("downscale", {"sync_lazer": on}) == {claims.DOWNSCALER, claims.LAZER}
    # The action reads the flag with the same helper, so the claim and the write agree.
    for value in (True, "true", "1", "yes", "on", False, "0", "", None, 1, 0):
        assert (claims.LAZER in job_resources("downscale", {"sync_lazer": value})) == actions._flag({"sync_lazer": value}, "sync_lazer")


def test_generation_runs_beside_the_other_tools():
    gen = job_resources("generate", {"backend": "mapperatorinator"})
    for kind, params in (("downscale", {"sync_lazer": True}), ("profile_replay", {}), ("sync_once", {}),
                         ("import_replays", {})):
        assert not (gen & job_resources(kind, params)), kind


@pytest.mark.parametrize("name", ["true", "1", "yes", "on", "TRUE", " on ", "x.osu"])
def test_an_uploaded_files_parameter_never_turns_a_flag_on_when_the_job_runs(tmp_path, name):
    # Uploads replace their parameter with a path in the job's folder when the job starts; whatever the file's name,
    # that path reads as unset, so the claim made at submission (from the name) covers the write.
    seen = {}

    def downscale_like(params, ctx):
        seen["flag"] = actions._flag(params, "sync_lazer")
        return {}

    manager = JobManager({"downscale": downscale_like}, tmp_path / "jobs", resources=job_resources)
    try:
        job = _wait(manager.submit("downscale", {}, uploads={"sync_lazer": (name, b"x")}))
        assert job.status == "done", job.error
        assert seen["flag"] is False
    finally:
        manager.shutdown()


# --- write claims, enforced where the writes happen -------------------------------------------------------------


def test_require_outside_a_job_does_nothing():
    claims.require(claims.LAZER)
    with claims.holding({claims.PROFILER}):
        claims.require(claims.PROFILER)
        with pytest.raises(claims.ClaimError):
            claims.require(claims.LAZER)
    claims.require(claims.LAZER)
    assert claims.current() is None


def test_a_job_that_writes_the_profiler_database_without_claiming_it_fails_before_writing(tmp_path, monkeypatch):
    from proj7k.profiler.storage import MatchSnapshot, ProfilerStorage

    db = tmp_path / "profiler.db"

    def snapshot():
        return MatchSnapshot(player_name="P", timestamp=1.0, beatmap_hash="h", replay_hash="r1", play_duration_s=60.0,
                             completion_rate=1.0, is_valid_play=True, is_failed=False, overall_ur=None, capacities={})

    def write(params, ctx):
        with ProfilerStorage(db_path=db) as storage:
            storage.save_snapshot(snapshot())
        return {}

    monkeypatch.setitem(JOB_RESOURCES, "profile_replay", frozenset({claims.DOWNSCALER}))  # a wrong claim
    manager = JobManager({"profile_replay": write, "import_replays": write}, tmp_path / "jobs", resources=job_resources)
    try:
        bad = _wait(manager.submit("profile_replay"))
        assert bad.status == "failed" and "profiler" in bad.error
        with ProfilerStorage(db_path=db) as storage:
            assert storage.list_players() == []  # nothing written
        good = _wait(manager.submit("import_replays"))
        assert good.status == "done", good.error
        with ProfilerStorage(db_path=db) as storage:
            assert storage.list_players() == ["P"]
    finally:
        manager.shutdown()


def test_library_writes_check_their_claim(tmp_path):
    from proj7k.lazer.backup import LazerBackupManager
    from proj7k.lazer.bridge import RealmBridgeClient
    from proj7k.lazer.client import LazerClient

    realm = tmp_path / "client.realm"
    realm.write_bytes(b"realm")
    backup = LazerBackupManager(realm_path=realm, cache_dir=tmp_path / "cache")
    bridge = RealmBridgeClient(bridge_dir=tmp_path / "no-bridge", default_realm_path=realm)
    game = LazerClient(executable=tmp_path / "osu!", run=lambda args: None, launcher=lambda exe: None)
    writes = [
        backup.create_realm_snapshot,
        lambda: backup.record_original_states([]),
        backup.clear_backup_states,
        lambda: bridge.apply_batch_update([], auto_setup=False),
        lambda: bridge.revert_batch([], auto_setup=False),
        game.close,
        game.start,
        game.restart,
    ]
    with claims.holding({claims.PROFILER, claims.DOWNSCALER, claims.GENERATOR}):
        for write in writes:
            with pytest.raises(claims.ClaimError):
                write()
    assert backup.list_snapshots() == []
    assert not backup.state_file.exists()


def test_a_job_without_a_resource_function_may_write_anything(tmp_path):
    def write(params, ctx):
        claims.require(claims.LAZER)
        claims.require(claims.PROFILER)
        return {}

    manager = JobManager({"w": write}, tmp_path / "jobs")
    try:
        assert _wait(manager.submit("w")).status == "done"
    finally:
        manager.shutdown()


# --- audits of every writer -------------------------------------------------------------------------------------


def _functions(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _requires(fn: ast.AST, resource: str) -> bool:
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "require"
                and node.args and isinstance(node.args[0], ast.Attribute) and node.args[0].attr == resource.upper()):
            return True
    return False


def _js_functions(text: str):
    """{name: body} of bridge.js's top-level `async function` handlers (bodies end at the next one)."""
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"^async function (\w+)\(", text, flags=re.M)]
    return {name: text[pos:(starts[i + 1][0] if i + 1 < len(starts) else len(text))] for i, (pos, name) in enumerate(starts)}


def test_bridge_js_writes_the_library_only_in_update_and_revert():
    text = (REPO / "tools" / "lazer-bridge" / "bridge.js").read_text(encoding="utf-8")
    handlers = _js_functions(text)
    writers = {name for name, body in handlers.items() if "readOnly: false" in body or re.search(r"realm\.write\(", body)}
    assert writers == {"handleUpdateBatch", "handleRevertBatch"}
    # Every other Realm the bridge opens is read-only, and nothing outside the handlers opens one.
    for name, body in handlers.items():
        if name in writers:
            continue
        for opened in re.findall(r"new Realm\(\{[^}]*\}\)", body):
            assert "readOnly: true" in opened, (name, opened)
    assert text.count("new Realm(") == sum(body.count("new Realm(") for body in handlers.values())
    # Only the two commands reach the writers.
    cases = dict(re.findall(r"case '([\w-]+)':\s*(?:[^;]*?)(handle\w+)\(", text))
    assert {cmd for cmd, fn in cases.items() if fn in writers} == {"update-batch", "revert-batch"}


def test_only_the_claimed_bridge_methods_send_the_write_commands():
    bridge = SRC / "lazer" / "bridge.py"
    senders = {}
    for fn in _functions(bridge):
        for node in ast.walk(fn):
            if isinstance(node, ast.Constant) and node.value in ("update-batch", "revert-batch"):
                senders[node.value] = fn
    assert {k: v.name for k, v in senders.items()} == {"update-batch": "apply_batch_update", "revert-batch": "revert_batch"}
    assert all(_requires(fn, claims.LAZER) for fn in senders.values())
    # Nothing else in the package runs the bridge script, names its write commands, or sends a command of its own.
    for path in SRC.rglob("*.py"):
        if path == bridge:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in ("update-batch", "revert-batch") and "bridge.js" not in node.value, path
            if isinstance(node, ast.Attribute):
                assert node.attr != "_run_command", path


def test_every_file_write_in_the_backup_manager_checks_its_claim():
    writing = ("copy2", "copyfile", "unlink", "replace", "rename", "write_text", "write_bytes", "rmtree")
    for fn in _functions(SRC / "lazer" / "backup.py"):
        calls = {n.func.attr for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        opens_for_write = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open"
            and any(isinstance(a, ast.Constant) and isinstance(a.value, str) and set(a.value) & set("wax+") for a in n.args[1:])
            for n in ast.walk(fn)
        )
        if calls & set(writing) or opens_for_write:
            assert _requires(fn, claims.LAZER), fn.name


def test_every_profiler_database_write_checks_its_claim():
    sql_write = re.compile(r"^\s*(INSERT|DELETE|UPDATE|REPLACE)\b", re.I | re.M)
    # Schema statements run when any connection opens (readers too): idempotent, each in one SQLite transaction.
    schema = {"_init_schema", "_dedupe_replay_hashes", "_ensure_player_index"}
    writers = set()
    for fn in _functions(SRC / "profiler" / "storage.py"):
        if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and sql_write.search(n.value) for n in ast.walk(fn)):
            writers.add(fn.name)
    assert writers - schema == {"save_snapshot", "delete_replay"}
    for fn in _functions(SRC / "profiler" / "storage.py"):
        if fn.name in writers - schema:
            assert _requires(fn, claims.PROFILER), fn.name
    # The profiler database is opened only by its storage class.
    for path in SRC.rglob("*.py"):
        if "sqlite3.connect" in path.read_text(encoding="utf-8"):
            # The others are the online catalog's own database, and the generator's evaluation reading it immutable.
            assert path == SRC / "profiler" / "storage.py" or "catalog" in path.parts or path == SRC / "generator" / "evaluate.py", path


def test_the_game_is_closed_and_started_only_under_a_claim():
    for fn in _functions(SRC / "lazer" / "client.py"):
        if fn.name in ("close", "start"):
            assert _requires(fn, claims.LAZER), fn.name


def test_no_action_starts_threads_of_its_own():
    # A write claim lives on the job's thread; the actions' code never hands work to another thread.
    allowed = {SRC / "dashboard" / "jobs.py", SRC / "dashboard" / "player.py", SRC / "batch.py"}
    for path in SRC.rglob("*.py"):
        if path in allowed or "catalog" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"threading\.Thread\(|ThreadPoolExecutor\(|ProcessPoolExecutor\(", text), path


# --- the job manager under real threads -------------------------------------------------------------------------


def _by_kind(kind, params):
    return frozenset(params["res"])


def test_jobs_on_different_resources_run_at_the_same_time(tmp_path):
    barrier = threading.Barrier(3, timeout=5)

    def meet(params, ctx):
        barrier.wait()
        return {}

    manager = JobManager({"m": meet}, tmp_path / "jobs", resources=_by_kind)
    try:
        jobs = [manager.submit("m", {"res": [r]}) for r in ("lazer", "generator", "profiler")]
        for job in jobs:
            assert _wait(job).status == "done", job.error  # a broken barrier (no overlap) fails the jobs
    finally:
        manager.shutdown()


def test_jobs_on_the_same_resource_run_one_after_another(tmp_path):
    active, peak, order = [0], [0], []
    lock = threading.Lock()

    def work(params, ctx):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            order.append(params["n"])
        time.sleep(0.02)
        with lock:
            active[0] -= 1
        return {}

    manager = JobManager({"w": work}, tmp_path / "jobs", resources=_by_kind)
    try:
        jobs = [manager.submit("w", {"res": ["lazer"], "n": i}) for i in range(5)]
        for job in jobs:
            _wait(job)
        assert peak[0] == 1
        assert order == list(range(5))
    finally:
        manager.shutdown()


def test_random_jobs_never_share_a_resource_while_running(tmp_path):
    rng = random.Random(7)
    names = ("lazer", "profiler", "downscaler", "generator")
    lock = threading.Lock()
    holders = {r: None for r in names}
    starts, violations, peak, active = [], [], [0], [0]

    def work(params, ctx):
        mine = params["res"]
        with lock:
            for r in mine:
                if holders[r] is not None:
                    violations.append((params["n"], r, holders[r]))
                holders[r] = params["n"]
            starts.append(params["n"])
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(params["sleep"])
        with lock:
            for r in mine:
                holders[r] = None
            active[0] -= 1
        if params["fail"]:
            raise RuntimeError("boom")
        return {}

    manager = JobManager({"w": work}, tmp_path / "jobs", resources=_by_kind, max_parallel=3)
    try:
        specs, jobs = [], []
        for n in range(80):
            res = rng.sample(names, rng.randint(1, 2))
            specs.append(set(res))
            jobs.append(manager.submit("w", {"res": res, "n": n, "sleep": rng.random() * 0.004, "fail": rng.random() < 0.1}))
            if rng.random() < 0.1 and jobs:
                manager.cancel(rng.choice(jobs).id)
        for job in jobs:
            _wait(job, timeout=30)
        assert violations == []
        assert 1 < peak[0] <= 3
        ran = [j for j in jobs if j.status in ("done", "failed")]
        assert len(ran) == len(starts)
        position = {n: i for i, n in enumerate(starts)}
        # Jobs that share a resource started in the order they were submitted.
        ids = [j.params["n"] for j in ran]
        for x, y in itertools.combinations(ids, 2):
            if specs[x] & specs[y]:
                assert position[x] < position[y], (x, y)
    finally:
        manager.shutdown()


def test_cancelling_a_waiting_job_lets_the_next_one_start(tmp_path):
    gate = threading.Event()
    started = threading.Event()

    def hold(params, ctx):
        gate.wait(5)
        return {}

    def mark(params, ctx):
        started.set()
        return {}

    manager = JobManager({"hold": hold, "mark": mark}, tmp_path / "jobs", resources=_by_kind)
    try:
        first = manager.submit("hold", {"res": ["lazer"]})
        blocked = manager.submit("hold", {"res": ["lazer", "downscaler"]})
        later = manager.submit("mark", {"res": ["downscaler"]})
        assert not started.wait(0.1)  # held back by the waiting job on the downscaler
        assert manager.cancel(blocked.id) and blocked.status == "cancelled"
        assert started.wait(5)
        assert _wait(later).status == "done"
        gate.set()
        assert _wait(first).status == "done"
        assert blocked.started_at is None
    finally:
        manager.shutdown()


def test_parallel_jobs_keep_their_own_logs_and_language(tmp_path):
    import logging

    from proj7k.i18n import tr

    log = logging.getLogger("proj7k.test_scheduler")
    log.setLevel(logging.INFO)
    barrier = threading.Barrier(2, timeout=5)

    def talk(params, ctx):
        barrier.wait()
        for i in range(20):
            log.info(f"{params['who']} {i} {tr('中', 'en')}")
        barrier.wait()
        return {}

    manager = JobManager({"t": talk}, tmp_path / "jobs", resources=_by_kind)
    try:
        a = manager.submit("t", {"res": ["lazer"], "who": "A"}, lang="zh")
        b = manager.submit("t", {"res": ["profiler"], "who": "B"}, lang="en")
        _wait(a), _wait(b)
        assert len(a.logs) == 20 and all(" A " in f" {line} " and line.endswith("中") for line in a.logs)
        assert len(b.logs) == 20 and all(" B " in f" {line} " and line.endswith("en") for line in b.logs)
    finally:
        manager.shutdown()


def test_shutdown_cancels_waiting_jobs(tmp_path):
    gate = threading.Event()
    manager = JobManager({"hold": lambda p, c: gate.wait(5)}, tmp_path / "jobs", resources=_by_kind)
    running = manager.submit("hold", {"res": ["lazer"]})
    waiting = manager.submit("hold", {"res": ["lazer"]})
    manager.shutdown()
    gate.set()
    assert waiting.status == "cancelled"
    _wait(running)
    with pytest.raises(RuntimeError):
        manager.submit("hold", {"res": ["lazer"]})


def test_the_dashboard_schedules_by_job_resources(tmp_path):
    from proj7k.dashboard import DashboardServer

    server = DashboardServer(port=0, no_watch=True, config=DashboardConfig(
        realm_path=tmp_path / "lazer" / "client.realm", cache_dir=tmp_path / "cache", db_path=tmp_path / "p.db",
        output_dir=tmp_path / "dash"))
    try:
        assert server.jobs.resources is job_resources
        job = server.jobs.submit("profile_player", {"player": "nobody"})
        _wait(job)
        assert job.to_dict()["resources"] == [claims.PROFILER]
    finally:
        server.jobs.shutdown()


def test_the_bridge_is_installed_once_when_threads_ask_together(tmp_path, monkeypatch):
    from proj7k.lazer import bridge as bridge_mod

    installed = threading.Event()
    runs = []

    class Status:
        def __init__(self, ready):
            self.is_ready, self.has_node, self.has_npm, self.npm_path = ready, True, True, "npm"

    def check(self):
        return Status(installed.is_set())

    def npm(cmd, **kwargs):
        runs.append(cmd)
        time.sleep(0.05)
        installed.set()

        class Done:
            returncode, stderr = 0, ""
        return Done()

    (tmp_path / "bridge").mkdir()
    monkeypatch.setattr(bridge_mod.RealmBridgeClient, "check_environment", check)
    monkeypatch.setattr(bridge_mod.subprocess, "run", npm)
    client = bridge_mod.RealmBridgeClient(bridge_dir=tmp_path / "bridge")
    threads = [threading.Thread(target=client.ensure_installed) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(runs) == 1
