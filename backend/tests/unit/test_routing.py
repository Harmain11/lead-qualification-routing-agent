def test_routing_bands_are_configured_and_contiguous(routing) -> None:
    assert routing.band_for(100).key == "hot"
    assert routing.band_for(80).key == "hot"
    assert routing.band_for(79).key == "warm"
    assert routing.band_for(60).key == "warm"
    assert routing.band_for(59).key == "needs_qualifying"
    assert routing.band_for(40).key == "needs_qualifying"
    assert routing.band_for(39).key == "nurture"
    assert routing.band_for(20).key == "nurture"
    assert routing.band_for(19).key == "disqualify"
    assert routing.band_for(0).key == "disqualify"


def test_every_spec_boundary_maps_to_the_expected_band(routing) -> None:
    expected = {
        100: "hot",
        80: "hot",
        79: "warm",
        60: "warm",
        59: "needs_qualifying",
        40: "needs_qualifying",
        39: "nurture",
        20: "nurture",
        19: "disqualify",
        0: "disqualify",
    }
    assert {score: routing.band_for(score).key for score in expected} == expected
