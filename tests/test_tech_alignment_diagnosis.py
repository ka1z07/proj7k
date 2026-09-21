def test_regular_tech_benchmark_tracks_classified_as_tech(benchmark_radar):
    """
    Unit test for SPEC-P2.2-03 / ADR-0008:
    Verify that canonical Regular Tech benchmark charts are recognized as dominant 'tech',
    and their tech radar score exceeds stream and jack scores.
    """

    for tier in ["6th", "7th", "10th", "Stellium"]:
        radar = benchmark_radar("Regular Tech", tier)
        assert radar.dominant_technique == "tech", (
            f"Regular Tech {tier} misclassified as {radar.dominant_technique} "
            f"(tech={radar.tech:.2f}★, stream={radar.stream:.2f}★, jack={radar.jack:.2f}★)"
        )
        assert radar.tech > radar.stream, (
            f"Regular Tech {tier} tech score ({radar.tech:.2f}★) <= stream score ({radar.stream:.2f}★)"
        )


def test_ln_tech_benchmark_tracks_classified_as_ln_tech(benchmark_radar):
    """
    Unit test for SPEC-P2.2-03 / ADR-0008:
    Verify that canonical LN Tech benchmark charts are recognized as dominant 'ln_tech',
    and their ln_tech radar score exceeds ln_general and ln_release scores.
    """

    for tier in ["5th", "10th", "Gamma", "Stellium"]:
        radar = benchmark_radar("LN Tech", tier)
        assert radar.dominant_technique == "ln_tech", (
            f"LN Tech {tier} misclassified as {radar.dominant_technique} "
            f"(ln_tech={radar.ln_tech:.2f}★, ln_gen={radar.ln_general:.2f}★, ln_rel={radar.ln_release:.2f}★)"
        )
        assert radar.ln_tech >= radar.ln_general, (
            f"LN Tech {tier} ln_tech score ({radar.ln_tech:.2f}★) < ln_gen score ({radar.ln_general:.2f}★)"
        )


def test_regular_stream_and_ln_general_not_hijacked_by_tech(benchmark_radar):
    """
    Regression verification for SPEC-P2.2-03:
    Ensures regular stream charts and LN general charts do not suffer semantic drift into tech.
    """

    # 1. Regular Stream benchmarks must retain stream dominance
    for tier in ["5th", "10th", "Stellium"]:
        radar = benchmark_radar("Regular Stream", tier)
        assert radar.dominant_technique == "stream", (
            f"Regular Stream {tier} misclassified as {radar.dominant_technique} "
            f"(stream={radar.stream:.2f}★, tech={radar.tech:.2f}★)"
        )
        assert radar.stream > radar.tech, (
            f"Regular Stream {tier} stream score ({radar.stream:.2f}★) <= tech score ({radar.tech:.2f}★)"
        )

    # 2. LN General benchmarks must retain ln_general dominance
    for tier in ["5th", "10th", "Stellium"]:
        radar = benchmark_radar("LN General", tier)
        assert radar.dominant_technique == "ln_general", (
            f"LN General {tier} misclassified as {radar.dominant_technique} "
            f"(ln_gen={radar.ln_general:.2f}★, ln_tech={radar.ln_tech:.2f}★)"
        )
        assert radar.ln_general > radar.ln_tech, (
            f"LN General {tier} ln_gen score ({radar.ln_general:.2f}★) <= ln_tech score ({radar.ln_tech:.2f}★)"
        )
