"""
Canonical star-rating calibration constants for the om7k difficulty engine.

Single source of truth for every constant that maps a raw physical quantity onto the star
scale. Consumers read these constants from one options object (`RatingOptions`), never from a
private copy:
- `strain.compute_raw_strain_star_rating` / `rating.synthesize_star_rating`: the strain anchor
  law SR = a * S^exp + b.
- `radar.compute_technique_radar`: the *technique* anchor laws, one per axis, which carry each
  raw driver onto that same star scale as an absolute technique star (ADR-0016).
- `lazer.annotator`: the injected methodology fingerprint, so a calibration change marks
  every previously injected beatmap for re-evaluation.
"""

from dataclasses import dataclass
import hashlib
import math
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class TechniqueStarAnchor:
    """
    One technique's absolute driver-to-star law: SR_k = a * r_k ** exp (ADR-0016).

    The axis' raw driver is in the units its own operator produces, and the anchor is what puts
    that driver on the star scale the canonical Dan ladder is stated in: a chart at tier T has
    its own axis' driver read as approximately `CANONICAL_DAN_SR[T]`. Anchoring per axis rather
    than by a shared rescaling of the driver vector is the point — the eight drivers are in
    eight different units, so a common scale can only be established by calibrating each against
    the one ladder they all share.

    **The law has no offset, deliberately.** The anchor family the ticket names is
    `a * r ** exp + b`, and a non-zero `b` reads as *b stars on a chart with none of this
    technique* — the radar's cross-inhibition (`radar.compute_raw_technique_drivers` zeroes the
    LN drivers on a rice chart, and `tests/test_radar.py` pins that a pure rice chart's LN
    dimensions are exactly 0.0) is the same claim. A fit that is free to offset buys one or two
    more charts inside the ±8% band per axis and costs that floor, which is a worse trade than
    it looks: it would put a floor under *every* chart's value on that axis and so under the
    composite total. With the floor kept, `a` and `b` are not separately identifiable anyway —
    `a * (r / b_ref) ** exp` is the same family — so the parameters that remain are the gain and
    the exponent. `tools/technique_star_fit.py` reports both fits side by side.
    """

    technique: str
    a: float
    exp: float

    def score(self, driver: float) -> float:
        """This axis' absolute technique star for one chart's driver value."""
        if driver <= 0.0:
            return 0.0
        return self.a * math.pow(driver, self.exp)


#: The eight absolute technique anchors, fitted by `tools/technique_star_fit.py` on each axis'
#: own 15-tier ladder against `dan.CANONICAL_DAN_SR` (issue #50). Order is irrelevant — each
#: anchor names its own technique — and a test holds the names to `radar.TECHNIQUE_NAMES`.
_TECHNIQUE_ANCHORS: Tuple[TechniqueStarAnchor, ...] = (
    TechniqueStarAnchor(technique="jack", a=2.653052, exp=0.183579),
    TechniqueStarAnchor(technique="tech", a=0.371562, exp=0.879417),
    TechniqueStarAnchor(technique="speed", a=2.117124, exp=0.31253),
    TechniqueStarAnchor(technique="stream", a=1.434932, exp=0.501519),
    TechniqueStarAnchor(technique="ln_general", a=1.478473, exp=0.407781),
    TechniqueStarAnchor(technique="ln_tech", a=2.150186, exp=0.321906),
    TechniqueStarAnchor(technique="ln_inverse", a=3.658571, exp=0.093018),
    TechniqueStarAnchor(technique="ln_release", a=2.260767, exp=0.303427),
)


@dataclass(frozen=True)
class StrainStarCalibration:
    """
    The star scale's calibration: physical strain to star rating, and each raw technique driver
    to its absolute technique star (ADR-0011, ADR-0006, ADR-0016).

    - strain_a / strain_b / strain_exp: the strain anchor law SR = a * S^exp + b, which is the
      engine's *raw* strain rating and the scale the anchor tiers were calibrated in.
    - technique_anchors: the eight per-axis laws SR_k = a_k * r_k ** exp_k. They replaced
      `driver_backpressure_exp`, which expressed every axis as a *share* of the strain rating
      (`SR_base * (r_k / max r) ** exp`) and so made the dominant axis' score equal to `SR_base`
      on every chart, whatever the chart was.
    """
    strain_a: float = 0.268980
    strain_b: float = 0.129915
    strain_exp: float = 0.65
    technique_anchors: Tuple[TechniqueStarAnchor, ...] = _TECHNIQUE_ANCHORS

    def star_rating_from_strain(self, s_base: float) -> float:
        """Maps raw physical strain to the raw star rating: SR_raw = a * S^exp + b."""
        if s_base <= 0.0:
            return 0.0
        return max(0.0, self.strain_a * math.pow(s_base, self.strain_exp) + self.strain_b)

    def anchor_for(self, technique: str) -> TechniqueStarAnchor:
        """The named axis' anchor. Raises KeyError for a name that is not a technique."""
        for anchor in self.technique_anchors:
            if anchor.technique == technique:
                return anchor
        raise KeyError(f"no technique anchor for {technique!r}")


#: Canonical calibration: the constants every engine stage falls back to.
DEFAULT_CALIBRATION = StrainStarCalibration()


def block_fingerprint_constants(module: Any) -> Dict[str, Any]:
    """
    A calibration block's constants, keyed by name, for the methodology fingerprint.

    A *calibration block* is a module on the star-rating path that holds its constants as
    module-level names and lists every one of them in `CALIBRATION_CONSTANTS`. The list is the
    block's own declaration of what it contributes to the fingerprint, and
    `tests/test_engine_literal_registry.py` asserts it is complete against the module's source —
    a constant defined in a block but missing from its list would move a star rating without
    moving the engine version, which is the staleness ADR-0014 exists to prevent.

    Reading through `getattr` at call time rather than copying at import time is what lets the
    guards above monkeypatch a constant and watch the version move.
    """
    return {name: getattr(module, name) for name in module.CALIBRATION_CONSTANTS}


def compute_methodology_fingerprint(**constants: Any) -> str:
    """
    Stable 8-hex-char SHA-256 digest over the named calibration constants.

    Two calls agree iff every constant agrees, so the digest changes exactly when the
    methodology it summarizes changes. Used as the algorithm version stamped into
    osu!lazer difficulty names (lazer annotator) to detect stale injections.
    """
    parts = "\n".join(f"{name}={constants[name]!r}" for name in sorted(constants))
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:8]
