"""
Train the generator's pattern model (`proj7k/generator/pattern_model.json.gz`, ADR-0030) from the
online chart catalog: every ranked, loved and qualified 7K difficulty, as written and mirrored.

    PYTHONPATH=src python3 tools/train_pattern_model.py --db ~/.cache/proj7k/catalog.sqlite3

Sets are split by id: one in `--holdout-every` goes to a held-out set whose per-row log-likelihood is
printed (and stored in the model's meta), so a retrain can be compared with the last one.
"""

import argparse
import sqlite3
import sys
import zlib
from pathlib import Path

import numpy as np

from proj7k.generator.patterns import MODEL_PATH, PatternModel, rows_of_chart
from proj7k.parser import parse_osu_7k

STATUSES = ("ranked", "loved", "qualified")
QUERY_CATALOG = ("select b.beatmapset_id, b.osu_blob from beatmaps b join beatmapsets s on s.id = b.beatmapset_id "
                 "where s.status in ({})".format(",".join("?" * len(STATUSES))))
QUERY_EXPORT = "select set_id, osu_blob from charts where status in ({})".format(",".join("?" * len(STATUSES)))


def charts(db: Path):
    con = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    query = QUERY_CATALOG if "beatmaps" in tables else QUERY_EXPORT
    for set_id, blob in con.execute(query, STATUSES):
        try:
            bm = parse_osu_7k(zlib.decompress(blob).decode("utf-8", "replace"))
        except Exception:
            continue
        rows = rows_of_chart(bm.hit_objects)
        if len(rows) >= 50:
            yield int(set_id), rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=Path.home() / ".cache" / "proj7k" / "catalog.sqlite3")
    ap.add_argument("--output", type=Path, default=MODEL_PATH)
    ap.add_argument("--holdout-every", type=int, default=10)
    ap.add_argument("--min-context", type=int, default=4)
    args = ap.parse_args(argv)

    model = PatternModel()
    held = []
    n_train = 0
    for set_id, rows in charts(args.db):
        if set_id % args.holdout_every == 0:
            held.append(rows)
        else:
            model.add_chart(rows)
            n_train += 1
    model.prune(args.min_context)
    lp = sum(model.log_prob(r) for r in held)
    n_rows = sum(len(r) for r in held)
    per_row = lp / max(n_rows, 1)
    print(f"trained on {n_train} charts; held out {len(held)} charts / {n_rows} rows: "
          f"log-likelihood {per_row:.4f} per row (perplexity {np.exp(-per_row):.2f})")
    model.save(args.output, meta={"charts": n_train, "statuses": list(STATUSES),
                                  "holdout_charts": len(held), "holdout_ll_per_row": round(per_row, 4)})
    print(f"wrote {args.output} ({args.output.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
