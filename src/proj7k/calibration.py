"""
Canonical star-rating calibration constants for the om7k difficulty engine.

Single source of truth for every constant that maps physical strain onto the star scale.
Consumers read these constants from one options object (`RatingOptions`), never from a
private copy:
- `strain.compute_raw_strain_star_rating` / `rating.synthesize_star_rating`: anchor law
  SR = a * S^exp + b.
- `radar.compute_technique_radar`: the same anchor law, plus the driver back-pressure
  exponent that compresses the 8 raw technique drivers onto that star scale.
- `lazer.annotator`: the injected methodology fingerprint, so a calibration change marks
  every previously injected beatmap for re-evaluation.
"""

from dataclasses import dataclass
import hashlib
import math
from typing import Any


@dataclass(frozen=True)
class StrainStarCalibration:
    """
    Physical-strain to star-rating calibration (ADR-0011, ADR-0006).

    - strain_a / strain_b / strain_exp: anchor law SR = a * S^exp + b separating the raw
      strain magnitude from the star dimension.
    - driver_backpressure_exp: exponent compressing raw 8D technique drivers into the star
      scale, SR_k = SR_base * (r_k / max(r))^exp. It sets the *relative* spread between the
      dominant dimension and the noise floor, so moving it shifts every technique score.
    """
    strain_a: float = 0.268980
    strain_b: float = 0.129915
    strain_exp: float = 0.65
    driver_backpressure_exp: float = 0.75

    def star_rating_from_strain(self, s_base: float) -> float:
        """Maps raw physical strain to the raw star rating: SR_raw = a * S^exp + b."""
        if s_base <= 0.0:
            return 0.0
        return max(0.0, self.strain_a * math.pow(s_base, self.strain_exp) + self.strain_b)


#: Canonical calibration: the constants every engine stage falls back to.
DEFAULT_CALIBRATION = StrainStarCalibration()


def compute_methodology_fingerprint(**constants: Any) -> str:
    """
    Stable 8-hex-char SHA-256 digest over the named calibration constants.

    Two calls agree iff every constant agrees, so the digest changes exactly when the
    methodology it summarizes changes. Used as the algorithm version stamped into
    osu!lazer difficulty names (lazer annotator) to detect stale injections.
    """
    parts = "\n".join(f"{name}={constants[name]!r}" for name in sorted(constants))
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:8]
