"""
Pack the LoRA training job (ADR-0048) into one tar for another GPU machine: the exported dataset, the code it needs
(src/, tools/, requirements.txt from this checkout) and tools/lora_remote.sh at the top.

    PYTHONPATH=src python tools/pack_lora_job.py --dataset ~/.cache/proj7k/lora/dataset --out ~/lora-job.tar

On the other machine: tar -xf lora-job.tar -C /scratch0/$USER/ && bash /scratch0/$USER/lora-job/lora_remote.sh
The dataset's audio is hard-linked from lazer's files; the tar holds full copies (about 3.3 GB for 1,194 charts).
"""

import argparse
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = ("__pycache__", ".pytest_cache", "node_modules")


def keep(info: tarfile.TarInfo):
    return None if any(part in SKIP for part in Path(info.name).parts) else info


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dataset", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    dataset = Path(a.dataset).expanduser()
    if not (dataset / "metadata.parquet").is_file():
        raise SystemExit(f"{dataset} has no metadata.parquet: run tools/train_lora.py prepare first")
    out = Path(a.out).expanduser()
    with tarfile.open(out, "w") as tar:
        tar.add(ROOT / "tools" / "lora_remote.sh", "lora-job/lora_remote.sh")
        tar.add(ROOT / "requirements.txt", "lora-job/code/requirements.txt")
        for name in ("src", "tools"):
            tar.add(ROOT / name, f"lora-job/code/{name}", filter=keep)
        # dereference: the audio is hard-linked into the dataset; each file goes in whole
        tar.dereference = True
        tar.add(dataset, "lora-job/dataset", filter=keep)
    print(f"{out} ({out.stat().st_size / 2 ** 30:.2f} GiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
