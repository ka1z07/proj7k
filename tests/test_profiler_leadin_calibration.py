import io
import struct
import lzma
import pytest

from proj7k.parser import Beatmap7K, HitObject, NoteType
from proj7k.profiler.osr import OSRReplay, ReplayFrame, parse_osr, serialize_osr
from proj7k.profiler.matcher import align_replay_hits, HitJudgment
from proj7k.profiler.cli import run_ingestion
from proj7k.strain import compute_8d_strain_timeseries
from proj7k.profiler.response import analyze_strain_response


def test_parse_osr_preserves_audio_leadin():
    """Verifies that negative lead-in (e.g. -589ms) is not discarded as seed marker."""
    stream = io.BytesIO()
    stream.write(bytes([3]))                         # mode 3
    stream.write(struct.pack("<i", 20240101))        # version
    stream.write(b"\x0b\x03abc")                     # beatmap_hash
    stream.write(b"\x0b\x04user")                    # player_name
    stream.write(b"\x0b\x03def")                     # replay_hash
    stream.write(struct.pack("<hhhhhh", 10, 0, 0, 0, 0, 0))  # judgments
    stream.write(struct.pack("<ihbi", 1000, 10, 0, 0))      # score, combo, perf, mods
    stream.write(b"\x00")                            # life bar
    stream.write(struct.pack("<q", 123456789))       # timestamp

    # Lead-in at -500ms, followed by keypress at 1000ms (-500 + 1500)
    raw_frames = "-500|0|0|0,1500|1|0|0,100|0|0|0"
    compressed = lzma.compress(raw_frames.encode("utf-8"))
    stream.write(struct.pack("<i", len(compressed)))
    stream.write(compressed)

    parsed = parse_osr(stream.getvalue())
    assert len(parsed.action_frames) == 3
    assert parsed.action_frames[0].time_ms == -500.0
    assert parsed.action_frames[1].time_ms == 1000.0
    assert parsed.action_frames[1].keys == 1
    assert parsed.action_frames[2].time_ms == 1100.0
    assert parsed.action_frames[2].keys == 0


def test_alignment_with_leadin_retains_perfect_judgment():
    """Asserts that keypress at 1000ms aligns with a note at 1000ms even with lead-in."""
    bm = Beatmap7K(
        circle_size=7,
        overall_difficulty=8.0,
        hit_objects=[
            HitObject(column=0, time=1000.0, note_type=NoteType.RICE),
        ],
    )
    frames = [
        ReplayFrame(time_ms=-500.0, keys=0),
        ReplayFrame(time_ms=1000.0, keys=1),   # Col 0 pressed at 1000ms
        ReplayFrame(time_ms=1080.0, keys=0),   # Released
    ]
    alignment = align_replay_hits(bm, frames)
    assert alignment.total_hits == 1
    assert alignment.miss_count == 0
    assert alignment.judgment_counts[HitJudgment.MAX] == 1
    assert alignment.total_ghost_taps == 0


def test_sparse_technique_strain_does_not_explode():
    """Asserts that sparse techniques do not produce astronomical strain due to p90 sparsity."""
    # Chart with 100 notes, only 2 quick notes on col 0 (a single short jack)
    hos = []
    # Stream flow across other lanes
    for t in range(1000, 30000, 200):
        c = (t // 200) % 6 + 1
        hos.append(HitObject(column=c, time=float(t), note_type=NoteType.RICE))
    # Two quick jack notes on col 0 at t=10000 and t=10100
    hos.append(HitObject(column=0, time=10000.0, note_type=NoteType.RICE))
    hos.append(HitObject(column=0, time=10100.0, note_type=NoteType.RICE))

    bm = Beatmap7K(
        circle_size=7,
        overall_difficulty=8.0,
        hit_objects=hos,
    )
    ts = compute_8d_strain_timeseries(bm)
    # Peak jack strain should be bounded (not thousands or millions)
    assert max(ts.jack) < 200.0
