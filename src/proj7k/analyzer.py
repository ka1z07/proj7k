from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import argparse
import sys

from proj7k.parser import Beatmap7K, HitObject, NoteType, parse_osu_7k
from proj7k.window import extract_time_window, extract_measure_window
from proj7k.slicer import parse_timestamp


COL_NAMES = ["L3", "L2", "L1", "S", "R1", "R2", "R3"]


def compute_hand_partition(cols: List[int]) -> str:
    """
    Computes a topological hand partition signature for a list of columns.
    Supported column indexes: 0..6
    """
    cols = sorted(set(cols))
    l_cols = [c for c in cols if c in (0, 1, 2)]
    s_cols = [c for c in cols if c == 3]
    r_cols = [c for c in cols if c in (4, 5, 6)]

    parts = []

    # Left hand
    if l_cols:
        finger_nums = []
        for c in l_cols:
            if c == 0:
                finger_nums.append(3)
            elif c == 1:
                finger_nums.append(2)
            elif c == 2:
                finger_nums.append(1)
        # Order by outer to inner: 3, 2, 1
        finger_nums.sort(reverse=True)
        nums_str = ",".join(str(x) for x in finger_nums)

        if len(l_cols) == 1:
            loc = "out" if 0 in l_cols else ("mid" if 1 in l_cols else "in")
            parts.append(f"L{{{nums_str}}}[{loc}]")
        elif len(l_cols) == 2:
            if l_cols == [0, 1] or l_cols == [1, 2]:
                parts.append(f"L{{{nums_str}}}[adj]")
            elif l_cols == [0, 2]:
                parts.append(f"L{{{nums_str}}}[gap:1]")
            else:
                parts.append(f"L{{{nums_str}}}")
        elif len(l_cols) == 3:
            parts.append(f"L{{{nums_str}}}[full]")

    # Space
    if s_cols:
        parts.append("S")

    # Right hand
    if r_cols:
        finger_nums = []
        for c in r_cols:
            if c == 4:
                finger_nums.append(1)
            elif c == 5:
                finger_nums.append(2)
            elif c == 6:
                finger_nums.append(3)
        # Order by inner to outer: 1, 2, 3
        finger_nums.sort()
        nums_str = ",".join(str(x) for x in finger_nums)

        if len(r_cols) == 1:
            loc = "in" if 4 in r_cols else ("mid" if 5 in r_cols else "out")
            parts.append(f"R{{{nums_str}}}[{loc}]")
        elif len(r_cols) == 2:
            if r_cols == [4, 5] or r_cols == [5, 6]:
                parts.append(f"R{{{nums_str}}}[adj]")
            elif r_cols == [4, 6]:
                parts.append(f"R{{{nums_str}}}[gap:1]")
            else:
                parts.append(f"R{{{nums_str}}}")
        elif len(r_cols) == 3:
            parts.append(f"R{{{nums_str}}}[full]")

    return " | ".join(parts)


def compute_chord_signature(cols: List[int]) -> str:
    """
    Returns standard 7k-VSDL chord signature:
    (n_L, n_S, n_R) L{..}[..] | S | R{..}[..]
    """
    cols = sorted(set(cols))
    l_cnt = sum(1 for c in cols if c in (0, 1, 2))
    s_cnt = sum(1 for c in cols if c == 3)
    r_cnt = sum(1 for c in cols if c in (4, 5, 6))

    topo = compute_hand_partition(cols)
    return f"({l_cnt},{s_cnt},{r_cnt}) {topo}"


class PrimitiveState:
    EMPTY = "."
    RICE = "o"
    LN_HEAD = "⎵"
    LN_HOLD = "|"
    LN_TAIL = "⎴"
    OVERLAP = "x"


@dataclass
class SliceAnalysis:
    start_time: float
    end_time: float
    total_notes: int
    rice_count: int
    ln_count: int
    bpm: float
    nps: float
    mean_locked_fingers: float
    lock_distribution: Dict[int, float]
    antiphase_count: int
    chords_count: int
    vsdl_dsl: str


def analyze_slice(beatmap: Beatmap7K, start_time: float, end_time: float) -> SliceAnalysis:
    """
    Performs full 7k-VSDL objective geometric and cognitive analysis on a time slice.
    """
    # 1. Filter hit objects intersecting window
    all_notes = beatmap.hit_objects
    slice_notes = [
        n for n in all_notes
        if (start_time <= n.time <= end_time) or
           (n.note_type == NoteType.LN and n.end_time is not None and
            start_time <= n.end_time and n.time <= end_time)
    ]

    total_notes = sum(1 for n in slice_notes if start_time <= n.time <= end_time)
    rice_count = sum(1 for n in slice_notes if n.note_type == NoteType.RICE and start_time <= n.time <= end_time)
    ln_count = total_notes - rice_count
    duration_s = max((end_time - start_time) / 1000.0, 0.001)
    nps = total_notes / duration_s

    # Find active BPM
    active_bpm = 150.0
    for tp in sorted(beatmap.timing_points, key=lambda x: x.time):
        if tp.uninherited and tp.time <= end_time:
            if tp.bpm:
                active_bpm = tp.bpm

    # 2. Compute finger lock profile (sampling every 10ms)
    step_ms = 10
    total_samples = 0
    sum_locked = 0
    lock_counts: Dict[int, int] = {i: 0 for i in range(8)}

    cur = start_time
    while cur <= end_time:
        active_holds = 0
        for n in slice_notes:
            if n.note_type == NoteType.LN and n.end_time is not None:
                if n.time <= cur < n.end_time:
                    active_holds += 1
        active_holds = min(active_holds, 7)
        lock_counts[active_holds] += 1
        sum_locked += active_holds
        total_samples += 1
        cur += step_ms

    mean_locked = sum_locked / max(total_samples, 1)
    lock_dist = {k: (v / max(total_samples, 1)) * 100.0 for k, v in lock_counts.items()}

    # 3. Discrete Event Timeline
    events = set()
    for n in slice_notes:
        if start_time <= n.time <= end_time:
            events.add(int(round(n.time)))
        if n.note_type == NoteType.LN and n.end_time is not None:
            if start_time <= n.end_time <= end_time:
                events.add(int(round(n.end_time)))

    sorted_events = sorted(events)

    # 4. Generate State Matrix at each tick
    matrix_rows: List[Tuple[int, List[str], str]] = []
    antiphase_count = 0
    chords_count = 0

    for t in sorted_events:
        row = [PrimitiveState.EMPTY] * 7
        pressed_cols = []
        tails_in_tick = []
        heads_in_tick = []

        for n in slice_notes:
            col = n.column
            st = int(round(n.time))
            et = int(round(n.end_time)) if n.note_type == NoteType.LN and n.end_time is not None else st

            if n.note_type == NoteType.RICE:
                if st == t:
                    pressed_cols.append(col)
                    if row[col] == PrimitiveState.LN_TAIL:
                        row[col] = PrimitiveState.OVERLAP
                    else:
                        row[col] = PrimitiveState.RICE
            else:
                if st == t:
                    pressed_cols.append(col)
                    heads_in_tick.append(col)
                    if row[col] == PrimitiveState.LN_TAIL:
                        row[col] = PrimitiveState.OVERLAP
                    else:
                        row[col] = PrimitiveState.LN_HEAD
                elif et == t:
                    tails_in_tick.append(col)
                    if row[col] in (PrimitiveState.RICE, PrimitiveState.LN_HEAD):
                        row[col] = PrimitiveState.OVERLAP
                    else:
                        row[col] = PrimitiveState.LN_TAIL
                elif st < t < et:
                    if row[col] == PrimitiveState.EMPTY:
                        row[col] = PrimitiveState.LN_HOLD

        # Antiphase check: tail on one track and head on another track
        if tails_in_tick and heads_in_tick:
            antiphase_count += len(tails_in_tick) * len(heads_in_tick)

        # Chord annotation
        ann = ""
        if len(pressed_cols) >= 2:
            chords_count += 1
            ann = compute_chord_signature(pressed_cols)
        elif len(pressed_cols) == 1:
            ann = compute_hand_partition(pressed_cols)

        matrix_rows.append((t, row, ann))

    # 5. Format VSDL DSL Document (Reading Upward: latest time first in string)
    dsl_lines = []
    dsl_lines.append(f"@slice: {int(start_time)}ms ~ {int(end_time)}ms | Beatmap: {beatmap.title} [{beatmap.version}]")
    dsl_lines.append(f"@timing: {active_bpm:.2f} BPM | Notes: {total_notes} ({nps:.1f} NPS) | LN: {ln_count}/{total_notes}")
    dsl_lines.append(f"@profile: Mean Locked: {mean_locked:.2f}/7 fingers | Chords: {chords_count} | Antiphase: {antiphase_count}")
    dsl_lines.append("")
    dsl_lines.append("# 空间-时间矩阵 (自底向上 reading upward: line 1 = future, bottom = t0)")
    dsl_lines.append("[L3  L2  L1 | S | R1  R2  R3]")

    # Reverse rows for bottom-to-top reading:
    for t, r, ann in reversed(matrix_rows):
        row_str = "  ".join(r[:3]) + " | " + r[3] + " | " + "  ".join(r[4:])
        ann_str = f"  # {t}ms: {ann}" if ann else f"  # {t}ms"
        dsl_lines.append(f"| {row_str} |{ann_str}")

    dsl_lines.append("[L3  L2  L1 | S | R1  R2  R3]")
    dsl_lines.append("")
    dsl_lines.append("@macro:")
    dsl_lines.append(f"  mean_locked_fingers: {mean_locked:.2f}")
    dsl_lines.append(f"  chords_count: {chords_count}")
    dsl_lines.append(f"  antiphase_count: {antiphase_count}")

    vsdl_dsl = "\n".join(dsl_lines)

    return SliceAnalysis(
        start_time=start_time,
        end_time=end_time,
        total_notes=total_notes,
        rice_count=rice_count,
        ln_count=ln_count,
        bpm=active_bpm,
        nps=nps,
        mean_locked_fingers=mean_locked,
        lock_distribution=lock_dist,
        antiphase_count=antiphase_count,
        chords_count=chords_count,
        vsdl_dsl=vsdl_dsl,
    )


def main():
    parser = argparse.ArgumentParser(
        prog="python3 -m proj7k.analyzer",
        description="7k-VSDL Slice Analyzer: Extracts objective geometric, topological and cognitive metrics from 7K osu! beatmaps."
    )
    parser.add_argument("--osu", required=True, help="Path to .osu beatmap file (must be 7K)")
    parser.add_argument("--start", help="Start time (e.g. '12000', '01:23.500', '15.2s')")
    parser.add_argument("--end", help="End time (e.g. '18000', '01:29.500', '21.2s')")
    parser.add_argument("-m", "--measure", help="Measure range to slice (e.g. '12-16' or 'M12-M16')")
    parser.add_argument("-o", "--output", help="Output VSDL file path (default: stdout)")

    args = parser.parse_args()

    bm = parse_osu_7k(args.osu)

    # Determine window
    if args.measure:
        m_str = args.measure.strip().upper().replace("M", "")
        if "-" in m_str:
            parts = m_str.split("-", 1)
            start_m = int(parts[0])
            end_m = int(parts[1])
        else:
            start_m = int(m_str)
            end_m = start_m + 1
        window = extract_measure_window(bm, start_m, end_m)
        start_ms, end_ms = window.start_ms, window.end_ms
    elif args.start and args.end:
        start_ms = parse_timestamp(args.start)
        end_ms = parse_timestamp(args.end)
    else:
        start_ms = 0.0
        end_ms = 10000.0

    analysis = analyze_slice(bm, start_ms, end_ms)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(analysis.vsdl_dsl)
        print(f"Generated 7k-VSDL analysis -> {args.output}")
    else:
        print(analysis.vsdl_dsl)


if __name__ == "__main__":
    main()
