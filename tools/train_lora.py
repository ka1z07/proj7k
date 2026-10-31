"""
Fine-tune Mapperatorinator on 7K charts with a LoRA (ADR-0048), on the machine with the GPU.

    # 1. the dataset, from the osu!lazer library (see tools/export_lora_dataset.py)
    PYTHONPATH=src python tools/export_lora_dataset.py --out ~/.cache/proj7k/lora/dataset --hold-out MD5 ...
    # 2. metadata.parquet, written with the model's own Python (it has pandas)
    PYTHONPATH=src python tools/train_lora.py prepare --dataset ~/.cache/proj7k/lora/dataset
    # 3. a few steps to see it runs and how long a step takes, then the real run
    PYTHONPATH=src python tools/train_lora.py train --dataset ... --out ~/.cache/proj7k/lora/smoke --steps 4
    PYTHONPATH=src python tools/train_lora.py train --dataset ... --out ~/.cache/proj7k/lora/run1 --steps 1000
    # or on part of it: only the long-note charts (shares the audio and .osu files, new split and parquet)
    PYTHONPATH=src python tools/train_lora.py subset --dataset ... --out ~/.cache/proj7k/lora/dataset-ln --min-ln-share 0.5

The run uses the pinned checkout the generator installed (~/.cache/proj7k/mapperatorinator) and its `lora_v32`
config, pointed at the mania checkpoint (`gamemode=3`): the LoRA only loads on that checkpoint at inference. Each
checkpoint lands in OUT/run/checkpoints/checkpoint_<n>/lora; the generator takes one with
`--model-arg lora_path=<that folder>`.
"""

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

from proj7k.generator import mapperatorinator as m

#: Share of a subset's sets held out for evaluation, as tools/export_lora_dataset.py splits the whole library.
TEST_SHARE = 0.05

#: Written into the dataset with the model's Python: the jsonl rows as the parquet `MmrsDataset` reads.
TO_PARQUET = r"""
import json, sys
import pandas as pd
path = sys.argv[1]
rows = [json.loads(l) for l in open(path + "/metadata.jsonl", encoding="utf-8")]
df = pd.DataFrame(rows)
for col in ("SubmittedDate", "RankedDate"):
    df[col] = pd.to_datetime(df[col], errors="coerce").fillna(pd.Timestamp("2024-01-01"))
df = df.sort_values(["BeatmapSetId", "Id"]).reset_index(drop=True)
df.to_parquet(path + "/metadata.parquet")
print(len(df), "rows ->", path + "/metadata.parquet")
"""


def summary(dataset: Path) -> dict:
    return json.loads((dataset / "summary.json").read_text(encoding="utf-8"))


def prepare(args):
    dataset = Path(args.dataset).expanduser().resolve()
    subprocess.run([str(m.venv_python()), "-c", TO_PARQUET, dataset.as_posix()], check=True, env=m.child_env())


def subset(args):
    """The charts of DATASET with at least `min_ln_share` long notes and `min_stars` stars, as a dataset of their own
    in OUT: its data/ links to DATASET's, the sets split anew (TEST_SHARE of them held out), metadata.parquet
    written."""
    source = Path(args.dataset).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in (source / "metadata.jsonl").read_text(encoding="utf-8").splitlines()]
    kept = [r for r in rows if r["LnShare"] >= args.min_ln_share and r["DifficultyRating"] >= args.min_stars]
    if not kept:
        raise SystemExit("no chart left")
    link = out / "data"
    if not link.exists():
        os.symlink(os.path.relpath(source / "data", out), link, target_is_directory=True)
    with (out / "metadata.jsonl").open("w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    sets = sorted({r["BeatmapSetId"] for r in kept})
    n_test = max(1, round(len(sets) * TEST_SHARE)) if len(sets) > 1 else 0
    info = {"charts": len(kept), "sets": len(sets), "train_sets": len(sets) - n_test, "test_sets": n_test,
            "from": source.as_posix(), "min_ln_share": args.min_ln_share, "min_stars": args.min_stars}
    (out / "summary.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print(json.dumps(info))
    args.dataset = str(out)
    prepare(args)


def overrides(args) -> list:
    dataset = Path(args.dataset).expanduser().resolve().as_posix()
    out = Path(args.out).expanduser().resolve()
    s = summary(Path(dataset))
    train_sets, test_sets = s["train_sets"], s["test_sets"]
    every = max(1, min(args.steps, args.checkpoint_every))
    return [
        f"hydra.run.dir={m.quote(out.joinpath('run').as_posix())}",
        "pretrained_gamemode=3",
        "lora_metadata.ckpt_subfolders=['gamemode=3']",
        f"compile={'true' if args.compile else 'false'}",
        f"attn_implementation={args.attention}",
        "data.gamemodes=[3]",
        f"data.train_dataset_path={m.quote(dataset)}",
        f"data.test_dataset_path={m.quote(dataset)}",
        "data.train_dataset_start=0",
        f"data.train_dataset_end={train_sets}",
        f"data.test_dataset_start={train_sets}",
        f"data.test_dataset_end={train_sets + test_sets}",
        "data.dt_augment_prob=0",            # the dataset has a star rating at 1.0x only (the others are scaled)
        f"dataloader.num_workers={args.workers}",
        f"optim.batch_size={args.batch * args.grad_acc}",  # Mapperatorinator's batch_size is per optimizer step
        f"optim.grad_acc={args.grad_acc}",
        f"optim.total_steps={args.steps}",
        f"optim.base_lr={args.lr}",
        f"checkpoint.every_steps={every}",
        f"checkpoint.local_total_limit={args.steps // every + 2}",  # keep them all (the default keeps the last 2)
        f"eval.every_steps={every}",
        f"eval.steps={args.eval_steps}",
        f"logging.every_steps={math.gcd(every, 10)}",  # the train log has to land on every eval step
        "logging.mode=disabled",
        f"lora.r={args.rank}",
        f"lora.lora_alpha={2 * args.rank}",
    ] + list(args.extra)


def train(args):
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    cmd = [str(m.venv_python()), "osuT5/train.py", "-cn", "lora_v32", *overrides(args)]
    (out / "command.json").write_text(json.dumps(cmd, ensure_ascii=False, indent=1), encoding="utf-8")
    print(" ".join(cmd), flush=True)
    with (out / "train.log").open("a", encoding="utf-8") as log:
        proc = subprocess.run(cmd, cwd=m.repo_dir(), stdout=log, stderr=subprocess.STDOUT,
                              env=m.child_env(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", WANDB_MODE="disabled"))
    print(f"exit {proc.returncode}; log {out / 'train.log'}")
    return proc.returncode


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("prepare")
    a.add_argument("--dataset", required=True)
    b = sub.add_parser("subset")
    b.add_argument("--dataset", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--min-ln-share", type=float, default=0.5)
    b.add_argument("--min-stars", type=float, default=0.0, help="osu!'s official star rating")
    t = sub.add_parser("train")
    t.add_argument("--dataset", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--steps", type=int, default=1000)
    t.add_argument("--batch", type=int, default=4, help="sequences per step on the GPU")
    t.add_argument("--grad-acc", type=int, default=8, help="steps added up per optimizer step")
    t.add_argument("--lr", type=float, default=0.0004)
    t.add_argument("--rank", type=int, default=64)
    t.add_argument("--workers", type=int, default=2)
    t.add_argument("--checkpoint-every", type=int, default=250)
    t.add_argument("--eval-steps", type=int, default=50)
    t.add_argument("--attention", default="sdpa")
    t.add_argument("--compile", action="store_true", help="torch.compile (needs triton; off on Windows)")
    t.add_argument("--extra", nargs="*", default=[], metavar="KEY=VALUE", help="more Hydra overrides")
    args = p.parse_args(argv)
    if args.cmd in ("prepare", "subset"):
        (prepare if args.cmd == "prepare" else subset)(args)
        return 0
    return train(args)


if __name__ == "__main__":
    sys.exit(main())
