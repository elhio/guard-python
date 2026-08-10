"""
End-to-end tests for the high-level analyze lifecycle.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from guard_client import (
    ActivityFailedError,
    AsyncGuardClient,
    Engine,
    GuardClient,
    GuardError,
    UnsupportedMediaTypeError,
)

from .conftest import (
    ACTIVITY_ID,
    API_KEY,
    BASE_URL,
    SPACE_ID,
    TASK_ID,
    UPLOAD_URL,
    confirm_response,
    create_response,
    detail_response,
    png_bytes,
    status_response,
)

ACTIVITIES_URL = f"{BASE_URL}/api/v1/activities/"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Ensure polling tests do not actually wait during execution."""
    monkeypatch.setattr("guard_client.activities.time.sleep", lambda _: None)

    async def _async_sleep(_):
        return None

    monkeypatch.setattr("guard_client.activities.asyncio.sleep", _async_sleep)


def mock_full_lifecycle(status: str = "completed"):
    """Wire up every route the happy path touches and return the route objects."""
    return {
        "create": respx.post(ACTIVITIES_URL).mock(
            return_value=httpx.Response(200, json=create_response())
        ),
        "upload": respx.post(UPLOAD_URL).mock(return_value=httpx.Response(204)),
        "confirm": respx.post(f"{ACTIVITIES_URL}{ACTIVITY_ID}/confirm").mock(
            return_value=httpx.Response(200, json=confirm_response())
        ),
        "status": respx.get(f"{ACTIVITIES_URL}{ACTIVITY_ID}/status").mock(
            return_value=httpx.Response(200, json=status_response(status))
        ),
        "detail": respx.get(f"{ACTIVITIES_URL}{ACTIVITY_ID}").mock(
            return_value=httpx.Response(200, json=detail_response(status))
        ),
    }


@respx.mock
def test_analyze_runs_full_lifecycle(client, png):
    """Verify that analyze executes the complete sequence of API calls."""
    routes = mock_full_lifecycle()

    result = client.analyze(png)

    for name, route in routes.items():
        assert route.called, f"{name} was never called"

    assert result.engine is Engine.CLOUD
    assert result.activity_id == ACTIVITY_ID
    assert result.max_score == 87
    assert result.results[0].label == "Deepfake"
    assert result.score_for(TASK_ID) == 87


@respx.mock
def test_cloud_results_leave_the_local_only_fields_unset(client, png):
    """Ensure cloud results leave local-only fields like detected and matches unset."""
    mock_full_lifecycle()

    result = client.analyze(png)

    assert result.results[0].detected is None
    assert result.results[0].matches is None


@respx.mock
def test_analyze_accepts_raw_bytes(client):
    """Verify that analyze correctly accepts raw bytes directly."""
    mock_full_lifecycle()

    result = client.analyze(png_bytes())

    assert result.activity_id == ACTIVITY_ID


@respx.mock
def test_analyze_accepts_file_object(client, png):
    """Verify that analyze correctly accepts an open binary file object."""
    mock_full_lifecycle()

    with open(png, "rb") as handle:
        result = client.analyze(handle)

    assert result.activity_id == ACTIVITY_ID


@respx.mock
def test_analyze_sends_detected_type_and_size(client, png):
    """Ensure the detected media type and size are included in the create request."""
    routes = mock_full_lifecycle()

    client.analyze(png)

    body = routes["create"].calls.last.request.read().decode().replace(" ", "")
    assert '"media_type":"image/png"' in body
    assert f'"media_size":{len(png_bytes())}' in body


@respx.mock
def test_analyze_upload_is_unauthenticated(client, png):
    """Verify the presigned S3 POST request does not carry the API bearer token."""
    routes = mock_full_lifecycle()

    client.analyze(png)

    upload_request = routes["upload"].calls.last.request
    assert "Authorization" not in upload_request.headers

    create_request = routes["create"].calls.last.request
    assert create_request.headers["Authorization"] == f"Bearer {API_KEY}"


@respx.mock
def test_analyze_orders_multipart_fields_before_file(client, png):
    """Ensure presigned policy fields precede the file part in the multipart body."""
    routes = mock_full_lifecycle()

    client.analyze(png)

    body = routes["upload"].calls.last.request.read()
    assert body.index(b'name="key"') < body.index(b'name="file"')


@respx.mock
def test_analyze_raises_on_failed_activity(client, png):
    """Verify an error is raised if the activity ends in a failed state."""
    mock_full_lifecycle(status="failed")

    with pytest.raises(ActivityFailedError) as exc_info:
        client.analyze(png)

    assert exc_info.value.status == "failed"


@respx.mock
def test_analyze_handles_empty_results(client, png):
    """Ensure analyze handles missing result payloads gracefully."""
    respx.post(ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, json=create_response())
    )
    respx.post(UPLOAD_URL).mock(return_value=httpx.Response(204))
    respx.post(f"{ACTIVITIES_URL}{ACTIVITY_ID}/confirm").mock(
        return_value=httpx.Response(200, json=confirm_response())
    )
    respx.get(f"{ACTIVITIES_URL}{ACTIVITY_ID}/status").mock(
        return_value=httpx.Response(200, json=status_response("completed"))
    )
    respx.get(f"{ACTIVITIES_URL}{ACTIVITY_ID}").mock(
        return_value=httpx.Response(200, json=detail_response(result_payload=None))
    )

    result = client.analyze(png_bytes())

    assert result.results == []
    assert result.max_score == 0
    assert result.score_for(TASK_ID) is None


@respx.mock
def test_analyze_rejects_unsupported_media_before_any_call(client, tmp_path):
    """Verify unsupported media types are rejected locally before network calls."""
    route = respx.post(ACTIVITIES_URL).mock(return_value=httpx.Response(200))
    bad = tmp_path / "doc.pdf"
    bad.write_bytes(b"%PDF-1.4 nope")

    with pytest.raises(UnsupportedMediaTypeError):
        client.analyze(bad)

    assert not route.called


@respx.mock
def test_analyze_space_id_override(client, png):
    """Ensure an explicitly passed space ID overrides the client default."""
    other = "99999999-9999-9999-9999-999999999999"
    routes = mock_full_lifecycle()

    client.analyze(png, space_id=other)

    assert other in routes["create"].calls.last.request.read().decode()


def test_analyze_without_space_id_raises(png):
    """Check that omitting a space ID completely raises a validation error."""
    bare = GuardClient(api_key=API_KEY, base_url=BASE_URL)
    try:
        with pytest.raises(GuardError, match="space_id is required"):
            bare.analyze(png)
    finally:
        bare.close()


def test_cloud_client_requires_api_key(monkeypatch):
    """Verify the cloud engine requires an API key."""
    monkeypatch.delenv("GUARD_API_KEY", raising=False)

    with pytest.raises(GuardError, match="API key is required"):
        GuardClient(space_id=SPACE_ID)


def test_local_client_needs_no_api_key(monkeypatch):
    """Verify the local engine initializes successfully without an API key."""
    monkeypatch.delenv("GUARD_API_KEY", raising=False)

    client = GuardClient(engine="local")

    assert client.engine is Engine.LOCAL


def test_unknown_engine_rejected():
    """Check that passing an invalid engine name raises an error."""
    with pytest.raises(GuardError, match=r"Invalid engine='quantum'.*'cloud', 'local'"):
        GuardClient(api_key=API_KEY, engine="quantum")


@respx.mock
async def test_async_analyze_runs_full_lifecycle(async_client, png):
    """Verify the async client executes the full analyze lifecycle."""
    routes = mock_full_lifecycle()

    result = await async_client.analyze(png)

    for name, route in routes.items():
        assert route.called, f"{name} was never called"

    assert result.engine is Engine.CLOUD
    assert result.activity_id == ACTIVITY_ID
    assert result.max_score == 87


@respx.mock
async def test_async_analyze_raises_on_canceled(async_client, png):
    """Ensure the async client raises an error if an activity is canceled."""
    mock_full_lifecycle(status="canceled")

    with pytest.raises(ActivityFailedError) as exc_info:
        await async_client.analyze(png)

    assert exc_info.value.status == "canceled"


async def test_async_cloud_client_requires_api_key(monkeypatch):
    """Verify the async cloud client requires an API key."""
    monkeypatch.delenv("GUARD_API_KEY", raising=False)

    with pytest.raises(GuardError, match="API key is required"):
        AsyncGuardClient(space_id=SPACE_ID)
