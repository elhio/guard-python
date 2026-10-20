"""
End-to-end tests for the high-level analyze lifecycle.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from guard_client import (
    AsyncGuardClient,
    Engine,
    GuardClient,
    GuardError,
    MediaType,
    PredictionFailedError,
    UnsupportedMediaTypeError,
)
from guard_client.probe import MediaInfo

from .conftest import (
    API_KEY,
    BASE_URL,
    PREDICTION_ID,
    SPACE_ID,
    TARGET_ID,
    UPLOAD_URL,
    confirm_response,
    create_response,
    detail_response,
    png_bytes,
    status_response,
)

PREDICTIONS_URL = f"{BASE_URL}/api/v1/predictions"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Ensure polling tests do not actually wait during execution."""
    monkeypatch.setattr("guard_client.predictions.time.sleep", lambda _: None)

    async def _async_sleep(_):
        return None

    monkeypatch.setattr("guard_client.predictions.asyncio.sleep", _async_sleep)


def mock_full_lifecycle(status: str = "completed"):
    """Wire up every route the happy path touches and return the route objects."""
    return {
        "create": respx.post(PREDICTIONS_URL).mock(
            return_value=httpx.Response(200, json=create_response())
        ),
        "upload": respx.post(UPLOAD_URL).mock(return_value=httpx.Response(204)),
        "confirm": respx.post(f"{PREDICTIONS_URL}/{PREDICTION_ID}/confirm").mock(
            return_value=httpx.Response(200, json=confirm_response())
        ),
        "status": respx.get(f"{PREDICTIONS_URL}/{PREDICTION_ID}/status").mock(
            return_value=httpx.Response(200, json=status_response(status))
        ),
        "detail": respx.get(f"{PREDICTIONS_URL}/{PREDICTION_ID}").mock(
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
    assert result.prediction_id == PREDICTION_ID
    assert result.max_score == 87
    assert result.results[0].label == "Deepfake"
    assert result.score_for(TARGET_ID) == 87


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

    assert result.prediction_id == PREDICTION_ID


@respx.mock
def test_analyze_accepts_file_object(client, png):
    """Verify that analyze correctly accepts an open binary file object."""
    mock_full_lifecycle()

    with open(png, "rb") as handle:
        result = client.analyze(handle)

    assert result.prediction_id == PREDICTION_ID


@respx.mock
def test_analyze_sends_detected_type_and_size(client, png):
    """Ensure the detected media type and size are included in the create request."""
    routes = mock_full_lifecycle()

    client.analyze(png)

    body = routes["create"].calls.last.request.read().decode().replace(" ", "")
    assert '"media_type":"image/png"' in body
    assert f'"media_size":{len(png_bytes())}' in body


def create_body(routes):
    """The JSON body of the create request."""
    return json.loads(routes["create"].calls.last.request.read())


@respx.mock
def test_analyze_declares_the_probed_dimensions(client, png):
    """Ensure an image's dimensions are declared, and no duration for a still."""
    routes = mock_full_lifecycle()

    client.analyze(png)

    body = create_body(routes)
    assert (body["width"], body["height"]) == (1, 1)
    assert "duration_seconds" not in body


@respx.mock
def test_analyze_declares_a_videos_duration(client, monkeypatch):
    """Verify a video declares its duration along with its dimensions."""
    monkeypatch.setattr(
        "guard_client.client.probe_media",
        lambda *_, **__: MediaInfo(
            media_type=MediaType.MP4,
            width=1280,
            height=720,
            duration_seconds=4.5,
            frames=5,
        ),
    )
    routes = mock_full_lifecycle()

    client.analyze(b"\x00" * 64, media_type="video/mp4")

    body = create_body(routes)
    assert (body["width"], body["height"], body["duration_seconds"]) == (1280, 720, 4.5)


@respx.mock
def test_analyze_without_readable_dimensions_still_runs(client, png, monkeypatch):
    """Ensure media the probe cannot read is analyzed without declared dimensions."""

    def unreadable(*_, **__):
        raise GuardError("cannot read the header")

    monkeypatch.setattr("guard_client.client.probe_media", unreadable)
    routes = mock_full_lifecycle()

    result = client.analyze(png)

    assert not {"width", "height", "duration_seconds"} & create_body(routes).keys()
    assert result.prediction_id == PREDICTION_ID


@respx.mock
async def test_async_analyze_declares_the_probed_dimensions(async_client, png):
    """Verify the async client declares dimensions as the sync one does."""
    routes = mock_full_lifecycle()

    await async_client.analyze(png)

    assert (create_body(routes)["width"], create_body(routes)["height"]) == (1, 1)


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
def test_analyze_raises_on_failed_prediction(client, png):
    """Verify an error is raised if the prediction ends in a failed state."""
    mock_full_lifecycle(status="failed")

    with pytest.raises(PredictionFailedError) as exc_info:
        client.analyze(png)

    assert exc_info.value.status == "failed"


@respx.mock
def test_analyze_handles_empty_results(client, png):
    """Ensure analyze handles missing result payloads gracefully."""
    respx.post(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json=create_response())
    )
    respx.post(UPLOAD_URL).mock(return_value=httpx.Response(204))
    respx.post(f"{PREDICTIONS_URL}/{PREDICTION_ID}/confirm").mock(
        return_value=httpx.Response(200, json=confirm_response())
    )
    respx.get(f"{PREDICTIONS_URL}/{PREDICTION_ID}/status").mock(
        return_value=httpx.Response(200, json=status_response("completed"))
    )
    respx.get(f"{PREDICTIONS_URL}/{PREDICTION_ID}").mock(
        return_value=httpx.Response(200, json=detail_response(result_payload=None))
    )

    result = client.analyze(png_bytes())

    assert result.results == []
    assert result.max_score == 0
    assert result.score_for(TARGET_ID) is None


@respx.mock
def test_analyze_rejects_unsupported_media_before_any_call(client, tmp_path):
    """Verify unsupported media types are rejected locally before network calls."""
    route = respx.post(PREDICTIONS_URL).mock(return_value=httpx.Response(200))
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
    assert result.prediction_id == PREDICTION_ID
    assert result.max_score == 87


@respx.mock
async def test_async_analyze_raises_on_canceled(async_client, png):
    """Ensure the async client raises an error if a prediction is canceled."""
    mock_full_lifecycle(status="canceled")

    with pytest.raises(PredictionFailedError) as exc_info:
        await async_client.analyze(png)

    assert exc_info.value.status == "canceled"


async def test_async_cloud_client_requires_api_key(monkeypatch):
    """Verify the async cloud client requires an API key."""
    monkeypatch.delenv("GUARD_API_KEY", raising=False)

    with pytest.raises(GuardError, match="API key is required"):
        AsyncGuardClient(space_id=SPACE_ID)
