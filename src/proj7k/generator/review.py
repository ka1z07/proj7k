"""
The consistency review of a generated chart against its design brief (ADR-0033 decision 6).

    python3 -m proj7k.generator.review --chart gen.osu --brief brief.json [--analysis analysis.json]
                                      [--target 6.96] --out <folder>

Writes ``review.json``, ``review.md`` (a table per section and the six checks, in Chinese) and
``preview.png`` (the first measures of every section, side by side). The six checks:

1. measures with the same rhythm are charted with the same keys (mirrored or moved across the keys
   counts as the same);
2. the hardest section is a chorus or drop, not an intro or a break;
3. each section's rows per measure follow its lead layer's onsets (needs the analysis);
4. no section boundary makes the hands jump across the keys at once;
5. each section's primary pattern holds at least `PRIMARY_MIN` of its rows, accents only where marked;
6. the chart's stars are within the tolerance of the target.
"""

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from proj7k.engine import evaluate_notes
from proj7k.generator import brief as brief_mod
from proj7k.generator.patterns import mirror
from proj7k.generator.rhythm import places
from proj7k.parser import Beatmap7K, NoteType, parse_osu_7k

PRIMARY_MIN = 0.7
#: A section other than a chorus may be at most this much harder than the hardest chorus.
HARDEST_SLACK = 0.15
LOUD_ROLES = ("chorus", "drop")
#: Rows per measure should correlate with the lead layer's onsets at least this well (sections of 8+ bars).
DENSITY_CORR_MIN = 0.2
#: Hands jump when the key centre moves this many columns within half a beat across a boundary.
JUMP_COLUMNS = 4.0
STAR_TOLERANCE = 0.1


def _notes(objs):
    return [(o.column, o.time / 1000.0, o.end_time / 1000.0 if o.note_type == NoteType.LN and o.end_time
             and o.end_time > o.time else None) for o in objs]


def bar_times(bm: Beatmap7K) -> Dict[int, Tuple[float, float]]:
    timing = [tp for tp in bm.timing_points if tp.uninherited]
    end = max((o.end_time or o.time) for o in bm.hit_objects) + 4000.0
    pl = places(timing, end)
    out: Dict[int, List[float]] = {}
    for t, b in zip(pl.time_ms, pl.bar):
        lo, hi = out.get(int(b), [t, t])
        out[int(b)] = [min(lo, t), max(hi, t)]
    bars = sorted(out)
    return {b: (out[b][0], out[bars[i + 1]][0] if i + 1 < len(bars) else out[b][1]) for i, b in enumerate(bars)}


def review(bm: Beatmap7K, brief: brief_mod.Brief, analysis: Optional[dict] = None,
           target: Optional[float] = None) -> Dict[str, Any]:
    spans = bar_times(bm)
    beat_ms = next(tp.beat_length for tp in bm.timing_points if tp.uninherited)
    rows = collections.defaultdict(int)
    for o in bm.hit_objects:
        rows[int(round(o.time))] |= 1 << o.column
    times = sorted(rows)
    masks = [rows[t] for t in times]
    bar_of = {}
    starts = sorted((a, b) for b, (a, _e) in spans.items())
    starts_t = [a for a, _ in starts]
    for t in times:
        k = max(0, np.searchsorted(starts_t, t + 2.0) - 1)   # note times are rounded to the ms
        bar_of[t] = starts[k][1]
    total = evaluate_notes(_notes(bm.hit_objects))
    lead_by_bar = {}
    loud_by_bar = {}
    if analysis:
        for b in analysis.get("bars", []):
            loud_by_bar[b["bar"]] = b.get("loudness_db", 0.0)
            lead_by_bar[b["bar"]] = b.get("stems") or {}

    sections = []
    for s in brief.sections:
        objs = [o for o in bm.hit_objects if s.first_bar <= bar_of.get(int(round(o.time)), -1) <= s.last_bar]
        idx = [i for i, t in enumerate(times) if s.first_bar <= bar_of[t] <= s.last_bar]
        d: Dict[str, Any] = {"bars": [s.first_bar, s.last_bar], "role": s.role, "technique": s.technique,
                             "pattern": s.pattern, "lead": s.lead, "notes": len(objs), "rows": len(idx)}
        d["ln_share"] = round(sum(o.note_type == NoteType.LN for o in objs) / max(len(objs), 1), 3)
        if s.ln:
            d["ln_planned"] = {"ratio": s.ln[0], "kind": s.ln[1]}
        if len(objs) >= 16:
            prof = evaluate_notes(_notes(objs))
            d["stars"] = round(prof.total_stars, 2)
            d["dominant_skill"] = prof.dominant_skill
        n_bars = s.last_bar - s.first_bar + 1
        d["rows_per_bar"] = round(len(idx) / n_bars, 2)
        # Check 5: the primary pattern's share, and accents only where marked.
        shapes = brief_mod.row_shapes(brief, [bar_of[times[i]] for i in idx],
                                      [(times[i] - spans[bar_of[times[i]]][0]) / beat_ms for i in idx])
        prim = acc_ok = acc_n = stray = n = 0
        for j, i in enumerate(idx):
            if i < 2:
                continue
            m, r1, r2 = masks[i], masks[i - 1], masks[i - 2]
            n += 1
            if s.pattern and s.pattern != "free" and brief_mod.PATTERNS[s.pattern][1](m, r1, r2):
                prim += 1
            if s.accent is not None:
                hit = brief_mod.PATTERNS[s.accent.pattern][1](m, r1, r2)
                if shapes[j] == s.accent.pattern:
                    acc_n += 1
                    acc_ok += hit
                elif hit and not (s.pattern and brief_mod.PATTERNS[s.pattern][1](m, r1, r2)):
                    stray += 1
        if s.pattern and s.pattern != "free":
            d["primary_share"] = round(prim / max(n, 1), 3)
        if s.accent is not None:
            d["accent_at_marks"] = round(acc_ok / max(acc_n, 1), 3)
            d["accent_elsewhere"] = stray
        # Check 3: rows per measure against the lead layer's onsets.
        if lead_by_bar and n_bars >= 8:
            per_bar = collections.Counter(bar_of[times[i]] for i in idx)
            lead = s.lead
            xs, ys = [], []
            for b in range(s.first_bar, s.last_bar + 1):
                st = lead_by_bar.get(b, {})
                if st:
                    xs.append(st.get(lead, max(st.values())) if lead else max(st.values()))
                    ys.append(per_bar.get(b, 0))
            if len(xs) >= 8 and np.std(xs) > 1e-6 and np.std(ys) > 1e-6:
                d["density_follows_lead"] = round(float(np.corrcoef(xs, ys)[0, 1]), 3)
            quiet = [b for b in range(s.first_bar, s.last_bar + 1) if loud_by_bar.get(b, 0.0) < -30.0 and per_bar.get(b, 0)]
            if quiet:
                d["notes_in_silent_bars"] = quiet
        sections.append(d)

    checks = []
    # 1. Phrases that come back.
    rep = _repeats(times, masks, bar_of, spans, beat_ms)
    checks.append({"id": 1, "name": "相同乐句同键形", "ok": rep["rate"] >= 0.6 if rep["pairs"] else True, **rep})
    # 2. The hardest section.
    rated = [d for d in sections if "stars" in d]
    loud = [d["stars"] for d in rated if d["role"] in LOUD_ROLES]
    if loud:
        # Every chorus and drop, not just the hardest one: a main drop thinner than a verse fails.
        over = [d["bars"] for d in rated if d["role"] not in LOUD_ROLES and d["stars"] > min(loud) + HARDEST_SLACK]
        weakest = min((d for d in rated if d["role"] in LOUD_ROLES), key=lambda d: d["stars"])
        checks.append({"id": 2, "name": "最难处在高潮", "ok": not over, "harder_than_chorus": over,
                       "weakest_chorus": weakest["bars"]})
    else:
        checks.append({"id": 2, "name": "最难处在高潮", "ok": None, "note": "设计稿里没有 chorus/drop 段"})
    # 3. Density follows the lead.
    if lead_by_bar:
        bad = [d["bars"] for d in sections if d.get("density_follows_lead", 1.0) < DENSITY_CORR_MIN]
        silent = [b for d in sections for b in d.get("notes_in_silent_bars", [])]
        checks.append({"id": 3, "name": "密度跟主跟层", "ok": not bad and not silent, "weak_sections": bad,
                       "notes_in_silent_bars": silent})
    else:
        checks.append({"id": 3, "name": "密度跟主跟层", "ok": None, "note": "没有给 --analysis"})
    # 4. Boundaries.
    jumps = []
    for s in brief.sections[1:]:
        a = spans.get(s.first_bar, (None, None))[0]
        if a is None:
            continue
        k = int(np.searchsorted(times, a - 1))
        if 0 < k < len(times) and times[k] - times[k - 1] <= beat_ms / 2 + 1:
            if abs(_centre(masks[k]) - _centre(masks[k - 1])) >= JUMP_COLUMNS:
                jumps.append(s.first_bar)
    checks.append({"id": 4, "name": "段落交界不别手", "ok": not jumps, "jumps_at_bars": jumps})
    # 5. Primary patterns and accents.
    weak = [d["bars"] for d in sections if d.get("primary_share", 1.0) < PRIMARY_MIN]
    strays = [d["bars"] for d in sections if d.get("accent_elsewhere", 0) > max(2, 0.05 * d["rows"])]
    checks.append({"id": 5, "name": "主键形七成、点缀在标记处", "ok": not weak and not strays,
                   "primary_below": weak, "accents_elsewhere": strays})
    # 6. Stars.
    if target is not None:
        checks.append({"id": 6, "name": "星级在目标内", "ok": abs(total.total_stars - target) <= STAR_TOLERANCE,
                       "stars": round(total.total_stars, 2), "target": target})
    else:
        checks.append({"id": 6, "name": "星级在目标内", "ok": None, "stars": round(total.total_stars, 2)})
    return {"stars": round(total.total_stars, 2), "dominant_skill": total.dominant_skill,
            "ln_share": round(sum(o.note_type == NoteType.LN for o in bm.hit_objects) / max(len(bm.hit_objects), 1), 3),
            "sections": sections, "checks": checks,
            "passed": all(c["ok"] is not False for c in checks)}


def _centre(mask: int) -> float:
    cols = [c for c in range(7) if mask >> c & 1]
    return float(np.mean(cols)) if cols else 3.0


def _repeats(times, masks, bar_of, spans, beat_ms) -> Dict[str, Any]:
    """Measures with the same rhythm (rows at the same places): how often their keys are the same or mirrored."""
    by_bar: Dict[int, List[Tuple[int, int]]] = collections.defaultdict(list)
    for t, m in zip(times, masks):
        b = bar_of[t]
        by_bar[b].append((int(round((t - spans[b][0]) / beat_ms * 48)), m))
    by_rhythm: Dict[tuple, List[int]] = collections.defaultdict(list)
    for b, rs in by_bar.items():
        if len(rs) >= 4:
            by_rhythm[tuple(p for p, _ in rs)].append(b)
    same = pairs = 0
    for bars in by_rhythm.values():
        for a, b in zip(bars, bars[1:]):
            ka = [m for _, m in by_bar[a]]
            kb = [m for _, m in by_bar[b]]
            pairs += 1
            same += _same_shape(ka, kb)
    return {"pairs": pairs, "same_keys": same, "rate": round(same / pairs, 3) if pairs else None}


def _same_shape(a: List[int], b: List[int]) -> bool:
    """The same keys, mirrored, or moved across the keys as a whole."""
    if a == b or [mirror(m) for m in a] == b:
        return True
    for d in range(-6, 7):
        if d and all((m << d if d > 0 else m >> -d) == n and (m << d if d > 0 else m >> -d) < 128
                     and bin(m).count("1") == bin(n).count("1") for m, n in zip(a, b)):
            return True
    return False


def markdown(r: Dict[str, Any]) -> str:
    lines = [f"# 审稿：{r['stars']}★，主技能 {r['dominant_skill']}，长条 {r['ln_share']:.0%}", "",
             "| 小节 | 功能 | 技法 / 键形 | 星级 | 每小节行数 | 主键形占比 | 长条 | 密度跟主跟层 |",
             "|---|---|---|---|---|---|---|---|"]
    for d in r["sections"]:
        lines.append(f"| {d['bars'][0]}–{d['bars'][1]} | {d['role'] or '-'} | {d['technique'] or '-'} / {d['pattern'] or '-'} "
                     f"| {d.get('stars', '-')} | {d['rows_per_bar']} | {d.get('primary_share', '-')} | {d['ln_share']:.0%} "
                     f"| {d.get('density_follows_lead', '-')} |")
    lines += ["", "## 六项检查", ""]
    for c in r["checks"]:
        mark = {True: "通过", False: "**不通过**", None: "未检查"}[c["ok"]]
        extra = {k: v for k, v in c.items() if k not in ("id", "name", "ok")}
        lines.append(f"{c['id']}. {c['name']}：{mark} {json.dumps(extra, ensure_ascii=False) if extra else ''}")
    return "\n".join(lines) + "\n"


def preview(bm: Beatmap7K, brief: brief_mod.Brief, path: Path, bars_each: int = 4) -> None:
    from PIL import Image, ImageDraw, ImageFont

    from proj7k.renderer import RenderOptions, render_slice
    from proj7k.window import extract_time_window

    spans = bar_times(bm)
    panels = []
    for s in brief.sections:
        if s.first_bar not in spans:
            continue
        a = spans[s.first_bar][0]
        last = min(s.last_bar, s.first_bar + bars_each - 1)
        e = spans.get(last, spans[s.first_bar])[1]
        img = render_slice(extract_time_window(bm, a - 10, e), RenderOptions(pixels_per_second=220.0, column_width=22,
                                                                            note_height=7, margin_left=34, margin_right=6))
        panels.append((f"{s.first_bar}-{s.last_bar} {s.role} {s.pattern or ''}", img))
    if not panels:
        return
    h = max(p.height for _, p in panels) + 18
    w = sum(p.width + 6 for _, p in panels)
    sheet = Image.new("RGB", (w, h), (10, 10, 14))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    x = 0
    for label, p in panels:
        sheet.paste(p, (x, h - p.height))
        draw.text((x + 2, 2), label[:28], fill=(230, 230, 230), font=font)
        x += p.width + 6
    sheet.save(path)


def main(argv: Optional[List[str]] = None) -> int:
    from proj7k.generator.cli import utf8_console

    utf8_console()
    p = argparse.ArgumentParser(prog="python3 -m proj7k.generator.review", description=__doc__.split("\n\n")[0])
    p.add_argument("--chart", required=True)
    p.add_argument("--brief", required=True)
    p.add_argument("--analysis")
    p.add_argument("--target", type=float)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    bm = parse_osu_7k(Path(args.chart).expanduser().read_text(encoding="utf-8", errors="replace"))
    b = brief_mod.load(Path(args.brief).expanduser())
    analysis = json.loads(Path(args.analysis).expanduser().read_text(encoding="utf-8")) if args.analysis else None
    r = review(bm, b, analysis, args.target)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    (out / "review.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "review.md").write_text(markdown(r), encoding="utf-8")
    preview(bm, b, out / "preview.png")
    print(markdown(r))
    return 0 if r["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
