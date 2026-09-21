"""
Freeze the benchmark chart corpus into an in-repository fixture.

Resolves every chart listed in the benchmark manifest against a local osu!lazer
content-addressed store, and writes their raw `.osu` text keyed by BeatmapID to a gzipped
JSON corpus. The result is what `assets.load_corpus_fixture` reads, so engine validation
(`python3 -m proj7k.batch --corpus ...`) and the end-to-end guard tests run without a local
osu! installation.

Run this only when the benchmark manifest itself changes — the corpus is a frozen copy of a
fixed chart set, not a cache that should track the local library:

    PYTHONPATH=src python3 tools/export_benchmark_corpus.py \
        --manifest docs/research/structured_index.json \
        --library-dir "$HOME/Library/Application Support/osu/files" \
        --output tests/fixtures/benchmark_corpus.json.gz
"""

import argparse
import gzip
import json
from pathlib import Path
import sys
from typing import Dict, List, Optional

from proj7k.assets import scan_local_asset_library


def collect_corpus(manifest_path: Path, library_dir: Path) -> Dict[int, str]:
    """
    Resolves every manifest entry to its raw `.osu` text, keyed by BeatmapID.

    Raises RuntimeError listing any unresolved entries rather than writing a partial corpus —
    a corpus missing charts would silently weaken every gate that reads it.
    """
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    index = scan_local_asset_library(library_dir)

    corpus: Dict[int, str] = {}
    unresolved: List[str] = []

    for technique, tiers in manifest.items():
        for tier, meta in tiers.items():
            path = index.find_path(meta.get("id"), meta.get("song"))
            if path is None:
                unresolved.append(f"{technique} {tier}: {meta.get('song')} (id {meta.get('id')})")
                continue
            corpus[int(meta["id"])] = Path(path).read_text(encoding="utf-8", errors="replace")

    if unresolved:
        raise RuntimeError(
            f"{len(unresolved)} manifest entr(ies) could not be resolved in {library_dir}:\n"
            + "\n".join(f"  - {entry}" for entry in unresolved)
        )

    return corpus


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 tools/export_benchmark_corpus.py")
    parser.add_argument("--manifest", required=True, help="Benchmark manifest JSON path")
    parser.add_argument("--library-dir", required=True, help="Local osu! store to resolve charts from")
    parser.add_argument("--output", required=True, help="Destination .json.gz corpus path")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    corpus = collect_corpus(Path(args.manifest), Path(args.library_dir))

    payload = json.dumps({str(k): v for k, v in sorted(corpus.items())}, ensure_ascii=False)

    # mtime=0 keeps the gzip container byte-identical across runs, so re-freezing an unchanged
    # manifest produces no diff — the fixture is meant to be frozen, not churned.
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as f:
            f.write(payload.encode("utf-8"))

    print(f"Frozen {len(corpus)} chart(s) into {output} ({output.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
