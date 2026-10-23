"""
Jobs: the dashboard's long-running work (a library sync, a downscale, a replay diagnosis) run off the event loop.

Each job claims a set of resources when it is submitted (the osu!lazer library, the profiler database, the
generator's GPU and caches, ...), and jobs whose sets overlap run one at a time, in submission order; jobs whose sets
are disjoint run side by side (ADR-0043, replacing ADR-0024's single worker). The sync and the downscaler's lazer
injection both claim the library, so they still never write it at the same time. `Scheduler` is the whole decision,
with no threads of its own, so it can be checked exhaustively (tests/test_dashboard_scheduler.py). Without a resource
function every job claims the same resource and the jobs run one at a time, as before.

Each job's log lines are captured from the standard `logging` records its worker thread emits, so the actions log as
the CLIs do.

A job's outputs are registered files (`Job.artifacts`); the server serves exactly those, by index, never a path a
client names.
"""

from concurrent.futures import ThreadPoolExecutor
import contextlib
from dataclasses import dataclass, field
import itertools
import logging
from pathlib import Path
import re
import shutil
import threading
import time
import traceback
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple
from urllib.parse import quote

from proj7k import claims, i18n

logger = logging.getLogger("proj7k.dashboard.jobs")

#: Log lines kept per job; older lines are dropped from the front.
MAX_LOG_LINES = 400
#: Finished jobs kept in memory (and their working folders on disk); the oldest are forgotten first.
MAX_FINISHED_JOBS = 50
#: Jobs running at once, at most (each holds a worker thread).
MAX_PARALLEL_JOBS = 4
#: What a job claims when nobody says otherwise: one resource every such job shares, so they run one at a time.
SERIAL: FrozenSet[str] = frozenset({"serial"})


class JobCancelled(Exception):
    """Raised inside an action that saw its job's stop flag."""


@dataclass
class Artifact:
    """A file a job produced, offered for download (and, for an .osz, for import into osu!lazer)."""
    path: Path
    label: str = ""

    def to_dict(self, job_id: str, index: int) -> Dict[str, Any]:
        return {
            "name": self.path.name,
            "label": self.label or self.path.name,
            "path": str(self.path),
            "url": f"/files/{job_id}/{index}/{quote(self.path.name)}",
            "kind": self.path.suffix.lower().lstrip("."),
        }


@dataclass
class JobContext:
    """What an action gets: its own working folder, a way to register outputs, and its stop flag."""
    job: "Job"
    workdir: Path

    def add_artifact(self, path: Path, label: str = "") -> None:
        self.job.artifacts.append(Artifact(Path(path), label))

    @property
    def stop_event(self) -> threading.Event:
        return self.job.stop_event


@dataclass
class Job:
    id: str
    kind: str
    params: Dict[str, Any]
    status: str = "queued"  # queued | running | done | failed | cancelled
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    logs: List[str] = field(default_factory=list)
    artifacts: List[Artifact] = field(default_factory=list)
    stop_event: threading.Event = field(default_factory=threading.Event)
    workdir: Optional[Path] = None
    #: Files the page uploaded with the job, by parameter: (file name, content). Written into the job's folder
    #: when it starts, and the parameter becomes the written path.
    uploads: Dict[str, Tuple[str, bytes]] = field(default_factory=dict, repr=False)
    #: The language of the page that started it, for its messages (ADR-0035).
    lang: str = "zh"
    #: What it holds while it runs; a job waits while an earlier or running job holds any of them (ADR-0043).
    resources: FrozenSet[str] = SERIAL

    @property
    def finished(self) -> bool:
        return self.status in ("done", "failed", "cancelled")

    def to_dict(self, with_logs: bool = False) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "kind": self.kind,
            "params": self.params,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "artifacts": [a.to_dict(self.id, i) for i, a in enumerate(self.artifacts)],
            # What it is doing now, for a list of jobs that doesn't follow every log line.
            "last_log": self.logs[-1] if self.logs else None,
            "resources": sorted(self.resources),
        }
        if with_logs:
            data["logs"] = list(self.logs)
        return data


Action = Callable[[Dict[str, Any], JobContext], Optional[Dict[str, Any]]]
Notify = Callable[[Dict[str, Any]], None]
#: (kind, params) -> the resources a job of that kind with those parameters claims.
Resources = Callable[[str, Dict[str, Any]], FrozenSet[str]]


class Scheduler:
    """
    Which queued jobs may start (ADR-0043). Not thread-safe: the caller holds one lock around every call.

    A queued job starts when (1) no running job holds any of its resources, (2) no job queued before it claims any of
    them, and (3) fewer than `max_running` jobs run. So two jobs that share a resource never run at the same time, and
    they start in the order they were submitted; a job blocks only the later jobs it shares a resource with.
    """

    def __init__(self, max_running: int = MAX_PARALLEL_JOBS):
        if max_running < 1:
            raise ValueError("max_running must be at least 1")
        self.max_running = max_running
        self.queue: List[Tuple[str, FrozenSet[str]]] = []
        self.running: Dict[str, FrozenSet[str]] = {}

    def held(self) -> FrozenSet[str]:
        return frozenset().union(*self.running.values())

    def enqueue(self, job_id: str, resources: FrozenSet[str]) -> None:
        if not resources:
            raise ValueError("a job claims at least one resource")
        if job_id in self.running or any(q == job_id for q, _ in self.queue):
            raise ValueError(f"job {job_id} is already scheduled")
        self.queue.append((job_id, frozenset(resources)))

    def remove(self, job_id: str) -> bool:
        """Drop a queued job (cancelled before it started); False when it isn't queued."""
        for i, (queued, _) in enumerate(self.queue):
            if queued == job_id:
                del self.queue[i]
                return True
        return False

    def finish(self, job_id: str) -> None:
        """A running job ended (done, failed or stopped) and releases its resources."""
        del self.running[job_id]

    def start_ready(self) -> List[str]:
        """Move every job that may start now from the queue to the running set; the ids, in queue order."""
        started: List[str] = []
        busy = set(self.held())
        waiting: List[Tuple[str, FrozenSet[str]]] = []
        for job_id, resources in self.queue:
            if len(self.running) < self.max_running and not (resources & busy):
                self.running[job_id] = resources
                started.append(job_id)
            else:
                waiting.append((job_id, resources))
            # Started or not, a job keeps everything after it in the queue off its resources: a running one because
            # it holds them, a waiting one so it starts first (no later job overtakes it on a shared resource).
            busy |= resources
        self.queue = waiting
        return started


class _JobLogHandler(logging.Handler):
    """Routes each log record to the job running on the thread that emitted it."""

    def __init__(self, manager: "JobManager"):
        super().__init__(level=logging.INFO)
        self.manager = manager
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        job = self.manager._job_on_thread()
        if job is None:
            return
        try:
            self.manager.append_log(job, self.format(record))
        except Exception:
            self.handleError(record)


class JobManager:
    """
    Runs jobs on worker threads as the `Scheduler` allows and reports every change through `notify` (called from any
    thread). `resources` says what each job claims; without it every job claims `SERIAL` and they run one at a time.
    """

    def __init__(self, actions: Dict[str, Action], root_dir: Path, notify: Optional[Notify] = None,
                 resources: Optional[Resources] = None, max_parallel: int = MAX_PARALLEL_JOBS):
        self.actions = actions
        self.root_dir = Path(root_dir)
        self.notify: Notify = notify or (lambda frame: None)
        self.resources: Resources = resources or (lambda kind, params: SERIAL)
        self._claims_checked = resources is not None
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._scheduler = Scheduler(max_parallel)
        self._closed = False
        self._ids = itertools.count(1)
        self._prefix = time.strftime("%Y%m%d-%H%M%S")
        self._local = threading.local()
        # The scheduler never runs more than `max_parallel` jobs, so a started job gets a thread at once (or as soon as
        # the job that made room for it has returned from its thread).
        self._executor = ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="proj7k-job")
        self._handler = _JobLogHandler(self)
        logging.getLogger().addHandler(self._handler)

    # --- bookkeeping -------------------------------------------------------------------------------------------

    def _job_on_thread(self) -> Optional[Job]:
        return getattr(self._local, "job", None)

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at)

    def append_log(self, job: Job, line: str) -> None:
        job.logs.append(line)
        if len(job.logs) > MAX_LOG_LINES:
            del job.logs[: len(job.logs) - MAX_LOG_LINES]
        self.notify({"type": "job_log", "id": job.id, "line": line})

    def _publish(self, job: Job) -> None:
        self.notify({"type": "job_update", "job": job.to_dict()})

    def _forget_old(self) -> None:
        with self._lock:
            finished = sorted((j for j in self._jobs.values() if j.finished), key=lambda j: j.created_at)
            stale = finished[: max(0, len(finished) - MAX_FINISHED_JOBS)]
            for job in stale:
                del self._jobs[job.id]
        for job in stale:
            if job.workdir is not None:
                shutil.rmtree(job.workdir, ignore_errors=True)

    # --- lifecycle ---------------------------------------------------------------------------------------------

    def submit(
        self,
        kind: str,
        params: Optional[Dict[str, Any]] = None,
        uploads: Optional[Dict[str, Tuple[str, bytes]]] = None,
        lang: str = "zh",
    ) -> Job:
        if kind not in self.actions:
            raise ValueError(f"Unknown job kind: {kind!r}")
        job = Job(id=f"{self._prefix}-{next(self._ids):03d}", kind=kind, params=dict(params or {}))
        job.uploads = dict(uploads or {})
        job.lang = lang
        for key, (name, _) in job.uploads.items():
            job.params[key] = name
        # Claimed from the parameters as the action will read them; an upload's parameter changes from its name to
        # a path in the job's folder when it starts, which no flag reads as set (ADR-0043).
        job.resources = frozenset(self.resources(kind, job.params))
        job.workdir = self.root_dir / job.id
        with self._lock:
            if self._closed:
                raise RuntimeError("The job manager is shut down")
            self._jobs[job.id] = job
            self._scheduler.enqueue(job.id, job.resources)
        self._publish(job)
        self._dispatch()
        return job

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued job, or ask a running one to stop (only long loops, such as the daemon, check)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.finished:
                return False
            job.stop_event.set()
            dropped = self._scheduler.remove(job.id)
            if dropped:
                job.status = "cancelled"
                job.finished_at = time.time()
        if dropped:
            self._publish(job)
            self._dispatch()  # a later job it held back may start now
        return True

    def _dispatch(self) -> None:
        """Start every job the scheduler lets start. Their status turns to running under the lock, so a cancel
        either removes a job from the queue first or finds it running."""
        with self._lock:
            if self._closed:
                return
            started = [self._jobs[job_id] for job_id in self._scheduler.start_ready()]
            for job in started:
                job.status = "running"
                job.started_at = time.time()
        for job in started:
            self._publish(job)
            self._executor.submit(self._run, job)

    def _run(self, job: Job) -> None:
        self._local.job = job
        language = i18n.enter(job.lang)
        try:
            assert job.workdir is not None
            job.workdir.mkdir(parents=True, exist_ok=True)
            for key, (name, content) in job.uploads.items():
                target = job.workdir / (re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or key)
                target.write_bytes(content)
                job.params[key] = str(target)
            job.uploads = {}
            # A job that runs alone (no resource function) may write anything; otherwise only what it claimed.
            guard = claims.holding(job.resources) if self._claims_checked else contextlib.nullcontext()
            with guard:
                result = self.actions[job.kind](job.params, JobContext(job=job, workdir=job.workdir))
            job.result = result or {}
            job.status = "done"
        except JobCancelled:
            job.status = "cancelled"
        except Exception as e:
            job.status = "failed"
            job.error = str(e) or type(e).__name__
            logger.debug("Job %s failed", job.id, exc_info=True)
            self.append_log(job, "".join(traceback.format_exception_only(type(e), e)).strip())
        finally:
            i18n.leave(language)
            self._local.job = None
            job.finished_at = time.time()
            with self._lock:
                self._scheduler.finish(job.id)
            self._publish(job)
            self._forget_old()
            self._dispatch()

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
            dropped = [job for job in jobs if self._scheduler.remove(job.id)]
            for job in dropped:
                job.status = "cancelled"
                job.finished_at = time.time()
        for job in jobs:
            job.stop_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
        logging.getLogger().removeHandler(self._handler)
