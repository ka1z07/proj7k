#!/usr/bin/env python3
"""
PROTOTYPE (throwaway) — held-out check of spec v0.2 on the labelled external charts in `practice_maps/`.

19 full-length charts named `[P-<tier> <skill>]` that are NOT among the 120 benchmark charts (8 88-note test fixtures
are skipped). They are whole songs (2.3k-10k notes against 1k-5k for the benchmark cuts), and their labels say which
dan slot a song belongs to, not that the chart is a pure example of the technique (the three `ln_inverse` ones are
12-16% LN with ~0.2 held columns; the benchmark LN Inverse pool is 77% LN with 2.6). Nothing here is tuned on them.

Reads: engine stars at the labelled tier (against the benchmark tier ladder of the same family), and whether the labelled
technique is the top-1 / top-2 / top-3 of the dominance ranking.

Usage: PYTHONPATH=src python3 prototypes/prototype_spec_v02_holdout.py
"""
import json
from pathlib import Path
import re
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prototype_spec_v01_engine as E  # noqa: E402

D = Path(__file__).resolve().parent
R = json.loads((D / "prototype_spec_v01_engine.results.json").read_text())
A, B = R["stars"]["a"], R["stars"]["b"]
T = E.TIERS
LN_POOLS = ["LN General", "LN Tech", "LN Inverse", "LN Release"]
LN_TIER = {t: float(np.exp(np.mean([np.log(R["charts"][p + " " + t]["total"]["stars"]) for p in LN_POOLS]))) for t in T}
RC_TIER = {t: R["stars"]["rc_tier_level"][t]["stars"] for t in T}
SK = {"jack": "rc_jack", "tech": "rc_tech", "speed": "rc_speed", "stream": "rc_stamina", "ln_general": "ln_general",
      "ln_tech": "ln_tech", "ln_inverse": "ln_inverse", "ln_release": "ln_release"}

rows = []
for f in sorted((D.parent / "practice_maps").glob("*.osu")):
    m = re.search(r"\[\[P-([^\s\]]+)\s+([a-z_]+)\]", f.name)
    if not m:
        continue
    notes = E.notes_of(f.read_text(encoding="utf-8", errors="ignore"))
    if len(notes) < 200:
        continue
    r = E.evaluate(notes)
    st = A * r["total"]["D"] ** B
    tier, skill = m.group(1), SK[m.group(2)]
    ref = LN_TIER if skill.startswith("ln") else RC_TIER
    rows.append(dict(name=f.name.split(" [[")[0][:40], tier=tier, skill=skill, notes=len(notes), stars=st, ladder=ref[tier],
                     reads_as=min(T, key=lambda t: abs(np.log(ref[t] / st))), dominant=r["dominant_skill"],
                     rank=r["dominance_rank"].index(skill) + 1))
for x in rows:
    print(f"{x['name']:40s} {x['tier'] + ' ' + x['skill']:22s} {x['notes']:5d} stars {x['stars']:5.2f} (ladder {x['ladder']:5.2f}) reads as {x['reads_as']:8s} dominant {x['dominant']:11s} label rank {x['rank']}")
err = [T.index(x["reads_as"]) - T.index(x["tier"]) for x in rows]
print(f"\ntier error: mean {np.mean(err):+.1f}, median {np.median(err):+.0f}; within 1 tier {sum(abs(e) <= 1 for e in err)}/{len(err)}, within 2 {sum(abs(e) <= 2 for e in err)}/{len(err)}")
print(f"label technique is top-1 {sum(x['rank'] == 1 for x in rows)}, top-2 {sum(x['rank'] <= 2 for x in rows)}, top-3 {sum(x['rank'] <= 3 for x in rows)} of {len(rows)}")
