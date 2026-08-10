"""
Tests for the low-level activity bindings, sync and async.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import respx

from guard_client import (
    MAX_HISTORY,
    ActivityFailedError,
    ActivityStatus,
    GuardError,
    GuardTimeoutError,
    MediaType,
)

from .conftest import (
    ACTIVITY_ID,
    BASE_URL,
    SPACE_ID,
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
    """Polling tests should not actually wait."""
    monkeypatch.setattr("guard_client.activities.time.sleep", lambda _: None)

    async def _async_sleep(_):
        return None

    monkeypatch.setattr("guard_client.activities.asyncio.sleep", _async_sleep)


@respx.mock
def test_create_sends_expected_payload(client):
    """Verify that creating an activity sends the correct JSON payload to the API."""
    route = respx.post(ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, json=create_response())
    )

    activity = client.activities.create(media_type=MediaType.PNG, media_size=1024)

    body = route.calls.last.request.read()
    assert b'"space_id":"11111111-1111-1111-1111-111111111111"' in body.replace(
        b" ", b""
    )
    assert b'"media_type":"image/png"' in body.replace(b" ", b"")
    assert activity.id == ACTIVITY_ID
    assert activity.status is ActivityStatus.PENDING_UPLOAD
    assert activity.upload_data.url == UPLOAD_URL


@respx.mock
def test_create_accepts_string_media_type(client):
    """Ensure that string MIME types are correctly parsed into MediaType enums."""
    respx.post(ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, json=create_response())
    )

    activity = client.activities.create(media_type="image/png", media_size=10)

    assert activity.media_type is MediaType.PNG


@respx.mock
def test_create_includes_owner_ids(client):
    """Verify that provided owner IDs are included while empty ones are omitted."""
    route = respx.post(ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, json=create_response())
    )

    client.activities.create(media_type=MediaType.PNG, media_size=1, user_id=SPACE_ID)

    body = route.calls.last.request.read().decode()
    assert str(SPACE_ID) in body
    assert "account_id" not in body  # omitted rather than sent as null
    assert "guest_id" not in body  # the client never creates guest-owned activities


@respx.mock
def test_create_overrides_default_space(client):
    """Ensure an explicitly provided space ID overrides the client default space."""
    other = "99999999-9999-9999-9999-999999999999"
    route = respx.post(ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, json=create_response())
    )

    client.activities.create(media_type=MediaType.PNG, media_size=1, space_id=other)

    assert other in route.calls.last.request.read().decode()


def test_create_without_space_id_raises():
    """Check that omitting a space ID completely raises a local validation error."""
    from guard_client import GuardClient

    bare = GuardClient(api_key="k", base_url=BASE_URL)
    try:
        with pytest.raises(GuardError, match="space_id is required"):
            bare.activities.create(media_type=MediaType.PNG, media_size=1)
    finally:
        bare.close()


@respx.mock
def test_upload_posts_fields_and_file(client):
    """Verify that the presigned upload fields precede the file bytes in the body."""
    route = respx.post(UPLOAD_URL).mock(return_value=httpx.Response(204))
    activity = _make_activity()

    client.activities.upload(activity.upload_data, png_bytes())

    body = route.calls.last.request.read()
    assert b'name="key"' in body
    assert b'name="file"' in body
    # presigned policy fields must precede the file part
    assert body.index(b'name="key"') < body.index(b'name="file"')


@respx.mock
def test_confirm(client):
    """
    Ensure that confirming an upload transitions the activity status to processing.
    """
    route = respx.post(f"{ACTIVITIES_URL}{ACTIVITY_ID}/confirm").mock(
        return_value=httpx.Response(200, json=confirm_response())
    )

    activity = client.activities.confirm(ACTIVITY_ID)

    assert route.called
    assert activity.status is ActivityStatus.PROCESSING


@respx.mock
def test_confirm_sends_no_guest_header(client):
    """
    The guest flow is gone so every request goes out as the authenticated identity.
    """
    route = respx.post(f"{ACTIVITIES_URL}{ACTIVITY_ID}/confirm").mock(
        return_value=httpx.Response(200, json=confirm_response())
    )

    client.activities.confirm(ACTIVITY_ID)

    assert "x-guest-id" not in route.calls.last.request.headers


def test_confirm_rejects_guest_id(client):
    """Verify that passing the removed guest_id argument raises a TypeError."""
    with pytest.raises(TypeError):
        client.activities.confirm(ACTIVITY_ID, guest_id=SPACE_ID)


@respx.mock
def test_get_status(client):
    """Ensure that polling an activity status returns the correct current state."""
    respx.get(f"{ACTIVITIES_URL}{ACTIVITY_ID}/status").mock(
        return_value=httpx.Response(200, json=status_response("processing"))
    )

    status = client.activities.get_status(ACTIVITY_ID)

    assert status.status is ActivityStatus.PROCESSING


@respx.mock
def test_get_returns_results(client):
    """Verify that fetching an activity retrieves its full detailed results payload."""
    respx.get(f"{ACTIVITIES_URL}{ACTIVITY_ID}").mock(
        return_value=httpx.Response(200, json=detail_response())
    )

    detail = client.activities.get(ACTIVITY_ID)

    assert detail.result_payload is not None
    assert detail.result_payload.results[0].label == "Deepfake"
    assert detail.result_payload.results[0].score == 87


@respx.mock
def test_get_tolerates_missing_result(client):
    """A still-processing activity has no result_payload yet."""
    respx.get(f"{ACTIVITIES_URL}{ACTIVITY_ID}").mock(
        return_value=httpx.Response(
            200, json=detail_response(status="processing", result_payload=None)
        )
    )

    detail = client.activities.get(ACTIVITY_ID)

    assert detail.result_payload is None


@respx.mock
def test_list_unwraps_data_envelope(client):
    """Ensure the list endpoint correctly unwraps the paginated data envelope."""
    respx.get(ACTIVITIES_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [confirm_response()], "count": 1}
        )
    )

    activities = client.activities.list(space_id=SPACE_ID)

    assert len(activities) == 1
    assert activities[0].id == ACTIVITY_ID


@respx.mock
def test_list_passes_statuses(client):
    """Verify that multiple status filters are correctly sent in the query."""
    route = respx.get(ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    client.activities.list(statuses=[ActivityStatus.COMPLETED, "failed"], limit=5)

    params = route.calls.last.request.url.params
    assert params.get_list("statuses") == ["completed", "failed"]
    assert params["limit"] == "5"


@respx.mock
def test_list_returns_page_with_count(client):
    """Ensure the returned page object correctly exposes the total match count."""
    respx.get(ACTIVITIES_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [confirm_response()], "count": 17}
        )
    )

    page = client.activities.list()

    assert page.count == 17
    assert len(page) == 1
    assert page.has_more is True


@respx.mock
def test_list_passes_sorting(client):
    """Verify that sort_by and sort_order parameters are properly sent to the API."""
    route = respx.get(ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    client.activities.list(sort_by="created_at", sort_order="asc")

    params = route.calls.last.request.url.params
    assert params["sort_by"] == "created_at"
    assert params["sort_order"] == "asc"


@respx.mock
def test_list_passes_date_range(client):
    """Ensure that date ranges are correctly formatted as ISO-8601 strings."""
    route = respx.get(ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    client.activities.list(
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

    The read_activities endpoint has no mutual-exclusion check, so imposing one
    client-side would block a query the API actually serves.
    """
    route = respx.get(ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    client.activities.list(user_id=SPACE_ID, organization_id=ACTIVITY_ID)

    params = route.calls.last.request.url.params
    assert params["user_id"] == str(SPACE_ID)
    assert params["organization_id"] == str(ACTIVITY_ID)


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
    route = respx.get(ACTIVITIES_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="only 365 days of history"):
        client.activities.list(start_date=too_old)

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
    route = respx.get(ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    client.activities.list(start_date=recent)

    assert route.called


@respx.mock
def test_retention_guard_has_clock_grace(client):
    """A boundary date must not be rejected by a client clock running slightly fast."""
    route = respx.get(ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    # exactly one year back: the server's cutoff is later than ours, so it would accept
    client.activities.list(start_date=datetime.now(timezone.utc) - MAX_HISTORY)

    assert route.called


@respx.mock
def test_end_date_is_not_retention_checked(client):
    """Only start_date has a retention rule since end_date is unconstrained."""
    route = respx.get(ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    client.activities.list(end_date=datetime(2000, 1, 1, tzinfo=timezone.utc))

    assert route.called


@respx.mock
def test_list_rejects_invalid_sort(client):
    """Ensure that an invalid sort order raises an error before hitting the network."""
    route = respx.get(ACTIVITIES_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="Invalid sort_order"):
        client.activities.list(sort_order="sideways")

    assert not route.called


@respx.mock
def test_iter_all_walks_pages(client):
    """Verify that iter_all correctly paginates through all available results."""
    route = respx.get(ACTIVITIES_URL).mock(
        side_effect=[
            httpx.Response(
                200, json={"data": [confirm_response(), confirm_response()], "count": 3}
            ),
            httpx.Response(200, json={"data": [confirm_response()], "count": 3}),
        ]
    )

    assert len(list(client.activities.iter_all(page_size=2))) == 3
    assert route.call_count == 2


@respx.mock
def test_wait_until_done_polls_until_complete(client):
    """Ensure wait_until_done polls correctly until a completed status is returned."""
    route = respx.get(f"{ACTIVITIES_URL}{ACTIVITY_ID}/status").mock(
        side_effect=[
            httpx.Response(200, json=status_response("pending_upload")),
            httpx.Response(200, json=status_response("processing")),
            httpx.Response(200, json=status_response("completed")),
        ]
    )

    result = client.activities.wait_until_done(ACTIVITY_ID, interval=0.01)

    assert result.status is ActivityStatus.COMPLETED
    assert route.call_count == 3


@pytest.mark.parametrize("terminal", ["failed", "canceled"])
@respx.mock
def test_wait_until_done_raises_on_failure(client, terminal):
    """Verify that wait_until_done raises an error if the activity fails or cancels."""
    respx.get(f"{ACTIVITIES_URL}{ACTIVITY_ID}/status").mock(
        return_value=httpx.Response(200, json=status_response(terminal))
    )

    with pytest.raises(ActivityFailedError) as exc_info:
        client.activities.wait_until_done(ACTIVITY_ID, interval=0.01)

    assert exc_info.value.status == terminal
    assert exc_info.value.activity_id == ACTIVITY_ID


@respx.mock
def test_wait_until_done_times_out(client):
    """Ensure wait_until_done raises a timeout error if the polling deadline passes."""
    respx.get(f"{ACTIVITIES_URL}{ACTIVITY_ID}/status").mock(
        return_value=httpx.Response(200, json=status_response("processing"))
    )

    with pytest.raises(GuardTimeoutError) as exc_info:
        client.activities.wait_until_done(ACTIVITY_ID, interval=10.0, timeout=0.0)

    assert exc_info.value.activity_id == ACTIVITY_ID
    assert "processing" in str(exc_info.value)


@respx.mock
async def test_async_create_and_status(async_client):
    """Verify that the async client can successfully create and poll an activity."""
    respx.post(ACTIVITIES_URL).mock(
        return_value=httpx.Response(200, json=create_response())
    )
    respx.get(f"{ACTIVITIES_URL}{ACTIVITY_ID}/status").mock(
        return_value=httpx.Response(200, json=status_response("completed"))
    )

    activity = await async_client.activities.create(
        media_type=MediaType.PNG, media_size=8
    )
    status = await async_client.activities.get_status(activity.id)

    assert activity.id == ACTIVITY_ID
    assert status.status is ActivityStatus.COMPLETED


@respx.mock
async def test_async_wait_until_done_uses_asyncio_sleep(async_client, monkeypatch):
    """The async poller must never block the event loop with time.sleep."""
    calls = []

    async def _tracking_sleep(delay):
        calls.append(delay)

    monkeypatch.setattr("guard_client.activities.asyncio.sleep", _tracking_sleep)

    def _boom(_):
        raise AssertionError("async path must not call time.sleep")

    monkeypatch.setattr("guard_client.activities.time.sleep", _boom)

    respx.get(f"{ACTIVITIES_URL}{ACTIVITY_ID}/status").mock(
        side_effect=[
            httpx.Response(200, json=status_response("processing")),
            httpx.Response(200, json=status_response("completed")),
        ]
    )

    await async_client.activities.wait_until_done(ACTIVITY_ID, interval=0.25)

    assert calls == [0.25]


@respx.mock
async def test_async_wait_until_done_raises_on_failure(async_client):
    """Ensure the async wait_until_done raises an error if the activity fails."""
    respx.get(f"{ACTIVITIES_URL}{ACTIVITY_ID}/status").mock(
        return_value=httpx.Response(200, json=status_response("failed"))
    )

    with pytest.raises(ActivityFailedError):
        await async_client.activities.wait_until_done(ACTIVITY_ID, interval=0.01)


@respx.mock
async def test_async_upload(async_client):
    """Verify that the async client successfully uploads media to the presigned URL."""
    route = respx.post(UPLOAD_URL).mock(return_value=httpx.Response(204))
    activity = _make_activity()

    await async_client.activities.upload(activity.upload_data, png_bytes())

    assert route.called


def _make_activity():
    """Helper to build a valid ActivityCreateResponse for testing."""
    from guard_client import ActivityCreateResponse

    return ActivityCreateResponse.model_validate(create_response())
