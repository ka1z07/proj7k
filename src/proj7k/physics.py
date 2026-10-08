"""
Shared physical constants.

A threshold read by more than one module is defined here exactly once, so no module carries a
private literal that can silently drift from another's.
"""

# ---------------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------------
#: Tempo assumed for a chart that carries no uninherited timing point. Read by both the parser
#: (single BPM source) and the feature extractor, which previously carried the same literal in
#: two signatures.
DEFAULT_BPM: float = 150.0
