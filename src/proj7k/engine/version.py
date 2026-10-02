"""
The engine's version token: what a stamped result says about the formulas that produced it.

It is derived, never bumped by hand (cf. ADR-0014): a digest of the syntax trees of every module in
this package (comments and layout do not move it), the constants in effect, and the anchor file. A
change that can move a result moves the token, so a consumer holding an older token knows its
values are stale.
"""

import ast
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from proj7k.engine.params import DEFAULT, Params
from proj7k.engine.scale import ANCHORS_PATH

_PACKAGE = Path(__file__).parent


def engine_version(params: Optional[Params] = None) -> str:
    """Eight hex digits."""
    h = hashlib.sha256()
    for path in sorted(_PACKAGE.glob("*.py")):
        h.update(path.name.encode())
        h.update(ast.dump(ast.parse(path.read_text(encoding="utf-8"))).encode())
    h.update(json.dumps(asdict(DEFAULT if params is None else params), sort_keys=True).encode())
    h.update(ANCHORS_PATH.read_bytes())
    return h.hexdigest()[:8]
