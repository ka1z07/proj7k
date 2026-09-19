import copy
import hashlib
import os
from typing import Callable, Collection, Optional, Tuple, Union

from proj7k.parser import (
    Beatmap7K,
    HitObject,
    dump_osu_7k,
    parse_osu_7k,
)


def _clone_hit_object(ho: HitObject) -> HitObject:
    """Creates an independent clone of a HitObject."""
    return HitObject(
        column=ho.column,
        time=ho.time,
        note_type=ho.note_type,
        end_time=ho.end_time,
        hit_sound=getattr(ho, "hit_sound", 0),
        addition=getattr(ho, "addition", "0:0:0:0:"),
    )


def apply_pure_deletion(
    beatmap: Beatmap7K,
    notes_to_remove: Optional[
        Union[Collection[HitObject], Collection[int], Callable[[HitObject], bool]]
    ] = None,
) -> Beatmap7K:
    """
    Applies pure deletion mutation (ADR-0011) to a Beatmap7K.

    变异动作空间限定为原子级音符剔除：
    - 米键（Rice）直接移除时间戳与轨位记录；
    - 长音符（LN）头、身、尾一体完整移除，图元闭集无残留，绝不降解为米键或留下孤立长音符残段；
    - 保证输入母谱不变性（Immutability），返回剔除后的新 Beatmap7K 实例。
    """
    if not notes_to_remove:
        new_bm = copy.copy(beatmap)
        new_bm.extra_sections = {
            k: dict(v) for k, v in beatmap.extra_sections.items()
        }
        new_bm.hit_objects = [_clone_hit_object(ho) for ho in beatmap.hit_objects]
        return new_bm

    # Prepare removal criteria
    should_remove_fn: Callable[[int, HitObject], bool]

    if callable(notes_to_remove):
        pred = notes_to_remove
        should_remove_fn = lambda idx, ho: bool(pred(ho))
    elif isinstance(notes_to_remove, (set, list, tuple)):
        first_elem = next(iter(notes_to_remove)) if notes_to_remove else None
        if isinstance(first_elem, int):
            index_set = set(notes_to_remove)
            should_remove_fn = lambda idx, ho: idx in index_set
        else:
            ho_ids = {id(h) for h in notes_to_remove}
            ho_keys = {
                (
                    h.column,
                    round(h.time, 3),
                    h.note_type,
                    round(h.end_time, 3) if h.end_time is not None else None,
                )
                for h in notes_to_remove
            }
            should_remove_fn = lambda idx, ho: (
                id(ho) in ho_ids
                or (
                    ho.column,
                    round(ho.time, 3),
                    ho.note_type,
                    round(ho.end_time, 3) if ho.end_time is not None else None,
                )
                in ho_keys
            )
    else:
        raise TypeError(f"Unsupported notes_to_remove type: {type(notes_to_remove)}")

    surviving_hit_objects = [
        _clone_hit_object(ho)
        for idx, ho in enumerate(beatmap.hit_objects)
        if not should_remove_fn(idx, ho)
    ]

    new_bm = copy.copy(beatmap)
    new_bm.extra_sections = {
        k: dict(v) for k, v in beatmap.extra_sections.items()
    }
    new_bm.hit_objects = surviving_hit_objects
    return new_bm


def update_practice_metadata(
    beatmap: Beatmap7K,
    target_dan: str,
    original_md5: Optional[str] = None,
    dominant_skill: Optional[str] = None,
) -> Beatmap7K:
    """
    Updates beatmap metadata for derivative practice beatmaps (SPEC-P5.1-01 / ADR-0011):
    - Version: '[P-{TargetDan}] {OriginalVersion}' or '[P-{TargetDan} {DominantSkill}] {OriginalVersion}'
    - Tags: appends 'proj7k_downscaled target_{TargetDan} orig_md5_{OriginalMD5[:8]}'
      (and 'dominant_{DominantSkill}' if provided)
    """
    md5_str = original_md5 or beatmap.md5
    if not md5_str:
        md5_str = hashlib.md5(dump_osu_7k(beatmap).encode("utf-8")).hexdigest()

    if dominant_skill:
        prefix = f"[P-{target_dan} {dominant_skill}]"
    else:
        prefix = f"[P-{target_dan}]"

    orig_version = beatmap.version.strip()
    if orig_version.startswith(prefix):
        new_version = orig_version
    elif orig_version:
        new_version = f"{prefix} {orig_version}"
    else:
        new_version = prefix

    required_tags = [
        "proj7k_downscaled",
        f"target_{target_dan}",
        f"orig_md5_{md5_str[:8]}",
    ]
    if dominant_skill:
        required_tags.append(f"dominant_{dominant_skill}")

    existing_tokens = beatmap.tags.split() if beatmap.tags else []
    for tag in required_tags:
        if tag not in existing_tokens:
            existing_tokens.append(tag)
    new_tags = " ".join(existing_tokens)

    # Return modified copy without mutating original
    new_bm = copy.copy(beatmap)
    new_bm.version = new_version
    new_bm.tags = new_tags
    new_bm.extra_sections = {
        k: dict(v) for k, v in beatmap.extra_sections.items()
    }

    if "Metadata" not in new_bm.extra_sections:
        new_bm.extra_sections["Metadata"] = {}

    meta = new_bm.extra_sections["Metadata"]
    meta["Version"] = new_version
    meta["Tags"] = new_tags
    # Enforce Independent Local Beatmap identity (ADR-0011 / CONTEXT.md)
    # BeatmapID: 0 marks an unsubmitted local diff, BeatmapSetID: -1 marks an independent local set
    meta["BeatmapID"] = "0"
    meta["BeatmapSetID"] = "-1"
    # Remove any online IDs to prevent osu! client from replacing or conflicting with original online beatmap
    for online_key in ["BeatmapOnlineID", "BeatmapSetOnlineID"]:
        meta.pop(online_key, None)

    return new_bm


def create_practice_beatmap(
    beatmap: Beatmap7K,
    target_dan: str,
    original_md5: Optional[str] = None,
    dominant_skill: Optional[str] = None,
    notes_to_remove: Optional[
        Union[Collection[HitObject], Collection[int], Callable[[HitObject], bool]]
    ] = None,
) -> Beatmap7K:
    """
    Creates a derivative practice beatmap by applying pure deletion mutation
    and updating provenance metadata.
    """
    mutated = apply_pure_deletion(beatmap, notes_to_remove)
    return update_practice_metadata(
        mutated,
        target_dan=target_dan,
        original_md5=original_md5,
        dominant_skill=dominant_skill,
    )


def export_practice_beatmap(
    content_or_path: Union[str, Beatmap7K],
    target_dan: str,
    dominant_skill: Optional[str] = None,
    notes_to_remove: Optional[
        Union[Collection[HitObject], Collection[int], Callable[[HitObject], bool]]
    ] = None,
    output_path: Optional[str] = None,
) -> Tuple[Beatmap7K, str]:
    """
    Reads an .osu 7K file or Beatmap7K instance, executes pure deletion mutation,
    updates holographic provenance metadata, and exports standard .osu text.
    Optionally writes to output_path if provided.
    """
    if isinstance(content_or_path, Beatmap7K):
        bm = content_or_path
        orig_md5 = bm.md5 or hashlib.md5(dump_osu_7k(bm).encode("utf-8")).hexdigest()
    else:
        if os.path.exists(content_or_path):
            with open(content_or_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            orig_md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
            bm = parse_osu_7k(content)
        else:
            orig_md5 = hashlib.md5(content_or_path.encode("utf-8")).hexdigest()
            bm = parse_osu_7k(content_or_path)

    practice_bm = create_practice_beatmap(
        bm,
        target_dan=target_dan,
        original_md5=orig_md5,
        dominant_skill=dominant_skill,
        notes_to_remove=notes_to_remove,
    )

    dumped_text = dump_osu_7k(practice_bm)
    # Synchronize derived beatmap's MD5 with the dumped content
    practice_bm.md5 = hashlib.md5(dumped_text.encode("utf-8")).hexdigest()

    if output_path:
        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(dumped_text)

    return practice_bm, dumped_text
