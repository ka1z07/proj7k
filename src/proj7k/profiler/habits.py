"""
Technique habits read from replays across plays (ADR-0029).

A replay holds, for each of the 7 keys, the millisecond it went down and the millisecond it came up, and nothing about
which finger pressed it. This module reads, per play, what that timing does show (when each column is struck relative
to the note, how long a key stays down, which key of a chord goes first, whether a same-hand key is still held when the
next one goes down, how jacks drift, how LNs are let go) and keeps it as sums and histograms, so plays merge by adding.
The profile across plays is read from the merged sums; one hand is told from the other by how closely the timing errors
of a chord's keys move together.

All times are in real (wall-clock) milliseconds: song-time offsets are divided by the play's clock rate.
"""

from dataclasses import dataclass, field
import math
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from proj7k.parser import NoteType
from proj7k.profiler.matcher import (
    AlignedHit,
    HitAlignmentResult,
    HitJudgment,
    _extract_column_key_presses,
    column_to_canonical_lane,
)
from proj7k.profiler.osr import ReplayFrame
from proj7k.profiler.pathology import CHORD_EPS_MS

#: Version of the per-play statistics below; a snapshot with another version is not merged.
HABITS_VERSION = 1

COLUMNS = 7
MIDDLE = 3
#: The keys each hand surely plays; the middle key is assigned by `infer_hands`.
LEFT_COLS = (0, 1, 2)
RIGHT_COLS = (4, 5, 6)

#: Histogram bin widths (ms) and the last bin (everything at or past it lands there).
HOLD_BIN_MS = 4
HOLD_CAP_MS = 400
DIFF_CAP_MS = 60

#: Same-column gap bands (ms) the rice hold time is read over.
HOLD_GAP_EDGES: Tuple[float, ...] = (0, 100, 150, 200, 300, 500, math.inf)
#: Same-hand gap bands (ms) the key overlap (rolling) is read over.
ROLL_GAP_EDGES: Tuple[float, ...] = (20, 40, 60, 80, 100, 120, 140, 160, 200)
#: Same-column gap bands (ms) jacks are read over.
JACK_GAP_EDGES: Tuple[float, ...] = (60, 90, 120, 160, 220)

#: LN releases further than this from the tail (ms) count as early / late.
LN_RELEASE_MARGIN_MS = 50.0

#: A column pair's timing correlation is read once it has this many chords.
MIN_PAIR_CHORDS = 30
#: How much closer (in mean timing correlation) the middle key must sit to one hand to be called that hand's.
MIDDLE_HAND_MARGIN = 0.03


def _band(edges: Sequence[float], x: float) -> Optional[int]:
    for i in range(len(edges) - 1):
        if edges[i] <= x < edges[i + 1]:
            return i
    return None


def _hand(col: int) -> Optional[str]:
    if col in LEFT_COLS:
        return "L"
    if col in RIGHT_COLS:
        return "R"
    return None


HOLD_BINS = HOLD_CAP_MS // HOLD_BIN_MS + 1
DIFF_BINS = DIFF_CAP_MS + 1


def _bump(hist: List[int], value: float, width: int) -> None:
    """Counts `value` (ms, >= 0) into `hist`, bin i spanning [i*width, (i+1)*width), the last bin open-ended."""
    hist[min(max(int(value // width), 0), len(hist) - 1)] += 1


def _merge_hist(into: List[int], other: Sequence[int]) -> None:
    for i, v in enumerate(other):
        into[i] += int(v)


def hist_quantile(hist: Sequence[int], q: float, width: int = 1) -> Optional[float]:
    """The `q` quantile of a histogram from `_bump`: the bin's lower edge for whole-ms bins, else its centre."""
    total = sum(hist)
    if not total:
        return None
    seen = 0
    for i, v in enumerate(hist):
        seen += v
        if v and seen >= q * total:
            return float(i) if width == 1 else (i + 0.5) * width
    return None


def _rounded(x: Any) -> Any:
    """`x` with its floats to 0.1 ms (sums of ms, and of ms squared), so a snapshot's summary stays small."""
    if isinstance(x, float):
        return round(x, 1)
    if isinstance(x, dict):
        return {k: _rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_rounded(v) for v in x]
    return x


def _new_pair() -> Dict[str, Any]:
    return {"n": 0, "sx": 0.0, "sy": 0.0, "sxx": 0.0, "syy": 0.0, "sxy": 0.0,
            "a_first": 0, "b_first": 0, "same": 0, "diff": [0] * DIFF_BINS}


def _new_column() -> Dict[str, Any]:
    return {"n": 0, "off_sum": 0.0, "off_sq": 0.0, "hold": [0] * HOLD_BINS,
            "ln_n": 0, "ln_sum": 0.0, "ln_sq": 0.0, "ln_early": 0, "ln_late": 0, "ghost": 0}


@dataclass
class HabitStats:
    """One play's habit statistics, or the sum of many plays' (`merge`)."""

    plays: int = 0
    columns: List[Dict[str, Any]] = field(default_factory=lambda: [_new_column() for _ in range(COLUMNS)])
    #: "a-b" (a < b) -> chord statistics of that column pair: offsets' sums for their correlation, who went first,
    #: and the histogram of |press b - press a| in whole ms (`DIFF_BINS`).
    pairs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Per `HOLD_GAP_EDGES` band: rice hold-time histogram (`HOLD_BINS`).
    hold_by_gap: List[List[int]] = field(default_factory=lambda: [[0] * HOLD_BINS for _ in range(len(HOLD_GAP_EDGES) - 1)])
    #: Per `ROLL_GAP_EDGES` band: same-hand successive notes, and how many had the earlier key still down.
    roll: List[Dict[str, int]] = field(default_factory=lambda: [{"n": 0, "overlap": 0} for _ in range(len(ROLL_GAP_EDGES) - 1)])
    #: Per `JACK_GAP_EDGES` band: jack pairs and the sums of the first and second note's offsets.
    jack: List[Dict[str, float]] = field(default_factory=lambda: [{"n": 0, "first": 0.0, "second": 0.0} for _ in range(len(JACK_GAP_EDGES) - 1)])

    def to_dict(self) -> Dict[str, Any]:
        return _rounded({"v": HABITS_VERSION, "plays": self.plays, "columns": self.columns, "pairs": self.pairs,
                         "hold_by_gap": self.hold_by_gap, "roll": self.roll, "jack": self.jack})

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Optional["HabitStats"]:
        if not isinstance(d, dict) or d.get("v") != HABITS_VERSION:
            return None
        return cls(plays=int(d.get("plays", 1)), columns=d["columns"], pairs=d["pairs"],
                   hold_by_gap=d["hold_by_gap"], roll=d["roll"], jack=d["jack"])

    def merge(self, other: "HabitStats") -> None:
        self.plays += other.plays
        for mine, theirs in zip(self.columns, other.columns):
            for k, v in theirs.items():
                if k == "hold":
                    _merge_hist(mine["hold"], v)
                else:
                    mine[k] += v
        for key, theirs in other.pairs.items():
            mine = self.pairs.setdefault(key, _new_pair())
            for k, v in theirs.items():
                if k == "diff":
                    _merge_hist(mine["diff"], v)
                else:
                    mine[k] += v
        for mine, theirs in zip(self.hold_by_gap, other.hold_by_gap):
            _merge_hist(mine, theirs)
        for group in ("roll", "jack"):
            for mine, theirs in zip(getattr(self, group), getattr(other, group)):
                for k, v in theirs.items():
                    mine[k] += v


def extract_play_habits(
    alignment: HitAlignmentResult,
    frames: List[ReplayFrame],
    clock_rate: float = 1.0,
) -> HabitStats:
    """
    One play's habit statistics. `alignment` and `frames` are in song time and in the key space the play was made in
    (a Mirror play's chart is already flipped), so the columns are the player's physical keys.
    """
    rate = clock_rate if clock_rate > 0 else 1.0
    stats = HabitStats(plays=1)

    releases: List[Dict[float, Optional[float]]] = []
    for c in range(COLUMNS):
        releases.append({k.press_time: k.release_time for k in _extract_column_key_presses(frames, c)})
    for c, n in alignment.ghost_taps_by_column.items():
        if 0 <= c < COLUMNS:
            stats.columns[c]["ghost"] += int(n)

    # The notes the player struck: (target, press, release or None, column, is_rice), all real ms.
    struck: List[Tuple[float, float, Optional[float], int, bool, AlignedHit]] = []
    for h in alignment.aligned_hits:
        if h.hit_time is None or h.judgment == HitJudgment.MISS or not (0 <= h.column < COLUMNS):
            continue
        rel = releases[h.column].get(h.hit_time)
        struck.append((h.target_time / rate, h.hit_time / rate, None if rel is None else rel / rate,
                       h.column, h.note_type != NoteType.LN, h))
    struck.sort(key=lambda s: (s[0], s[3]))

    # Per column: offsets, rice holds, LN releases.
    for target, press, rel, c, rice, h in struck:
        col = stats.columns[c]
        off = press - target
        col["n"] += 1
        col["off_sum"] += off
        col["off_sq"] += off * off
        if rice and rel is not None:
            _bump(col["hold"], rel - press, HOLD_BIN_MS)
        if not rice and h.end_time is not None and h.tail_release_time is not None:
            tail = (h.tail_release_time - h.end_time) / rate
            col["ln_n"] += 1
            col["ln_sum"] += tail
            col["ln_sq"] += tail * tail
            col["ln_early"] += tail < -LN_RELEASE_MARGIN_MS
            col["ln_late"] += tail > LN_RELEASE_MARGIN_MS

    # Same column: hold against the gap to the next note, and jacks.
    by_col: List[List[Tuple[float, float, Optional[float], int, bool, AlignedHit]]] = [[] for _ in range(COLUMNS)]
    for s in struck:
        by_col[s[3]].append(s)
    for seq in by_col:
        for a, b in zip(seq, seq[1:]):
            gap = b[0] - a[0]
            if a[4] and a[2] is not None:
                band = _band(HOLD_GAP_EDGES, gap)
                if band is not None:
                    _bump(stats.hold_by_gap[band], a[2] - a[1], HOLD_BIN_MS)
            band = _band(JACK_GAP_EDGES, gap)
            if band is not None and a[4]:
                j = stats.jack[band]
                j["n"] += 1
                j["first"] += a[1] - a[0]
                j["second"] += b[1] - b[0]

    # Chords: every pair of struck notes within one chord step.
    eps = CHORD_EPS_MS / rate
    i = 0
    while i < len(struck):
        k = i + 1
        while k < len(struck) and struck[k][0] - struck[i][0] <= eps:
            k += 1
        chord = sorted(struck[i:k], key=lambda s: s[3])
        for x in range(len(chord)):
            for y in range(x + 1, len(chord)):
                a, b = chord[x], chord[y]
                if a[3] == b[3]:
                    continue
                p = stats.pairs.setdefault(f"{a[3]}-{b[3]}", _new_pair())
                oa, ob = a[1] - a[0], b[1] - b[0]
                p["n"] += 1
                p["sx"] += oa
                p["sy"] += ob
                p["sxx"] += oa * oa
                p["syy"] += ob * ob
                p["sxy"] += oa * ob
                d = int(round(b[1] - a[1]))
                if d > 0:
                    p["a_first"] += 1
                elif d < 0:
                    p["b_first"] += 1
                else:
                    p["same"] += 1
                _bump(p["diff"], abs(d), 1)
        i = k

    # Rolling: successive notes of one hand on different keys, the earlier one a rice.
    for a, b in zip(struck, struck[1:]):
        ha, hb = _hand(a[3]), _hand(b[3])
        if ha is None or ha != hb or a[3] == b[3] or not a[4] or a[2] is None:
            continue
        band = _band(ROLL_GAP_EDGES, b[0] - a[0])
        if band is None:
            continue
        stats.roll[band]["n"] += 1
        stats.roll[band]["overlap"] += a[2] > b[1]
    return stats


# --- the profile across plays ----------------------------------------------------------------------------------------


def _pair_corr(p: Dict[str, Any]) -> Optional[float]:
    n = p["n"]
    if n < MIN_PAIR_CHORDS:
        return None
    vx = p["sxx"] / n - (p["sx"] / n) ** 2
    vy = p["syy"] / n - (p["sy"] / n) ** 2
    if vx <= 0 or vy <= 0:
        return None
    return (p["sxy"] / n - (p["sx"] / n) * (p["sy"] / n)) / math.sqrt(vx * vy)


def _mean(xs: Sequence[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _r(x: Optional[float], nd: int = 1) -> Optional[float]:
    return None if x is None else round(x, nd)


def infer_hands(stats: HabitStats) -> Dict[str, Any]:
    """
    Which hand plays the middle key. Keys moved by one hand err together, so in chords their timing errors correlate
    more than across hands; the middle key belongs to the hand it correlates (and lines up) with more. When the hands
    do not show in the timing at all, nothing is said about the middle key.
    """
    corr = {key: _pair_corr(p) for key, p in stats.pairs.items()}

    def c(a: int, b: int) -> Optional[float]:
        return corr.get(f"{min(a, b)}-{max(a, b)}")

    def med_diff(a: int, b: int) -> Optional[float]:
        p = stats.pairs.get(f"{min(a, b)}-{max(a, b)}")
        return hist_quantile(p["diff"], 0.5) if p and p["n"] >= MIN_PAIR_CHORDS else None

    within = _mean([c(a, b) for hand in (LEFT_COLS, RIGHT_COLS) for i, a in enumerate(hand) for b in hand[i + 1:]])
    cross = _mean([c(a, b) for a in LEFT_COLS for b in RIGHT_COLS])
    mid_l = _mean([c(MIDDLE, a) for a in LEFT_COLS])
    mid_r = _mean([c(MIDDLE, a) for a in RIGHT_COLS])
    diff_l = _mean([med_diff(MIDDLE, a) for a in LEFT_COLS])
    diff_r = _mean([med_diff(MIDDLE, a) for a in RIGHT_COLS])

    separable = within is not None and cross is not None and within > cross + MIDDLE_HAND_MARGIN
    middle = None
    if separable and mid_l is not None and mid_r is not None:
        if mid_r - mid_l >= MIDDLE_HAND_MARGIN and (diff_l is None or diff_r is None or diff_r <= diff_l):
            middle = "R"
        elif mid_l - mid_r >= MIDDLE_HAND_MARGIN and (diff_l is None or diff_r is None or diff_l <= diff_r):
            middle = "L"
    return {
        "middle_hand": middle,
        "within_hand_corr": _r(within, 3), "cross_hand_corr": _r(cross, 3),
        "middle_left_corr": _r(mid_l, 3), "middle_right_corr": _r(mid_r, 3),
        "middle_left_diff_ms": _r(diff_l), "middle_right_diff_ms": _r(diff_r),
        #: Whether the two hands show up in the timing at all (keys of one hand err together more than across).
        "hands_separable": separable,
    }


@dataclass
class HabitProfile:
    player_name: str
    window_mode: str
    horizon_days: Optional[float]
    plays: int
    #: Snapshots in the window written before habits were kept; "导入回放" re-reads them.
    stale_plays: int
    stats: HabitStats

    def to_dict(self) -> Dict[str, Any]:
        s = self.stats
        hands = infer_hands(s)
        lanes = [column_to_canonical_lane(c) for c in range(COLUMNS)]

        columns = []
        for c, col in enumerate(s.columns):
            n, ln = col["n"], col["ln_n"]
            mean = col["off_sum"] / n if n else None
            sd = math.sqrt(max(0.0, col["off_sq"] / n - mean * mean)) if n else None
            columns.append({
                "column": c, "lane": lanes[c], "hits": n,
                "mean_offset_ms": _r(mean), "sd_offset_ms": _r(sd),
                "hold_median_ms": _r(hist_quantile(col["hold"], 0.5, HOLD_BIN_MS), 0),
                "hold_p10_ms": _r(hist_quantile(col["hold"], 0.1, HOLD_BIN_MS), 0),
                "hold_p90_ms": _r(hist_quantile(col["hold"], 0.9, HOLD_BIN_MS), 0),
                "ln_tails": ln, "ln_tail_mean_ms": _r(col["ln_sum"] / ln) if ln else None,
                "ln_early_rate": _r(col["ln_early"] / ln, 4) if ln else None,
                "ln_late_rate": _r(col["ln_late"] / ln, 4) if ln else None,
                "ghost_taps": col["ghost"],
            })

        pairs = []
        for a in range(COLUMNS):
            for b in range(a + 1, COLUMNS):
                p = s.pairs.get(f"{a}-{b}")
                if not p or p["n"] < MIN_PAIR_CHORDS:
                    continue
                n = p["n"]
                pairs.append({
                    "a": a, "b": b, "lanes": f"{lanes[a]}+{lanes[b]}", "chords": n,
                    "corr": _r(_pair_corr(p), 3), "median_diff_ms": hist_quantile(p["diff"], 0.5),
                    "a_first_rate": _r(p["a_first"] / n, 3), "b_first_rate": _r(p["b_first"] / n, 3),
                    "same_ms_rate": _r(p["same"] / n, 3),
                })

        # Who leads a hand's chords: per key, how often it went down first among that hand's chord pairs.
        middle = hands["middle_hand"]
        hand_cols = {"L": list(LEFT_COLS) + ([MIDDLE] if middle == "L" else []),
                     "R": ([MIDDLE] if middle == "R" else []) + list(RIGHT_COLS)}
        hand_lead: Dict[str, List[Dict[str, Any]]] = {}
        for hand, cols in hand_cols.items():
            rows = []
            for c in sorted(cols):
                first = total = 0
                for o in cols:
                    if o == c:
                        continue
                    p = s.pairs.get(f"{min(c, o)}-{max(c, o)}")
                    if not p:
                        continue
                    first += p["a_first"] if c < o else p["b_first"]
                    total += p["n"]
                rows.append({"column": c, "lane": lanes[c], "first_rate": _r(first / total, 3) if total else None,
                             "chords": total})
            hand_lead[hand] = rows

        hold_by_gap = []
        for i, hist in enumerate(s.hold_by_gap):
            lo, hi = HOLD_GAP_EDGES[i], HOLD_GAP_EDGES[i + 1]
            hold_by_gap.append({"lo": lo, "hi": None if math.isinf(hi) else hi, "notes": sum(hist),
                                "hold_median_ms": _r(hist_quantile(hist, 0.5, HOLD_BIN_MS), 0)})

        roll = []
        for i, b in enumerate(s.roll):
            roll.append({"lo": ROLL_GAP_EDGES[i], "hi": ROLL_GAP_EDGES[i + 1], "pairs": b["n"],
                         "overlap_rate": _r(b["overlap"] / b["n"], 3) if b["n"] else None})

        jack = []
        for i, b in enumerate(s.jack):
            n = b["n"]
            jack.append({"lo": JACK_GAP_EDGES[i], "hi": JACK_GAP_EDGES[i + 1], "pairs": int(n),
                         "first_offset_ms": _r(b["first"] / n) if n else None,
                         "second_offset_ms": _r(b["second"] / n) if n else None})

        return {
            "player_name": self.player_name, "window_mode": self.window_mode, "horizon_days": self.horizon_days,
            "plays": self.plays, "stale_plays": self.stale_plays, "notes": sum(c["hits"] for c in columns),
            "hands": hands, "hand_lead": hand_lead, "columns": columns, "pairs": pairs,
            "hold_by_gap": hold_by_gap, "roll": roll, "roll_threshold_ms": _r(roll_threshold(roll), 0), "jack": jack,
        }


#: A band is read for the rolling threshold once it has this many note pairs.
MIN_ROLL_PAIRS = 50


def roll_threshold(roll: List[Dict[str, Any]]) -> Optional[float]:
    """The same-hand gap (ms) at which the earlier key stops being held into the next one half the time."""
    pts = [((b["lo"] + b["hi"]) / 2.0, b["overlap_rate"]) for b in roll
           if b["pairs"] >= MIN_ROLL_PAIRS and b["overlap_rate"] is not None]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if y0 >= 0.5 > y1:
            return x0 + (y0 - 0.5) * (x1 - x0) / (y0 - y1)
    return None


def aggregate_habits(
    storage: Any,
    player_name: str,
    horizon_days: Optional[float] = None,
    reference_timestamp: Optional[float] = None,
) -> HabitProfile:
    """Merges the habit statistics of a player's stored plays (within `horizon_days` of the reference, or all)."""
    snapshots = storage.get_snapshots(player_name)
    if horizon_days is not None:
        ref = reference_timestamp if reference_timestamp is not None else time.time()
        snapshots = [s for s in snapshots if ref - horizon_days * 86400.0 <= s.timestamp <= ref]
    total = HabitStats()
    stale = 0
    for snap in snapshots:
        stats = HabitStats.from_dict((snap.summary or {}).get("habits"))
        if stats is None:
            stale += 1
            continue
        total.merge(stats)
    return HabitProfile(
        player_name=player_name,
        window_mode="all_time" if horizon_days is None else "rolling",
        horizon_days=horizon_days,
        plays=total.plays,
        stale_plays=stale,
        stats=total,
    )


def format_habit_profile(profile: Dict[str, Any]) -> str:
    """A plain-text reading of `HabitProfile.to_dict()` for the command line."""
    lines = [f"Habits of {profile['player_name']}: {profile['plays']} plays, {profile['notes']} notes"]
    if profile["stale_plays"]:
        lines.append(f"  ({profile['stale_plays']} older snapshots carry no habit data; re-run --import-replays)")
    h = profile["hands"]
    who = {"L": "left hand", "R": "right hand", None: "undetermined"}[h["middle_hand"]]
    lines.append(f"Middle key: {who} (timing corr with L {h['middle_left_corr']} / R {h['middle_right_corr']}; "
                 f"median chord gap L {h['middle_left_diff_ms']} / R {h['middle_right_diff_ms']} ms)")
    lines.append("Column  offset    sd  hold(p10-p90)  LN tail  early  late  ghost")
    for c in profile["columns"]:
        lines.append(
            f"  {c['lane']:4s} {c['mean_offset_ms'] if c['mean_offset_ms'] is not None else '-':>7} "
            f"{c['sd_offset_ms'] if c['sd_offset_ms'] is not None else '-':>5} "
            f"{c['hold_median_ms']}({c['hold_p10_ms']}-{c['hold_p90_ms']}) "
            f"{c['ln_tail_mean_ms'] if c['ln_tail_mean_ms'] is not None else '-':>8} "
            f"{c['ln_early_rate'] if c['ln_early_rate'] is not None else '-':>6} "
            f"{c['ln_late_rate'] if c['ln_late_rate'] is not None else '-':>5} {c['ghost_taps']:>6}")
    for hand, rows in profile["hand_lead"].items():
        lines.append(f"{hand} hand chord lead: " + ", ".join(
            f"{r['lane']} {r['first_rate']}" for r in rows if r["first_rate"] is not None))
    lines.append("Rolling (same hand, earlier key still down): " + ", ".join(
        f"{r['lo']}-{r['hi']}ms {r['overlap_rate']}" for r in profile["roll"] if r["overlap_rate"] is not None))
    lines.append(f"Rolling threshold: {profile['roll_threshold_ms']} ms")
    lines.append("Jacks (first / second offset): " + ", ".join(
        f"{j['lo']}-{j['hi']}ms {j['first_offset_ms']}/{j['second_offset_ms']}" for j in profile["jack"] if j["pairs"]))
    return "\n".join(lines)
