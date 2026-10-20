"""
Tests for the low-level prediction bindings, sync and async.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import respx

from guard_client import (
    MAX_HISTORY,
    GuardError,
    GuardTimeoutError,
    MediaType,
    PredictionFailedError,
    PredictionStatus,
)

from .conftest import (
    BASE_URL,
    PREDICTION_ID,
    SPACE_ID,
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
    """Polling tests should not actually wait."""
    monkeypatch.setattr("guard_client.predictions.time.sleep", lambda _: None)

    async def _async_sleep(_):
        return None

    monkeypatch.setattr("guard_client.predictions.asyncio.sleep", _async_sleep)


@respx.mock
def test_create_sends_expected_payload(client):
    """Verify that creating a prediction sends the correct JSON payload to the API."""
    route = respx.post(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json=create_response())
    )

    prediction = client.predictions.create(media_type=MediaType.PNG, media_size=1024)

    body = route.calls.last.request.read()
    assert b'"space_id":"11111111-1111-1111-1111-111111111111"' in body.replace(
        b" ", b""
    )
    assert b'"media_type":"image/png"' in body.replace(b" ", b"")
    assert prediction.id == PREDICTION_ID
    assert prediction.status is PredictionStatus.PENDING_UPLOAD
    assert prediction.upload_data.url == UPLOAD_URL


@respx.mock
def test_create_sends_declared_dimensions(client):
    """Verify the dimensions the API reserves tokens from go in the body."""
    route = respx.post(PREDICTIONS_URL).mock(
        return_value=httpx.Response(201, json=create_response(media_type="video/mp4"))
    )

    client.predictions.create(
        media_type=MediaType.MP4,
        media_size=2048,
        width=1920,
        height=1080,
        duration_seconds=12,
    )

    body = json.loads(route.calls.last.request.read())
    assert body["width"] == 1920
    assert body["height"] == 1080
    assert body["duration_seconds"] == 12.0


@respx.mock
def test_create_omits_undeclared_dimensions(client):
    """Ensure no dimension is sent unless the caller supplied it."""
    route = respx.post(PREDICTIONS_URL).mock(
        return_value=httpx.Response(201, json=create_response())
    )

    client.predictions.create(media_type=MediaType.PNG, media_size=1024)

    body = json.loads(route.calls.last.request.read())
    assert not {"width", "height", "duration_seconds"} & body.keys()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"width": 10}, "together"),
        ({"height": 10}, "together"),
        ({"width": 0, "height": 10}, "width"),
        ({"width": 10, "height": -1}, "height"),
        ({"width": 10.5, "height": 10}, "width"),
        ({"width": True, "height": 10}, "width"),
        ({"duration_seconds": -1}, "duration_seconds"),
    ],
)
def test_create_rejects_invalid_dimensions(client, kwargs, message):
    """Verify invalid dimensions are refused before any request is sent."""
    with respx.mock(assert_all_called=False) as router:
        route = router.post(PREDICTIONS_URL)
        with pytest.raises(GuardError, match=message):
            client.predictions.create(
                media_type=MediaType.PNG, media_size=1024, **kwargs
            )
        assert not route.called


@respx.mock
def test_create_accepts_string_media_type(client):
    """Ensure that string MIME types are correctly parsed into MediaType enums."""
    respx.post(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json=create_response())
    )

    prediction = client.predictions.create(media_type="image/png", media_size=10)

    assert prediction.media_type is MediaType.PNG


@respx.mock
def test_create_includes_owner_ids(client):
    """Verify that provided owner IDs are included while empty ones are omitted."""
    route = respx.post(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json=create_response())
    )

    client.predictions.create(media_type=MediaType.PNG, media_size=1, user_id=SPACE_ID)

    body = route.calls.last.request.read().decode()
    assert str(SPACE_ID) in body
    assert "account_id" not in body  # omitted rather than sent as null
    assert "guest_id" not in body  # the client never creates guest-owned predictions


@respx.mock
def test_create_overrides_default_space(client):
    """Ensure an explicitly provided space ID overrides the client default space."""
    other = "99999999-9999-9999-9999-999999999999"
    route = respx.post(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json=create_response())
    )

    client.predictions.create(media_type=MediaType.PNG, media_size=1, space_id=other)

    assert other in route.calls.last.request.read().decode()


def test_create_without_space_id_raises():
    """Check that omitting a space ID completely raises a local validation error."""
    from guard_client import GuardClient

    bare = GuardClient(api_key="k", base_url=BASE_URL)
    try:
        with pytest.raises(GuardError, match="space_id is required"):
            bare.predictions.create(media_type=MediaType.PNG, media_size=1)
    finally:
        bare.close()


@respx.mock
def test_upload_posts_fields_and_file(client):
    """Verify that the presigned upload fields precede the file bytes in the body."""
    route = respx.post(UPLOAD_URL).mock(return_value=httpx.Response(204))
    prediction = _make_prediction()

    client.predictions.upload(prediction.upload_data, png_bytes())

    body = route.calls.last.request.read()
    assert b'name="key"' in body
    assert b'name="file"' in body
    # presigned policy fields must precede the file part
    assert body.index(b'name="key"') < body.index(b'name="file"')


@respx.mock
def test_confirm(client):
    """
    Ensure that confirming an upload transitions the prediction status to processing.
    """
    route = respx.post(f"{PREDICTIONS_URL}/{PREDICTION_ID}/confirm").mock(
        return_value=httpx.Response(200, json=confirm_response())
    )

    prediction = client.predictions.confirm(PREDICTION_ID)

    assert route.called
    assert prediction.status is PredictionStatus.PROCESSING


@respx.mock
def test_confirm_sends_no_guest_header(client):
    """
    The guest flow is gone so every request goes out as the authenticated identity.
    """
    route = respx.post(f"{PREDICTIONS_URL}/{PREDICTION_ID}/confirm").mock(
        return_value=httpx.Response(200, json=confirm_response())
    )

    client.predictions.confirm(PREDICTION_ID)

    assert "x-guest-id" not in route.calls.last.request.headers


def test_confirm_rejects_guest_id(client):
    """Verify that passing the removed guest_id argument raises a TypeError."""
    with pytest.raises(TypeError):
        client.predictions.confirm(PREDICTION_ID, guest_id=SPACE_ID)


@respx.mock
def test_get_status(client):
    """Ensure that polling a prediction status returns the correct current state."""
    respx.get(f"{PREDICTIONS_URL}/{PREDICTION_ID}/status").mock(
        return_value=httpx.Response(200, json=status_response("processing"))
    )

    status = client.predictions.get_status(PREDICTION_ID)

    assert status.status is PredictionStatus.PROCESSING


@respx.mock
def test_get_returns_results(client):
    """Verify that fetching a prediction retrieves its full detailed results payload."""
    respx.get(f"{PREDICTIONS_URL}/{PREDICTION_ID}").mock(
        return_value=httpx.Response(200, json=detail_response())
    )

    detail = client.predictions.get(PREDICTION_ID)

    assert detail.result_payload is not None
    assert detail.result_payload.results[0].label == "Deepfake"
    assert detail.result_payload.results[0].score == 87


@respx.mock
def test_get_tolerates_missing_result(client):
    """A still-processing prediction has no result_payload yet."""
    respx.get(f"{PREDICTIONS_URL}/{PREDICTION_ID}").mock(
        return_value=httpx.Response(
            200, json=detail_response(status="processing", result_payload=None)
        )
    )

    detail = client.predictions.get(PREDICTION_ID)

    assert detail.result_payload is None


@respx.mock
def test_list_unwraps_data_envelope(client):
    """Ensure the list endpoint correctly unwraps the paginated data envelope."""
    respx.get(PREDICTIONS_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [confirm_response()], "count": 1}
        )
    )

    predictions = client.predictions.list(space_id=SPACE_ID)

    assert len(predictions) == 1
    assert predictions[0].id == PREDICTION_ID


@respx.mock
def test_list_parses_a_labs_prediction(client):
    """Ensure a prediction of a lab, which has no space, still parses."""
    lab_id = "77777777-7777-7777-7777-777777777777"
    respx.get(PREDICTIONS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    confirm_response(
                        space_id=None, space_name=None, lab_id=lab_id, lab_name="Lab"
                    )
                ],
                "count": 1,
            },
        )
    )

    (prediction,) = client.predictions.list()

    assert prediction.space_id is None
    assert str(prediction.lab_id) == lab_id
    assert prediction.lab_name == "Lab"


@respx.mock
def test_list_passes_statuses(client):
    """Verify that multiple status filters are correctly sent in the query."""
    route = respx.get(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    client.predictions.list(statuses=[PredictionStatus.COMPLETED, "failed"], limit=5)

    params = route.calls.last.request.url.params
    assert params.get_list("statuses") == ["completed", "failed"]
    assert params["limit"] == "5"


@respx.mock
def test_list_returns_page_with_count(client):
    """Ensure the returned page object correctly exposes the total match count."""
    respx.get(PREDICTIONS_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [confirm_response()], "count": 17}
        )
    )

    page = client.predictions.list()

    assert page.count == 17
    assert len(page) == 1
    assert page.has_more is True


@respx.mock
def test_list_passes_sorting(client):
    """Verify that sort_by and sort_order parameters are properly sent to the API."""
    route = respx.get(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    client.predictions.list(sort_by="created_at", sort_order="asc")

    params = route.calls.last.request.url.params
    assert params["sort_by"] == "created_at"
    assert params["sort_order"] == "asc"


@respx.mock
def test_list_passes_date_range(client):
    """Ensure that date ranges are correctly formatted as ISO-8601 strings."""
    route = respx.get(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    client.predictions.list(
        start_date=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        end_date="2026-02-01T00:00:00Z",
    )

    params = route.calls.last.request.url.params
    assert params["start_date"] == "2026-01-02T03:04:05+00:00"  # datetime -> ISO
    assert params["end_date"] == "2026-02-01T00:00:00Z"  # string passes through


@respx.mock
def test_list_accepts_both_owner_filters(client):
    """
    Unlike /spaces/, this route applies both owner filters independently.

    The read_predictions endpoint has no mutual-exclusion check, so imposing one
    client-side would block a query the API actually serves.
    """
    route = respx.get(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    client.predictions.list(user_id=SPACE_ID, organization_id=PREDICTION_ID)

    params = route.calls.last.request.url.params
    assert params["user_id"] == str(SPACE_ID)
    assert params["organization_id"] == str(PREDICTION_ID)


@pytest.mark.parametrize(
    "too_old",
    [
        datetime(2000, 1, 1, tzinfo=timezone.utc),
        datetime(2000, 1, 1),  # naive, treated as UTC like the server does
        date(2000, 1, 1),
        "2000-01-01T00:00:00+00:00",
        "2000-01-01T00:00:00Z",  # fromisoformat rejects "Z" before 3.11
        "2000-01-01",
    ],
)
@respx.mock
def test_list_rejects_start_date_beyond_retention(client, too_old):
    """The API keeps one year of history and returns 400 for anything older."""
    route = respx.get(PREDICTIONS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="only 365 days of history"):
        client.predictions.list(start_date=too_old)

    assert not route.called


@pytest.mark.parametrize(
    "recent",
    [
        None,
        datetime.now(timezone.utc) - timedelta(days=364),
        datetime.now(timezone.utc) - timedelta(days=365) + timedelta(hours=1),
        datetime.now(timezone.utc),
        "not-a-date",  # unparseable: leave format validation to the server
    ],
)
@respx.mock
def test_list_allows_dates_within_retention(client, recent):
    """Verify that dates falling within the API retention window are accepted."""
    route = respx.get(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    client.predictions.list(start_date=recent)

    assert route.called


@respx.mock
def test_retention_guard_has_clock_grace(client):
    """A boundary date must not be rejected by a client clock running slightly fast."""
    route = respx.get(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    # exactly one year back: the server's cutoff is later than ours, so it would accept
    client.predictions.list(start_date=datetime.now(timezone.utc) - MAX_HISTORY)

    assert route.called


@respx.mock
def test_end_date_is_not_retention_checked(client):
    """Only start_date has a retention rule since end_date is unconstrained."""
    route = respx.get(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    client.predictions.list(end_date=datetime(2000, 1, 1, tzinfo=timezone.utc))

    assert route.called


@respx.mock
def test_list_rejects_invalid_sort(client):
    """Ensure that an invalid sort order raises an error before hitting the network."""
    route = respx.get(PREDICTIONS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="Invalid sort_order"):
        client.predictions.list(sort_order="sideways")

    assert not route.called


@respx.mock
def test_iter_all_walks_pages(client):
    """Verify that iter_all correctly paginates through all available results."""
    route = respx.get(PREDICTIONS_URL).mock(
        side_effect=[
            httpx.Response(
                200, json={"data": [confirm_response(), confirm_response()], "count": 3}
            ),
            httpx.Response(200, json={"data": [confirm_response()], "count": 3}),
        ]
    )

    assert len(list(client.predictions.iter_all(page_size=2))) == 3
    assert route.call_count == 2


@respx.mock
def test_wait_until_done_polls_until_complete(client):
    """Ensure wait_until_done polls correctly until a completed status is returned."""
    route = respx.get(f"{PREDICTIONS_URL}/{PREDICTION_ID}/status").mock(
        side_effect=[
            httpx.Response(200, json=status_response("pending_upload")),
            httpx.Response(200, json=status_response("processing")),
            httpx.Response(200, json=status_response("completed")),
        ]
    )

    result = client.predictions.wait_until_done(PREDICTION_ID, interval=0.01)

    assert result.status is PredictionStatus.COMPLETED
    assert route.call_count == 3


@pytest.mark.parametrize("terminal", ["failed", "canceled"])
@respx.mock
def test_wait_until_done_raises_on_failure(client, terminal):
    """Verify that wait_until_done raises if the prediction fails or is canceled."""
    respx.get(f"{PREDICTIONS_URL}/{PREDICTION_ID}/status").mock(
        return_value=httpx.Response(200, json=status_response(terminal))
    )

    with pytest.raises(PredictionFailedError) as exc_info:
        client.predictions.wait_until_done(PREDICTION_ID, interval=0.01)

    assert exc_info.value.status == terminal
    assert exc_info.value.prediction_id == PREDICTION_ID


@respx.mock
def test_wait_until_done_times_out(client):
    """Ensure wait_until_done raises a timeout error if the polling deadline passes."""
    respx.get(f"{PREDICTIONS_URL}/{PREDICTION_ID}/status").mock(
        return_value=httpx.Response(200, json=status_response("processing"))
    )

    with pytest.raises(GuardTimeoutError) as exc_info:
        client.predictions.wait_until_done(PREDICTION_ID, interval=10.0, timeout=0.0)

    assert exc_info.value.prediction_id == PREDICTION_ID
    assert "processing" in str(exc_info.value)


@respx.mock
async def test_async_create_and_status(async_client):
    """Verify that the async client can successfully create and poll a prediction."""
    respx.post(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json=create_response())
    )
    respx.get(f"{PREDICTIONS_URL}/{PREDICTION_ID}/status").mock(
        return_value=httpx.Response(200, json=status_response("completed"))
    )

    prediction = await async_client.predictions.create(
        media_type=MediaType.PNG, media_size=8
    )
    status = await async_client.predictions.get_status(prediction.id)

    assert prediction.id == PREDICTION_ID
    assert status.status is PredictionStatus.COMPLETED


@respx.mock
async def test_async_wait_until_done_uses_asyncio_sleep(async_client, monkeypatch):
    """The async poller must never block the event loop with time.sleep."""
    calls = []

    async def _tracking_sleep(delay):
        calls.append(delay)

    monkeypatch.setattr("guard_client.predictions.asyncio.sleep", _tracking_sleep)

    def _boom(_):
        raise AssertionError("async path must not call time.sleep")

    monkeypatch.setattr("guard_client.predictions.time.sleep", _boom)

    respx.get(f"{PREDICTIONS_URL}/{PREDICTION_ID}/status").mock(
        side_effect=[
            httpx.Response(200, json=status_response("processing")),
            httpx.Response(200, json=status_response("completed")),
        ]
    )

    await async_client.predictions.wait_until_done(PREDICTION_ID, interval=0.25)

    assert calls == [0.25]


@respx.mock
async def test_async_wait_until_done_raises_on_failure(async_client):
    """Ensure the async wait_until_done raises an error if the prediction fails."""
    respx.get(f"{PREDICTIONS_URL}/{PREDICTION_ID}/status").mock(
        return_value=httpx.Response(200, json=status_response("failed"))
    )

    with pytest.raises(PredictionFailedError):
        await async_client.predictions.wait_until_done(PREDICTION_ID, interval=0.01)


@respx.mock
async def test_async_upload(async_client):
    """Verify that the async client successfully uploads media to the presigned URL."""
    route = respx.post(UPLOAD_URL).mock(return_value=httpx.Response(204))
    prediction = _make_prediction()

    await async_client.predictions.upload(prediction.upload_data, png_bytes())

    assert route.called


def _make_prediction():
    """Helper to build a valid PredictionCreateResponse for testing."""
    from guard_client import PredictionCreateResponse

    return PredictionCreateResponse.model_validate(create_response())
