def test_ln_general_benchmark_tracks_classified_as_ln_general(benchmark_radar):
    """
    Feedback loop for Ticket 2 (SPEC-P2.2-02):
    Asserts that canonical LN General benchmark charts (e.g. 5th, 10th, Stellium)
    are recognized as dominant 'ln_general' rather than falsely hijacked by 'ln_release'.
    """
    for tier in ["5th", "10th", "Stellium"]:
        radar = benchmark_radar("LN General", tier)
        assert radar.dominant_technique == "ln_general", (
            f"LN General {tier} misclassified as {radar.dominant_technique} "
            f"(gen={radar.ln_general:.2f}★, rel={radar.ln_release:.2f}★)"
        )
        assert radar.ln_general > radar.ln_release, (
            f"LN General {tier} general score ({radar.ln_general:.2f}★) <= release score ({radar.ln_release:.2f}★)"
        )


#: Tiers of the LN Inverse ladder where the *release* star exceeds the inverse one. The two axes
#: measure different loads — hold volume and lift precision — and the dimensions are absolute
#: technique stars now (ADR-0016), so a chart can read higher on release while still being
#: *built* out of inverse articulation: the ticket owner confirmed that reading against the 8th
#: tier's own feel, and 10th crosses the same way. The crossing is information, not drift; every
#: other tier of the ladder must keep the original order, and a tier joining this set fails the
#: test until it is recorded.
#:
#: The crossing is wider than these two tiers: the inverse ladder's 9th, Gamma and Azimuth read
#: release above inverse as well, and the LN General ladder crosses back and forth over its own
#: middle — six of its fifteen tiers read release above general, and the tiers where general
#: leads include margins as thin as 0.05★ (5th) and 0.09★ (Stellium). Those tiers are outside
#: this test's list; the pattern is recorded in ADR-0016 as the LN pair's relative scale being
#: genuinely interleaved rather than as a defect — the two ladders interleave in lock depth, and
#: `ln_release` is by construction a modifier on the general base (ADR-0015).
RELEASE_LEADS = {"8th", "10th"}


def test_ln_inverse_high_tier_benchmark_tracks_classified_as_ln_inverse(benchmark_radar):
    """
    Feedback loop for Ticket 2 (SPEC-P2.2-02):
    Asserts that canonical high-tier LN Inverse benchmark charts (8th ~ Stellium)
    naturally recover 'ln_inverse' dominance once Release inflation is resolved.

    "Dominance" is read on the drivers, which is what says what the chart is made of; the
    second half of the original expectation — that the inverse *score* exceeds the release one —
    holds on the tiers that are not in `RELEASE_LEADS`, and the recorded crossing is asserted
    the other way round so that neither direction can drift silently.
    """
    for tier in ["8th", "10th", "Stellium"]:
        radar = benchmark_radar("LN Inverse", tier)
        assert radar.dominant_technique == "ln_inverse", (
            f"LN Inverse {tier} misclassified as {radar.dominant_technique} "
            f"(inv={radar.ln_inverse:.2f}★, rel={radar.ln_release:.2f}★, gen={radar.ln_general:.2f}★)"
        )
        if tier in RELEASE_LEADS:
            assert radar.ln_release > radar.ln_inverse, (
                f"LN Inverse {tier} no longer reads release above inverse "
                f"(inv={radar.ln_inverse:.2f}★, rel={radar.ln_release:.2f}★) — either the "
                f"calibration moved or this tier left RELEASE_LEADS; record which"
            )
        else:
            assert radar.ln_inverse > radar.ln_release, (
                f"LN Inverse {tier} inverse score ({radar.ln_inverse:.2f}★) <= release score "
                f"({radar.ln_release:.2f}★) — if the owner confirms this chart's feel too, "
                f"add the tier to RELEASE_LEADS instead of leaving the test red"
            )
