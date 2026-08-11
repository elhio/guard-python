"""
Every cloud request must carry an API key.

The constructor already refuses a keyless cloud client, but a client built with
`engine="local"` is legitimately keyless and still exposes every cloud resource.
These tests pin the transport-level backstop that closes that path.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from guard_client import AsyncGuardClient, GuardClient, GuardError
from guard_client.transport import AsyncTransport, SyncTransport, TransportConfig

from .conftest import BASE_URL, SPACE_ID, UPLOAD_URL, png_bytes

SPACES_URL = f"{BASE_URL}/spaces/"
ACTIVITIES_URL = f"{BASE_URL}/activities/"


@pytest.fixture
def keyless(isolate_env):
    """
    Provide a local-engine client that is legitimately constructible without a key.
    """
    with GuardClient(
        engine="local", space_id=SPACE_ID, base_url=BASE_URL, max_retries=0
    ) as c:
        yield c


@pytest.fixture
async def async_keyless(isolate_env):
    """
    Provide an async local-engine client that is legitimately constructible without a
    key.
    """
    async with AsyncGuardClient(
        engine="local", space_id=SPACE_ID, base_url=BASE_URL, max_retries=0
    ) as c:
        yield c


@respx.mock
def test_resource_call_without_key_raises(keyless):
    """Ensure that making a cloud resource call without an API key raises an error."""
    route = respx.get(SPACES_URL).mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(GuardError, match="API key is required"):
        keyless.spaces.list()

    assert not route.called, "the refusal must happen before any request is sent"


@respx.mock
def test_per_call_cloud_override_without_key_raises(keyless):
    """Verify the per-call cloud engine override does not escape the API key guard."""
    route = respx.post(ACTIVITIES_URL).mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(GuardError, match="API key is required"):
        keyless.analyze(png_bytes(), engine="cloud")

    assert not route.called


@respx.mock
async def test_async_resource_call_without_key_raises(async_keyless):
    """
    Ensure that making an async cloud resource call without an API key raises an error.
    """
    route = respx.get(SPACES_URL).mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(GuardError, match="API key is required"):
        await async_keyless.spaces.list()

    assert not route.called


@respx.mock
async def test_async_per_call_cloud_override_without_key_raises(async_keyless):
    """
    Verify the async per-call cloud engine override does not escape the API key guard.
    """
    route = respx.post(ACTIVITIES_URL).mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(GuardError, match="API key is required"):
        await async_keyless.analyze(png_bytes(), engine="cloud")

    assert not route.called


def test_error_names_every_way_to_supply_a_key(keyless):
    """
    Check that the error message lists all valid methods to supply an API key.
    """
    with pytest.raises(GuardError) as excinfo:
        keyless.tasks.list()

    message = str(excinfo.value)
    assert "api_key=" in message
    assert "GUARD_API_KEY" in message
    assert ".env" in message


@respx.mock
def test_presigned_upload_still_works_without_a_key():
    """
    Ensure presigned uploads succeed without an API key since they bypass the auth
    guard.
    """
    route = respx.post(UPLOAD_URL).mock(return_value=httpx.Response(204))
    config = TransportConfig(base_url=BASE_URL, max_retries=0)

    with SyncTransport(config) as transport:
        transport.upload(UPLOAD_URL, {"key": "uploads/a"}, "a.png", png_bytes())

    assert route.called
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
async def test_async_presigned_upload_still_works_without_a_key():
    """
    Ensure async presigned uploads succeed without an API key since they bypass auth.
    """
    route = respx.post(UPLOAD_URL).mock(return_value=httpx.Response(204))
    config = TransportConfig(base_url=BASE_URL, max_retries=0)

    async with AsyncTransport(config) as transport:
        await transport.upload(UPLOAD_URL, {"key": "uploads/a"}, "a.png", png_bytes())

    assert route.called
    assert "Authorization" not in route.calls.last.request.headers


def test_local_detection_still_works_without_a_key(keyless):
    """
    Verify that local on-device detection functions correctly without an API key.
    """
    from guard_client import Engine
    from guard_client.local import LocalRunner

    from .test_local import FakeEngine

    keyless._local = LocalRunner(engine=FakeEngine())

    result = keyless.analyze(png_bytes())

    assert result.engine is Engine.LOCAL


def test_analyze_rejects_guest_id(client):
    """Ensure that passing a guest ID to analyze raises a type error."""
    with pytest.raises(TypeError):
        client.analyze(png_bytes(), guest_id=SPACE_ID)
