"""
Tests for client.estimate_tokens(): probing, multiplier lookup and overrides.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from guard_client import GuardClient, GuardError

from .conftest import API_KEY, BASE_URL, SPACE_ID, png_bytes, space_detail_response

SPACE_URL = f"{BASE_URL}/api/v1/spaces/{SPACE_ID}"


def mp4_bytes(width: int, height: int, duration: float) -> bytes:
    """Reuse the ISO-BMFF builder the probe tests already exercise."""
    from .test_probe import mp4_with

    return mp4_with(width, height, duration=duration)


@respx.mock
def test_multiplier_comes_from_the_space(client):
    """Verify that the multiplier is fetched from the space details."""
    route = respx.get(SPACE_URL).mock(
        return_value=httpx.Response(200, json=space_detail_response())
    )

    est = client.estimate_tokens(frames=1, width=800, height=600)

    assert route.called
    assert est.multiplier == 3
    assert est.tokens == 3  # 1 frame x 1 x 3


@respx.mock
def test_explicit_multiplier_makes_it_offline(client):
    """Ensure that providing an explicit multiplier avoids any network calls."""
    route = respx.get(SPACE_URL).mock(return_value=httpx.Response(200))

    est = client.estimate_tokens(frames=10, width=1920, height=1080, multiplier=2)

    assert not route.called
    assert est.tokens == 20


@respx.mock
def test_per_call_space_id_overrides_the_default(client):
    """Verify that passing a space ID to the call overrides the default space."""
    other = "99999999-9999-9999-9999-999999999999"
    route = respx.get(f"{BASE_URL}/api/v1/spaces/{other}").mock(
        return_value=httpx.Response(
            200, json=space_detail_response(predictor_multiplier=5)
        )
    )

    est = client.estimate_tokens(frames=1, width=800, height=600, space_id=other)

    assert route.called
    assert est.multiplier == 5


@respx.mock
def test_missing_multiplier_and_space_raises_before_any_call():
    """
    Ensure an error is raised before network activity if both multiplier and space are
    missing.
    """
    route = respx.get(SPACE_URL).mock(return_value=httpx.Response(200))
    bare = GuardClient(api_key=API_KEY, base_url=BASE_URL)
    try:
        with pytest.raises(GuardError, match="multiplier is required"):
            bare.estimate_tokens(frames=1, width=800, height=600)
    finally:
        bare.close()

    assert not route.called


@respx.mock
def test_space_without_multiplier_raises(client):
    """Verify that a space lacking a predictor multiplier raises an error."""
    respx.get(SPACE_URL).mock(
        return_value=httpx.Response(
            200, json=space_detail_response(predictor_multiplier=None)
        )
    )

    with pytest.raises(GuardError, match="no predictor_multiplier"):
        client.estimate_tokens(frames=1, width=800, height=600)


@respx.mock
def test_probes_a_video(client):
    """
    Ensure that the token estimator correctly probes a video file for dimensions and
    duration.
    """
    respx.get(SPACE_URL).mock(
        return_value=httpx.Response(
            200, json=space_detail_response(predictor_multiplier=1)
        )
    )

    est = client.estimate_tokens(mp4_bytes(2560, 1440, 10.4), filename="clip.mp4")

    assert est.frames == 11  # 10.4s rounded up
    assert (est.width, est.height) == (2560, 1440)
    assert est.tier_cost == 2
    assert est.tokens == 22


@respx.mock
def test_probes_an_image(client):
    """Ensure that the token estimator correctly probes an image file."""
    respx.get(SPACE_URL).mock(
        return_value=httpx.Response(
            200, json=space_detail_response(predictor_multiplier=1)
        )
    )

    est = client.estimate_tokens(png_bytes(), filename="x.png")

    assert est.frames == 1
    assert est.tokens == 1


@respx.mock
def test_explicit_values_override_the_probe(client):
    """
    Verify that explicitly provided values override those found by probing the media.
    """
    respx.get(SPACE_URL).mock(
        return_value=httpx.Response(
            200, json=space_detail_response(predictor_multiplier=1)
        )
    )

    est = client.estimate_tokens(
        mp4_bytes(1280, 720, 10.0), filename="clip.mp4", duration_seconds=60.0
    )

    assert est.frames == 60  # the override, not the probed 10
    assert (est.width, est.height) == (1280, 720)  # still from the file


@respx.mock
def test_explicit_frames_beat_duration(client):
    """Ensure that an explicit frame count takes precedence over duration."""
    respx.get(SPACE_URL).mock(
        return_value=httpx.Response(
            200, json=space_detail_response(predictor_multiplier=1)
        )
    )

    est = client.estimate_tokens(
        mp4_bytes(1280, 720, 10.0), filename="clip.mp4", frames=3
    )

    assert est.frames == 3


def test_no_source_and_no_dimensions_raises(client):
    """
    Verify that an error is raised if neither a media source nor dimensions are
    provided.
    """
    with pytest.raises(GuardError, match="Nothing to estimate from"):
        client.estimate_tokens(multiplier=1)


@respx.mock
def test_pure_calculation_touches_nothing(client):
    """
    Ensure that supplying all required values manually skips probing and network
    requests entirely.
    """
    route = respx.get(SPACE_URL).mock(return_value=httpx.Response(200))

    est = client.estimate_tokens(frames=10, width=3840, height=2160, multiplier=3)

    assert not route.called
    assert est.tokens == 120


@respx.mock
def test_above_top_tier_raises(client):
    """Verify that dimensions exceeding the maximum tier raise an error."""
    respx.get(SPACE_URL).mock(
        return_value=httpx.Response(200, json=space_detail_response())
    )

    with pytest.raises(GuardError, match="exceeds the largest tier"):
        client.estimate_tokens(frames=1, width=4096, height=2160, multiplier=1)


@respx.mock
async def test_async_estimate_fetches_multiplier(async_client):
    """Ensure the async client fetches the multiplier from the space details."""
    route = respx.get(SPACE_URL).mock(
        return_value=httpx.Response(200, json=space_detail_response())
    )

    est = await async_client.estimate_tokens(frames=2, width=1920, height=1080)

    assert route.called
    assert est.tokens == 6  # 2 x 1 x 3


@respx.mock
async def test_async_estimate_offline_with_multiplier(async_client):
    """
    Verify that the async client avoids network calls when given an explicit multiplier.
    """
    route = respx.get(SPACE_URL).mock(return_value=httpx.Response(200))

    est = await async_client.estimate_tokens(
        mp4_bytes(1920, 1080, 5.0), filename="clip.mp4", multiplier=4
    )

    assert not route.called
    assert est.frames == 5
    assert est.tokens == 20
