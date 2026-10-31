"""
Charts osu! can play: no two notes in one column at the same time, no note inside a long note's hold, every
column one of the seven, every hold longer than its head.

The generator's own notes keep this by construction (`holds.settle`); Mapperatorinator's do not: the model writes
each window on its own and the windows overlap, so a note can come back twice, or land under a hold an earlier
window wrote. Every chart the generator hands out goes through `sanitize` first, on the millisecond times the
.osu file will carry.

Its timing goes through `constant_scroll`: the notes scroll at one speed from start to end. osu! (stable and
lazer) scrolls a red line faster or slower by its BPM against the chart's most common one, so a chart whose red
lines change BPM (the model's own timing, or a borrowed chart's) changes speed even with no green line at all.
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
#: The scroll multipliers a green line can carry in osu!. A red line that would need more to scroll at the
#: chart's speed is a gimmick (a stop, a jump) rather than the song's tempo, and is dropped.
SV_RANGE = (0.01, 10.0)


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


def _main_beat(red: Sequence[TimingPoint], end_ms: float) -> float:
    """The beat length the chart spends most time in, up to `end_ms`: the one osu! scrolls at multiplier 1."""
    spans: dict = {}
    for tp, nxt in zip(red, list(red[1:]) + [None]):
        until = nxt.time if nxt is not None else max(end_ms, tp.time)
        key = round(tp.beat_length, 3)
        spans[key] = spans.get(key, 0.0) + max(0.0, until - tp.time)
    return max(spans, key=lambda k: (spans[k], -k)) if spans else DEFAULT_BEAT_MS


def constant_scroll(timing: Sequence[TimingPoint], end_ms: float = 0.0) -> List[TimingPoint]:
    """The red lines only, each one that changes BPM followed by the green line that brings the scroll back to the
    main BPM's speed (`_main_beat`); a red line needing a multiplier outside `SV_RANGE` is dropped. Any green line
    in `timing` is dropped: the chart has no scroll speed change of its own."""
    red = sorted((tp for tp in timing if tp.uninherited and tp.beat_length > 0), key=lambda tp: tp.time)
    if not red:
        return []
    main = _main_beat(red, end_ms)
    out: List[TimingPoint] = []
    for tp in red:
        sv = tp.beat_length / main
        if not SV_RANGE[0] <= sv <= SV_RANGE[1]:
            continue
        out.append(TimingPoint(time=tp.time, beat_length=tp.beat_length, meter=tp.meter, uninherited=True))
        if abs(sv - 1.0) > 1e-6:
            out.append(TimingPoint(time=tp.time, beat_length=-100.0 / sv, meter=tp.meter, uninherited=False))
    return out


def end_ms(objs: Sequence[HitObject]) -> float:
    """When the last note (or hold) ends."""
    return float(max((_end(o) for o in objs), default=0))
