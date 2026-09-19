"""
Binary parser and serializer for osu!mania replay files (.osr).
"""

from dataclasses import dataclass, field
import io
import lzma
from pathlib import Path
import struct
from typing import BinaryIO, List, Optional, Union


@dataclass
class ReplayFrame:
    """
    A single discrete action frame in an osu!mania replay.
    time_ms: cumulative time elapsed in milliseconds since song clock 0.
    keys: bitmask representing active pressed keys (bit 0 = column 0, ..., bit 6 = column 6).
    """
    time_ms: float
    keys: int

    def is_pressed(self, column: int) -> bool:
        """Returns True if the given column (0-indexed) is pressed in this frame."""
        return bool(self.keys & (1 << column))


@dataclass
class OSRReplay:
    """
    In-memory representation of an osu!mania replay.
    """
    mode: int = 3  # 3 = osu!mania
    game_version: int = 0
    beatmap_hash: str = ""
    player_name: str = ""
    replay_hash: str = ""
    c300g: int = 0  # MAX / Rainbow 300
    c300: int = 0   # Standard 300
    c200: int = 0   # 200 / Katu
    c100: int = 0   # 100
    c50: int = 0    # 50
    miss: int = 0   # Miss
    total_score: int = 0
    max_combo: int = 0
    perfect: bool = False
    mods: int = 0
    life_bar: str = ""
    timestamp_ticks: int = 0
    score_id: Optional[int] = None
    action_frames: List[ReplayFrame] = field(default_factory=list)


def _read_uleb128(stream: BinaryIO) -> int:
    result = 0
    shift = 0
    while True:
        b = stream.read(1)
        if not b:
            raise EOFError("Unexpected EOF while decoding ULEB128")
        byte_val = b[0]
        result |= (byte_val & 0x7F) << shift
        if not (byte_val & 0x80):
            break
        shift += 7
    return result


def _write_uleb128(stream: BinaryIO, val: int) -> None:
    while True:
        byte = val & 0x7F
        val >>= 7
        if val != 0:
            byte |= 0x80
        stream.write(bytes([byte]))
        if val == 0:
            break


def _read_osu_string(stream: BinaryIO) -> str:
    flag_byte = stream.read(1)
    if not flag_byte:
        return ""
    flag = flag_byte[0]
    if flag == 0x00:
        return ""
    if flag != 0x0B:
        raise ValueError(f"Invalid osu string indicator: {hex(flag)} (expected 0x0b or 0x00)")
    length = _read_uleb128(stream)
    data = stream.read(length)
    return data.decode("utf-8", errors="replace")


def _write_osu_string(stream: BinaryIO, s: str) -> None:
    if not s:
        stream.write(b"\x00")
        return
    stream.write(b"\x0b")
    encoded = s.encode("utf-8")
    _write_uleb128(stream, len(encoded))
    stream.write(encoded)


def parse_osr(source: Union[bytes, BinaryIO, Path, str]) -> OSRReplay:
    """
    Parses an osu!mania .osr binary stream or file.
    """
    if isinstance(source, (str, Path)):
        with open(source, "rb") as f:
            return parse_osr(f)
    elif isinstance(source, bytes):
        return parse_osr(io.BytesIO(source))

    stream: BinaryIO = source

    # 1. Mode (1 byte)
    mode_byte = stream.read(1)
    if not mode_byte:
        raise ValueError("Empty replay stream")
    mode = mode_byte[0]
    if mode != 3:
        raise ValueError(f"Only osu!mania (mode=3) replays are supported, got mode={mode}")

    # 2. Game version (4 bytes int32)
    game_version = struct.unpack("<i", stream.read(4))[0]

    # 3. Hashes and player name (osu strings)
    beatmap_hash = _read_osu_string(stream)
    player_name = _read_osu_string(stream)
    replay_hash = _read_osu_string(stream)

    # 4. Judgment counts (int16 x 6)
    # In mania:
    # 300 = c300
    # 100 = c100
    # 50  = c50
    # geki = c300g (MAX)
    # katu = c200
    # miss = miss
    c300, c100, c50, geki, katu, miss = struct.unpack("<hhhhhh", stream.read(12))

    # 5. Score, max combo, perfect, mods
    total_score = struct.unpack("<i", stream.read(4))[0]
    max_combo = struct.unpack("<h", stream.read(2))[0]
    perfect = bool(stream.read(1)[0])
    mods = struct.unpack("<i", stream.read(4))[0]

    # 6. Life bar string
    life_bar = _read_osu_string(stream)

    # 7. Timestamp ticks (int64)
    timestamp_ticks = struct.unpack("<q", stream.read(8))[0]

    # 8. Compressed replay data
    compressed_len = struct.unpack("<i", stream.read(4))[0]
    compressed_data = stream.read(compressed_len)

    # 9. Optional online score ID
    score_id: Optional[int] = None
    remaining = stream.read(8)
    if len(remaining) == 8:
        score_id = struct.unpack("<q", remaining)[0]

    # Decompress action frames
    action_frames: List[ReplayFrame] = []
    if compressed_data:
        try:
            decompressed_text = lzma.decompress(compressed_data).decode("utf-8", errors="ignore")
            # Frames: delta_ms|keys|0|0,delta_ms|keys|0|0,...
            current_time = 0.0
            for item in decompressed_text.split(","):
                part = item.strip()
                if not part:
                    continue
                tokens = part.split("|")
                if len(tokens) >= 2:
                    delta_ms = float(tokens[0])
                    raw_keys = int(float(tokens[1]))
                    # Legacy osu! replays may start with an RNG seed marker frame (e.g. -123456)
                    if delta_ms <= -10000 and current_time == 0.0:
                        continue
                    current_time += delta_ms
                    # Mask to 7K mania valid keys (bits 0..6)
                    action_frames.append(ReplayFrame(time_ms=current_time, keys=raw_keys & 0x7F))
        except Exception as e:
            raise ValueError(f"Failed to decompress and parse replay frames: {e}")

    return OSRReplay(
        mode=mode,
        game_version=game_version,
        beatmap_hash=beatmap_hash,
        player_name=player_name,
        replay_hash=replay_hash,
        c300g=geki,
        c300=c300,
        c200=katu,
        c100=c100,
        c50=c50,
        miss=miss,
        total_score=total_score,
        max_combo=max_combo,
        perfect=perfect,
        mods=mods,
        life_bar=life_bar,
        timestamp_ticks=timestamp_ticks,
        score_id=score_id,
        action_frames=action_frames,
    )


def serialize_osr(replay: OSRReplay) -> bytes:
    """
    Serializes an OSRReplay object into osu! .osr binary format.
    Useful for synthetic test fixtures and replay generation.
    """
    stream = io.BytesIO()

    # 1. Mode
    stream.write(bytes([replay.mode]))

    # 2. Version
    stream.write(struct.pack("<i", replay.game_version))

    # 3. Hashes and player name
    _write_osu_string(stream, replay.beatmap_hash)
    _write_osu_string(stream, replay.player_name)
    _write_osu_string(stream, replay.replay_hash)

    # 4. Judgments
    stream.write(
        struct.pack(
            "<hhhhhh",
            replay.c300,
            replay.c100,
            replay.c50,
            replay.c300g,  # geki
            replay.c200,   # katu
            replay.miss,
        )
    )

    # 5. Score, max combo, perfect, mods
    stream.write(struct.pack("<i", replay.total_score))
    stream.write(struct.pack("<h", replay.max_combo))
    stream.write(b"\x01" if replay.perfect else b"\x00")
    stream.write(struct.pack("<i", replay.mods))

    # 6. Life bar
    _write_osu_string(stream, replay.life_bar)

    # 7. Timestamp ticks
    stream.write(struct.pack("<q", replay.timestamp_ticks))

    # 8. Compress action frames
    raw_frame_strings: List[str] = []
    prev_time = 0.0
    for f in replay.action_frames:
        delta = f.time_ms - prev_time
        raw_frame_strings.append(f"{delta:.2f}|{f.keys}|0|0")
        prev_time = f.time_ms

    decompressed_content = ",".join(raw_frame_strings)
    if raw_frame_strings:
        decompressed_content += ","
    compressed_bytes = lzma.compress(decompressed_content.encode("utf-8"))

    stream.write(struct.pack("<i", len(compressed_bytes)))
    stream.write(compressed_bytes)

    # 9. Score ID if provided
    if replay.score_id is not None:
        stream.write(struct.pack("<q", replay.score_id))

    return stream.getvalue()
