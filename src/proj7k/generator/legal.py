"""
Charts osu! can play: no two notes in one column at the same time, no note inside a long note's hold, every
column one of the seven, every hold longer than its head.

The generator's own notes keep this by construction (`holds.settle`); Mapperatorinator's do not: the model writes
each window on its own and the windows overlap, so a note can come back twice, or land under a hold an earlier
window wrote. Every chart the generator hands out goes through `sanitize` first, on the millisecond times the
.osu file will carry.
"""

import bisect
from dataclasses import dataclass, replace
from typing import List, Optional, Sequence, Tuple

from proj7k.parser import HitObject, NoteType, TimingPoint

KEYS = 7
#: A tail stops this much of a beat before the next head in its column (as `holds.MIN_GAP`).
MIN_GAP = 0.125
#: A hold cut shorter than this much of a beat becomes a tap (as `holds.MIN_HOLD`).
MIN_HOLD = 0.125
#: The beat when a chart has no timing.
DEFAULT_BEAT_MS = 500.0


@dataclass(frozen=True)
class Problem:
    kind: str     # "column", "duplicate", "inside_hold", "empty_hold"
    column: int
    time: int     # ms, as written
    other: Optional[int] = None  # the earlier note's head, for "duplicate" and "inside_hold"


def _ms(t: Optional[float]) -> int:
    return int(round(t)) if t is not None else 0


def _end(o: HitObject) -> int:
    return _ms(o.end_time) if o.note_type == NoteType.LN else _ms(o.time)


def problems(objs: Sequence[HitObject]) -> List[Problem]:
    """Everything that makes the chart unplayable, on the times as written (whole milliseconds)."""
    out: List[Problem] = []
    cols: List[List[HitObject]] = [[] for _ in range(KEYS)]
    for o in objs:
        if not 0 <= o.column < KEYS:
            out.append(Problem("column", o.column, _ms(o.time)))
            continue
        if o.note_type == NoteType.LN and _end(o) <= _ms(o.time):
            out.append(Problem("empty_hold", o.column, _ms(o.time)))
        cols[o.column].append(o)
    for c, notes in enumerate(cols):
        notes.sort(key=lambda o: (_ms(o.time), -_end(o)))
        for a, b in zip(notes, notes[1:]):
            if _ms(b.time) == _ms(a.time):
                out.append(Problem("duplicate", c, _ms(b.time), _ms(a.time)))
            elif _ms(b.time) <= _end(a):
                out.append(Problem("inside_hold", c, _ms(b.time), _ms(a.time)))
    return sorted(out, key=lambda p: (p.time, p.column))


def _beat_at(timing: Sequence[TimingPoint]):
    red = sorted((tp for tp in timing if tp.uninherited and tp.beat_length > 0), key=lambda tp: tp.time)
    times = [tp.time for tp in red]

    def beat(t: float) -> float:
        if not red:
            return DEFAULT_BEAT_MS
        return red[max(0, bisect.bisect_right(times, t) - 1)].beat_length

    return beat


def sanitize(objs: Sequence[HitObject], timing: Sequence[TimingPoint] = ()) -> Tuple[List[HitObject], int]:
    """(a playable chart, how many notes it changed or dropped). Times are rounded to whole milliseconds, as the
    .osu file writes them. In each column, by head: a note off the seven columns is dropped; of notes on one
    head, the longest stays; a note under the hold before it is kept and the hold ends `MIN_GAP` of a beat before
    it (a tap if what is left is shorter than `MIN_HOLD`); a hold with no length is a tap."""
    beat = _beat_at(timing)
    changed = 0
    cols: List[List[HitObject]] = [[] for _ in range(KEYS)]
    for o in objs:
        if not 0 <= o.column < KEYS:
            changed += 1
            continue
        t = _ms(o.time)
        if o.note_type == NoteType.LN and _ms(o.end_time) > t:
            n = replace(o, time=float(t), end_time=float(_ms(o.end_time)))
        else:
            if o.note_type == NoteType.LN:
                changed += 1
            n = replace(o, time=float(t), note_type=NoteType.RICE, end_time=None)
        cols[o.column].append(n)
    out: List[HitObject] = []
    for notes in cols:
        notes.sort(key=lambda o: (o.time, -_end(o)))
        kept: List[HitObject] = []
        for n in notes:
            if kept and n.time == kept[-1].time:
                changed += 1
                continue
            if kept and kept[-1].note_type == NoteType.LN and n.time <= kept[-1].end_time:
                prev = kept[-1]
                changed += 1
                b = beat(prev.time)
                tail = _ms(n.time - MIN_GAP * b)
                if tail - prev.time < MIN_HOLD * b - 1e-6:
                    kept[-1] = replace(prev, note_type=NoteType.RICE, end_time=None)
                else:
                    kept[-1] = replace(prev, end_time=float(tail))
            kept.append(n)
        out.extend(kept)
    return sorted(out, key=lambda o: (o.time, o.column)), changed
