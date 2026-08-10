"""
Tests for the runners resource: read, create and delete.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from guard_client import (
    GuardClient,
    GuardConflictError,
    GuardError,
    GuardNotFoundError,
    GuardPaymentRequiredError,
    GuardServerError,
    RunnerOrder,
    RunnerStatus,
)

from .conftest import (
    API_KEY,
    BASE_URL,
    ORG_ID,
    PREDICTOR_ID,
    RUNNER_ID,
    SPACE_ID,
    page_response,
    runner_response,
)

RUNNERS_URL = f"{BASE_URL}/api/v1/runners/"
OTHER_ORG = "99999999-9999-9999-9999-999999999999"


@pytest.fixture
def org_client(isolate_env):
    """Provide a client preconfigured with a default organization ID."""
    with GuardClient(
        api_key=API_KEY, organization_id=ORG_ID, base_url=BASE_URL, max_retries=0
    ) as c:
        yield c


def params_of(route):
    """Extract query parameters from the last request of a mock route."""
    return route.calls.last.request.url.params


def body_of(route):
    """Extract and parse the JSON request body from the last call to a mock route."""
    return json.loads(route.calls.last.request.read())


@respx.mock
def test_list_parses_runners(org_client):
    """
    Verify that listing runners correctly parses their attributes from the response.
    """
    respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(
            200, json=page_response([runner_response()], count=2)
        )
    )

    page = org_client.runners.list()

    assert page.count == 2
    runner = page[0]
    assert runner.id == RUNNER_ID
    assert runner.status is RunnerStatus.RUNNING
    assert runner.name == "runner-1"
    assert runner.predictor_id == PREDICTOR_ID
    assert runner.organization_name == "Test Org"
    assert runner.terminated_at is None


@respx.mock
def test_list_parses_terminated_runner(org_client):
    """Ensure that runners with a terminated status parse successfully."""
    respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(
            200, json=page_response([runner_response(status="terminated")], count=1)
        )
    )

    assert org_client.runners.list()[0].status is RunnerStatus.TERMINATED


@respx.mock
def test_get_runner(org_client):
    """Verify that fetching a single runner by ID returns the correct runner object."""
    route = respx.get(f"{RUNNERS_URL}{RUNNER_ID}").mock(
        return_value=httpx.Response(200, json=runner_response())
    )

    runner = org_client.runners.get(RUNNER_ID)

    assert route.called
    assert runner.id == RUNNER_ID


@respx.mock
def test_list_passes_filters(org_client):
    """
    Ensure that all provided runner list filters are correctly sent as query parameters.
    """
    route = respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    org_client.runners.list(
        predictor_id=PREDICTOR_ID,
        statuses=["running", RunnerStatus.PENDING],
        sort_by="name",
        sort_order="desc",
        skip=5,
        limit=20,
    )

    params = params_of(route)
    assert params["organization_id"] == str(ORG_ID)
    assert params["predictor_id"] == str(PREDICTOR_ID)
    assert params.get_list("statuses") == ["running", "pending"]
    assert params["sort_by"] == "name"
    assert params["sort_order"] == "desc"
    assert params["skip"] == "5"
    assert params["limit"] == "20"


@respx.mock
def test_list_omits_unset_filters(org_client):
    """
    Verify that optional filters not explicitly provided are omitted from the request.
    """
    route = respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    org_client.runners.list()

    params = params_of(route)
    assert params["organization_id"] == str(ORG_ID)
    for absent in ("predictor_id", "statuses", "sort_by", "sort_order"):
        assert absent not in params


@respx.mock
def test_organization_id_comes_from_client_default(org_client):
    """Ensure requests default to the organization ID configured on the client."""
    route = respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    org_client.runners.list()

    assert params_of(route)["organization_id"] == str(ORG_ID)


@respx.mock
def test_per_call_organization_id_overrides_default(org_client):
    """Verify that an explicit per-call organization ID overrides the client default."""
    route = respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    org_client.runners.list(organization_id=OTHER_ORG)

    assert params_of(route)["organization_id"] == OTHER_ORG


@respx.mock
def test_missing_organization_id_raises_before_sending(client):
    """Ensure an error is raised locally if no organization ID is available anywhere."""
    route = respx.get(RUNNERS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="GUARD_ORGANIZATION_ID"):
        client.runners.list()

    assert not route.called


@respx.mock
def test_organization_id_from_dotenv(isolate_env):
    """Verify that the organization ID is successfully loaded from a .env file."""
    from pathlib import Path

    Path(".env").write_text(f"GUARD_API_KEY=k\nGUARD_ORGANIZATION_ID={ORG_ID}\n")
    route = respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    with GuardClient(base_url=BASE_URL) as c:
        c.runners.list()

    assert params_of(route)["organization_id"] == str(ORG_ID)


@respx.mock
def test_terminated_is_not_filterable(org_client):
    """
    Ensure filtering by 'terminated' is locally rejected since the API does not support
    it.
    """
    route = respx.get(RUNNERS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match=r"Invalid statuses='terminated'"):
        org_client.runners.list(statuses=["terminated"])

    assert not route.called


@respx.mock
def test_terminated_rejection_lists_the_filterable_values(org_client):
    """
    Verify that rejecting an invalid filter status lists the valid filter options in the
    message.
    """
    respx.get(RUNNERS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError) as exc_info:
        org_client.runners.list(statuses=[RunnerStatus.TERMINATED])

    message = str(exc_info.value)
    for allowed in ("pending", "running", "draining", "failed"):
        assert allowed in message
    assert "'terminated'," not in message  # not offered as an option


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"statuses": ["bogus"]}, "Invalid statuses"),
        ({"sort_by": "bogus"}, r"Invalid sort_by='bogus'.*'name', 'created_at'"),
        ({"sort_order": "sideways"}, "Invalid sort_order"),
        ({"skip": -1}, "Invalid skip=-1"),
        ({"limit": 101}, "Invalid limit=101"),
    ],
)
@respx.mock
def test_list_validates_before_sending(org_client, kwargs, message):
    """
    Ensure that invalid pagination or sorting options raise an error locally before
    network calls.
    """
    route = respx.get(RUNNERS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match=message):
        org_client.runners.list(**kwargs)

    assert not route.called


@respx.mock
def test_enum_member_accepted(org_client):
    """Verify that sorting enum members can be passed directly as arguments."""
    route = respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    org_client.runners.list(sort_by=RunnerOrder.CREATED_AT)

    assert params_of(route)["sort_by"] == "created_at"


@respx.mock
def test_create_minimal(org_client):
    """
    Verify that creating a runner with minimal arguments sends the expected payload.
    """
    route = respx.post(RUNNERS_URL).mock(
        return_value=httpx.Response(200, json=runner_response(status="pending"))
    )

    runner = org_client.runners.create(predictor_id=PREDICTOR_ID)

    assert body_of(route) == {
        "predictor_id": str(PREDICTOR_ID),
        "organization_id": str(ORG_ID),
        "is_default": False,
    }
    assert runner.status is RunnerStatus.PENDING


@respx.mock
def test_create_sends_is_default(org_client):
    """Ensure the is_default flag is correctly included in the creation payload."""
    route = respx.post(RUNNERS_URL).mock(
        return_value=httpx.Response(200, json=runner_response())
    )

    org_client.runners.create(predictor_id=PREDICTOR_ID, is_default=True)

    assert body_of(route)["is_default"] is True


@respx.mock
def test_create_dedupes_space_ids_preserving_order(org_client):
    """Verify that dedicated space IDs are deduplicated while preserving their order."""
    route = respx.post(RUNNERS_URL).mock(
        return_value=httpx.Response(200, json=runner_response())
    )
    second = "66666666-6666-6666-6666-666666666666"

    org_client.runners.create(
        predictor_id=PREDICTOR_ID,
        organization_id=OTHER_ORG,
        dedicated_space_ids=[SPACE_ID, second, SPACE_ID],
    )

    body = body_of(route)
    assert body["dedicated_space_ids"] == [str(SPACE_ID), second]
    assert body["organization_id"] == OTHER_ORG


@respx.mock
def test_create_omits_unset_space_ids(org_client):
    """Ensure dedicated_space_ids is omitted from the body when not supplied."""
    route = respx.post(RUNNERS_URL).mock(
        return_value=httpx.Response(200, json=runner_response())
    )

    org_client.runners.create(predictor_id=PREDICTOR_ID)

    assert "dedicated_space_ids" not in body_of(route)


@pytest.mark.parametrize("bad", ["yes", 1, "true"])
@respx.mock
def test_create_rejects_non_bool_is_default(org_client, bad):
    """Verify that passing a non-boolean value to is_default raises an error."""
    route = respx.post(RUNNERS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="Expected a boolean"):
        org_client.runners.create(predictor_id=PREDICTOR_ID, is_default=bad)

    assert not route.called


@respx.mock
def test_create_without_organization_raises(client):
    """Ensure an error is raised if creation is attempted without an organization ID."""
    route = respx.post(RUNNERS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="GUARD_ORGANIZATION_ID"):
        client.runners.create(predictor_id=PREDICTOR_ID)

    assert not route.called


@pytest.mark.parametrize(
    ("status", "expected", "detail"),
    [
        (402, GuardPaymentRequiredError, "Organization needs active subscription"),
        (402, GuardPaymentRequiredError, "Organization has reached limit of 3 runners"),
        (409, GuardConflictError, "A runner with this name already exists"),
        (404, GuardNotFoundError, "Predictor not found"),
    ],
)
@respx.mock
def test_create_maps_server_errors(org_client, status, expected, detail):
    """
    Verify that specific server error responses correctly map to custom exceptions.
    """
    respx.post(RUNNERS_URL).mock(
        return_value=httpx.Response(status, json={"detail": detail})
    )

    with pytest.raises(expected) as exc_info:
        org_client.runners.create(predictor_id=PREDICTOR_ID)

    assert exc_info.value.status_code == status
    assert detail in str(exc_info.value)


@respx.mock
def test_delete_returns_none(org_client):
    """Verify that successfully deleting a runner returns None."""
    route = respx.delete(f"{RUNNERS_URL}{RUNNER_ID}").mock(
        return_value=httpx.Response(
            200, json={"message": "Runner deleted successfully"}
        )
    )

    assert org_client.runners.delete(RUNNER_ID) is None
    assert route.called


@respx.mock
def test_delete_is_never_retried(monkeypatch):
    """
    Ensure runner deletion requests are never retried to prevent triggering unintended
    actions.
    """
    monkeypatch.setattr("guard_client.transport.time.sleep", lambda _: None)
    route = respx.delete(f"{RUNNERS_URL}{RUNNER_ID}").mock(
        return_value=httpx.Response(503, json={"detail": "unavailable"})
    )

    retrying = GuardClient(
        api_key=API_KEY, organization_id=ORG_ID, base_url=BASE_URL, max_retries=3
    )
    try:
        with pytest.raises(GuardServerError):
            retrying.runners.delete(RUNNER_ID)
    finally:
        retrying.close()

    assert route.call_count == 1


@respx.mock
def test_delete_not_retried_on_connection_error(monkeypatch):
    """Ensure deletion requests are not retried even on connection errors."""
    monkeypatch.setattr("guard_client.transport.time.sleep", lambda _: None)
    route = respx.delete(f"{RUNNERS_URL}{RUNNER_ID}").mock(
        side_effect=httpx.ConnectError("dropped")
    )

    retrying = GuardClient(
        api_key=API_KEY, organization_id=ORG_ID, base_url=BASE_URL, max_retries=3
    )
    try:
        with pytest.raises(GuardError):
            retrying.runners.delete(RUNNER_ID)
    finally:
        retrying.close()

    assert route.call_count == 1


@respx.mock
def test_delete_maps_not_found(org_client):
    """Verify that a 404 response on deletion maps to a GuardNotFoundError."""
    respx.delete(f"{RUNNERS_URL}{RUNNER_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "Runner not found"})
    )

    with pytest.raises(GuardNotFoundError, match="Runner not found"):
        org_client.runners.delete(RUNNER_ID)


@respx.mock
def test_iter_all_walks_pages(org_client):
    """Ensure iter_all correctly handles pagination to yield all matching runners."""
    route = respx.get(RUNNERS_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=page_response(
                    [runner_response(name=f"r{i}") for i in range(2)], 3
                ),
            ),
            httpx.Response(200, json=page_response([runner_response(name="r2")], 3)),
        ]
    )

    names = [r.name for r in org_client.runners.iter_all(page_size=2)]

    assert names == ["r0", "r1", "r2"]
    assert route.call_count == 2


@pytest.fixture
async def async_org_client(isolate_env):
    """Provide an async client preconfigured with a default organization ID."""
    from guard_client import AsyncGuardClient

    async with AsyncGuardClient(
        api_key=API_KEY, organization_id=ORG_ID, base_url=BASE_URL, max_retries=0
    ) as c:
        yield c


@respx.mock
async def test_async_list_and_get(async_org_client):
    """Verify the async client can successfully list and retrieve runners."""
    respx.get(RUNNERS_URL).mock(
        return_value=httpx.Response(
            200, json=page_response([runner_response()], count=1)
        )
    )
    respx.get(f"{RUNNERS_URL}{RUNNER_ID}").mock(
        return_value=httpx.Response(200, json=runner_response())
    )

    page = await async_org_client.runners.list()
    runner = await async_org_client.runners.get(RUNNER_ID)

    assert page[0].id == RUNNER_ID
    assert runner.id == RUNNER_ID


@respx.mock
async def test_async_create_and_delete(async_org_client):
    """Verify the async client can successfully create and delete runners."""
    create = respx.post(RUNNERS_URL).mock(
        return_value=httpx.Response(200, json=runner_response(status="pending"))
    )
    delete = respx.delete(f"{RUNNERS_URL}{RUNNER_ID}").mock(
        return_value=httpx.Response(200, json={"message": "ok"})
    )

    runner = await async_org_client.runners.create(predictor_id=PREDICTOR_ID)
    result = await async_org_client.runners.delete(RUNNER_ID)

    assert body_of(create)["organization_id"] == str(ORG_ID)
    assert runner.status is RunnerStatus.PENDING
    assert result is None
    assert delete.called


@respx.mock
async def test_async_iter_all(async_org_client):
    """
    Ensure the async client can correctly paginate through all runners via iter_all.
    """
    route = respx.get(RUNNERS_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=page_response(
                    [runner_response(name=f"r{i}") for i in range(2)], 3
                ),
            ),
            httpx.Response(200, json=page_response([runner_response(name="r2")], 3)),
        ]
    )

    names = [r.name async for r in async_org_client.runners.iter_all(page_size=2)]

    assert names == ["r0", "r1", "r2"]
    assert route.call_count == 2
