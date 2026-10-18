"""
Export training data for the generator's rhythm model (ADR-0032) from charts with their audio.

For every 7K chart in osu!lazer's library (or an osu!stable Songs folder) that people wrote (charts by
proj7k, generated or practice versions, are skipped), the places of its timing are read against the
audio (`generator.rhythm.features`) and labelled with the chart's rows. A third of the measures of each
chart is kept (which third depends on the chart), so the file stays small without biasing the classes.
One song's difficulties share the decoded audio.

    PYTHONPATH=src python3 tools/export_rhythm_data.py --lazer --output ~/.cache/proj7k/rhythm-data.npz

The output holds X [n, features] float16, y [n] int8, chart [n] int32 (index into `names`), and per chart
its name, audio file name, density and timing source.
"""

import argparse
import collections
import hashlib
import sys
import time
from pathlib import Path
from typing import List, Optional

import numpy as np

from proj7k.generator import rhythm as R
from proj7k.generator.audio import load_audio
from proj7k.generator.evaluate import lazer_charts, songs_folder_charts
from proj7k.generator.grid import onset_shift_ms
from proj7k.generator.onset import onset_envelope
from proj7k.parser import parse_osu_7k

KEEP_BARS = 3


def is_people_chart(chart) -> bool:
    creator = (chart.creator or "").lower()
    version = chart.version or ""
    return creator != "proj7k" and not version.startswith("[P-") and not version.startswith("Gen ")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--songs", help="osu!stable Songs folder")
    src.add_argument("--lazer", action="store_true", help="osu!lazer library (read-only, via the Realm bridge)")
    p.add_argument("--realm", help="client.realm path")
    p.add_argument("--limit", type=int, default=100000, help="at most this many charts")
    p.add_argument("--output", required=True)
    args = p.parse_args(argv)

    charts = songs_folder_charts(Path(args.songs).expanduser()) if args.songs else \
        lazer_charts(Path(args.realm).expanduser() if args.realm else None)
    Xs, ys, cs = [], [], []
    names, audios, dens, missed = [], [], [], []
    env_cache = {}
    t0 = time.time()
    for name, osu, audio in charts:
        if len(names) >= args.limit:
            break
        try:
            chart = parse_osu_7k(osu.read_text(encoding="utf-8", errors="replace"))
            if chart.circle_size != 7 or not is_people_chart(chart):
                continue
            timing = [tp for tp in chart.timing_points if tp.uninherited]
            rows = collections.Counter(int(round(o.time)) for o in chart.hit_objects)
            if len(rows) < 50 or not timing:
                continue
            key = str(audio)
            if key not in env_cache:
                env_cache.clear()
                env_cache[key] = onset_envelope(load_audio(audio))
            env = env_cache[key]
            pl = R.places(timing, env.duration_s * 1000.0)
            rt = sorted(rows)
            y, miss = R.labels(pl, rt, [rows[t] for t in rt])
            d = R.chart_density(rt)
            X = R.features(env, pl, onset_shift_ms(env, timing), d)
            h = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16)
            keep = (pl.bar % KEEP_BARS) == (h % KEEP_BARS)
        except Exception as e:  # one bad chart must not stop a long export
            print(f"skip {name[:60]}: {str(e)[:120]}", flush=True)
            continue
        i = len(names)
        Xs.append(X[keep].astype(np.float16))
        ys.append(y[keep])
        cs.append(np.full(int(keep.sum()), i, dtype=np.int32))
        names.append(name)
        audios.append(Path(audio).name)
        dens.append(d)
        missed.append(miss / max(len(rt), 1))
        print(f"[{i + 1:4d}] {name[:60]:60s} rows {len(rt):5d} dens {d:5.2f} unmatched {missed[-1]:.2f} "
              f"({time.time() - t0:.0f}s)", flush=True)
    if not names:
        print("no charts", file=sys.stderr)
        return 1
    np.savez_compressed(args.output, X=np.concatenate(Xs), y=np.concatenate(ys), chart=np.concatenate(cs),
                        names=np.array(names), audios=np.array(audios), density=np.array(dens),
                        unmatched=np.array(missed), n_features=R.n_features())
    print(f"wrote {args.output}: {len(names)} charts, {sum(len(y) for y in ys)} places")
    return 0


if __name__ == "__main__":
    sys.exit(main())
