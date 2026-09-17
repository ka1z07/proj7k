from dataclasses import dataclass, field
from enum import Enum
import hashlib
import math
import os
from typing import List, Optional, Dict


class NoteType(Enum):
    RICE = "rice"
    LN = "ln"


@dataclass
class HitObject:
    column: int
    time: float
    note_type: NoteType
    end_time: Optional[float] = None
    hit_sound: int = 0
    addition: str = "0:0:0:0:"

    def __post_init__(self):
        if self.note_type == NoteType.LN and self.end_time is None:
            self.end_time = self.time


@dataclass
class TimingPoint:
    time: float
    beat_length: float
    meter: int = 4
    uninherited: bool = True
    sample_set: int = 0
    sample_index: int = 0
    volume: int = 100
    effects: int = 0

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
    audio_filename: str = ""
    tags: str = ""
    extra_sections: Dict[str, Dict[str, str]] = field(default_factory=dict)
    raw_events: List[str] = field(default_factory=list)
    md5: str = ""


def _format_num(val: float) -> str:
    if abs(val - round(val)) < 1e-9:
        return str(int(round(val)))
    # Retain up to 12 significant digits for fractional beat length and sub-ms timing
    s = f"{val:.10f}".rstrip("0").rstrip(".")
    return s



def parse_osu_7k(content_or_path: str) -> Beatmap7K:
    if os.path.exists(content_or_path):
        with open(content_or_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    else:
        # If it looks like a path (e.g. ends with .osu or single line) but doesn't exist
        if content_or_path.endswith(".osu") or "\n" not in content_or_path:
            raise FileNotFoundError(f"Beatmap file not found: {content_or_path}")
        content = content_or_path

    content_md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
    lines = content.splitlines()
    current_section = ""
    
    general: Dict[str, str] = {}
    metadata: Dict[str, str] = {}
    difficulty: Dict[str, str] = {}
    timing_points: List[TimingPoint] = []
    hit_objects: List[HitObject] = []
    raw_events: List[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            continue

        if current_section == "Events":
            raw_events.append(raw_line)
            continue

        if line.startswith("//"):
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
                sample_set = int(parts[3]) if len(parts) > 3 else 0
                sample_index = int(parts[4]) if len(parts) > 4 else 0
                volume = int(parts[5]) if len(parts) > 5 else 100
                uninherited = True
                if len(parts) >= 7:
                    uninherited = bool(int(parts[6]))
                effects = int(parts[7]) if len(parts) > 7 else 0
                timing_points.append(
                    TimingPoint(
                        time=time_val,
                        beat_length=beat_len,
                        meter=meter_val,
                        uninherited=uninherited,
                        sample_set=sample_set,
                        sample_index=sample_index,
                        volume=volume,
                        effects=effects,
                    )
                )
        elif current_section == "HitObjects":
            parts = line.split(",")
            if len(parts) >= 5:
                x_val = float(parts[0])
                time_val = float(parts[2])
                type_flag = int(parts[3])
                hit_sound = int(parts[4]) if len(parts) > 4 else 0
                
                # Column formula for mania: floor(x * 7 / 512)
                col = int(math.floor(x_val * 7.0 / 512.0))
                col = max(0, min(6, col))

                is_ln = bool(type_flag & 128)
                end_time_val = None
                addition_str = "0:0:0:0:"
                if is_ln and len(parts) >= 6:
                    end_parts = parts[5].split(":", 1)
                    try:
                        end_time_val = float(end_parts[0])
                    except ValueError:
                        end_time_val = time_val
                    if len(end_parts) > 1:
                        addition_str = end_parts[1]
                elif not is_ln and len(parts) >= 6:
                    addition_str = parts[5]
                
                hit_objects.append(
                    HitObject(
                        column=col,
                        time=time_val,
                        note_type=NoteType.LN if is_ln else NoteType.RICE,
                        end_time=end_time_val,
                        hit_sound=hit_sound,
                        addition=addition_str,
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
        audio_filename=general.get("AudioFilename", ""),
        tags=metadata.get("Tags", ""),
        extra_sections={
            "General": general,
            "Metadata": metadata,
            "Difficulty": difficulty,
        },
        raw_events=raw_events,
        md5=content_md5,
    )


def dump_osu_7k(beatmap: Beatmap7K) -> str:
    """Serializes a Beatmap7K data structure back to standard osu!mania 7K .osu format."""
    lines: List[str] = ["osu file format v14", ""]

    # 1. [General]
    lines.append("[General]")
    if "General" in beatmap.extra_sections and beatmap.extra_sections["General"]:
        gen_dict = dict(beatmap.extra_sections["General"])
        gen_dict["Mode"] = str(beatmap.mode)
        if beatmap.audio_filename:
            gen_dict["AudioFilename"] = beatmap.audio_filename
        elif "AudioFilename" not in gen_dict:
            gen_dict["AudioFilename"] = "audio.mp3"
        for k, v in gen_dict.items():
            lines.append(f"{k}: {v}")
    else:
        lines.append(f"AudioFilename: {beatmap.audio_filename or 'audio.mp3'}")
        lines.append("AudioLeadIn: 0")
        lines.append("PreviewTime: -1")
        lines.append("Countdown: 0")
        lines.append("SampleSet: Normal")
        lines.append("StackLeniency: 0.7")
        lines.append(f"Mode: {beatmap.mode}")
        lines.append("LetterboxInBreaks: 0")
        lines.append("SpecialStyle: 0")
        lines.append("WidescreenStoryboard: 0")
    lines.append("")

    # 2. [Editor] (if preserved)
    if "Editor" in beatmap.extra_sections and beatmap.extra_sections["Editor"]:
        lines.append("[Editor]")
        for k, v in beatmap.extra_sections["Editor"].items():
            lines.append(f"{k}: {v}")
        lines.append("")

    # 3. [Metadata]
    lines.append("[Metadata]")
    if "Metadata" in beatmap.extra_sections and beatmap.extra_sections["Metadata"]:
        meta_dict = dict(beatmap.extra_sections["Metadata"])
        meta_dict["Title"] = beatmap.title
        meta_dict["Artist"] = beatmap.artist
        meta_dict["Creator"] = beatmap.creator
        meta_dict["Version"] = beatmap.version
        meta_dict["Tags"] = beatmap.tags
        for k, v in meta_dict.items():
            lines.append(f"{k}:{v}")
    else:
        lines.append(f"Title:{beatmap.title}")
        lines.append(f"TitleUnicode:{beatmap.title}")
        lines.append(f"Artist:{beatmap.artist}")
        lines.append(f"ArtistUnicode:{beatmap.artist}")
        lines.append(f"Creator:{beatmap.creator}")
        lines.append(f"Version:{beatmap.version}")
        lines.append("Source:")
        lines.append(f"Tags:{beatmap.tags}")
        lines.append("BeatmapID:0")
        lines.append("BeatmapSetID:-1")
    lines.append("")

    # 4. [Difficulty]
    lines.append("[Difficulty]")
    if "Difficulty" in beatmap.extra_sections and beatmap.extra_sections["Difficulty"]:
        diff_dict = dict(beatmap.extra_sections["Difficulty"])
        diff_dict["CircleSize"] = str(beatmap.circle_size)
        diff_dict["OverallDifficulty"] = _format_num(beatmap.overall_difficulty)
        for k, v in diff_dict.items():
            lines.append(f"{k}:{v}")
    else:
        lines.append("HPDrainRate:8")
        lines.append(f"CircleSize:{beatmap.circle_size}")
        lines.append(f"OverallDifficulty:{_format_num(beatmap.overall_difficulty)}")
        lines.append("ApproachRate:8")
        lines.append("SliderMultiplier:1.4")
        lines.append("SliderTickRate:1")
    lines.append("")

    # 5. [Events]
    lines.append("[Events]")
    if beatmap.raw_events:
        for ev in beatmap.raw_events:
            lines.append(ev)
    else:
        lines.append("//Background and Video events")
        lines.append("//Break Periods")
        lines.append("//Storyboard Sound Samples")
    lines.append("")

    # 6. [TimingPoints]
    lines.append("[TimingPoints]")
    sorted_tps = sorted(beatmap.timing_points, key=lambda tp: tp.time)
    for tp in sorted_tps:
        lines.append(
            f"{_format_num(tp.time)},{_format_num(tp.beat_length)},{tp.meter},"
            f"{tp.sample_set},{tp.sample_index},{tp.volume},{1 if tp.uninherited else 0},{tp.effects}"
        )
    lines.append("")

    # 7. [HitObjects]
    lines.append("[HitObjects]")
    sorted_hos = sorted(beatmap.hit_objects, key=lambda ho: (ho.time, ho.column))
    for ho in sorted_hos:
        x_val = int(math.floor((ho.column * 512.0 + 256.0) / 7.0))
        y_val = 192
        time_val = int(round(ho.time))
        hit_sound = getattr(ho, "hit_sound", 0)
        addition = getattr(ho, "addition", "0:0:0:0:")

        if ho.note_type == NoteType.RICE:
            type_flag = 1
            lines.append(f"{x_val},{y_val},{time_val},{type_flag},{hit_sound},{addition}")
        else:
            type_flag = 128
            end_val = int(round(ho.end_time if ho.end_time is not None else ho.time))
            lines.append(f"{x_val},{y_val},{time_val},{type_flag},{hit_sound},{end_val}:{addition}")

    lines.append("")
    return "\n".join(lines)

