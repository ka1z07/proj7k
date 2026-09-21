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


def test_ln_inverse_high_tier_benchmark_tracks_classified_as_ln_inverse(benchmark_radar):
    """
    Feedback loop for Ticket 2 (SPEC-P2.2-02):
    Asserts that canonical high-tier LN Inverse benchmark charts (8th ~ Stellium)
    naturally recover 'ln_inverse' dominance once Release inflation is resolved.
    """
    for tier in ["8th", "10th", "Stellium"]:
        radar = benchmark_radar("LN Inverse", tier)
        assert radar.dominant_technique == "ln_inverse", (
            f"LN Inverse {tier} misclassified as {radar.dominant_technique} "
            f"(inv={radar.ln_inverse:.2f}★, rel={radar.ln_release:.2f}★, gen={radar.ln_general:.2f}★)"
        )
        assert radar.ln_inverse > radar.ln_release, (
            f"LN Inverse {tier} inverse score ({radar.ln_inverse:.2f}★) <= release score ({radar.ln_release:.2f}★)"
        )
