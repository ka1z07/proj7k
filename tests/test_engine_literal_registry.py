"""
The registration guard for numeric literals on the star-rating path.

**Why this exists.** `DifficultyOptions.engine_fingerprint` is supposed to cover every constant
that can move a star rating, because a constant it misses moves ratings *without* moving the
engine version — and the version is what marks already-injected beatmaps for re-evaluation
(ADR-0014). Before issue #48 the coverage was a hand-kept list of option objects plus five
physics constants, and roughly forty calibration literals lived inside the radar and feature
operators' function bodies, outside all of it. Changing one moved star ratings (10 of the 120
benchmark charts, up to 0.06★ on the switch that was measured) while `current_engine_version()`
stayed exactly the same.

**What closes it.** Every one of those literals is now a field of an option object or a named
constant of a calibration block, so the fingerprint sees it — `test_calibration` proves that
field by field. What is left in the function bodies is the numbers that are *not* calibration:
algebraic identities, indices and counts, unit conversions, sentinels and epsilons. The guard
below is what keeps it that way. It reads each module's syntax tree and requires the numeric
literals left in its function bodies to match this registry exactly, in value and in count.

The registry is a whitelist, not a baseline. A literal added, removed or retuned anywhere in
these modules fails this test until the registry is edited to match — and the edit is the
review: an entry that says *why* the number is not calibration, next to the count the tree is
expected to carry. Adding a calibration constant therefore has exactly two routes, both
explicit: give it a home in an option object or a calibration block (and it joins the
fingerprint), or write down here why it does not need one.
"""

import ast
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from proj7k import physics, scaling, strain
from proj7k.features import FeatureOptions
from proj7k.radar import RadarOptions
from proj7k.rating import RatingOptions
from proj7k.strain import StrainOptions


REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_SRC = REPO_ROOT / "src" / "proj7k"

#: The modules whose arithmetic decides a chart's star rating, and which are therefore held to
#: the registry. `parser` and `window` are here because they shape the note stream and the beat
#: grid the operators read; changing either moves ratings on every chart.
RATING_PATH_MODULES: Tuple[str, ...] = (
    "calibration.py",
    "features.py",
    "parser.py",
    "radar.py",
    "rating.py",
    "scaling.py",
    "strain.py",
    "window.py",
)

#: The calibration blocks whose constant lists the fingerprint reads. Same modules as above by
#: design: a block that is not also scanned for literals would have an unwatched half.
CALIBRATION_BLOCKS = (physics, scaling, strain)

#: The constants each scanned module declares for the fingerprint. Everything here is checked
#: against the module's source: an entry the module no longer defines is stale, and a module
#: constant that is not listed here and not a registered literal is an unregistered one.
RATING_PATH_CONSTANTS: Dict[str, Tuple[str, ...]] = {
    module.__name__.removeprefix("proj7k.") + ".py": tuple(module.CALIBRATION_CONSTANTS)
    for module in CALIBRATION_BLOCKS
}

#: The option objects the fingerprint folds in whole. Each one's fields must be read by an
#: operator, not merely enumerated into the hash.
OPTION_CLASSES = (RatingOptions, RadarOptions, FeatureOptions, StrainOptions)


@dataclass(frozen=True)
class LiteralRule:
    """
    What a function's remaining numeric literals are allowed to be.

    `allowed` maps each literal value to how many times the function body carries it. The count
    is what gives the guard teeth: a second `0.5` slipped into a function that already has one
    changes the count, so it cannot hide behind a value that was already reviewed.

    `reason` is read by the next person who has to decide whether an entry still belongs.
    """
    allowed: Dict[float, int]
    reason: str
    notes: Dict[float, str] = field(default_factory=dict)


# --- calibration.py -------------------------------------------------------------------------
# The fingerprint digest itself carries only its truncation width, and the anchor law only the
# zero it returns below.
_CALIBRATION = [
    ("compute_methodology_fingerprint", {8: 1}, "Hash digest length: 8 hex characters.", {}),
    ("star_rating_from_strain", {0.0: 3}, "Zero floor of the anchor law and the empty-input guard.", {}),
]

# --- rating.py -------------------------------------------------------------------------------
_RATING = [
    (
        "aggregate_p_norm",
        {0.0: 3, 1.0: 2, 0.5: 1, 1e-09: 1},
        "Zero floors, the p-root exponent (1/p) and the damping half-power; the epsilon is a "
        "zero-maximum guard.",
        {},
    ),
    (
        "apply_tanh_soft_cap",
        {0.0: 1},
        "Zero floor of the uncompressed branch.",
        {},
    ),
    (
        "synthesize_star_rating",
        {4: 5, 0.0: 5, 1e-09: 1},
        "Round to the engine's reported precision (4 decimals) and zero/epsilon guards.",
        {4: "Star ratings are reported at 4 decimals; rounding to fewer would move them, to more would not."},
    ),
]

# --- scaling.py ------------------------------------------------------------------------------
_SCALING = [
    ("apply_inverse_bpm_scaling", {4: 1}, "Reported-precision rounding of the calibrated metric.", {}),
    (
        "compute_action_window",
        {0: 1, 4: 1, 60000.0: 1},
        "Milliseconds per minute; the divisor is the `DEFAULT_DIVISOR` default argument.",
        {},
    ),
    (
        "normalize_notation_bpm",
        {0.0: 3},
        "Non-positive tempo, note-value and divisor guards: any of the three unusable leaves the "
        "annotated number alone rather than dividing by it.",
        {},
    ),
    (
        "compute_inverse_scaling_factor",
        {0.0: 4, 1.0: 1, 7.0: 2, 4: 3},
        "Zero floors and the empty-tempo guard; the identity offset of the lock amplification, "
        "which clamps and normalises over the 7 keys; and reported-precision rounding.",
        {7.0: "The keyboard has 7 lanes, so a fully locked chart is 7/7."},
    ),
    (
        "compute_inverse_score",
        {4: 1, 0.0: 2, 1.0: 1},
        "Zero floors, the identity offset of the NPS factor, and reported-precision rounding.",
        {},
    ),
]

# --- features.py -----------------------------------------------------------------------------
_FEATURES = [
    ("get_dominant_bpm", {2: 1}, "Display rounding to 2 decimals of an otherwise unrounded tempo.", {}),
    ("_calc_rate", {0: 2, 4: 1}, "Zero-duration guard and reported-precision rounding.", {}),
    (
        "extract_beatmap_features",
        {0: 74, 1: 45, 2: 10, 4: 19, 5: 5, 6: 4, 7: 6, 8: 2, 3.0: 1, 100.0: 2, 1000.0: 3, 60000.0: 2},
        "Indices, counts and 7-lane topology throughout — including the left/right hand lane "
        "tuples the release-articulation terms read, whose members are lane numbers, not "
        "thresholds; ms/s and bpm/beat-length conversions; the percentage scaling of hold_pct; "
        "the two-class guards; the counts of isolated LN tails and of same-hand locked keys; and "
        "the 3-way binary/ternary/irregular mixing entropy, whose class count is the literal 3.",
        {
            4: "Reported precision (4 decimals); the non-overlapping-interval bookkeeping; the "
               "isolated-tail count's reported share; and the 4-measure window of the peak_4m_nps "
               "diagnostic, which that field's name pins — it is a diagnostic the star rating "
               "never reads.",
            5: "The 4-measure window needs 5 measure starts to form one window, plus the "
               "reported precision.",
            100.0: "hold_pct is a percentage, read back as a ratio by the operators.",
        },
    ),
]

# --- parser.py --------------------------------------------------------------------------------
_PARSER = [
    ("dominant_bpm", {0: 1}, "Empty-timing-point guard.", {}),
    ("notation_normalized_bpm", {0.0: 1}, "Non-positive tempo-override guard.", {}),
    (
        "observed_note_value",
        {0.0: 1, 1: 1, 2: 1},
        "Non-positive beat-length guard; an interval needs two onsets to exist, so the second "
        "one is index 1 and the minimum length is 2.",
        {},
    ),
    ("uninherited_timing_points", {0: 1}, "Zero-time floor when filtering timing points.", {}),
    ("bpm", {0: 1, 60000.0: 1}, "Milliseconds per minute and the non-positive beat-length guard.", {}),
    (
        "dominant_timing_point",
        {0: 3, 1: 4, 10000.0: 1},
        "List indices and offsets over the timing-point sequence; the 10 s span assumes the last "
        "timing point runs to the end of the chart.",
        {},
    ),
    ("_format_num", {1e-09: 1}, "Integrality epsilon when deciding how to print a time value.", {}),
    (
        "parse_osu_7k",
        {0: 8, 1: 9, 2: 4, 3: 4, 4: 5, 5: 5, 6: 4, 7: 5, 100: 1, 128: 1, 512.0: 1},
        "osu! file-format grammar: field indices, the 7K column range, and the 512-px playfield "
        "with its 128-px centre offset.",
        {512.0: "Playfield width in osu! pixels — the column mapping is a format fact, not a knob."},
    ),
    (
        "dump_osu_7k",
        {0: 2, 1: 2, 128: 1, 192: 1, 7.0: 1, 256.0: 1, 512.0: 1},
        "The inverse of the same format grammar: column to x-position and the default hit-object "
        "fields the game round-trips.",
        {},
    ),
]

# --- radar.py ---------------------------------------------------------------------------------
_RADAR = [
    (
        "_partition_chord_steps",
        {0: 2, 1: 1},
        "List indices into the sorted hit objects.",
        {},
    ),
    (
        "_compute_jack_and_stream_raw",
        {0.0: 26, 1: 27, 2: 4, 3: 2, 4: 3, 5: 3, 6: 3, 7: 1, 999: 1, 1000.0: 4, 1000000000.0: 2},
        "Algebraic identities (the 1 of 1 + w * x, the 0 of a floor), lane counts and 7-lane "
        "topology, list indices and step counts, sentinels, and ms/s conversions. Every weight, "
        "threshold and exponent here is a RadarOptions field.",
        {
            1000.0: "Milliseconds to seconds; the chart x-axis is milliseconds throughout.",
            1000000000.0: "Far-past sentinel for 'this lane has never been struck'.",
        },
    ),
    (
        "_compute_speed_raw",
        {1: 4, 0.0: 1, 1000.0: 1},
        "Loop offsets into the sorted note list, the accumulator's zero start, and the ms/s "
        "conversion. The burst law comes from physics and the gain from RadarOptions.",
        {},
    ),
    (
        "compute_raw_technique_drivers",
        {0.0: 40, 1.0: 16, 4: 4, 100.0: 1},
        "Zero floors of every driver, the identity baselines of the multiplicative modulations "
        "(1 + w * x), reported-precision rounding of the 4D breakdown, and the percent-to-ratio "
        "conversion of hold_pct.",
        {},
    ),
    (
        "compute_tech_4d_components",
        {1.0: 2, 4: 4},
        "The identity cap of the rhythm scale and the zero-division floor on avg_nps, plus "
        "reported-precision rounding. Its weights are RadarOptions fields, shared with the "
        "technique operator rather than restated.",
        {},
    ),
    (
        "compute_technique_radar",
        {0.0: 12, 1e-06: 3},
        "Zero scores for an empty chart and the zero-driver/zero-base guards.",
        {},
    ),
    (
        "to_dict",
        {4: 13},
        "Reported precision (4 decimals) of the serialized radar vector.",
        {},
    ),
]

# --- strain.py --------------------------------------------------------------------------------
_STRAIN = [
    ("_build_locked_finger_counts", {0: 4, 1: 4}, "Difference-array indices and step increments.", {}),
    (
        "_calculate_percentile",
        {0.0: 1, 1.0: 2, 100.0: 1},
        "The percentile fraction (q/100), the interpolation weights, and the empty-list guard.",
        {},
    ),
    (
        "_compute_hand_load",
        {0.0: 6, 1.0: 11, 2: 2, 1000.0: 2},
        "Zero floors and empty-window guards, identity baselines, loop offsets, and ms/s "
        "conversions. The weights are StrainOptions fields.",
        {},
    ),
    (
        "compute_micro_speed_burst",
        {0.0: 1, 1: 2, 2: 1, 1000.0: 1},
        "Accumulator zero, the adjacency offsets across the sorted note list, and ms/s.",
        {},
    ),
    (
        "compute_judgment_overlap_buffer",
        {0: 1, 60000.0: 1},
        "Milliseconds per minute and the non-positive-tempo guard.",
        {},
    ),
    (
        "compute_high_speed_scaling_factor",
        {1.0: 2},
        "The identity baseline the low-speed and transition branches are expressed relative to.",
        {},
    ),
    (
        "compute_dual_hand_strain",
        {0.0: 11, 1: 3, 3: 2, 4: 1, 5: 2, 6: 1, 7: 5, 2.0: 2, 0.5: 1, 1000.0: 6},
        "Lane topology and column ranges, list indices, the space-parallel L2 norm, zero floors, "
        "and ms/s conversions. The 0.5 is the minimum chart span the sampler will integrate over. "
        "The two 3s index the shared centre lane (L3 | L2 | L1 | S | R1 | R2 | R3).",
        {},
    ),
    (
        "compute_8d_strain_timeseries",
        {
            0.0: 42, 1: 29, 2: 8, 5: 2, 7: 3, 0.0001: 3, 0.001: 1, 0.15: 1, 0.25: 1, 0.3: 1,
            0.5: 2, 0.8: 1, 1.25: 1, 1.35: 1, 1.5: 3, 2.5: 1, 3.0: 3, 35.0: 1, 50.0: 2,
            90.0: 2, 250.0: 1, 1000.0: 9, 1000000000.0: 1,
        },
        "EXEMPT: this operator consumes the radar and emits a profile's target strain curve — "
        "it is downstream of the star rating, so nothing here can move one, and it is "
        "deliberately not held to the calibration homes. Note the overlap it keeps: the jack "
        "saturation law (1.5 / 3.0), the burst law (5.0-ish / 50.0 / 1.35) and the 250 ms flow "
        "window are restated here rather than read from RadarOptions and physics. That is a "
        "genuine drift hazard for the profiler and a follow-up of its own (issue #49, filed "
        "lowest-priority and unscheduled); the guard's job is to make it visible, not to pretend "
        "it is not there.",
        {},
    ),
    ("get_strains_at", {0.0: 6, 1: 4, 2: 1}, "List indexing and zero-strain guards.", {}),
    ("sample_times_ms", {2: 1, 1000.0: 1}, "Seconds to milliseconds and its display rounding.", {}),
    ("to_dict", {3: 8}, "Reported precision (3 decimals) of the serialized strain curves.", {}),
]

# --- window.py --------------------------------------------------------------------------------
_WINDOW = [
    ("parse_measure_arg", {0: 1, 1: 3}, "Measure-range offsets and the single-measure default.", {}),
    (
        "generate_all_barlines",
        {0: 6, 1: 3, 2: 1, 4: 1, 10000.0: 2},
        "Beat and measure indices (including the 4/4 default meter), a 2 s tail, and the "
        "ms/s conversion of the timing-point span.",
        {},
    ),
    (
        "extract_measure_window",
        {0.0: 4, 1: 3, 2000.0: 1, 600000.0: 1},
        "Zero-time floors, the 1-indexed to 0-indexed measure conversion, and the fallback "
        "window lengths (2 s, 10 min) used when a measure cannot be located.",
        {},
    ),
]


def _rules() -> Dict[Tuple[str, str], LiteralRule]:
    """The registry, keyed by module file name and innermost enclosing function."""
    groups = {
        "calibration.py": _CALIBRATION,
        "features.py": _FEATURES,
        "parser.py": _PARSER,
        "radar.py": _RADAR,
        "rating.py": _RATING,
        "scaling.py": _SCALING,
        "strain.py": _STRAIN,
        "window.py": _WINDOW,
    }
    registry: Dict[Tuple[str, str], LiteralRule] = {}
    for module, entries in groups.items():
        for function, allowed, reason, notes in entries:
            registry[(module, function)] = LiteralRule(dict(allowed), reason, dict(notes))
    return registry


REGISTRY = _rules()


def scan_literals(source: str) -> Dict[str, Dict[float, int]]:
    """
    Every numeric literal in a module's function bodies, grouped by the innermost function that
    encloses it and counted. Module-level constants are not literals in this sense — they are
    named, and the calibration blocks report theirs through `CALIBRATION_CONSTANTS`.
    """
    hits: List[Tuple[str, float]] = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        ):
            hits.append((function, node.value))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(ast.parse(source), "<module>")

    grouped: Dict[str, Dict[float, int]] = {}
    for function, value in hits:
        if function == "<module>":
            continue
        counts = grouped.setdefault(function, {})
        counts[value] = counts.get(value, 0) + 1
    return grouped


def scan_module_constants(source: str) -> Dict[str, float]:
    """
    A module's own numeric constants: `NAME = 3.6` and `NAME: float = 3.6` at the top level.

    These are the second door into the same room. A literal moved out of a function body and
    into a module constant is no longer *in* the body, so the body scan cannot see it — but
    unless the module declares it in `CALIBRATION_CONSTANTS`, the fingerprint cannot see it
    either, and the two of them together are exactly the hole issue #48 exists to close.
    """
    found: Dict[str, float] = {}

    for node in ast.parse(source).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names, value = [node.target.id], node.value
        elif isinstance(node, ast.Assign):
            names, value = [t.id for t in node.targets if isinstance(t, ast.Name)], node.value
        else:
            continue

        if not isinstance(value, ast.Constant) or not isinstance(value.value, (int, float)):
            continue
        if isinstance(value.value, bool):
            continue
        for name in names:
            found[name] = value.value

    return found


def check_module(
    module: str,
    source: str,
    registry: Optional[Dict[Tuple[str, str], LiteralRule]] = None,
    declared_constants: Tuple[str, ...] = (),
) -> List[str]:
    """
    Every way `source` disagrees with `registry` (the registry above by default), as readable
    lines. The negative controls below drive this with a synthetic registry, so what they
    exercise is the guard's teeth rather than today's entries.
    """
    rules = REGISTRY if registry is None else registry
    observed = scan_literals(source)
    problems: List[str] = []

    for function, counts in sorted(observed.items()):
        rule = rules.get((module, function))
        if rule is None:
            problems.append(
                f"{module}:{function} has numeric literals but no registry entry: {counts}"
            )
            continue
        for value in sorted(set(counts) | set(rule.allowed)):
            got, want = counts.get(value, 0), rule.allowed.get(value, 0)
            if got != want:
                problems.append(
                    f"{module}:{function} literal {value!r}: found {got}, registered {want}"
                )

    for module_name, function in rules:
        if module_name == module and function not in observed:
            problems.append(
                f"{module}:{function} is registered but no longer has any numeric literals"
            )

    for name in sorted(scan_module_constants(source)):
        if name not in declared_constants:
            problems.append(
                f"{module}: module constant {name} is neither scanned as a literal nor declared "
                f"in CALIBRATION_CONSTANTS — it reaches no fingerprint, so a change to it would "
                f"move a star rating silently"
            )

    return problems


def test_every_literal_on_the_rating_path_is_registered():
    """
    The guard itself, run over the real modules. A fallback to a source digest would pass this
    trivially; the registry does not, which is what makes the diff of a new entry mean something.
    """
    problems: List[str] = []
    for module in RATING_PATH_MODULES:
        source = (ENGINE_SRC / module).read_text(encoding="utf-8")
        declared = RATING_PATH_CONSTANTS.get(module, ())
        problems.extend(check_module(module, source, declared_constants=declared))

    assert not problems, (
        "numeric literals on the star-rating path disagree with the registry — every one of "
        "these is either a calibration constant that needs a home in an option object or a "
        "calibration block, or a non-calibration literal that needs a registered reason:\n  "
        + "\n  ".join(problems)
    )


def test_the_registry_covers_every_rating_path_module():
    """A module dropped from the scan would silently stop being guarded."""
    registered = {module for module, _ in REGISTRY}
    assert registered == set(RATING_PATH_MODULES)


def test_the_guard_catches_an_unregistered_literal():
    """The positive control: without this, the guard could pass by checking nothing."""
    source = "def operator(x):\n    return x * 3.6\n"
    problems = check_module("radar.py", source, registry={})
    assert len(problems) == 1 and "no registry entry" in problems[0], problems


def test_the_guard_catches_a_retuned_literal():
    """
    Retuning a registered literal is the change that moves a rating, so it must not pass. This
    is the exact shape of the measured gap: `1.35 -> 1.5` in the burst exponent.
    """
    registry = {("radar.py", "operator"): LiteralRule({1.35: 1}, "the burst exponent")}

    unchanged = "def operator(x):\n    return x ** 1.35\n"
    retuned = "def operator(x):\n    return x ** 1.5\n"

    assert check_module("radar.py", unchanged, registry=registry) == []
    problems = check_module("radar.py", retuned, registry=registry)
    assert any("1.5" in p and "found 1, registered 0" in p for p in problems), problems


def test_the_guard_catches_a_duplicated_literal():
    """
    A literal added at a value that is already registered must fail too: the count is part of
    the registration, so a second copy cannot hide behind the first.
    """
    registry = {("radar.py", "operator"): LiteralRule({0: 1}, "a zero floor")}

    one_zero = "def operator(x):\n    return max(0, x)\n"
    two_zeros = "def operator(x):\n    return max(0, x) + 0\n"

    assert check_module("radar.py", one_zero, registry=registry) == []
    problems = check_module("radar.py", two_zeros, registry=registry)
    assert any("found 2, registered 1" in p for p in problems), problems


def test_the_guard_catches_a_removed_registered_literal():
    """A vanished literal is a change too — the entry that explained it must not outlive it."""
    registry = {("radar.py", "operator"): LiteralRule({0: 1, 1.0: 1}, "a zero floor, an identity")}

    # One literal gone while the function still has one: the per-value count catches it...
    reduced = "def operator(x):\n    return x * 1.0\n"
    problems = check_module("radar.py", reduced, registry=registry)
    assert any("0: found 0, registered 1" in p for p in problems), problems

    # ...and the whole function emptied: the entry itself catches it.
    emptied = "def operator(x):\n    return x\n"
    problems = check_module("radar.py", emptied, registry=registry)
    assert any("no longer has any numeric literals" in p for p in problems), problems


def test_the_guard_ignores_booleans_and_module_level_constants():
    """
    `True`/`False` are `int` subclasses and module-level constants are named rather than inline,
    so neither is a literal this guard should be counting.
    """
    source = "LIMIT = 3.6\n\n\ndef operator(x):\n    return x if True else False\n"
    assert scan_literals(source) == {}


def test_every_calibration_constant_is_reported_by_its_block():
    """
    A calibration block's constant list is the fingerprint's only view of it, so the list has to
    be complete: a constant defined in a block but missing from its list moves a star rating
    without moving the engine version — the gap issue #48 closed, one level down. This reads
    each block's source for the module-level numbers it declares and requires the list to name
    every one of them, in every block the engine fingerprint reads.
    """
    assert CALIBRATION_BLOCKS, "no calibration blocks to check"

    for module in CALIBRATION_BLOCKS:
        path = Path(module.__file__)
        declared = set(scan_module_constants(path.read_text(encoding="utf-8")))
        listed = set(module.CALIBRATION_CONSTANTS)

        assert listed, f"{path.name} reports no calibration constants at all"
        assert declared == listed, (
            f"{path.name} defines {sorted(declared - listed)} without listing them in "
            f"CALIBRATION_CONSTANTS, and lists {sorted(listed - declared)} that it does not define"
        )


def test_the_guard_catches_a_module_constant_that_bypasses_the_body_scan():
    """
    The second door: moving a calibration literal out of a function body and into a module
    constant takes it out of the body scan's view. Without the module-constant check it reaches
    no fingerprint either, and a change to it moves star ratings with nothing watching.
    """
    source = "RADAR_JACK_GAIN = 3.6\n\n\ndef operator(x):\n    return x * RADAR_JACK_GAIN\n"

    problems = check_module("radar.py", source, registry={})
    assert any("module constant RADAR_JACK_GAIN" in p for p in problems), problems

    # Declaring it for the fingerprint is the way through — that is the whole point.
    declared = check_module("radar.py", source, registry={}, declared_constants=("RADAR_JACK_GAIN",))
    assert not declared, declared


def test_the_registry_of_declared_constants_matches_the_blocks():
    """A block's list is the only route a module constant has into the fingerprint."""
    for module in CALIBRATION_BLOCKS:
        key = module.__name__.removeprefix("proj7k.") + ".py"
        assert RATING_PATH_CONSTANTS[key] == tuple(module.CALIBRATION_CONSTANTS), key

    # `physics` is pure constants and appears in no literal scan; the map still covers it.
    assert "physics.py" in RATING_PATH_CONSTANTS


def test_every_fingerprinted_option_field_is_read_by_an_operator():
    """
    The other half of "no constant that only moves the version". Being listed in the fingerprint
    is not being *used*: `RatingOptions.max_star_rating` was enumerated into the hash and read by
    nothing, so changing it invalidated every injected beatmap and moved no number. A field that
    appears only inside a `*_fingerprint` body is that same empty knob.
    """
    usages = _attribute_reads_outside_fingerprints(ENGINE_SRC)

    orphans = [
        f"{cls.__name__}.{f.name}"
        for cls in OPTION_CLASSES
        for f in dataclasses.fields(cls)
        if f.name not in usages
    ]

    assert not orphans, (
        "these option fields are fingerprinted but never read by an operator, so changing one "
        f"moves the engine version and nothing else: {orphans}"
    )


def _attribute_reads_outside_fingerprints(root: Path) -> set:
    """
    Every attribute name read anywhere in the engine, except inside a function whose name ends
    in `_fingerprint` — enumerating a field into the hash is not using it.
    """
    reads = set()

    def visit(node: ast.AST, in_fingerprint: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            in_fingerprint = node.name.endswith("_fingerprint")
        if isinstance(node, ast.Attribute) and not in_fingerprint:
            reads.add(node.attr)
        for child in ast.iter_child_nodes(node):
            visit(child, in_fingerprint)

    for path in sorted(root.rglob("*.py")):
        visit(ast.parse(path.read_text(encoding="utf-8")), False)
    return reads


def test_every_calibration_block_with_operators_is_scanned_for_literals():
    """
    A block with operators that is not in the literal scan has a half nothing watches: its named
    constants would be checked for completeness while a literal slipped into one of its operators
    would not be checked at all. A block that is pure constants has no such half.
    """
    for module in CALIBRATION_BLOCKS:
        path = Path(module.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree)):
            assert path.name in RATING_PATH_MODULES, f"{path.name} has operators but is not scanned"
