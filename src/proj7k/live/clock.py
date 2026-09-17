"""
ClockState: Domain model and state machine for live gameplay clock synchronization.

SPEC-P2.4-03 / ADR-0010.
"""

from dataclasses import dataclass
import time
from typing import Any, Dict, Literal

ClockStatus = Literal["idle", "playing"]


@dataclass
class ClockState:
    """
    Represents the real-time playback clock state of the active beatmap.
    Encapsulates state transitions for Started, Seeking, Stopped, and Reset.
    """
    status: ClockStatus = "idle"
    start_ms: float = 0.0        # song position in ms at sync point
    rate: float = 0.0            # 1.0 when playing, 0.0 when idle
    last_sync_time: float = 0.0  # server epoch timestamp in seconds

    @classmethod
    def create_idle(cls, start_ms: float = 0.0) -> "ClockState":
        """Creates an idle clock state instance."""
        return cls(status="idle", start_ms=start_ms, rate=0.0, last_sync_time=time.time())

    @property
    def is_playing(self) -> bool:
        return self.status == "playing"

    def start(self) -> None:
        """Transitions clock to playing at 1.0x rate."""
        self.status = "playing"
        self.rate = 1.0
        self.last_sync_time = time.time()

    def seek(self, time_ms: float) -> None:
        """Updates seeking position. Maintains rate if playing, 0.0 if idle."""
        self.start_ms = time_ms
        self.last_sync_time = time.time()
        self.rate = 1.0 if self.is_playing else 0.0

    def stop(self) -> None:
        """Transitions clock to idle at 0.0x rate."""
        self.status = "idle"
        self.rate = 0.0
        self.last_sync_time = time.time()

    def reset(self) -> None:
        """Resets clock to idle at 0.0ms."""
        self.status = "idle"
        self.start_ms = 0.0
        self.rate = 0.0
        self.last_sync_time = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Converts clock state to canonical WebSocket 'clock_sync' frame."""
        return {
            "type": "clock_sync",
            "status": self.status,
            "active": self.is_playing,
            "start_ms": self.start_ms,
            "time_ms": self.start_ms,
            "rate": self.rate,
            "server_time": self.last_sync_time,
        }
