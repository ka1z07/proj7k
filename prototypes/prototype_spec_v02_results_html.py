#!/usr/bin/env python3
"""PROTOTYPE (throwaway) — render prototype_spec_v01_engine.results.json (spec v0.2 defaults) as one local HTML file."""
import html, json
from pathlib import Path

D = Path(__file__).parent
r = json.loads((D / "prototype_spec_v01_engine.results.json").read_text())
man = json.loads((D.parent / "docs/research/structured_index.json").read_text())
ch = r["charts"]
T = ["0th", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th", "Gamma", "Azimuth", "Zenith", "Stellium"]
SH = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "Γ", "Az", "Ze", "St"]
POOLS = {"Regular Jack": "rc_jack", "Regular Tech": "rc_tech", "Regular Speed": "rc_speed", "Regular Stream": "rc_stamina",
         "LN General": "ln_general", "LN Tech": "ln_tech", "LN Inverse": "ln_inverse", "LN Release": "ln_release"}
SK = list(POOLS.values())


def tip(p, t):
    c = ch[f"{p} {t}"]
    sk = " | ".join(f"{k} {c['skills'][k]['stars']:.2f} (π {c['skills'][k]['dominance']:.2f}, cov {c['skills'][k]['coverage']:.2f})" for k in SK)
    return html.escape(f"{man[p][t]['song']}\nD {c['total']['D']:.2f}  stars {c['total']['stars']:.2f}  SR {man[p][t]['sr']:.2f}  thumb {c['thumb_hand']}\ndominant {c['dominant_skill']}\n{sk}")


def hue(v, lo=1.0, hi=14.0):
    x = min(1, max(0, (v - lo) / (hi - lo)))
    return f"hsl({220 - 200 * x:.0f} 60% {88 - 38 * x:.0f}%)"


def table(title, note, cell):
    rows = "".join(f"<tr><th>{p}</th>" + "".join(cell(p, k, i, t) for i, t in enumerate(T)) + "</tr>" for p, k in POOLS.items())
    return f"<h2>{title}</h2><p class=n>{note}</p><table><tr><th></th>" + "".join(f"<th>{s}</th>" for s in SH) + f"</tr>{rows}</table>"


def total(p, k, i, t):
    v = ch[f"{p} {t}"]["total"]["stars"]
    return f'<td style="background:{hue(v)}" title="{tip(p, t)}">{v:.2f}</td>'


def own(p, k, i, t):
    v = ch[f"{p} {t}"]["skills"][k]["stars"]
    pv = ch[f"{p} {T[i-1]}"]["skills"][k]["stars"] if i else -1
    bad = " inv" if v <= pv else ""
    return f'<td class="{bad}" style="background:{hue(v)}" title="{tip(p, t)}">{v:.2f}</td>'


def diff(p, k, i, t):
    d = ch[f"{p} {t}"]["total"]["stars"] - man[p][t]["sr"]
    a = min(1, abs(d) / 2.5)
    col = f"hsl({0 if d > 0 else 210} 70% {95 - 35 * a:.0f}%)"
    return f'<td style="background:{col}" title="{tip(p, t)}">{d:+.2f}</td>'


def sr(p, k, i, t):
    return f'<td style="background:{hue(man[p][t]["sr"])}">{man[p][t]["sr"]:.2f}</td>'


s, t1 = r["stars"], r["T1_summary"]
anch = "".join(f"<li>{c['tier']}: engine {c['stars']:.2f} vs {c.get('target', '≥ ' + str(c.get('min_stars')))}</li>" for c in s["anchor_checks"])
lnanch = "".join(f"<li>{c['tier']}: engine {c['stars']:.2f} vs {c['target']}</li>" for c in s["ln_anchor_checks"])
body = f"""<h1>Difficulty engine, spec v0.2 — 120 benchmark charts</h1>
<p class=n>Priors, no calibration, no held-out data. Hover a cell for the chart, D, thumb hand and the eight technique readings. Stars = a·D^b with a={s['a']:.3f}, b={s['b']:.3f} from the RC consensus anchors.</p>
<div class=g><div><b>T1</b> {t1['adjacent_inversions']} inversions of 112, τ {t1['mean_kendall_tau']}, strict ladders {t1['ladders_strict']}/8 (fails)</div>
<div><b>T3</b> mirror pass · <b>T4</b> {r['T4']['max_abs_dlnD']:.4f} pass · <b>T6</b> pass · <b>T10</b> {r['T10']['dominant']} pass</div>
<div><b>T5</b> max {r['T5']['random']['N_times_abs_dlnD_max']:.1f} / {r['T5']['adversarial']['N_times_abs_dlnD_max']:.1f} · <b>T7</b> exponent {r['T7']['exponent_median']}, {r['T7']['share_in_0_85_1_15']:.0%} in band</div>
<div><b>T9</b> all RC charts: rc_tech dominant; all LN charts: ln_tech dominant (unresolved)</div></div>
<div class=g><div><b>RC anchors</b><ul>{anch}</ul></div><div><b>LN anchors (not fitted)</b><ul>{lnanch}</ul>log rms {s['ln_rms_log_error']}</div></div>"""
body += table("Total stars", "The engine's total difficulty, one cell per chart.", total)
body += table("Technique stars on its own pool", "D_k of the slot's skill. Red outline = adjacent inversion (T1).", own)
body += table("Community SR (reference only)", "Never in the engine or any fit; shown for comparison.", sr)
body += table("Engine stars minus SR", "Red = engine harder than SR, blue = easier.", diff)
css = """:root{--bg:#fff;--fg:#1d1d1f;--mut:#6b6b70;--bd:#d8d8dc}@media(prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececee;--mut:#9a9aa0;--bd:#38383c}}
body{background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,system-ui,sans-serif;margin:0;padding:24px 16px;max-width:1100px;margin-inline:auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 2px}.n{color:var(--mut);margin:0 0 8px;font-size:13px}
.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:6px 24px;margin:12px 0}ul{margin:2px 0;padding-left:18px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:12px}th,td{border:1px solid var(--bd);padding:3px 2px;text-align:center}
th:first-child{text-align:left;padding-left:6px;white-space:nowrap}td{color:#111}td.inv{outline:2px solid #d33;outline-offset:-2px;font-weight:700}
@media(max-width:700px){table{font-size:10px}}"""
out = D / "prototype_spec_v02_results.html"
out.write_text(f'<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Engine v0.2 results</title><style>{css}</style><body>{body}</body></html>')
print(out)
