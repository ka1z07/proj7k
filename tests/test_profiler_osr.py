import io
import pytest
from proj7k.profiler.osr import (
    OSRReplay,
    ReplayFrame,
    parse_osr,
    serialize_osr,
)


def test_osr_roundtrip_synthetic():
    frames = [
        ReplayFrame(time_ms=0.0, keys=0),
        ReplayFrame(time_ms=500.0, keys=1),    # Column 0 pressed
        ReplayFrame(time_ms=580.0, keys=0),    # Column 0 released
        ReplayFrame(time_ms=1000.0, keys=64),  # Column 6 pressed
        ReplayFrame(time_ms=1080.0, keys=0),   # Column 6 released
    ]

    original = OSRReplay(
        mode=3,
        game_version=20240101,
        beatmap_hash="d41d8cd98f00b204e9800998ecf8427e",
        player_name="TestPlayer",
        replay_hash="c4ca4238a0b923820dcc509a6f75849b",
        c300g=100,
        c300=20,
        c200=5,
        c100=2,
        c50=1,
        miss=3,
        total_score=985000,
        max_combo=450,
        perfect=False,
        mods=0,
        timestamp_ticks=638000000000000000,
        action_frames=frames,
    )

    encoded = serialize_osr(original)
    assert isinstance(encoded, bytes)
    assert len(encoded) > 0

    parsed = parse_osr(encoded)

    assert parsed.mode == 3
    assert parsed.game_version == 20240101
    assert parsed.beatmap_hash == "d41d8cd98f00b204e9800998ecf8427e"
    assert parsed.player_name == "TestPlayer"
    assert parsed.replay_hash == "c4ca4238a0b923820dcc509a6f75849b"
    assert parsed.c300g == 100
    assert parsed.c300 == 20
    assert parsed.c200 == 5
    assert parsed.c100 == 2
    assert parsed.c50 == 1
    assert parsed.miss == 3
    assert parsed.total_score == 985000
    assert parsed.max_combo == 450
    assert parsed.perfect is False
    assert parsed.mods == 0
    assert parsed.timestamp_ticks == 638000000000000000

    assert len(parsed.action_frames) == len(frames)
    for orig_f, parsed_f in zip(frames, parsed.action_frames):
        assert abs(orig_f.time_ms - parsed_f.time_ms) < 1e-3
        assert orig_f.keys == parsed_f.keys


def test_parse_osr_file_like_and_reject_non_mania():
    replay = OSRReplay(
        mode=0,  # Standard osu!, not Mania
        game_version=20240101,
        beatmap_hash="abc",
        player_name="StandardUser",
        replay_hash="def",
        action_frames=[],
    )
    raw = serialize_osr(replay)

    with pytest.raises(ValueError, match="Only osu!mania .* replays are supported"):
        parse_osr(io.BytesIO(raw))


def test_parse_osr_with_seed_marker_frame():
    import lzma
    import struct

    # Construct binary osr with real osu! seed marker (-123456 delta) and 8th bit key flags
    stream = io.BytesIO()
    stream.write(bytes([3]))                         # mode 3
    stream.write(struct.pack("<i", 20240101))        # version
    stream.write(b"\x0b\x03abc")                     # beatmap_hash
    stream.write(b"\x0b\x04user")                    # player_name
    stream.write(b"\x0b\x03def")                     # replay_hash
    stream.write(struct.pack("<hhhhhh", 0, 0, 0, 0, 0, 0))  # judgments
    stream.write(struct.pack("<ihbi", 1000, 10, 0, 0))      # score, combo, perf, mods
    stream.write(b"\x00")                            # life bar
    stream.write(struct.pack("<q", 123456789))       # timestamp
    
    # Raw frames text: first frame has negative delta (seed frame), second frame at 500ms
    raw_frames = "-123456|0|0|1337,500|1|0|0,100|0|0|0"
    compressed = lzma.compress(raw_frames.encode("utf-8"))
    stream.write(struct.pack("<i", len(compressed)))
    stream.write(compressed)

    parsed = parse_osr(stream.getvalue())
    assert len(parsed.action_frames) == 2
    assert parsed.action_frames[0].time_ms == 500.0
    assert parsed.action_frames[0].keys == 1
    assert parsed.action_frames[1].time_ms == 600.0
    assert parsed.action_frames[1].keys == 0

