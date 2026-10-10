"""
Mappers and their writing style, read off the catalog.

A mapper is whoever wrote a difficulty: osu!'s owner of a guest difficulty, else the set's creator. Their
profile pools every 7K difficulty they wrote:

- what the engine says their charts are about: the mean dominance of each of the eight skills (`skill_mix`),
  where their difficulties sit on the dan ladder, and how the engine's stars compare with osu!'s (`delta`);
- how they write, independent of difficulty (`charts.pattern_features`): LN share, notes per row, jack share,
  density, column use and hand balance, BPM and length.

Style tags and "similar mappers" compare a mapper with every other mapper who has at least
`MIN_DIFFS_FOR_STYLE` difficulties: a tag fires when the mapper sits in the top (or bottom) fifth of them,
and similarity is the cosine of their standardised feature vectors.
"""

import json
import math
import statistics
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from proj7k.catalog.store import CatalogStore
from proj7k.dan import CANONICAL_DAN_TIERS
from proj7k.engine import SKILLS

MIN_DIFFS_FOR_STYLE = 3
SIMILAR = 6

SKILL_ZH = {
    "rc_jack": "叠键", "rc_tech": "技巧", "rc_speed": "速度", "rc_stamina": "耐力切",
    "ln_general": "LN 综合", "ln_tech": "LN 技巧", "ln_inverse": "反键", "ln_release": "放手",
}

#: Writing features compared across mappers: key, label, how it is shown.
FEATURES = (
    ("ln_ratio", "长条占比", "pct"),
    ("chord", "平均每行音符", "num"),
    ("jack", "叠键比例", "pct"),
    ("nps", "密度 (NPS)", "num"),
    ("mid", "中键占比", "pct"),
    ("hand", "左右手偏向", "signed"),
    ("bpm", "BPM 中位数", "int"),
    ("length", "长度中位数", "time"),
    ("engine", "引擎 SR 中位数", "num"),
    ("delta", "引擎 − 官方", "signed"),
)


def mapper_key(mapper_id: Optional[int], name: str) -> str:
    """The id in `/mappers/<key>`: the osu! user id, or `@name` when osu! has not told us one."""
    return str(mapper_id) if mapper_id else f"@{name}"


@dataclass
class Profile:
    key: str
    name: str
    user_id: Optional[int]
    diffs: int = 0
    sets: int = 0
    statuses: Dict[str, int] = field(default_factory=dict)
    years: Tuple[Optional[int], Optional[int]] = (None, None)
    engine: List[float] = field(default_factory=list)
    official: List[float] = field(default_factory=list)
    deltas: List[float] = field(default_factory=list)
    dan_hist: List[int] = field(default_factory=lambda: [0] * len(CANONICAL_DAN_TIERS))
    dominant: Dict[str, int] = field(default_factory=dict)
    skill_sum: List[float] = field(default_factory=lambda: [0.0] * len(SKILLS))
    columns_sum: List[float] = field(default_factory=lambda: [0.0] * 7)
    patterned: int = 0
    raw: Dict[str, List[float]] = field(default_factory=dict)
    features: Dict[str, float] = field(default_factory=dict)
    percentiles: Dict[str, float] = field(default_factory=dict)
    tags: List[Dict[str, str]] = field(default_factory=list)
    # Each skill's share against the average mapper's: speed dominates most 7K charts, so what sets a mapper
    # apart is the skill they lean on more than others do, not the skill that is largest.
    skill_lift: Dict[str, float] = field(default_factory=dict)
    signature: Optional[str] = None
    similar: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def skill_mix(self) -> Dict[str, float]:
        return {k: self.skill_sum[i] / self.diffs for i, k in enumerate(SKILLS)} if self.diffs else {}

    @property
    def top_skill(self) -> Optional[str]:
        mix = self.skill_mix
        return max(mix, key=mix.get) if mix else None

    def summary(self) -> Dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "user_id": self.user_id, "diffs": self.diffs, "sets": self.sets,
            "engine_median": self.features.get("engine"), "official_median": _median(self.official),
            "ln_ratio": self.features.get("ln_ratio"), "delta": self.features.get("delta"),
            "top_skill": self.signature, "tags": self.tags[:3], "statuses": self.statuses,
        }

    def detail(self) -> Dict[str, Any]:
        out = self.summary()
        out.update({
            "tags": self.tags, "years": list(self.years), "skill_mix": self.skill_mix, "dominant": self.dominant,
            "skill_lift": self.skill_lift, "largest_skill": self.top_skill,
            "dan_hist": dict(zip(CANONICAL_DAN_TIERS, self.dan_hist)),
            "columns": [c / self.patterned for c in self.columns_sum] if self.patterned else None,
            "features": self.features, "percentiles": self.percentiles, "similar": self.similar,
            "engine_range": _quantiles(self.engine), "official_range": _quantiles(self.official),
            "styled": self.diffs >= MIN_DIFFS_FOR_STYLE,
        })
        return out


def _median(xs: List[float]) -> Optional[float]:
    return statistics.median(xs) if xs else None


def _quantiles(xs: List[float]) -> Optional[List[float]]:
    if not xs:
        return None
    s = sorted(xs)
    pick = lambda q: s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]  # noqa: E731
    return [s[0], pick(0.25), pick(0.5), pick(0.75), s[-1]]


def build_profiles(store: CatalogStore) -> Dict[str, Profile]:
    rows = store.mapper_rows()
    profiles: Dict[str, Profile] = {}
    sets: Dict[str, set] = {}
    for r in rows:
        name = r["mapper_name"] or r["creator"]
        key = mapper_key(r["mapper_id"], name)
        p = profiles.get(key)
        if p is None:
            p = profiles[key] = Profile(key=key, name=name, user_id=r["mapper_id"])
            p.raw = {k: [] for k, _, _ in FEATURES}
        p.diffs += 1
        sets.setdefault(key, set()).add(r["beatmapset_id"])
        p.statuses[r["status"]] = p.statuses.get(r["status"], 0) + 1
        year = (r["ranked_date"] or r["submitted_date"] or "")[:4]
        if year.isdigit():
            y = int(year)
            lo, hi = p.years
            p.years = (y if lo is None else min(lo, y), y if hi is None else max(hi, y))
        p.engine.append(r["engine_sr"])
        p.raw["engine"].append(r["engine_sr"])
        if r["official_sr"] is not None:
            p.official.append(r["official_sr"])
            p.deltas.append(r["engine_sr"] - r["official_sr"])
            p.raw["delta"].append(r["engine_sr"] - r["official_sr"])
        p.dan_hist[r["dan_index"]] += 1
        p.dominant[r["dominant_skill"]] = p.dominant.get(r["dominant_skill"], 0) + 1
        skills = json.loads(r["skills_json"])
        for i, k in enumerate(SKILLS):
            p.skill_sum[i] += skills[k]["dominance"]
        p.raw["ln_ratio"].append(r["ln_count"] / r["note_count"] if r["note_count"] else 0.0)
        p.raw["bpm"].append(r["bpm"])
        p.raw["length"].append(r["length_s"])
        if r["pattern_json"]:
            pat = json.loads(r["pattern_json"])
            p.patterned += 1
            cols = pat["columns"]
            for i in range(7):
                p.columns_sum[i] += cols[i]
            p.raw["chord"].append(pat["chord"])
            p.raw["jack"].append(pat["jack"])
            p.raw["nps"].append(pat["nps"])
            p.raw["mid"].append(cols[3])
            p.raw["hand"].append(sum(cols[4:]) - sum(cols[:3]))
    for key, p in profiles.items():
        p.sets = len(sets[key])
        medians = ("bpm", "length", "engine")
        p.features = {k: (_median(v) if k in medians else statistics.fmean(v))
                      for k, v in p.raw.items() if v}
    _compare(profiles)
    return profiles


def _compare(profiles: Dict[str, Profile]) -> None:
    """Percentiles, tags and similar mappers, against the mappers with enough difficulties."""
    pool = [p for p in profiles.values() if p.diffs >= MIN_DIFFS_FOR_STYLE]
    keys = [k for k, _, _ in FEATURES]
    sorted_vals = {k: sorted(p.features[k] for p in pool if k in p.features) for k in keys}
    mean_mix = {k: statistics.fmean(p.skill_mix[k] for p in pool) for k in SKILLS} if pool else {}
    for p in profiles.values():
        mix = p.skill_mix
        if mean_mix and mix:
            p.skill_lift = {k: mix[k] / mean_mix[k] if mean_mix[k] > 0 else 1.0 for k in SKILLS}
            leaning = [k for k in SKILLS if mix[k] >= 0.08]
            p.signature = max(leaning, key=p.skill_lift.get) if leaning else p.top_skill
        for k in keys:
            vals = sorted_vals[k]
            if k in p.features and len(vals) >= 5:
                below = sum(1 for v in vals if v < p.features[k])
                p.percentiles[k] = below / len(vals)
        p.tags = _tags(p) if p.diffs >= MIN_DIFFS_FOR_STYLE else []

    # Similarity: standardised [skill mix, writing features] vectors, cosine.
    vec_keys = ["ln_ratio", "chord", "jack", "nps", "mid", "engine"]
    stats = {}
    for k in vec_keys:
        vals = sorted_vals[k]
        if len(vals) >= 2:
            stats[k] = (statistics.fmean(vals), statistics.pstdev(vals) or 1.0)
    mixes = [p.skill_mix for p in pool]
    skill_stats = {k: (statistics.fmean(m[k] for m in mixes), statistics.pstdev([m[k] for m in mixes]) or 1.0)
                   for k in SKILLS} if len(pool) >= 2 else {}

    def vector(p: Profile) -> Optional[List[float]]:
        if not stats or not skill_stats or any(k not in p.features for k in stats):
            return None
        mix = p.skill_mix
        return [(mix[k] - m) / s for k, (m, s) in skill_stats.items()] + \
               [(p.features[k] - m) / s for k, (m, s) in stats.items()]

    vectors = {p.key: v for p in pool if (v := vector(p)) is not None}
    for key, v in vectors.items():
        nv = math.sqrt(sum(x * x for x in v)) or 1.0
        scored = []
        for other, w in vectors.items():
            if other == key:
                continue
            nw = math.sqrt(sum(x * x for x in w)) or 1.0
            scored.append((sum(a * b for a, b in zip(v, w)) / (nv * nw), other))
        scored.sort(reverse=True)
        profiles[key].similar = [{"key": o, "name": profiles[o].name, "user_id": profiles[o].user_id,
                                  "similarity": round(sim, 3), "diffs": profiles[o].diffs}
                                 for sim, o in scored[:SIMILAR]]


def _tags(p: Profile) -> List[Dict[str, str]]:
    """Plain-language style tags, strongest first. Each says why."""
    pc, f = p.percentiles, p.features
    tags: List[Tuple[float, str, str]] = []

    def pct(x: float) -> str:
        return f"{100 * x:.0f}%"

    mix, sig = p.skill_mix, p.signature
    if sig and p.skill_lift.get(sig, 0) >= 1.3:
        lift = p.skill_lift[sig]
        tags.append((min(1.0, 0.4 + lift / 4), f"{SKILL_ZH[sig]}型",
                     f"{pct(mix[sig])} 的难度来自{SKILL_ZH[sig]}，是谱师平均的 {lift:.1f} 倍"))
    if "ln_ratio" in f:
        if f["ln_ratio"] >= 0.5:
            tags.append((f["ln_ratio"], "LN 为主", f"平均 {pct(f['ln_ratio'])} 的音符是长条"))
        elif f["ln_ratio"] <= 0.03:
            tags.append((0.6, "纯米", "几乎不写长条"))
    rules = (
        ("chord", "多押密集", "单点为主", "平均每行 {:.2f} 个音符"),
        ("jack", "叠键多", "少叠键", "叠键比例 {:.0%}"),
        ("nps", "高密度", "低密度", "平均 {:.1f} NPS"),
        ("mid", "重中键", None, "中键占 {:.0%}"),
        ("bpm", "高 BPM", "慢歌", "BPM 中位数 {:.0f}"),
        ("length", "长图", "短图", "长度中位数 {:.0f} 秒"),
    )
    for key, high, low, text in rules:
        if key not in pc:
            continue
        if pc[key] >= 0.8:
            tags.append((pc[key], high, f"{text.format(f[key])}，高于 {pct(pc[key])} 的谱师"))
        elif low and pc[key] <= 0.2:
            tags.append((1 - pc[key], low, f"{text.format(f[key])}，低于 {pct(1 - pc[key])} 的谱师"))
    if "hand" in f and abs(f["hand"]) >= 0.06:
        side = "右" if f["hand"] > 0 else "左"
        tags.append((0.5 + abs(f["hand"]), f"偏{side}手", f"{side}手多打 {pct(abs(f['hand']))} 的音符"))
    # The two scales differ (the engine's top tiers sit above osu!'s), so the gap is judged against other mappers.
    if len(p.deltas) >= MIN_DIFFS_FOR_STYLE and "delta" in pc:
        if pc["delta"] >= 0.8:
            tags.append((pc["delta"], "引擎判定更难", f"引擎星级比官方平均高 {f['delta']:+.2f}，差距大于 {pct(pc['delta'])} 的谱师"))
        elif pc["delta"] <= 0.2:
            tags.append((1 - pc["delta"], "引擎判定更易", f"引擎星级比官方平均 {f['delta']:+.2f}，差距小于 {pct(1 - pc['delta'])} 的谱师"))
    if p.diffs >= 5:
        q = _quantiles(p.engine)
        spread = q[3] - q[1]
        if spread <= 0.6:
            tags.append((0.55, "难度集中", f"一半的难度落在引擎 {q[1]:.1f}–{q[3]:.1f} 星之间"))
        elif spread >= 3.0:
            tags.append((0.55, "难度跨度大", f"中间一半的难度从 {q[1]:.1f} 星到 {q[3]:.1f} 星"))
    tags.sort(key=lambda t: -t[0])
    return [{"label": label, "why": why} for _, label, why in tags]


class MapperIndex:
    """Profiles built once and rebuilt when the catalog changes."""

    def __init__(self, store: CatalogStore):
        self.store = store
        self._lock = threading.Lock()
        self._token: Any = None
        self._profiles: Dict[str, Profile] = {}

    def profiles(self) -> Dict[str, Profile]:
        token = self.store.change_token()
        with self._lock:
            if token != self._token:
                self._profiles = build_profiles(self.store)
                self._token = token
            return self._profiles

    def listing(self, q: str = "", sort: str = "diffs_desc", min_diffs: int = 1, page: int = 1,
                page_size: int = 50) -> Dict[str, Any]:
        profiles = [p for p in self.profiles().values() if p.diffs >= min_diffs]
        if q:
            needle = q.casefold()
            profiles = [p for p in profiles if needle in p.name.casefold()]
        key, _, direction = sort.rpartition("_")
        desc = direction != "asc"
        getters = {
            "diffs": lambda p: p.diffs, "sets": lambda p: p.sets, "name": lambda p: p.name.casefold(),
            "engine": lambda p: p.features.get("engine"), "ln": lambda p: p.features.get("ln_ratio"),
            "delta": lambda p: p.features.get("delta"),
        }
        get = getters.get(key, getters["diffs"])
        have = [p for p in profiles if get(p) is not None]
        have.sort(key=lambda p: (get(p), -p.diffs if desc else p.diffs), reverse=desc)
        ordered = have + [p for p in profiles if get(p) is None]
        page_size = max(1, min(page_size, 200))
        pages = max(1, math.ceil(len(ordered) / page_size))
        page = max(1, min(page, pages))
        return {"total": len(ordered), "page": page, "pages": pages,
                "sort": f"{key if key in getters else 'diffs'}_{'desc' if desc else 'asc'}",
                "mappers": [p.summary() for p in ordered[(page - 1) * page_size: page * page_size]]}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        p = self.profiles().get(key)
        return p.detail() if p else None
