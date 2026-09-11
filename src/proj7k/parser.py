from dataclasses import dataclass, field
from enum import Enum
import math
import os
from typing import List, Optional


class NoteType(Enum):
    RICE = "rice"
    LN = "ln"


@dataclass
class HitObject:
    column: int
    time: float
    note_type: NoteType
    end_time: Optional[float] = None

    def __post_init__(self):
        if self.note_type == NoteType.LN and self.end_time is None:
            self.end_time = self.time


@dataclass
class TimingPoint:
    time: float
    beat_length: float
    meter: int = 4
    uninherited: bool = True

    @property
    def bpm(self) -> Optional[float]:
        if self.uninherited and self.beat_length > 0:
            return 60000.0 / self.beat_length
        return None


@dataclass
class Beatmap7K:
    title: str = ""
    artist: str = ""
    creator: str = ""
    version: str = ""
    mode: int = 3
    circle_size: int = 7
    overall_difficulty: float = 8.0
    timing_points: List[TimingPoint] = field(default_factory=list)
    hit_objects: List[HitObject] = field(default_factory=list)


def parse_osu_7k(content_or_path: str) -> Beatmap7K:
    if os.path.exists(content_or_path):
        with open(content_or_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    else:
        # If it looks like a path (e.g. ends with .osu or single line) but doesn't exist
        if content_or_path.endswith(".osu") or "\n" not in content_or_path:
            raise FileNotFoundError(f"Beatmap file not found: {content_or_path}")
        content = content_or_path

    lines = content.splitlines()
    current_section = ""
    
    general: dict[str, str] = {}
    metadata: dict[str, str] = {}
    difficulty: dict[str, str] = {}
    timing_points: List[TimingPoint] = []
    hit_objects: List[HitObject] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue

        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            continue

        if current_section == "General":
            if ":" in line:
                k, v = line.split(":", 1)
                general[k.strip()] = v.strip()
        elif current_section == "Metadata":
            if ":" in line:
                k, v = line.split(":", 1)
                metadata[k.strip()] = v.strip()
        elif current_section == "Difficulty":
            if ":" in line:
                k, v = line.split(":", 1)
                difficulty[k.strip()] = v.strip()
        elif current_section == "TimingPoints":
            parts = line.split(",")
            if len(parts) >= 2:
                time_val = float(parts[0])
                beat_len = float(parts[1])
                meter_val = int(parts[2]) if len(parts) > 2 else 4
                uninherited = True
                if len(parts) >= 7:
                    uninherited = bool(int(parts[6]))
                timing_points.append(
                    TimingPoint(
                        time=time_val,
                        beat_length=beat_len,
                        meter=meter_val,
                        uninherited=uninherited,
                    )
                )
        elif current_section == "HitObjects":
            parts = line.split(",")
            if len(parts) >= 5:
                x_val = float(parts[0])
                time_val = float(parts[2])
                type_flag = int(parts[3])
                
                # Column formula for mania: floor(x * 7 / 512)
                col = int(math.floor(x_val * 7.0 / 512.0))
                col = max(0, min(6, col))

                is_ln = bool(type_flag & 128)
                end_time_val = None
                if is_ln and len(parts) >= 6:
                    end_str = parts[5].split(":")[0]
                    try:
                        end_time_val = float(end_str)
                    except ValueError:
                        end_time_val = time_val
                
                hit_objects.append(
                    HitObject(
                        column=col,
                        time=time_val,
                        note_type=NoteType.LN if is_ln else NoteType.RICE,
                        end_time=end_time_val,
                    )
                )

    mode_val = int(general.get("Mode", "0"))
    if mode_val != 3:
        raise ValueError(f"Expected Mode 3 (osu!mania), but found Mode {mode_val}")

    cs_val = int(float(difficulty.get("CircleSize", "0")))
    if cs_val != 7:
        raise ValueError(f"Expected 7K (CircleSize 7), but found {cs_val}K")

    timing_points.sort(key=lambda tp: tp.time)
    hit_objects.sort(key=lambda ho: ho.time)

    return Beatmap7K(
        title=metadata.get("Title", ""),
        artist=metadata.get("Artist", ""),
        creator=metadata.get("Creator", ""),
        version=metadata.get("Version", ""),
        mode=mode_val,
        circle_size=cs_val,
        overall_difficulty=float(difficulty.get("OverallDifficulty", "8.0")),
        timing_points=timing_points,
        hit_objects=hit_objects,
    )
