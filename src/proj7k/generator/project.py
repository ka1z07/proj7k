"""
A generated chart as a project: sections that can be written again one by one, locked, or tied to another section
(ADR-0047, phase 1).

Every generation writes `<chart>.p7kgen.json` beside its .osu. It names the song (audio, background), the target,
the timing, the sections and every take (version) of the chart. The chart is cut into sections of `SECTION_BARS`
measures from its timing. Operations make a new take from the current one and never touch the old ones:

- **rewrite** sections: Mapperatorinator writes that stretch again (`add_to_beatmap`, reading the notes around it),
  `candidates` times at the section's own target stars, and the take whose section lands closest, with the least
  jump at its edges, is kept. Notes outside the stretch are copied from the current take as they are.
- **lock** a section: nothing changes it any more (rewrites refuse it, anchors skip it).
- **anchor** a section to another: it is charted as that section, note for note or mirrored, beat for beat on its
  own beats, now and after every later change to the source.
- **undo** / **redo** move between takes.

Each take is an .osu (and an .osz with the song) of its own, its difficulty name ending in "v<n>", so importing one
into osu!lazer never replaces another (BeatmapSetID -1, as every generated chart).

    python3 -m proj7k.generator.project "generated_maps/A - T (proj7k) [Gen 5th (5.12)].p7kgen.json" show
    python3 -m proj7k.generator.project PROJECT rewrite 5 6 --target-sr 6.2 --candidates 3
    python3 -m proj7k.generator.project PROJECT lock 1 2
    python3 -m proj7k.generator.project PROJECT anchor 9 --to 5 --mirror
    python3 -m proj7k.generator.project PROJECT undo
"""

import argparse
import json
import logging
import sys
import zipfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from proj7k import cancel
from proj7k.engine import evaluate_notes
from proj7k.generator import legal
from proj7k.i18n import tr
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, dump_osu_7k, parse_osu_7k

logger = logging.getLogger(__name__)

SUFFIX = ".p7kgen.json"
#: Measures per section: a phrase of 8 bars is the unit a mapper usually writes and rewrites.
SECTION_BARS = 8
DEFAULT_CANDIDATES = 3
#: Beats on each side of a section's edge compared for the seam (notes per beat inside against outside).
SEAM_BEATS = 2
#: How much a seam's jump (|log2| of the density ratio, averaged over the edges) weighs against a star of error.
SEAM_WEIGHT = 0.5
VERSION = 1


@dataclass
class Section:
    first_bar: int
    last_bar: int
    start_ms: float
    end_ms: float
    locked: bool = False
    anchor: Optional[int] = None      # the index of the section it is charted as
    mirror: bool = False              # ...mirrored (column c -> 6 - c)


@dataclass
class Take:
    osu: str                          # file names, beside the project file
    osz: Optional[str]
    note: str                         # what made it: "generate", "rewrite 5–6", "lock 1", ...
    stars: float


@dataclass
class Project:
    audio: str
    audio_filename: str
    target_stars: float
    target_label: str = ""
    ln_ratio: Optional[float] = None
    model_fast: bool = False
    model_args: List[str] = field(default_factory=list)
    seed: int = 0
    pool_mix: Optional[Dict[str, float]] = None   # the technique mix it was generated with (mix.py)
    bg: Optional[str] = None
    bg_filename: Optional[str] = None
    base_version: str = ""
    sections: List[Section] = field(default_factory=list)
    takes: List[Take] = field(default_factory=list)
    current: int = 0
    version: int = VERSION
    path: Optional[Path] = None       # where it is saved (not saved itself)

    # --- files ------------------------------------------------------------------------------------------------

    @property
    def folder(self) -> Path:
        assert self.path is not None
        return self.path.parent

    def save(self) -> Path:
        assert self.path is not None
        data = asdict(self)
        data.pop("path")
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return self.path

    @classmethod
    def load(cls, path) -> "Project":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["sections"] = [Section(**s) for s in data.get("sections", [])]
        data["takes"] = [Take(**t) for t in data.get("takes", [])]
        return cls(**data, path=path)

    def chart(self, index: Optional[int] = None) -> Beatmap7K:
        take = self.takes[self.current if index is None else index]
        return parse_osu_7k((self.folder / take.osu).read_text(encoding="utf-8", errors="replace"))

    def audio_path(self, work: Path) -> Path:
        """The song: where it was, or else taken out of the first take's .osz."""
        path = Path(self.audio)
        if path.is_file():
            return path
        for take in self.takes:
            if take.osz and (self.folder / take.osz).is_file():
                with zipfile.ZipFile(self.folder / take.osz) as z:
                    if self.audio_filename in z.namelist():
                        work.mkdir(parents=True, exist_ok=True)
                        out = work / Path(self.audio_filename).name
                        out.write_bytes(z.read(self.audio_filename))
                        return out
        raise FileNotFoundError(tr(f"找不到这首歌的音频：{self.audio}", f"The song's audio is gone: {self.audio}"))


# --- beats and sections ---------------------------------------------------------------------------------------


def _uninherited(timing: Sequence[TimingPoint]) -> List[TimingPoint]:
    out = sorted((tp for tp in timing if tp.uninherited and tp.beat_length > 0), key=lambda tp: tp.time)
    if not out:
        raise ValueError(tr("谱面没有 timing", "The chart has no timing"))
    return out


class Beats:
    """Time (ms) to beat count and back, through every timing section (the first one reaching back to 0)."""

    def __init__(self, timing: Sequence[TimingPoint]):
        self.points = _uninherited(timing)
        self.starts = [0.0]
        for a, b in zip(self.points, self.points[1:]):
            self.starts.append(self.starts[-1] + (b.time - a.time) / a.beat_length)

    def beat(self, t: float) -> float:
        i = max(0, int(np.searchsorted([p.time for p in self.points], t, side="right")) - 1)
        p = self.points[i]
        return self.starts[i] + (t - p.time) / p.beat_length

    def time(self, b: float) -> float:
        i = max(0, int(np.searchsorted(self.starts, b, side="right")) - 1)
        p = self.points[i]
        return p.time + (b - self.starts[i]) * p.beat_length


def bar_starts(timing: Sequence[TimingPoint], end_ms: float) -> List[float]:
    """The start of every measure up to `end_ms`. Each timing point starts a measure; the first reaches back to the
    start of the song."""
    points = _uninherited(timing)
    out: List[float] = []
    for i, tp in enumerate(points):
        bar = tp.beat_length * (tp.meter if tp.meter and tp.meter > 0 else 4)
        stop = points[i + 1].time if i + 1 < len(points) else end_ms
        t = tp.time
        if i == 0:
            t -= np.floor(tp.time / bar) * bar if tp.time > 0 else 0.0
        while t < stop - 1e-6:
            out.append(float(t))
            t += bar
    return out


def cut_sections(chart: Beatmap7K, bars_per_section: int = SECTION_BARS) -> List[Section]:
    """Sections of `bars_per_section` measures, from the measure of the first note to the one of the last."""
    if not chart.hit_objects:
        return []
    first = min(o.time for o in chart.hit_objects)
    last = max(o.end_time if o.end_time is not None else o.time for o in chart.hit_objects)
    bars = bar_starts(chart.timing_points, last + 1.0)
    i0 = max(0, int(np.searchsorted(bars, first, side="right")) - 1)
    bars = bars[i0:]
    bar_ms = bars[1] - bars[0] if len(bars) > 1 else 2000.0
    edges = bars + [bars[-1] + bar_ms]
    out = []
    for k in range(0, len(bars), bars_per_section):
        hi = min(k + bars_per_section, len(bars))
        out.append(Section(first_bar=i0 + k, last_bar=i0 + hi - 1, start_ms=edges[k], end_ms=edges[hi]))
    return out


def _inside(o: HitObject, start: float, end: float) -> bool:
    return start <= o.time < end


def section_of(sections: Sequence[Section], t: float) -> Optional[int]:
    for i, s in enumerate(sections):
        if s.start_ms <= t < s.end_ms:
            return i
    return None


def _profile(objs: Sequence[HitObject]):
    return evaluate_notes([(o.column, o.time / 1000.0, o.end_time / 1000.0 if o.note_type == NoteType.LN else None)
                           for o in objs])


def _rated(objs: Sequence[HitObject]) -> float:
    if not objs:
        return 0.0
    return float(_profile(objs).total_stars)


def describe(project: Project) -> List[Dict]:
    """Each section of the current take: bars, time, notes, notes per second, engine stars and main skill, state."""
    chart = project.chart()
    out = []
    for i, s in enumerate(project.sections):
        objs = [o for o in chart.hit_objects if _inside(o, s.start_ms, s.end_ms)]
        skill, stars = None, 0.0
        if objs:
            prof = evaluate_notes([(o.column, o.time / 1000.0, o.end_time / 1000.0 if o.note_type == NoteType.LN else None)
                                   for o in objs])
            stars, skill = float(prof.total_stars), prof.dominant_skill
        span = max((s.end_ms - s.start_ms) / 1000.0, 1e-3)
        out.append({
            "index": i, "bars": [s.first_bar + 1, s.last_bar + 1], "start_ms": s.start_ms, "end_ms": s.end_ms,
            "notes": len(objs), "nps": round(len(objs) / span, 2), "stars": round(stars, 2), "skill": skill,
            "ln_share": round(sum(o.note_type == NoteType.LN for o in objs) / max(1, len(objs)), 2),
            "locked": s.locked, "anchor": s.anchor, "mirror": s.mirror,
        })
    return out


def to_dict(project: Project) -> Dict:
    take = project.takes[project.current]
    return {
        "project_file": str(project.path), "current": project.current, "takes": len(project.takes),
        "take": {"n": project.current + 1, "note": take.note, "stars": take.stars,
                 "output_file": str(project.folder / take.osu),
                 "osz_file": str(project.folder / take.osz) if take.osz else None},
        "can_undo": project.current > 0, "can_redo": project.current < len(project.takes) - 1,
        "sections": describe(project),
    }


# --- making takes ---------------------------------------------------------------------------------------------


def create(chart_path: Path, chart: Beatmap7K, osz_path: Optional[Path], *, audio: Path, audio_filename: str,
           target_stars: float, target_label: str = "", ln_ratio: Optional[float] = None, model_fast: bool = False,
           model_args: Sequence[str] = (), seed: int = 0, bg: Optional[Path] = None,
           bg_filename: Optional[str] = None, pool_mix: Optional[Dict[str, float]] = None) -> Project:
    """The project of a chart just generated, saved beside it with the chart as its first take."""
    project = Project(audio=str(Path(audio).resolve()), audio_filename=audio_filename, target_stars=float(target_stars),
                      target_label=target_label, ln_ratio=ln_ratio, model_fast=model_fast, model_args=list(model_args),
                      seed=seed, pool_mix=dict(pool_mix) if pool_mix else None, bg=str(bg) if bg else None, bg_filename=bg_filename, base_version=chart.version,
                      sections=cut_sections(chart),
                      takes=[Take(osu=chart_path.name, osz=osz_path.name if osz_path else None, note="generate",
                                  stars=round(_rated(chart.hit_objects), 3))],
                      path=chart_path.with_name(chart_path.stem + SUFFIX))
    project.save()
    return project


def apply_anchors(project: Project, objs: Sequence[HitObject], timing: Sequence[TimingPoint]) -> List[HitObject]:
    """The notes with every anchored section (not locked) charted as its source, beat for beat."""
    beats = Beats(timing)
    out = list(objs)
    for i, s in enumerate(project.sections):
        if s.anchor is None or s.locked:
            continue
        src = project.sections[s.anchor]
        s0, t0 = beats.beat(src.start_ms), beats.beat(s.start_ms)
        length = beats.beat(s.end_ms) - t0
        moved = []
        for o in out:
            if not _inside(o, src.start_ms, src.end_ms):
                continue
            b = beats.beat(o.time) - s0
            if b >= length - 1e-6:
                continue
            col = 6 - o.column if s.mirror else o.column
            end = None
            if o.end_time is not None:
                end = float(round(beats.time(min(beats.beat(o.end_time) - s0, length) + t0)))
            moved.append(replace(o, column=col, time=float(round(beats.time(b + t0))), end_time=end))
        out = [o for o in out if not _inside(o, s.start_ms, s.end_ms)] + moved
    out.sort(key=lambda o: (o.time, o.column))
    return legal.sanitize(out, timing)[0]


def new_take(project: Project, objs: Sequence[HitObject], note: str, package: bool = True) -> Project:
    """A take made of `objs` (anchors applied), written beside the project and made current. Later takes after the
    current one stay, so redo still finds them."""
    from proj7k.downscaler.locator import package_into_osz
    from proj7k.generator.cli import safe_filename

    base = project.chart(0)
    chart = project.chart()
    objs = apply_anchors(project, objs, chart.timing_points)
    stars = _rated(objs)
    n = len(project.takes) + 1
    # A project made before generated charts kept one scroll speed gets it here too.
    bm = replace(chart, hit_objects=list(objs), version=f"{project.base_version} v{n}",
                 timing_points=legal.constant_scroll(chart.timing_points, legal.end_ms(objs)))
    stem = safe_filename(f"{base.artist} - {base.title} ({base.creator}) [{bm.version}]")
    osu = project.folder / f"{stem}.osu"
    osu.write_text(dump_osu_7k(bm), encoding="utf-8")
    osz = None
    if package:
        work = project.folder / ".p7kgen-work"
        bg = Path(project.bg) if project.bg and Path(project.bg).is_file() else None
        osz = package_into_osz(osu, project.folder / f"{stem}.osz", audio_path=project.audio_path(work),
                               audio_filename=project.audio_filename, bg_path=bg,
                               bg_filename=project.bg_filename if bg else None)
    project.takes.append(Take(osu=osu.name, osz=osz.name if osz else None, note=note, stars=round(stars, 3)))
    project.current = len(project.takes) - 1
    project.save()
    return project


def _check(project: Project, indices: Sequence[int]) -> List[int]:
    idx = sorted(set(indices))
    if not idx or idx[0] < 0 or idx[-1] >= len(project.sections):
        raise ValueError(tr(f"段落编号要在 1–{len(project.sections)} 之间", f"Sections are numbered 1–{len(project.sections)}"))
    return idx


def lock(project: Project, indices: Sequence[int], locked: bool = True) -> Project:
    for i in _check(project, indices):
        project.sections[i].locked = locked
    project.save()
    return project


def anchor(project: Project, targets: Sequence[int], source: Optional[int], mirror: bool = False) -> Project:
    """Tie `targets` to `source` (or free them, with None) and make a take with the anchors applied."""
    targets = _check(project, targets)
    if source is not None:
        _check(project, [source])
        if source in targets:
            raise ValueError(tr("一段不能锚定到它自己", "A section can't be anchored to itself"))
        if project.sections[source].anchor is not None:
            raise ValueError(tr(f"第 {source + 1} 段自己锚定在别处；请锚定到它的来源",
                                f"Section {source + 1} is itself anchored; anchor to its source"))
        if any(project.sections[i].anchor == t for i in range(len(project.sections)) for t in targets):
            raise ValueError(tr("有别的段锚定在这些段上；先解开它们", "Other sections are anchored to these; free them first"))
    for i in targets:
        project.sections[i].anchor = source
        project.sections[i].mirror = bool(mirror) if source is not None else False
    if source is None:
        project.save()
        return project
    names = ", ".join(str(i + 1) for i in targets)
    note = tr(f"第 {names} 段{'镜像' if mirror else '同'}第 {source + 1} 段",
              f"section {names} {'mirrors' if mirror else 'as'} section {source + 1}")
    return new_take(project, project.chart().hit_objects, note)


def move(project: Project, step: int) -> Project:
    project.current = min(max(project.current + step, 0), len(project.takes) - 1)
    project.save()
    return project


def seam(objs: Sequence[HitObject], start: float, end: float, beats: Beats) -> float:
    """How much the density jumps at the stretch's edges: |log2| of notes per beat just inside over just outside,
    averaged over the edges that have notes on both sides."""
    times = np.array(sorted(o.time for o in objs))
    jumps = []
    for edge, inward in ((start, 1), (end, -1)):
        b = beats.beat(edge)
        a_in = sorted((beats.time(b), beats.time(b + inward * SEAM_BEATS)))
        a_out = sorted((beats.time(b - inward * SEAM_BEATS), beats.time(b)))
        n_in = int(((times >= a_in[0]) & (times < a_in[1])).sum())
        n_out = int(((times >= a_out[0]) & (times < a_out[1])).sum())
        if n_in or n_out:
            jumps.append(abs(np.log2((n_in + 1) / (n_out + 1))))
    return float(np.mean(jumps)) if jumps else 0.0


def rewrite(project: Project, indices: Sequence[int], *, target_sr: Optional[float] = None,
            ln_ratio: Optional[float] = None, seed: Optional[int] = None, candidates: int = DEFAULT_CANDIDATES,
            model_args: Sequence[str] = (), pool_mix=None, runner=None,
            log: Callable[[str], None] = logger.info) -> Project:
    """Write sections `indices` (one stretch, first to last) again and keep the best of `candidates` model runs.
    `pool_mix` (default: the project's) leans the pick toward the pools it weights up, as at generation."""
    from proj7k.generator import mapperatorinator as m
    from proj7k.generator import mix as mix_mod

    idx = _check(project, indices)
    if idx != list(range(idx[0], idx[-1] + 1)):
        raise ValueError(tr("一次只能重写相邻的几段", "Only neighbouring sections can be rewritten together"))
    for i in idx:
        s = project.sections[i]
        if s.locked:
            raise ValueError(tr(f"第 {i + 1} 段已锁定", f"Section {i + 1} is locked"))
        if s.anchor is not None:
            raise ValueError(tr(f"第 {i + 1} 段锚定在第 {s.anchor + 1} 段，先解开锚定再重写",
                                f"Section {i + 1} is anchored to section {s.anchor + 1}; free it before rewriting"))
    start, end = project.sections[idx[0]].start_ms, project.sections[idx[-1]].end_ms
    chart = project.chart()
    beats = Beats(chart.timing_points)
    outside = [o for o in chart.hit_objects if not _inside(o, start, end)]
    current_inside = [o for o in chart.hit_objects if _inside(o, start, end)]
    target = float(target_sr) if target_sr else (_rated(current_inside) or project.target_stars)
    work = project.folder / ".p7kgen-work"
    work.mkdir(parents=True, exist_ok=True)
    real_model = runner is None
    if real_model:
        m.install(log)
    runner = runner or m._default_runner(None, log)
    audio = project.audio_path(work)
    if real_model:
        audio = m.model_audio(audio, work, project.audio_filename)
    context = work / "current.osu"
    context.write_text(dump_osu_7k(chart), encoding="utf-8")
    ratio = project.ln_ratio if ln_ratio is None else ln_ratio
    seed0 = project.seed + 100 * len(project.takes) if seed is None else seed
    difficulty = min(max(target * m.AIM, m.MIN_DIFFICULTY), m.MAX_DIFFICULTY)
    names = "–".join(str(i + 1) for i in sorted({idx[0], idx[-1]}))
    mix = mix_mod.parse(pool_mix) if pool_mix is not None else mix_mod.parse(project.pool_mix)
    if mix:
        log(tr(f"技法配比：{mix_mod.describe(mix)}", f"Technique mix: {mix_mod.describe(mix)}"))
    best = None
    for k in range(max(1, int(candidates))):
        cancel.checkpoint()  # a stop leaves the project as it was: the new take is only made after the last candidate
        extra = m.overrides(audio, work, difficulty, seed0 + k, hold_note_ratio=ratio, timing_from=context) + \
            ["add_to_beatmap=true", f"start_time={int(start) + 1}", f"end_time={int(end) - 1}"] + \
            list(project.model_args) + list(model_args)
        log(tr(f"重写第 {names} 段：候选 {k + 1}/{candidates}，难度条件 {difficulty:.2f}★，种子 {seed0 + k}……",
               f"Rewriting section {names}: candidate {k + 1}/{candidates}, difficulty {difficulty:.2f}★, seed {seed0 + k}…"))
        written = m._read(runner(audio, work, difficulty, seed0 + k, extra), log)
        inside = [o for o in written.hit_objects if _inside(o, start, end)]
        merged = legal.sanitize(sorted(outside + inside, key=lambda o: (o.time, o.column)), chart.timing_points)[0]
        stretch = [o for o in merged if _inside(o, start, end)]
        profile = _profile(stretch) if stretch else None
        stars = float(profile.total_stars) if profile else 0.0
        jump = seam(merged, start, end, beats)
        score = abs(stars - target) + SEAM_WEIGHT * jump
        if mix and profile:
            score -= mix_mod.WEIGHT * mix_mod.lean(profile, mix)
        log(tr(f"  → 这段 {stars:.2f}★（目标 {target:.2f}★），边缘突变 {jump:.2f}",
               f"  → {stars:.2f}★ for the stretch (target {target:.2f}★), edge jump {jump:.2f}")
            + (f"；{mix_mod.pools(profile, mix)}" if mix and profile else ""))
        if best is None or score < best[0]:
            best = (score, merged, stars)
        difficulty = min(m.next_difficulty(difficulty, stars, target), m.MAX_DIFFICULTY)
    _, merged, stars = best
    note = tr(f"重写第 {names} 段（{stars:.2f}★）", f"rewrite section {names} ({stars:.2f}★)")
    return new_take(project, merged, note)


# --- command line ---------------------------------------------------------------------------------------------


def _format(project: Project) -> str:
    d = to_dict(project)
    t = d["take"]
    lines = [tr(f"第 {t['n']}/{d['takes']} 版：{t['note']}，{t['stars']:.2f}★", f"Take {t['n']}/{d['takes']}: {t['note']}, {t['stars']:.2f}★"),
             f"  {t['output_file']}"]
    for s in d["sections"]:
        state = []
        if s["locked"]:
            state.append(tr("锁定", "locked"))
        if s["anchor"] is not None:
            state.append(tr(f"{'镜像' if s['mirror'] else '同'}第 {s['anchor'] + 1} 段",
                            f"{'mirrors' if s['mirror'] else 'as'} {s['anchor'] + 1}"))
        lines.append(f"  {s['index'] + 1:>3}. {tr('小节', 'bars')} {s['bars'][0]:>3}–{s['bars'][1]:<3} "
                     f"{s['start_ms'] / 1000:6.1f}s  {s['notes']:>4} {tr('音符', 'notes')}  {s['nps']:5.1f}/s  "
                     f"{s['stars']:5.2f}★ {s['skill'] or '':<11} {' '.join(state)}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    from proj7k.generator.cli import utf8_console

    utf8_console()
    p = argparse.ArgumentParser(prog="python3 -m proj7k.generator.project", description=__doc__.split("\n\n")[1])
    p.add_argument("project", help="生成时写在谱面旁边的 .p7kgen.json")
    sub = p.add_subparsers(dest="op", required=True)
    sub.add_parser("show", help="列出段落与当前版本")
    r = sub.add_parser("rewrite", help="重写相邻的几段")
    r.add_argument("sections", type=int, nargs="+")
    r.add_argument("--target-sr", type=float, help="这几段的目标星级（引擎星级）；默认保持它们现在的星级")
    r.add_argument("--ln", type=float, help="这几段的长条比例；默认用生成时的设置")
    r.add_argument("--seed", type=int)
    r.add_argument("--candidates", type=int, default=DEFAULT_CANDIDATES, help=f"生成几个候选，取最好的（默认 {DEFAULT_CANDIDATES}）")
    r.add_argument("--mix", help="这几段的技法配比，如 rc_jack=1,ln_release=-1（1 多、-1 少）；默认用生成时的配比，none 不用")
    r.add_argument("--model-arg", action="append", default=[], metavar="KEY=VALUE", help="传给模型的额外条件，可重复")
    for name, text in (("lock", "锁定"), ("unlock", "解锁")):
        q = sub.add_parser(name, help=text)
        q.add_argument("sections", type=int, nargs="+")
    a = sub.add_parser("anchor", help="把几段锚定到另一段（写成与它一样或镜像）")
    a.add_argument("sections", type=int, nargs="+")
    a.add_argument("--to", type=int, required=True)
    a.add_argument("--mirror", action="store_true")
    u = sub.add_parser("unanchor", help="解开锚定")
    u.add_argument("sections", type=int, nargs="+")
    sub.add_parser("undo", help="退回上一版")
    sub.add_parser("redo", help="前进到下一版")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        project = Project.load(args.project)
        sections = [i - 1 for i in getattr(args, "sections", [])]
        if args.op == "rewrite":
            rewrite(project, sections, target_sr=args.target_sr, ln_ratio=args.ln, seed=args.seed,
                    candidates=args.candidates, model_args=args.model_arg,
                    pool_mix=None if args.mix is None else ({} if args.mix.strip().lower() == "none" else args.mix),
                    log=lambda msg: print(msg, file=sys.stderr, flush=True))
        elif args.op in ("lock", "unlock"):
            lock(project, sections, args.op == "lock")
        elif args.op == "anchor":
            anchor(project, sections, args.to - 1, args.mirror)
        elif args.op == "unanchor":
            anchor(project, sections, None)
        elif args.op in ("undo", "redo"):
            move(project, -1 if args.op == "undo" else 1)
    except Exception as e:  # every failure is reported, not traced
        print(tr(f"失败：{e}", f"Failed: {e}"), file=sys.stderr)
        return 1
    print(_format(project))
    return 0


if __name__ == "__main__":
    sys.exit(main())
