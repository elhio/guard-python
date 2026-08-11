"""
Tests for the activity-shares resource: read and create.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
import respx

from guard_client import (
    ActivityDetail,
    ActivityResultItem,
    DetectionResult,
    Engine,
    GuardAPIError,
    GuardClient,
    GuardConflictError,
    GuardError,
    GuardNotFoundError,
    GuardServerError,
    Share,
    ShareOrder,
)

from .conftest import (
    ACTIVITY_ID,
    API_KEY,
    BASE_URL,
    ORG_ID,
    SHARE_ID,
    TASK_ID,
    USER_ID,
    detail_response,
    page_response,
    share_response,
)

SHARES_URL = f"{BASE_URL}/api/v1/activities/shares/"


def params_of(route):
    """Extract query parameters from the last request of a mock route."""
    return route.calls.last.request.url.params


def body_of(route):
    """Extract and parse the JSON request body from the last call to a mock route."""
    return json.loads(route.calls.last.request.read())


def cloud_result(**overrides):
    """Build a mock DetectionResult representing a completed cloud analysis."""
    payload = {
        "engine": Engine.CLOUD,
        "activity_id": ACTIVITY_ID,
        "results": [ActivityResultItem(task_id=TASK_ID, score=87, label="Deepfake")],
    }
    payload.update(overrides)
    return DetectionResult(**payload)


@respx.mock
def test_list_parses_shares_and_nested_result(client):
    """
    Verify that listing shares properly parses top-level attributes and nested result
    objects.
    """
    respx.get(SHARES_URL).mock(
        return_value=httpx.Response(
            200, json=page_response([share_response()], count=3)
        )
    )

    page = client.shares.list()

    assert page.count == 3
    share = page[0]
    assert share.id == SHARE_ID
    assert share.share_url == "https://elhio.com/s/abc123"
    assert share.task_name == "Deepfake"
    assert share.expires_in == 7
    assert isinstance(share.result, ActivityResultItem)
    assert share.result.score == 87
    assert share.result.label == "Deepfake"


@respx.mock
def test_list_url_has_trailing_slash(client):
    """Ensure that list requests hit the endpoint URL with a trailing slash."""
    route = respx.get(SHARES_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.shares.list()

    assert route.calls.last.request.url.path == "/api/v1/activities/shares/"


@respx.mock
def test_get_share(client):
    """Verify that fetching a single share by ID returns the expected share object."""
    route = respx.get(f"{SHARES_URL}{SHARE_ID}").mock(
        return_value=httpx.Response(200, json=share_response())
    )

    share = client.shares.get(SHARE_ID)

    assert route.called
    assert route.calls.last.request.url.path == f"/api/v1/activities/shares/{SHARE_ID}"
    assert share.id == SHARE_ID


@respx.mock
def test_list_passes_filters(client):
    """
    Ensure that provided list filters are correctly serialized into request query
    parameters.
    """
    route = respx.get(SHARES_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.shares.list(
        user_id=USER_ID,
        statuses=["active", "expired"],
        sort_by="created_at",
        sort_order="asc",
        skip=3,
        limit=15,
    )

    params = params_of(route)
    assert params["user_id"] == str(USER_ID)
    assert params.get_list("statuses") == ["active", "expired"]
    assert params["sort_by"] == "created_at"
    assert params["sort_order"] == "asc"
    assert params["skip"] == "3"
    assert params["limit"] == "15"


@respx.mock
def test_list_omits_unset_filters(client):
    """Verify that unset optional filters are excluded from query parameters."""
    route = respx.get(SHARES_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.shares.list()

    params = params_of(route)
    for absent in ("user_id", "organization_id", "statuses", "sort_by", "sort_order"):
        assert absent not in params


@respx.mock
def test_list_accepts_both_owner_filters(client):
    """
    Ensure that specifying both user_id and organization_id filters simultaneously is
    allowed.
    """
    route = respx.get(SHARES_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.shares.list(user_id=USER_ID, organization_id=ORG_ID)

    params = params_of(route)
    assert params["user_id"] == str(USER_ID)
    assert params["organization_id"] == str(ORG_ID)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"statuses": ["bogus"]}, "Invalid statuses"),
        ({"sort_by": "name"}, r"Invalid sort_by='name'.*'created_at'"),
        ({"sort_order": "sideways"}, "Invalid sort_order"),
        ({"limit": 0}, "Invalid limit=0"),
    ],
)
@respx.mock
def test_list_validates_before_sending(client, kwargs, message):
    """
    Ensure invalid list query arguments raise validation errors before sending network
    calls.
    """
    route = respx.get(SHARES_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match=message):
        client.shares.list(**kwargs)

    assert not route.called


@respx.mock
def test_enum_member_accepted(client):
    """Verify that sort_by accepts ShareOrder enum members directly."""
    route = respx.get(SHARES_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.shares.list(sort_by=ShareOrder.CREATED_AT)

    assert params_of(route)["sort_by"] == "created_at"


def _share(expired_at: datetime) -> Share:
    """Build a mock Share model instance with a specified expiration timestamp."""
    return Share.model_validate(share_response(expired_at=expired_at.isoformat()))


def test_is_expired_reflects_expired_at():
    """
    Verify that the is_expired property accurately calculates expiry relative to
    current time.
    """
    past = _share(datetime.now(timezone.utc) - timedelta(hours=1))
    future = _share(datetime.now(timezone.utc) + timedelta(hours=1))

    assert past.is_expired is True
    assert future.is_expired is False


def test_is_expired_treats_naive_timestamps_as_utc():
    """Ensure naive expiration datetime strings are assumed to be in UTC."""
    aware_past = datetime.now(timezone.utc) - timedelta(days=1)
    naive_past = aware_past.replace(
        tzinfo=None
    )  # as a server without an offset sends it
    share = Share.model_validate(share_response(expired_at=naive_past.isoformat()))

    assert share.is_expired is True


@respx.mock
def test_create_minimal(client):
    """Verify that creating a share with minimal parameters sends expected JSON."""
    route = respx.post(SHARES_URL).mock(
        return_value=httpx.Response(200, json=share_response())
    )

    share = client.shares.create(activity_id=ACTIVITY_ID, task_id=TASK_ID)

    assert body_of(route) == {
        "activity_id": str(ACTIVITY_ID),
        "task_id": str(TASK_ID),
    }
    assert share.share_url == "https://elhio.com/s/abc123"


@respx.mock
def test_create_url_has_trailing_slash(client):
    """Ensure that share creation requests include a trailing slash in the URL."""
    route = respx.post(SHARES_URL).mock(
        return_value=httpx.Response(200, json=share_response())
    )

    client.shares.create(activity_id=ACTIVITY_ID, task_id=TASK_ID)

    assert route.calls.last.request.url.path == "/api/v1/activities/shares/"


@respx.mock
def test_create_sends_expires_in(client):
    """Verify that expires_in parameter is correctly sent when specified."""
    route = respx.post(SHARES_URL).mock(
        return_value=httpx.Response(200, json=share_response(expires_in=3))
    )

    share = client.shares.create(activity_id=ACTIVITY_ID, task_id=TASK_ID, expires_in=3)

    assert body_of(route)["expires_in"] == 3
    assert share.expires_in == 3


@respx.mock
def test_create_omits_unset_expires_in(client):
    """Ensure expires_in parameter is omitted from request body when not provided."""
    route = respx.post(SHARES_URL).mock(
        return_value=httpx.Response(200, json=share_response())
    )

    client.shares.create(activity_id=ACTIVITY_ID, task_id=TASK_ID)

    assert "expires_in" not in body_of(route)


@pytest.mark.parametrize("bad", [0, 8, -1, 100])
@respx.mock
def test_create_rejects_out_of_range_expires_in(client, bad):
    """
    Ensure expires_in values outside the 1-7 day range are rejected before network
    requests.
    """
    route = respx.post(SHARES_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="Expected between 1 and 7 days"):
        client.shares.create(activity_id=ACTIVITY_ID, task_id=TASK_ID, expires_in=bad)

    assert not route.called


@pytest.mark.parametrize("bad", [True, False, "3", 3.5])
@respx.mock
def test_create_rejects_non_int_expires_in(client, bad):
    """Ensure non-integer values for expires_in are rejected before network requests."""
    route = respx.post(SHARES_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="Expected an integer number of days"):
        client.shares.create(activity_id=ACTIVITY_ID, task_id=TASK_ID, expires_in=bad)

    assert not route.called


@respx.mock
def test_create_for_from_detection_result(client):
    """
    Verify create_for correctly extracts activity and task IDs from a DetectionResult.
    """
    route = respx.post(SHARES_URL).mock(
        return_value=httpx.Response(200, json=share_response())
    )
    result = cloud_result()

    client.shares.create_for(result, result.results[0], expires_in=1)

    assert body_of(route) == {
        "activity_id": str(ACTIVITY_ID),
        "task_id": str(TASK_ID),
        "expires_in": 1,
    }


@respx.mock
def test_create_for_from_activity_detail(client):
    """Verify create_for correctly extracts IDs from an ActivityDetail object."""
    route = respx.post(SHARES_URL).mock(
        return_value=httpx.Response(200, json=share_response())
    )
    detail = ActivityDetail.model_validate(detail_response())

    client.shares.create_for(detail, detail.result_payload.results[0])

    assert body_of(route) == {
        "activity_id": str(ACTIVITY_ID),
        "task_id": str(TASK_ID),
    }


@respx.mock
def test_create_for_rejects_foreign_item(client):
    """
    Ensure create_for rejects an ActivityResultItem that does not belong to the source
    activity.
    """
    route = respx.post(SHARES_URL).mock(return_value=httpx.Response(200))
    result = cloud_result()
    stranger = ActivityResultItem(task_id=uuid4(), score=1, label="Other")

    with pytest.raises(GuardError, match="is not part of this activity's results"):
        client.shares.create_for(result, stranger)

    assert not route.called


@respx.mock
def test_create_for_rejects_local_result(client):
    """Ensure create_for raises an error when passed a local engine DetectionResult."""
    route = respx.post(SHARES_URL).mock(return_value=httpx.Response(200))
    local = DetectionResult(
        engine=Engine.LOCAL,
        results=[ActivityResultItem(task_id=TASK_ID, score=10, label="safe")],
    )

    with pytest.raises(GuardError, match="local engine"):
        client.shares.create_for(local, local.results[0])

    assert not route.called


@respx.mock
def test_create_for_rejects_activity_without_results(client):
    """
    Ensure create_for raises an error if the activity detail has no result payload.
    """
    route = respx.post(SHARES_URL).mock(return_value=httpx.Response(200))
    detail = ActivityDetail.model_validate(
        detail_response(status="processing", result_payload=None)
    )
    item = ActivityResultItem(task_id=TASK_ID, score=1, label="x")

    with pytest.raises(GuardError, match="available: none"):
        client.shares.create_for(detail, item)

    assert not route.called


@pytest.mark.parametrize(
    ("status", "expected", "detail"),
    [
        (409, GuardConflictError, "Cannot create share for an activity multiple times"),
        (404, GuardNotFoundError, "Activity not found"),
        (404, GuardNotFoundError, "Associated media file no longer exists"),
        (400, GuardAPIError, "Invalid task"),
        (400, GuardAPIError, "No media key associated with this task"),
    ],
)
@respx.mock
def test_create_maps_server_errors(client, status, expected, detail):
    """
    Verify that API server error responses are properly mapped to corresponding client
    exceptions.
    """
    respx.post(SHARES_URL).mock(
        return_value=httpx.Response(status, json={"detail": detail})
    )

    with pytest.raises(expected) as exc_info:
        client.shares.create(activity_id=ACTIVITY_ID, task_id=TASK_ID)

    assert exc_info.value.status_code == status
    assert detail in str(exc_info.value)


@respx.mock
def test_create_is_never_retried(monkeypatch):
    """
    Ensure share creation is not retried on server error to avoid duplicate conflicts.
    """
    monkeypatch.setattr("guard_client.transport.time.sleep", lambda _: None)
    route = respx.post(SHARES_URL).mock(
        return_value=httpx.Response(503, json={"detail": "unavailable"})
    )

    retrying = GuardClient(api_key=API_KEY, base_url=BASE_URL, max_retries=3)
    try:
        with pytest.raises(GuardServerError):
            retrying.shares.create(activity_id=ACTIVITY_ID, task_id=TASK_ID)
    finally:
        retrying.close()

    assert route.call_count == 1


@respx.mock
def test_iter_all_walks_pages(client):
    """Ensure iter_all correctly fetches across multiple pages of shares."""
    route = respx.get(SHARES_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=page_response(
                    [share_response(task_name=f"s{i}") for i in range(2)], 3
                ),
            ),
            httpx.Response(
                200, json=page_response([share_response(task_name="s2")], 3)
            ),
        ]
    )

    names = [s.task_name for s in client.shares.iter_all(page_size=2)]

    assert names == ["s0", "s1", "s2"]
    assert route.call_count == 2


@respx.mock
async def test_async_list_and_get(async_client):
    """Verify that the async client properly handles listing and getting shares."""
    respx.get(SHARES_URL).mock(
        return_value=httpx.Response(
            200, json=page_response([share_response()], count=1)
        )
    )
    respx.get(f"{SHARES_URL}{SHARE_ID}").mock(
        return_value=httpx.Response(200, json=share_response())
    )

    page = await async_client.shares.list()
    share = await async_client.shares.get(SHARE_ID)

    assert page[0].id == SHARE_ID
    assert share.share_url == "https://elhio.com/s/abc123"


@respx.mock
async def test_async_create_and_create_for(async_client):
    """
    Verify that the async client properly handles creating shares and using create_for.
    """
    route = respx.post(SHARES_URL).mock(
        return_value=httpx.Response(200, json=share_response())
    )

    await async_client.shares.create(activity_id=ACTIVITY_ID, task_id=TASK_ID)
    assert body_of(route)["activity_id"] == str(ACTIVITY_ID)

    detail = ActivityDetail.model_validate(detail_response())
    share = await async_client.shares.create_for(
        detail, detail.result_payload.results[0], expires_in=2
    )

    assert body_of(route)["expires_in"] == 2
    assert share.id == SHARE_ID


@respx.mock
async def test_async_create_for_rejects_foreign_item(async_client):
    """
    Ensure async create_for rejects an ActivityResultItem from a different activity
    before making a call.
    """
    route = respx.post(SHARES_URL).mock(return_value=httpx.Response(200))
    result = cloud_result()
    stranger = ActivityResultItem(task_id=uuid4(), score=1, label="Other")

    with pytest.raises(GuardError, match="is not part of this activity's results"):
        await async_client.shares.create_for(result, stranger)

    assert not route.called
