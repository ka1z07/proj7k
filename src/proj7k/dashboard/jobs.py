"""
Jobs: the dashboard's long-running work (a library sync, a downscale, a replay diagnosis) run off the event loop.

Jobs run one at a time, in submission order: they share osu!lazer's database and the CPU, and the sync and the
downscaler's lazer injection must never write the library at the same time (ADR-0024). Each job's log lines are
captured from the standard `logging` records its worker thread emits, so the actions log as the CLIs do.

A job's outputs are registered files (`Job.artifacts`); the server serves exactly those, by index, never a path a
client names.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import itertools
import logging
from pathlib import Path
import re
import shutil
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

logger = logging.getLogger("proj7k.dashboard.jobs")

#: Log lines kept per job; older lines are dropped from the front.
MAX_LOG_LINES = 400
#: Finished jobs kept in memory (and their working folders on disk); the oldest are forgotten first.
MAX_FINISHED_JOBS = 50


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
        }
        if with_logs:
            data["logs"] = list(self.logs)
        return data


Action = Callable[[Dict[str, Any], JobContext], Optional[Dict[str, Any]]]
Notify = Callable[[Dict[str, Any]], None]


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
    """Queues jobs onto one worker thread and reports every change through `notify` (called from any thread)."""

    def __init__(self, actions: Dict[str, Action], root_dir: Path, notify: Optional[Notify] = None):
        self.actions = actions
        self.root_dir = Path(root_dir)
        self.notify: Notify = notify or (lambda frame: None)
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._prefix = time.strftime("%Y%m%d-%H%M%S")
        self._local = threading.local()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="proj7k-job")
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
    ) -> Job:
        if kind not in self.actions:
            raise ValueError(f"Unknown job kind: {kind!r}")
        job = Job(id=f"{self._prefix}-{next(self._ids):03d}", kind=kind, params=dict(params or {}))
        job.uploads = dict(uploads or {})
        for key, (name, _) in job.uploads.items():
            job.params[key] = name
        job.workdir = self.root_dir / job.id
        with self._lock:
            self._jobs[job.id] = job
        self._publish(job)
        self._executor.submit(self._run, job)
        return job

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued job, or ask a running one to stop (only long loops, such as the daemon, check)."""
        job = self.get(job_id)
        if job is None or job.finished:
            return False
        job.stop_event.set()
        if job.status == "queued":
            job.status = "cancelled"
            job.finished_at = time.time()
            self._publish(job)
        return True

    def _run(self, job: Job) -> None:
        if job.status == "cancelled":
            return
        job.status = "running"
        job.started_at = time.time()
        self._publish(job)
        self._local.job = job
        try:
            assert job.workdir is not None
            job.workdir.mkdir(parents=True, exist_ok=True)
            for key, (name, content) in job.uploads.items():
                target = job.workdir / (re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or key)
                target.write_bytes(content)
                job.params[key] = str(target)
            job.uploads = {}
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
            self._local.job = None
            job.finished_at = time.time()
            self._publish(job)
            self._forget_old()

    def shutdown(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.stop_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
        logging.getLogger().removeHandler(self._handler)
