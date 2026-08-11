"""
Tests for the reactions resource: feedback on an activity result.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
import respx

from guard_client import (
    ActivityDetail,
    ActivityResultItem,
    DetectionResult,
    Engine,
    GuardClient,
    GuardConflictError,
    GuardError,
    GuardNotFoundError,
    GuardServerError,
)

from .conftest import (
    ACTIVITY_ID,
    API_KEY,
    BASE_URL,
    REACTION_ID,
    SPACE_ID,
    TASK_ID,
    detail_response,
    reaction_response,
)

REACTIONS_URL = f"{BASE_URL}/api/v1/reactions/"


def body_of(route):
    """Extract and parse the JSON payload from the last call to a mock route."""
    return json.loads(route.calls.last.request.read())


def cloud_result(**overrides):
    """Build a mock DetectionResult mimicking what analyze() returns from the cloud."""
    payload = {
        "engine": Engine.CLOUD,
        "activity_id": ACTIVITY_ID,
        "results": [
            ActivityResultItem(task_id=TASK_ID, score=87, label="Deepfake"),
        ],
    }
    payload.update(overrides)
    return DetectionResult(**payload)


@respx.mock
def test_create_minimal(client):
    """Verify that creating a reaction sends the expected minimal payload."""
    route = respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(200, json=reaction_response())
    )

    reaction = client.reactions.create(
        activity_id=ACTIVITY_ID, task_id=TASK_ID, is_positive=True
    )

    assert body_of(route) == {
        "activity_id": str(ACTIVITY_ID),
        "task_id": str(TASK_ID),
        "is_positive": True,
    }
    assert reaction.id == REACTION_ID
    assert reaction.is_positive is True


@respx.mock
def test_create_with_every_field(client):
    """Verify that creating a reaction sends every optional field when provided."""
    route = respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(
            200,
            json=reaction_response(is_positive=False, key_value=2, description="wrong"),
        )
    )

    reaction = client.reactions.create(
        activity_id=ACTIVITY_ID,
        task_id=TASK_ID,
        is_positive=False,
        key_value=2,
        description="wrong",
    )

    assert body_of(route) == {
        "activity_id": str(ACTIVITY_ID),
        "task_id": str(TASK_ID),
        "is_positive": False,
        "key_value": 2,
        "description": "wrong",
    }
    assert reaction.key_value == 2
    assert reaction.description == "wrong"


@respx.mock
def test_create_omits_unset_optionals(client):
    """Ensure unset optional fields are omitted entirely rather than sent as null."""
    route = respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(200, json=reaction_response())
    )

    client.reactions.create(activity_id=ACTIVITY_ID, task_id=TASK_ID, is_positive=True)

    body = body_of(route)
    assert "key_value" not in body
    assert "description" not in body


@respx.mock
def test_create_strips_description(client):
    """
    Ensure the description string is stripped of leading and trailing whitespace before
    sending.
    """
    route = respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(200, json=reaction_response())
    )

    client.reactions.create(
        activity_id=ACTIVITY_ID,
        task_id=TASK_ID,
        is_positive=True,
        description="  padded  ",
    )

    assert body_of(route)["description"] == "padded"


@respx.mock
def test_create_treats_blank_description_as_unset(client):
    """Ensure a description composed only of whitespace is dropped entirely."""
    route = respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(200, json=reaction_response())
    )

    client.reactions.create(
        activity_id=ACTIVITY_ID, task_id=TASK_ID, is_positive=True, description="   "
    )

    assert "description" not in body_of(route)


@respx.mock
def test_create_sends_no_guest_header(client):
    """
    Ensure the client no longer sends a guest header, keeping requests authenticated.
    """
    route = respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(200, json=reaction_response())
    )

    client.reactions.create(activity_id=ACTIVITY_ID, task_id=TASK_ID, is_positive=True)

    assert "x-guest-id" not in route.calls.last.request.headers


def test_create_rejects_guest_id(client):
    """Verify that passing the removed guest_id argument raises a type error."""
    with pytest.raises(TypeError):
        client.reactions.create(
            activity_id=ACTIVITY_ID,
            task_id=TASK_ID,
            is_positive=True,
            guest_id=SPACE_ID,
        )


@respx.mock
def test_create_key_value_zero_is_sent(client):
    """
    Ensure a key_value of 0 is properly sent and not accidentally dropped as falsy.
    """
    route = respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(200, json=reaction_response(key_value=0))
    )

    client.reactions.create(
        activity_id=ACTIVITY_ID, task_id=TASK_ID, is_positive=False, key_value=0
    )

    assert body_of(route)["key_value"] == 0


@pytest.mark.parametrize("bad", ["yes", 1, "true", None])
@respx.mock
def test_create_rejects_non_bool_is_positive(client, bad):
    """Ensure is_positive strictly requires a boolean value."""
    route = respx.post(REACTIONS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="Expected a boolean"):
        client.reactions.create(
            activity_id=ACTIVITY_ID, task_id=TASK_ID, is_positive=bad
        )

    assert not route.called


@pytest.mark.parametrize("bad", ["2", 2.5, True, False, [2]])
@respx.mock
def test_create_rejects_non_int_key_value(client, bad):
    """Ensure key_value strictly requires an integer and rejects booleans."""
    route = respx.post(REACTIONS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="Invalid key_value"):
        client.reactions.create(
            activity_id=ACTIVITY_ID, task_id=TASK_ID, is_positive=True, key_value=bad
        )

    assert not route.called


@respx.mock
def test_create_rejects_overlong_description(client):
    """
    Verify that descriptions exceeding the maximum allowed length are rejected locally.
    """
    route = respx.post(REACTIONS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="at most 255 characters"):
        client.reactions.create(
            activity_id=ACTIVITY_ID,
            task_id=TASK_ID,
            is_positive=True,
            description="d" * 256,
        )

    assert not route.called


@respx.mock
def test_description_at_the_limit_is_accepted(client):
    """Ensure a description exactly at the maximum character limit is accepted."""
    route = respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(200, json=reaction_response())
    )

    client.reactions.create(
        activity_id=ACTIVITY_ID,
        task_id=TASK_ID,
        is_positive=True,
        description="d" * 255,
    )

    assert len(body_of(route)["description"]) == 255


@respx.mock
def test_create_for_extracts_ids(client):
    """Verify create_for correctly extracts the necessary IDs from a DetectionResult."""
    route = respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(200, json=reaction_response())
    )
    result = cloud_result()

    client.reactions.create_for(
        result, result.results[0], is_positive=False, key_value=1
    )

    assert body_of(route) == {
        "activity_id": str(ACTIVITY_ID),
        "task_id": str(TASK_ID),
        "is_positive": False,
        "key_value": 1,
    }


@respx.mock
def test_create_for_from_activity_detail(client):
    """Verify create_for works correctly using an ActivityDetail object."""
    route = respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(200, json=reaction_response())
    )
    detail = ActivityDetail.model_validate(detail_response())

    client.reactions.create_for(
        detail, detail.result_payload.results[0], is_positive=True
    )

    body = body_of(route)
    assert body["activity_id"] == str(ACTIVITY_ID)
    assert body["task_id"] == str(TASK_ID)


@respx.mock
def test_create_for_rejects_foreign_item(client):
    """
    Ensure create_for rejects an ActivityResultItem that does not belong to the result
    object.
    """
    route = respx.post(REACTIONS_URL).mock(return_value=httpx.Response(200))
    result = cloud_result()
    stranger = ActivityResultItem(task_id=uuid4(), score=1, label="Other")

    with pytest.raises(GuardError, match="is not part of this activity's results"):
        client.reactions.create_for(result, stranger, is_positive=True)

    assert not route.called


@respx.mock
def test_create_for_rejects_local_result(client):
    """
    Ensure create_for raises an error for local engine results since they lack server
    activity IDs.
    """
    route = respx.post(REACTIONS_URL).mock(return_value=httpx.Response(200))
    local = DetectionResult(
        engine=Engine.LOCAL,
        activity_id=None,
        results=[ActivityResultItem(task_id=TASK_ID, score=10, label="safe")],
    )

    with pytest.raises(GuardError, match="local engine"):
        client.reactions.create_for(local, local.results[0], is_positive=True)

    assert not route.called


@pytest.mark.parametrize(
    ("status", "expected", "detail"),
    [
        (409, GuardConflictError, "Cannot react to an activity result multiple times"),
        (404, GuardNotFoundError, "Activity not found"),
    ],
)
@respx.mock
def test_create_maps_server_errors(client, status, expected, detail):
    """Verify that specific server error statuses correctly map to custom exceptions."""
    respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(status, json={"detail": detail})
    )

    with pytest.raises(expected) as exc_info:
        client.reactions.create(
            activity_id=ACTIVITY_ID, task_id=TASK_ID, is_positive=True
        )

    assert exc_info.value.status_code == status
    assert detail in str(exc_info.value)


@respx.mock
def test_create_is_never_retried(monkeypatch):
    """
    Ensure reaction creation is never retried to prevent triggering a false conflict
    error.
    """
    monkeypatch.setattr("guard_client.transport.time.sleep", lambda _: None)
    route = respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(503, json={"detail": "unavailable"})
    )

    retrying = GuardClient(api_key=API_KEY, base_url=BASE_URL, max_retries=3)
    try:
        with pytest.raises(GuardServerError):
            retrying.reactions.create(
                activity_id=ACTIVITY_ID, task_id=TASK_ID, is_positive=True
            )
    finally:
        retrying.close()

    assert route.call_count == 1


@respx.mock
async def test_async_create(async_client):
    """Verify the async client correctly creates a reaction."""
    route = respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(200, json=reaction_response())
    )

    reaction = await async_client.reactions.create(
        activity_id=ACTIVITY_ID, task_id=TASK_ID, is_positive=True
    )

    assert body_of(route)["is_positive"] is True
    assert reaction.id == REACTION_ID


@respx.mock
async def test_async_create_for(async_client):
    """Verify the async client can create a reaction using existing objects."""
    route = respx.post(REACTIONS_URL).mock(
        return_value=httpx.Response(200, json=reaction_response())
    )
    result = cloud_result()

    await async_client.reactions.create_for(
        result, result.results[0], is_positive=True, description="good"
    )

    body = body_of(route)
    assert body["activity_id"] == str(ACTIVITY_ID)
    assert body["description"] == "good"


@respx.mock
async def test_async_create_for_rejects_local_result(async_client):
    """
    Ensure the async client raises an error for local results before hitting the
    network.
    """
    route = respx.post(REACTIONS_URL).mock(return_value=httpx.Response(200))
    local = DetectionResult(
        engine=Engine.LOCAL,
        results=[ActivityResultItem(task_id=TASK_ID, score=10, label="safe")],
    )

    with pytest.raises(GuardError, match="local engine"):
        await async_client.reactions.create_for(
            local, local.results[0], is_positive=True
        )

    assert not route.called
