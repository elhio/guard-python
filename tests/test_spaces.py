"""
Tests for the spaces resource: filters, validation and pagination.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from guard_client import (
    GuardAPIError,
    GuardAuthError,
    GuardConflictError,
    GuardError,
    GuardNotFoundError,
    GuardPaymentRequiredError,
    GuardServerError,
    MediaCategory,
    SpaceOrder,
    SpaceStatus,
)

from .conftest import (
    API_KEY,
    BASE_URL,
    ORG_ID,
    PREDICTOR_ID,
    RUNNER_ID,
    SPACE_ID,
    TASK_ID,
    USER_ID,
    space_response,
    spaces_page_response,
)

SPACES_URL = f"{BASE_URL}/api/v1/spaces/"


def params_of(route):
    """Extract query parameters from the last request of a mock route."""
    return route.calls.last.request.url.params


def body_of(route):
    """Extract and parse the JSON request body from the last call to a mock route."""
    return json.loads(route.calls.last.request.read())


@respx.mock
def test_create_organization_space(client):
    """Verify that creating an organization space sends the expected payload."""
    route = respx.post(SPACES_URL).mock(
        return_value=httpx.Response(200, json=space_response())
    )

    space = client.spaces.create(
        name="My Space", predictor_id=PREDICTOR_ID, organization_id=ORG_ID
    )

    assert body_of(route) == {
        "name": "My Space",
        "predictor_id": str(PREDICTOR_ID),
        "is_public": False,
        "organization_id": str(ORG_ID),
    }
    assert space.id == SPACE_ID
    assert space.name == "Test Space"


@respx.mock
def test_create_user_space(client):
    """Verify that creating a user space sends user_id and excludes organization_id."""
    route = respx.post(SPACES_URL).mock(
        return_value=httpx.Response(200, json=space_response())
    )

    client.spaces.create(name="Mine", predictor_id=PREDICTOR_ID, user_id=USER_ID)

    body = body_of(route)
    assert body["user_id"] == str(USER_ID)
    assert "organization_id" not in body


@respx.mock
def test_create_uses_client_organization_default(isolate_env):
    """
    Ensure the client-level default organization ID is used when no owner is explicitly
    named.
    """
    from guard_client import GuardClient

    route = respx.post(SPACES_URL).mock(
        return_value=httpx.Response(200, json=space_response())
    )

    with GuardClient(api_key=API_KEY, organization_id=ORG_ID, base_url=BASE_URL) as c:
        c.spaces.create(name="Defaulted", predictor_id=PREDICTOR_ID)

    assert body_of(route)["organization_id"] == str(ORG_ID)


@respx.mock
def test_explicit_user_id_beats_organization_default(isolate_env):
    """Ensure an explicit user ID overrides the client-level default organization ID."""
    from guard_client import GuardClient

    route = respx.post(SPACES_URL).mock(
        return_value=httpx.Response(200, json=space_response())
    )

    with GuardClient(api_key=API_KEY, organization_id=ORG_ID, base_url=BASE_URL) as c:
        c.spaces.create(name="Personal", predictor_id=PREDICTOR_ID, user_id=USER_ID)

    body = body_of(route)
    assert body["user_id"] == str(USER_ID)
    assert "organization_id" not in body


@respx.mock
def test_create_sends_all_supplied_fields(client):
    """
    Verify that all provided optional fields are correctly included in the creation
    body.
    """
    route = respx.post(SPACES_URL).mock(
        return_value=httpx.Response(200, json=space_response())
    )

    client.spaces.create(
        name="Full",
        predictor_id=PREDICTOR_ID,
        description="Everything set",
        is_public=True,
        organization_id=ORG_ID,
        enabled_task_ids=[TASK_ID],
        dedicated_runner_ids=[RUNNER_ID],
    )

    assert body_of(route) == {
        "name": "Full",
        "predictor_id": str(PREDICTOR_ID),
        "is_public": True,
        "description": "Everything set",
        "organization_id": str(ORG_ID),
        "enabled_task_ids": [str(TASK_ID)],
        "dedicated_runner_ids": [str(RUNNER_ID)],
    }


@respx.mock
def test_create_omits_unset_optionals(client):
    """Ensure unset optional fields are omitted from the request body."""
    route = respx.post(SPACES_URL).mock(
        return_value=httpx.Response(200, json=space_response())
    )

    client.spaces.create(
        name="Sparse", predictor_id=PREDICTOR_ID, organization_id=ORG_ID
    )

    body = body_of(route)
    for absent in (
        "description",
        "enabled_task_ids",
        "dedicated_runner_ids",
        "user_id",
    ):
        assert absent not in body
    assert "is_default" not in body  # never settable at creation


@respx.mock
def test_create_strips_name_and_description(client):
    """
    Verify that whitespace is stripped from space names and descriptions before sending.
    """
    route = respx.post(SPACES_URL).mock(
        return_value=httpx.Response(200, json=space_response())
    )

    client.spaces.create(
        name="  Padded  ",
        predictor_id=PREDICTOR_ID,
        description="  spaced  ",
        organization_id=ORG_ID,
    )

    body = body_of(route)
    assert body["name"] == "Padded"
    assert body["description"] == "spaced"


@respx.mock
def test_create_treats_blank_description_as_unset(client):
    """
    Ensure descriptions containing only whitespace are omitted from the creation
    payload.
    """
    route = respx.post(SPACES_URL).mock(
        return_value=httpx.Response(200, json=space_response())
    )

    client.spaces.create(
        name="Blank",
        predictor_id=PREDICTOR_ID,
        description="   ",
        organization_id=ORG_ID,
    )

    assert "description" not in body_of(route)


@respx.mock
def test_create_dedupes_task_ids_preserving_order(client):
    """
    Verify that duplicate task IDs are removed while preserving their original order.
    """
    route = respx.post(SPACES_URL).mock(
        return_value=httpx.Response(200, json=space_response())
    )
    second = "66666666-6666-6666-6666-666666666666"

    client.spaces.create(
        name="Dupes",
        predictor_id=PREDICTOR_ID,
        organization_id=ORG_ID,
        enabled_task_ids=[TASK_ID, second, TASK_ID],
    )

    assert body_of(route)["enabled_task_ids"] == [str(TASK_ID), second]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "needs an owner"),
        ({"user_id": USER_ID, "organization_id": ORG_ID}, "cannot belong to both"),
        ({"organization_id": ORG_ID, "name": "ab"}, "at least 3 characters"),
        ({"organization_id": ORG_ID, "name": "  ab  "}, "at least 3 characters"),
        ({"organization_id": ORG_ID, "name": "x" * 51}, "at most 50 characters"),
        (
            {"organization_id": ORG_ID, "description": "d" * 2001},
            "at most 2000 characters",
        ),
        ({"organization_id": ORG_ID, "is_public": "yes"}, "Expected a boolean"),
        (
            {"user_id": USER_ID, "dedicated_runner_ids": [RUNNER_ID]},
            "only available for organization spaces",
        ),
    ],
)
@respx.mock
def test_create_validates_before_sending(client, kwargs, message):
    """
    Ensure client-side parameter validation fails locally before issuing a create
    request.
    """
    route = respx.post(SPACES_URL).mock(return_value=httpx.Response(200))
    kwargs.setdefault("name", "Valid Name")

    with pytest.raises(GuardError, match=message):
        client.spaces.create(predictor_id=PREDICTOR_ID, **kwargs)

    assert not route.called


@pytest.mark.parametrize(
    ("status", "expected", "detail"),
    [
        (409, GuardConflictError, "A space with this name already exists"),
        (402, GuardPaymentRequiredError, "User needs active subscription"),
        (403, GuardAuthError, "Predictor is not enabled in your active subscription"),
        (404, GuardNotFoundError, "One or more tasks not found"),
        (400, GuardAPIError, "Cannot create space without an owner"),
    ],
)
@respx.mock
def test_create_maps_server_errors(client, status, expected, detail):
    """
    Verify that server HTTP error responses during creation map to corresponding client
    exceptions.
    """
    respx.post(SPACES_URL).mock(
        return_value=httpx.Response(status, json={"detail": detail})
    )

    with pytest.raises(expected) as exc_info:
        client.spaces.create(
            name="Boom", predictor_id=PREDICTOR_ID, organization_id=ORG_ID
        )

    assert exc_info.value.status_code == status
    assert detail in str(exc_info.value)


@respx.mock
def test_create_is_never_retried(monkeypatch):
    """
    Ensure creation requests are not retried to prevent creating duplicate resources on
    transient errors.
    """
    from guard_client import GuardClient

    monkeypatch.setattr("guard_client.transport.time.sleep", lambda _: None)
    route = respx.post(SPACES_URL).mock(
        return_value=httpx.Response(503, json={"detail": "unavailable"})
    )

    retrying = GuardClient(api_key=API_KEY, base_url=BASE_URL, max_retries=3)
    try:
        with pytest.raises(GuardServerError):
            retrying.spaces.create(
                name="Once Only", predictor_id=PREDICTOR_ID, organization_id=ORG_ID
            )
    finally:
        retrying.close()

    assert route.call_count == 1


@respx.mock
async def test_async_create(async_client):
    """
    Verify that the async client creates a space and parses the response correctly.
    """
    route = respx.post(SPACES_URL).mock(
        return_value=httpx.Response(200, json=space_response())
    )

    space = await async_client.spaces.create(
        name="Async Space", predictor_id=PREDICTOR_ID, organization_id=ORG_ID
    )

    assert body_of(route)["name"] == "Async Space"
    assert space.id == SPACE_ID


@respx.mock
async def test_async_create_validates_before_sending(async_client):
    """
    Ensure async space creation performs client-side validation before sending network
    requests.
    """
    route = respx.post(SPACES_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="needs an owner"):
        await async_client.spaces.create(name="No Owner", predictor_id=PREDICTOR_ID)

    assert not route.called


@respx.mock
def test_list_parses_page(client):
    """Verify that listing spaces correctly parses page metadata and space fields."""
    respx.get(SPACES_URL).mock(
        return_value=httpx.Response(200, json=spaces_page_response(count=42))
    )

    page = client.spaces.list()

    assert page.count == 42
    assert len(page) == 1
    space = page[0]
    assert space.id == SPACE_ID
    assert space.name == "Test Space"
    assert space.status is SpaceStatus.ACTIVE
    assert space.enabled_media == [MediaCategory.IMAGE, MediaCategory.VIDEO]
    assert space.enabled_task_names == ["Deepfake", "Violence"]


@respx.mock
def test_page_behaves_like_a_list(client):
    """
    Ensure the returned Page object supports sequence operations like iteration and
    indexing.
    """
    respx.get(SPACES_URL).mock(
        return_value=httpx.Response(
            200,
            json=spaces_page_response(
                items=[space_response(name="A"), space_response(name="B")]
            ),
        )
    )

    page = client.spaces.list()

    assert [s.name for s in page] == ["A", "B"]  # iterable
    assert page[1].name == "B"  # indexable
    assert len(page) == 2  # sized
    assert bool(page) is True
    assert page.data[0].name == "A"  # underlying list still reachable


@respx.mock
def test_empty_page_is_falsy(client):
    """
    Verify that an empty Page object evaluates as falsy and correctly reflects a zero
    length.
    """
    respx.get(SPACES_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    page = client.spaces.list()

    assert not page
    assert len(page) == 0
    assert page.has_more is False


@respx.mock
def test_has_more_reflects_count(client):
    """
    Ensure has_more returns True when total count exceeds the number of items on the
    current page.
    """
    respx.get(SPACES_URL).mock(
        return_value=httpx.Response(200, json=spaces_page_response(count=10))
    )

    assert client.spaces.list().has_more is True


@respx.mock
def test_owner_name_prefers_organization(client):
    """
    Verify that owner_name returns organization_name when an organization owns the
    space.
    """
    respx.get(SPACES_URL).mock(
        return_value=httpx.Response(200, json=spaces_page_response())
    )

    assert client.spaces.list()[0].owner_name == "Test Org"


@respx.mock
def test_get_parses_space_detail(client):
    """
    Verify that space detail requests parse into SpaceDetail objects with full task
    models.
    """
    from .conftest import PREDICTOR_ID, space_detail_response

    route = respx.get(f"{SPACES_URL}{SPACE_ID}").mock(
        return_value=httpx.Response(200, json=space_detail_response())
    )

    detail = client.spaces.get(SPACE_ID)

    assert route.called
    assert detail.id == SPACE_ID
    assert detail.predictor_multiplier == 3
    assert detail.predictor_id == PREDICTOR_ID
    assert detail.max_media_size == 52428800
    # enabled_tasks are full Task objects here, not the names the list endpoint returns.
    assert [t.name for t in detail.enabled_tasks] == ["Deepfake"]
    assert detail.enabled_tasks[0].reactions == {1: "Real photo", 2: "AI generated"}
    assert detail.task_thresholds[0].blur_threshold == 50
    assert detail.owner_name == "Test Org"


@respx.mock
async def test_async_get_space_detail(async_client):
    """Verify that the async client can fetch and parse detailed space information."""
    from .conftest import space_detail_response

    respx.get(f"{SPACES_URL}{SPACE_ID}").mock(
        return_value=httpx.Response(200, json=space_detail_response())
    )

    detail = await async_client.spaces.get(SPACE_ID)

    assert detail.predictor_multiplier == 3


@respx.mock
def test_defaults_send_only_pagination(client):
    """Ensure default space list calls send only skip and limit parameters."""
    route = respx.get(SPACES_URL).mock(
        return_value=httpx.Response(200, json=spaces_page_response())
    )

    client.spaces.list()

    params = params_of(route)
    assert params["skip"] == "0"
    assert params["limit"] == "100"
    for absent in (
        "user_id",
        "organization_id",
        "predictor_id",
        "is_public",
        "sort_by",
    ):
        assert absent not in params


@respx.mock
def test_all_filters_reach_the_query_string(client):
    """
    Verify that all supplied list filters are correctly appended to the request query
    string.
    """
    route = respx.get(SPACES_URL).mock(
        return_value=httpx.Response(200, json=spaces_page_response())
    )

    client.spaces.list(
        organization_id=ORG_ID,
        predictor_id=PREDICTOR_ID,
        is_public=True,
        is_default=False,
        statuses=["active"],
        sort_by="name",
        sort_order="desc",
        skip=10,
        limit=25,
    )

    params = params_of(route)
    assert params["organization_id"] == str(ORG_ID)
    assert params["predictor_id"] == str(PREDICTOR_ID)
    assert params["is_public"] == "true"
    assert params["is_default"] == "false"
    assert params.get_list("statuses") == ["active"]
    assert params["sort_by"] == "name"
    assert params["sort_order"] == "desc"
    assert params["skip"] == "10"
    assert params["limit"] == "25"


@pytest.mark.parametrize(("value", "expected"), [(True, "true"), (False, "false")])
@respx.mock
def test_boolean_filters_serialise(client, value, expected):
    """
    Verify that boolean filter parameters are formatted as 'true' or 'false' strings.
    """
    route = respx.get(SPACES_URL).mock(
        return_value=httpx.Response(200, json=spaces_page_response())
    )

    client.spaces.list(is_public=value)

    assert params_of(route)["is_public"] == expected


@pytest.mark.parametrize(
    "value", [1, 0, "true", "false", "yes", "no", "1", 1.0, "True"]
)
@respx.mock
def test_boolean_filters_reject_non_booleans(client, value):
    """
    Ensure non-boolean truthy/falsy values are rejected locally for boolean filters.
    """
    route = respx.get(SPACES_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="Expected a boolean: True or False"):
        client.spaces.list(is_public=value)

    assert not route.called


@respx.mock
def test_enum_filters_accept_strings_and_members(client):
    """Verify that enum filters accept both raw strings and enum member instances."""
    route = respx.get(SPACES_URL).mock(
        return_value=httpx.Response(200, json=spaces_page_response())
    )

    client.spaces.list(sort_by=SpaceOrder.NAME, statuses=[SpaceStatus.ACTIVE])
    assert params_of(route)["sort_by"] == "name"

    client.spaces.list(sort_by="created_at")
    assert params_of(route)["sort_by"] == "created_at"


@respx.mock
def test_conflicting_owner_filters_raise_before_any_request(client):
    """
    Ensure specifying both user_id and organization_id filters raises an error locally
    before calling the API.
    """
    route = respx.get(SPACES_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="both user_id and organization_id"):
        client.spaces.list(user_id=SPACE_ID, organization_id=ORG_ID)

    assert not route.called


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sort_by": "bogus"}, r"Invalid sort_by='bogus'.*'name', 'created_at'"),
        ({"sort_order": "sideways"}, r"Invalid sort_order='sideways'.*'asc', 'desc'"),
        ({"statuses": ["deleted"]}, r"Invalid statuses='deleted'.*'active'"),
        ({"is_public": "maybe"}, r"Invalid is_public='maybe'.*Expected a boolean"),
        ({"is_default": 7}, r"Invalid is_default=7.*Expected a boolean"),
    ],
)
@respx.mock
def test_invalid_filter_values_raise(client, kwargs, message):
    """
    Ensure invalid filter options trigger local validation failures before any network
    request.
    """
    route = respx.get(SPACES_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match=message):
        client.spaces.list(**kwargs)

    assert not route.called


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"skip": -1}, "Invalid skip=-1"),
        ({"limit": 0}, "Invalid limit=0"),
        ({"limit": 101}, "Invalid limit=101"),
    ],
)
@respx.mock
def test_invalid_pagination_raises(client, kwargs, message):
    """Ensure out-of-range pagination options trigger local validation failures."""
    route = respx.get(SPACES_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match=message):
        client.spaces.list(**kwargs)

    assert not route.called


@respx.mock
def test_iter_all_walks_pages(client):
    """Verify that iter_all transparently paginates across multiple pages of spaces."""
    first = [space_response(name=f"s{i}") for i in range(3)]
    second = [space_response(name="s3")]
    route = respx.get(SPACES_URL).mock(
        side_effect=[
            httpx.Response(200, json={"data": first, "count": 4}),
            httpx.Response(200, json={"data": second, "count": 4}),
        ]
    )

    names = [s.name for s in client.spaces.iter_all(page_size=3)]

    assert names == ["s0", "s1", "s2", "s3"]
    assert route.call_count == 2
    assert route.calls[0].request.url.params["skip"] == "0"
    assert route.calls[1].request.url.params["skip"] == "3"


@respx.mock
def test_iter_all_single_short_page_makes_one_request(client):
    """
    Ensure iter_all makes only one request when the first page contains all available
    items.
    """
    route = respx.get(SPACES_URL).mock(
        return_value=httpx.Response(200, json=spaces_page_response(count=1))
    )

    assert len(list(client.spaces.iter_all())) == 1
    assert route.call_count == 1


@respx.mock
def test_iter_all_stops_on_full_page_when_count_reached(client):
    """
    Verify iter_all stops fetching once total retrieved items equal total count, even
    on a full page.
    """
    items = [space_response(name=f"s{i}") for i in range(2)]
    route = respx.get(SPACES_URL).mock(
        return_value=httpx.Response(200, json={"data": items, "count": 2})
    )

    assert len(list(client.spaces.iter_all(page_size=2))) == 2
    assert route.call_count == 1


@respx.mock
def test_iter_all_terminates_when_count_is_wrong(client):
    """
    Ensure iter_all terminates when a returned page is short, preventing infinite loops
    on inaccurate counts.
    """
    items = [space_response(name="s0")]
    route = respx.get(SPACES_URL).mock(
        return_value=httpx.Response(200, json={"data": items, "count": 9999})
    )

    assert len(list(client.spaces.iter_all(page_size=5))) == 1
    assert route.call_count == 1


@respx.mock
def test_iter_all_passes_filters_to_every_page(client):
    """
    Verify that iter_all includes specified filter parameters in every page request.
    """
    pages = [space_response(name=f"s{i}") for i in range(2)]
    route = respx.get(SPACES_URL).mock(
        side_effect=[
            httpx.Response(200, json={"data": pages, "count": 3}),
            httpx.Response(200, json={"data": [space_response(name="s2")], "count": 3}),
        ]
    )

    list(client.spaces.iter_all(is_public=True, sort_by="name", page_size=2))

    for call in route.calls:
        assert call.request.url.params["is_public"] == "true"
        assert call.request.url.params["sort_by"] == "name"


@respx.mock
def test_iter_all_is_lazy(client):
    """
    Ensure breaking early out of iter_all halts pagination and avoids fetching remaining
    pages.
    """
    route = respx.get(SPACES_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [space_response(name=f"s{i}") for i in range(2)],
                "count": 100,
            },
        )
    )

    for space in client.spaces.iter_all(page_size=2):
        if space.name == "s0":
            break

    assert route.call_count == 1


@respx.mock
async def test_async_list(async_client):
    """Verify that the async client can fetch and parse a page of spaces."""
    respx.get(SPACES_URL).mock(
        return_value=httpx.Response(200, json=spaces_page_response(count=5))
    )

    page = await async_client.spaces.list(sort_by="name")

    assert page.count == 5
    assert page[0].id == SPACE_ID


@respx.mock
async def test_async_list_validates_filters(async_client):
    """
    Ensure the async client performs local validation on filter parameters prior to
    network requests.
    """
    route = respx.get(SPACES_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match="Invalid sort_by"):
        await async_client.spaces.list(sort_by="nope")

    assert not route.called


@respx.mock
async def test_async_iter_all(async_client):
    """Ensure async iter_all transparently paginates across multiple pages of spaces."""
    route = respx.get(SPACES_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": [space_response(name=f"s{i}") for i in range(2)],
                    "count": 3,
                },
            ),
            httpx.Response(200, json={"data": [space_response(name="s2")], "count": 3}),
        ]
    )

    names = [space.name async for space in async_client.spaces.iter_all(page_size=2)]

    assert names == ["s0", "s1", "s2"]
    assert route.call_count == 2
