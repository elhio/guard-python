"""
Tests for the token formula: frames x resolution cost x multiplier.
"""

from __future__ import annotations

import pytest

from guard_client import (
    GuardError,
    TokenEstimate,
    estimate_tokens,
    frames_for,
    tier_for,
)


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (0.0, 1),  # a still image bills a single frame
        (0.4, 1),
        (1.0, 1),
        (10.4, 11),  # a partial second is still processed
        (59.9, 60),
        (60.0, 60),
    ],
)
def test_frames_round_up(duration, expected):
    """Verify that video durations round up to the nearest integer frame count."""
    assert frames_for(duration) == expected


def test_negative_duration_raises():
    """Ensure that passing a negative duration raises a GuardError."""
    with pytest.raises(GuardError, match="Expected 0 or greater"):
        frames_for(-1.0)


@pytest.mark.parametrize(
    ("width", "height", "tier", "cost"),
    [
        (640, 480, 1, 1),
        (1920, 1080, 1, 1),  # top of tier 1
        (1921, 1080, 2, 2),  # one pixel over
        (2560, 1440, 2, 2),  # top of tier 2
        (2561, 1440, 3, 4),
        (3840, 2160, 3, 4),  # top of tier 3, UHD
    ],
)
def test_tier_boundaries(width, height, tier, cost):
    """
    Verify that resolution boundaries accurately map to the expected tier and cost.
    """
    assert tier_for(width, height) == (tier, cost)


@pytest.mark.parametrize(
    ("width", "height"),
    [
        (1920, 1080),
        (2560, 1440),
        (3840, 2160),
        (2560, 1080),
    ],
)
def test_tier_is_orientation_independent(width, height):
    """
    Verify that resolution tier calculations yield the same result regardless of aspect
    ratio orientation.
    """
    assert tier_for(width, height) == tier_for(height, width)


def test_ultrawide_costs_by_long_side():
    """Verify that ultrawide resolutions are priced based on their longest dimension."""
    assert tier_for(2560, 1080) == (2, 2)


def test_dci_4k_raises():
    """Ensure that DCI 4K resolution raises an error while standard UHD is accepted."""
    assert tier_for(3840, 2160) == (3, 4)

    with pytest.raises(GuardError, match="exceeds the largest tier"):
        tier_for(4096, 2160)


def test_above_top_tier_raises():
    """
    Ensure that resolutions exceeding the maximum long-side threshold raise a
    GuardError.
    """
    with pytest.raises(GuardError, match=r"long side \(3841\)"):
        tier_for(3841, 100)


@pytest.mark.parametrize(
    ("width", "height"),
    [
        (0, 1080),
        (1920, 0),
        (-1, 1080),
        (1920, -1),
        (0, 0),
    ],
)
def test_non_positive_dimensions_raise(width, height):
    """Verify that zero or negative dimensions raise a GuardError."""
    with pytest.raises(GuardError, match="must be positive"):
        tier_for(width, height)


def test_full_formula():
    """
    Verify that the token cost calculation produces the expected total and breakdown.
    """
    est = estimate_tokens(frames=10, width=3840, height=2160, multiplier=3)

    assert est.tokens == 120  # 10 x 4 x 3
    assert est.frames == 10
    assert est.resolution_tier == 3
    assert est.tier_cost == 4
    assert est.multiplier == 3


def test_single_image_at_tier_one():
    """Verify the calculation for a single tier-one image."""
    est = estimate_tokens(frames=1, width=800, height=600, multiplier=1)
    assert est.tokens == 1


def test_estimate_reports_the_breakdown():
    """
    Ensure TokenEstimate carries the complete parameter breakdown alongside the total.
    """
    est = estimate_tokens(
        frames=5, width=2560, height=1440, multiplier=2, duration_seconds=4.2
    )

    assert isinstance(est, TokenEstimate)
    assert (est.frames, est.tier_cost, est.multiplier) == (5, 2, 2)
    assert est.tokens == est.frames * est.tier_cost * est.multiplier
    assert est.width == 2560
    assert est.height == 1440
    assert est.duration_seconds == 4.2


def test_str_is_readable():
    """
    Verify that string representation of TokenEstimate formats as human-readable
    breakdown.
    """
    est = estimate_tokens(frames=10, width=1920, height=1080, multiplier=2)
    assert str(est) == "20 tokens (10 frames x 1 x 2)"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"frames": 0}, "Invalid frames=0.*Expected 1 or greater"),
        ({"frames": -3}, "Invalid frames=-3.*Expected 1 or greater"),
        ({"multiplier": 0}, "Invalid multiplier=0.*Expected 1 or greater"),
        ({"multiplier": -2}, "Invalid multiplier=-2.*Expected 1 or greater"),
    ],
)
def test_out_of_range_inputs_raise(kwargs, message):
    """Verify that non-positive frame or multiplier inputs raise a GuardError."""
    base = {"frames": 1, "width": 800, "height": 600, "multiplier": 1}
    base.update(kwargs)

    with pytest.raises(GuardError, match=message):
        estimate_tokens(**base)
