"""
Freeze the labelled external practice charts into an in-repository fixture.

`practice_maps/` (git-ignored) holds whole songs the owner labelled `[P-<tier> <skill>]` with the dan
slot the song belongs to. They are not among the 120 benchmark charts and nothing is fitted on them: the
frozen copy exists only so `tests/engine/test_external_holdout.py` can check that the engine's total
stars generalise off the benchmark cuts, in CI as much as on a machine that has the folder.

Only the chart text is kept — no audio, no background. Run this only when the folder's labelled set
changes; the corpus is a frozen copy, not a cache:

    PYTHONPATH=src python3 tools/export_practice_corpus.py \
        --practice-dir practice_maps \
        --output tests/fixtures/practice_corpus.json.gz
"""

import argparse
import gzip
import json
from pathlib import Path
import re
import sys
from typing import Dict, List, Optional

from proj7k.engine.events import notes_from_osu

#: Charts below this are the 88-note sync/subprocess test fixtures that share the folder, not songs.
MIN_NOTES = 200

#: `[[P-<tier> <skill>]` — the leading `[` is the one that opens the difficulty name in `[Version]`.
LABEL = re.compile(r"\[\[P-([^\s\]]+)\s+([a-z_]+)\]")


def collect_practice_corpus(practice_dir: Path) -> Dict[str, Dict[str, str]]:
    """Every labelled full-length `.osu` in `practice_dir`, keyed by file name, as `{tier, skill, osu}`."""
    corpus: Dict[str, Dict[str, str]] = {}
    for path in sorted(Path(practice_dir).glob("*.osu")):
        label = LABEL.search(path.name)
        if label is None:
            continue
        osu = path.read_text(encoding="utf-8", errors="replace")
        if len(notes_from_osu(osu)) < MIN_NOTES:
            continue
        corpus[path.name] = {"tier": label.group(1), "skill": label.group(2), "osu": osu}
    return corpus


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 tools/export_practice_corpus.py")
    parser.add_argument("--practice-dir", required=True, help="Folder of labelled practice charts")
    parser.add_argument("--output", required=True, help="Destination .json.gz corpus path")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    corpus = collect_practice_corpus(Path(args.practice_dir))
    payload = json.dumps(corpus, ensure_ascii=False)

    # mtime=0 keeps the gzip container byte-identical across runs, so re-freezing an unchanged
    # folder produces no diff.
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as f:
            f.write(payload.encode("utf-8"))

    print(f"Frozen {len(corpus)} chart(s) into {output} ({output.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
