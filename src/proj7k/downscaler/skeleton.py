import math
from typing import Collection, Dict, List, Optional, Set, Tuple, Union

from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint


def _select_skeleton_anchor(notes: List[HitObject]) -> HitObject:
    """
    Selects a canonical anchor note from concurrent notes:
    1. Long notes (LN) take precedence (preserving hold structure).
    2. Symmetric proximity to center track S (column 3): abs(col - 3) ascending.
    3. Deterministic track tie-breaker.
    """
    return min(
        notes,
        key=lambda ho: (
            0 if ho.note_type == NoteType.LN else 1,
            abs(ho.column - 3),
            ho.column,
        ),
    )


class MetricSkeletonDetector:
    """
    Detects metric downbeats and establishes composite skeleton invariants (SPEC-P5.1-02, ADR-0011):
    1. 1/1 Measure Downbeat Protection: The rhythmic foundation of every 1/1 downbeat
       must never be completely emptied.
    2. Chord Invariant: Concurrent chords (Chord >= 2) can only be thinned, never wiped out
       to 0 notes, preserving rhythmic continuity.
    """

    def __init__(self, beatmap: Beatmap7K, tolerance_ms: float = 3.0):
        self.beatmap = beatmap
        self.tolerance_ms = tolerance_ms

        uninherited = [
            tp for tp in beatmap.timing_points if tp.uninherited and tp.beat_length > 0
        ]
        if not uninherited:
            # Fallback default: 120 BPM, 4/4 meter starting at 0ms
            uninherited = [TimingPoint(time=0.0, beat_length=500.0, meter=4, uninherited=True)]
        self.uninherited_tps = sorted(uninherited, key=lambda tp: tp.time)

        # Pre-group hit objects by timestamp (rounded to 1ms)
        self._time_groups: Dict[float, List[HitObject]] = {}
        for ho in beatmap.hit_objects:
            t_key = round(ho.time, 1)
            self._time_groups.setdefault(t_key, []).append(ho)

    def is_downbeat(self, time_ms: float) -> bool:
        """
        Determines whether a given time in milliseconds falls on a 1/1 measure downbeat.
        """
        # Find active timing point segment
        active_tp: Optional[TimingPoint] = None
        for i, tp in enumerate(self.uninherited_tps):
            has_next = i + 1 < len(self.uninherited_tps)
            next_time = self.uninherited_tps[i + 1].time if has_next else float("inf")
            # If time is before the very first timing point, use the first timing point extrapolated backwards
            if i == 0 and time_ms < tp.time:
                active_tp = tp
                break
            if tp.time - self.tolerance_ms <= time_ms < next_time - self.tolerance_ms:
                active_tp = tp
                break
        if active_tp is None:
            active_tp = self.uninherited_tps[-1]

        meter = active_tp.meter if active_tp.meter > 0 else 4
        measure_len = active_tp.beat_length * meter
        if measure_len <= 0:
            return False

        delta = time_ms - active_tp.time
        measure_count = round(delta / measure_len)
        expected_time = active_tp.time + measure_count * measure_len

        return abs(time_ms - expected_time) <= self.tolerance_ms

    def detect_skeleton_notes(self) -> List[HitObject]:
        """
        Identifies immutable skeleton anchor notes across the beatmap:
        - For every 1/1 measure downbeat with notes, marks an anchor note as immutable.
        - For every concurrent chord (Chord >= 2), marks an anchor note as immutable,
          protecting it against multi-round wipeouts.
        """
        skeleton: List[HitObject] = []
        seen_ids = set()

        for t_key, notes in self._time_groups.items():
            if not notes:
                continue
            is_db = self.is_downbeat(t_key)
            is_chord = len(notes) >= 2
            if is_db or is_chord:
                anchor = _select_skeleton_anchor(notes)
                if id(anchor) not in seen_ids:
                    seen_ids.add(id(anchor))
                    skeleton.append(anchor)

        return skeleton

    def filter_candidate_removals(
        self,
        candidates: Union[Collection[HitObject], Collection[int]],
    ) -> List[HitObject]:
        """
        Filters a collection of candidate removals against composite metric invariants:
        - Downbeat base protection: never allows removing all notes on a 1/1 downbeat.
        - Chord preservation: never allows removing all notes on a chord (total >= 2).
        - Anchors in detect_skeleton_notes() are strictly preserved.
        """
        skeleton_anchors = self.detect_skeleton_notes()

        # Normalize candidates to HitObject set
        cand_list: List[HitObject] = []
        if candidates:
            first = next(iter(candidates))
            if isinstance(first, int):
                idx_set = set(candidates)
                cand_list = [
                    ho for idx, ho in enumerate(self.beatmap.hit_objects) if idx in idx_set
                ]
            else:
                cand_list = list(candidates)  # type: ignore

        cand_ids = {id(h) for h in cand_list}
        anchor_ids = {id(h) for h in skeleton_anchors}
        allowed_removals: List[HitObject] = []

        # Process timestamp groups to enforce invariants
        for t_key, group_notes in self._time_groups.items():
            total_count = len(group_notes)
            group_cands = [ho for ho in group_notes if id(ho) in cand_ids]
            if not group_cands:
                continue

            is_db = self.is_downbeat(t_key)
            is_chord = total_count >= 2

            # Determine minimum surviving notes required at this timestamp
            min_surviving = 0
            if is_db:
                min_surviving = max(min_surviving, 1)
            if is_chord:
                min_surviving = max(min_surviving, 1)

            max_allowed_to_remove = total_count - min_surviving

            # Filter out explicit skeleton anchors first
            non_anchor_cands = [ho for ho in group_cands if id(ho) not in anchor_ids]

            # If candidates exceed allowed removals, keep the highest priority ones
            if len(non_anchor_cands) > max_allowed_to_remove:
                # Keep first max_allowed_to_remove notes
                allowed_removals.extend(non_anchor_cands[:max_allowed_to_remove])
            else:
                allowed_removals.extend(non_anchor_cands)

        return allowed_removals
