"""
A simulated player for tests and for demo pages: plays a chart the way the engine says a player of a given
level would (ADR-0020): each event is lost with the engine's own probability p_i(theta), and timing
scatter grows with that probability. The replay it returns goes through the same matcher as a real one.
"""

import random
from typing import List, Optional, Tuple

import numpy as np

from proj7k.engine.solver import loss
from proj7k.field import ChartField
from proj7k.parser import Beatmap7K, NoteType
from proj7k.profiler.osr import ReplayFrame


def simulate_frames(
    beatmap: Beatmap7K,
    field: ChartField,
    level: float,
    seed: int = 7,
    fail_at_ms: Optional[float] = None,
    ghost_rate: float = 0.01,
) -> List[ReplayFrame]:
    """Replay frames (song time, rate 1) of a player of difficulty level `level` Hz."""
    rnd = random.Random(seed)
    p = loss(field.d, level, field.params)
    column_events: List[Tuple[float, int, int]] = []  # (time_ms, column, +1 press / -1 release)
    for ho in sorted(beatmap.hit_objects, key=lambda h: (h.time, h.column)):
        if fail_at_ms is not None and ho.time > fail_at_ms:
            break
        i = field.press_index(ho.column, ho.time / 1000.0)
        pl = float(p[i]) if i is not None else 0.0
        if rnd.random() < pl * 0.6:          # the press never comes
            continue
        spread = 6.0 + 55.0 * pl             # ms
        down = ho.time + rnd.gauss(2.0 * pl * 20, spread)
        if ho.note_type == NoteType.LN and ho.end_time is not None:
            r = field.release_index(i) if i is not None else None
            pr = float(p[r]) if r is not None else 0.0
            up = ho.end_time + rnd.gauss(-15.0 * pr * 3, 8.0 + 60.0 * pr)
            up = max(up, down + 30.0)
        else:
            up = down + 45.0
        column_events.append((down, ho.column, +1))
        column_events.append((up, ho.column, -1))
    # a few panic taps on a lane with nothing to do
    if column_events and ghost_rate > 0:
        t_end = max(t for t, _, _ in column_events)
        for _ in range(int(ghost_rate * len(column_events) / 2)):
            t = rnd.uniform(0, t_end)
            c = rnd.randrange(7)
            column_events.append((t, c, +1))
            column_events.append((t + 40.0, c, -1))
    column_events.sort(key=lambda e: (e[0], e[2]))
    keys, frames = 0, [ReplayFrame(time_ms=0.0, keys=0)]
    for t, c, kind in column_events:
        keys = keys | (1 << c) if kind > 0 else keys & ~(1 << c)
        frames.append(ReplayFrame(time_ms=max(0.0, t), keys=keys))
    return frames
