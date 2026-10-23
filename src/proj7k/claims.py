"""
Write claims (ADR-0043): what a dashboard job may write, checked where the writing happens.

The dashboard runs jobs side by side when their claimed resources are disjoint. That is only safe if a job writes
nothing it has not claimed, so each function that writes a shared store calls `require(resource)` first: on a thread
running a job, a resource the job did not claim raises `ClaimError` before anything is written. On any other thread
(the command line, tests, the dashboard's own readers) `require` does nothing.
"""

from contextlib import contextmanager
import threading
from typing import FrozenSet, Iterable, Iterator, Optional

#: The osu!lazer library: client.realm, proj7k's backups of it (snapshots and original states), its collections,
#: and the game process (closing or starting it).
LAZER = "lazer"
#: The profiler database (~/.proj7k/profiler.db) and the replay viewers and practice bundles written beside it.
PROFILER = "profiler"
#: The downscaler's output folder (practice charts and packs).
DOWNSCALER = "downscaler"
#: The generator's GPU (Mapperatorinator, Demucs), their installs and caches, and its output folder.
GENERATOR = "generator"
ALL: FrozenSet[str] = frozenset({LAZER, PROFILER, DOWNSCALER, GENERATOR})

_local = threading.local()


class ClaimError(RuntimeError):
    """A job tried to write a resource it did not claim (a bug in its claim, caught before the write)."""


def current() -> Optional[FrozenSet[str]]:
    """What the job on this thread claimed, or None when no job runs on it."""
    return getattr(_local, "claims", None)


@contextmanager
def holding(resources: Iterable[str]) -> Iterator[None]:
    """Run the body as a job that claimed `resources`."""
    previous = current()
    _local.claims = frozenset(resources)
    try:
        yield
    finally:
        _local.claims = previous


def require(resource: str) -> None:
    """Called before writing `resource`: raises when the job on this thread did not claim it."""
    claims = current()
    if claims is not None and resource not in claims:
        raise ClaimError(f"This job writes '{resource}' but claimed only {sorted(claims) or 'nothing'} (ADR-0043)")
