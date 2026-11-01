"""
The constants of the spec-v0.2 engine (`osu-mania-7k-difficulty-spec.md` §12), and nowhere else.

Every number that can move a result lives here; the other modules read it from a `Params`.
Names follow the spec's symbols so a constant can be traced to its section. They are priors: the
§13 calibration was tried on v0.2 and not adopted (ADR-0017), so nothing here was fitted. The
departures from the prototype are `k_same` (ADR-0044) and the jack speed-limit surcharge `jack_*`
(ADR-0051), chosen against the benchmark ladder and the owner's anchors, not fitted.

Not carried over from the prototype: its experiment switches, and the cognitive terms
(`M`, `R`), the fatigue factor `Phi` and the accumulators `E^f`, `E^g`. In v0.2 none of them
reaches any output (spec 修订记录), and `tests/engine/test_engine_golden.py` is what says dropping
them changed no reading.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Params:
    # §3.1–3.3 loss, solve and tolerance
    eps_rc: float = 0.04              # tolerance of an RC skill; also the base of the total's
    eps_ln: float = 0.05              # tolerance of an LN skill
    eps_total_ln_slope: float = 0.01  # total tolerance = eps_rc + slope * mean LN context (§3.3)
    beta: float = 6.0
    N0: float = 150.0
    theta_min: float = 0.01
    theta_max: float = 1000.0
    theta_tol: float = 1e-9           # bisection width in ln(theta); the spec's 1e-4 is a floor (R7)

    # §2 preprocessing
    l_min: float = 0.100              # an LN shorter than this is a rice (§2.2)
    row_tol: float = 0.001            # events within this of a row's first event join the row (§2.4, R1)
    ln_context_holds: float = 2.0     # held columns at which an event is fully in LN context (§8.1 lambda)

    # §4 instantaneous demand
    delta_h: float = 0.050
    k_same: float = 1.75              # weight of the same-finger rate j in v against the other fingers' coupling (§4.5)
    # ADR-0051 speed-limit surcharge: a sustained jack costs more than its rate as the rate nears jack_hz
    jack_hz: float = 13.3             # Hz (200 BPM 1/4); a press's same-finger cost is (1 + jack_gain / 2) x here
    jack_pow: float = 10.0            # how sharply the surcharge rises towards jack_hz
    jack_gain: float = 2.0            # the most the surcharge multiplies the same-finger cost by, on top of 1
    jack_run: int = 3                 # same-finger repeats a press needs behind it (a 4-note jack) for the surcharge
    jack_top: float = 20.0            # Hz; a faster repeat (a grace note, a flam) is charged as this rate
    delta_floor: float = 0.020
    delta_0: float = 0.040            # psi time scale of the first row, before any rhythm is known
    delta_0_rel: float = 0.5          # psi time scale = this * median row interval over the past delta_0_window
    delta_0_window: float = 2.0
    k_ring_mid: float = 1.0
    k_mid_idx: float = 0.7
    k_ring_idx: float = 0.5
    k_thumb_idx: float = 0.5
    k_thumb_other: float = 0.3
    k_scale: float = 1.0
    k_cross: float = 0.2
    chi_0: float = 0.15               # hold-constraint strength (§4.4)
    w_rel: float = 0.7                # a release event's weight in v (§4.5)
    v_cap: float = 40.0               # Hz; one very short interval must not reach downstream events (§4)

    # §5.2 rhythm surprise (feeds q^C only)
    T_c: float = 4.0
    U_max: float = 4.0
    alpha_r: float = 1.0
    b: float = 0.15
    rhythm_horizon: float = 12.0      # in T_c; the history sum is truncated here (R5)

    # §6.1 hand accumulator and §7 demand d_i = E^h ** (1 / gamma)
    tau_h: float = 4.0
    gamma: float = 2.0

    # §8 membership and attribution
    r_lo: float = 0.26
    r_hi: float = 0.42
    k_hi: float = 2.5
    u_lo: float = 0.5
    u_hi: float = 2.5
    h_ref: float = 3.0


DEFAULT = Params()

#: The constants of the frozen spec-v0.2 prototype, before ADR-0044 weighted the same-finger rate and
#: ADR-0051 added the jack speed-limit surcharge.
#: `tests/engine/test_engine_golden.py` checks the port against the prototype's output under these.
V02 = Params(k_same=1.0, jack_gain=0.0)
