"""
Tests for the targets resource.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from guard_client import GuardError, TargetOrder, TargetStatus

from .conftest import (
    BASE_URL,
    ORG_ID,
    PREDICTOR_ID,
    TARGET_ID,
    page_response,
    target_response,
)

TARGETS_URL = f"{BASE_URL}/api/v1/targets"


@respx.mock
def test_list_parses_targets(client):
    """
    Verify that listing targets correctly parses all fields including reaction
    dictionary key types.
    """
    respx.get(TARGETS_URL).mock(
        return_value=httpx.Response(
            200, json=page_response([target_response()], count=4)
        )
    )

    page = client.targets.list()

    assert page.count == 4
    target = page[0]
    assert target.id == TARGET_ID
    assert target.name == "Deepfake"
    assert target.status is TargetStatus.ACTIVE
    assert target.description == "Detects synthetic media"
    # keys arrive as JSON strings but are coerced to the ints the API means them as
    assert target.reactions == {1: "Real photo", 2: "AI generated"}
    assert all(isinstance(key, int) for key in target.reactions)


@respx.mock
def test_list_filters_by_predictor(client):
    """
    Ensure that filtering targets by predictor_id sends the expected query parameter.
    """
    route = respx.get(TARGETS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.targets.list(predictor_id=PREDICTOR_ID, sort_by="name", limit=50)

    params = route.calls.last.request.url.params
    assert params["predictor_id"] == str(PREDICTOR_ID)
    assert params["sort_by"] == "name"
    assert params["limit"] == "50"


@respx.mock
def test_list_omits_unset_filters(client):
    """Verify that unset target filters are omitted from query parameters."""
    route = respx.get(TARGETS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.targets.list()

    params = route.calls.last.request.url.params
    for absent in (
        "user_id",
        "organization_id",
        "predictor_id",
        "sort_by",
        "sort_order",
    ):
        assert absent not in params


@respx.mock
def test_conflicting_owner_filters_raise_before_any_request(client):
    """
    Ensure specifying both user_id and organization_id filters raises an error locally
    before calling the API, which answers 400 to that pair.
    """
    route = respx.get(TARGETS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    with pytest.raises(GuardError, match="both user_id and organization_id"):
        client.targets.list(user_id=TARGET_ID, organization_id=ORG_ID)

    assert not route.called


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sort_by": "bogus"}, r"Invalid sort_by='bogus'.*'name', 'created_at'"),
        ({"sort_order": "sideways"}, "Invalid sort_order"),
        ({"limit": 101}, "Invalid limit=101"),
    ],
)
@respx.mock
def test_list_validates_before_sending(client, kwargs, message):
    """
    Verify that invalid query arguments trigger client-side validation errors before
    calling the API.
    """
    route = respx.get(TARGETS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match=message):
        client.targets.list(**kwargs)

    assert not route.called


@respx.mock
def test_enum_member_accepted(client):
    """
    Verify that TargetOrder enum members are accepted directly as sort_by parameters.
    """
    route = respx.get(TARGETS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.targets.list(sort_by=TargetOrder.CREATED_AT)

    assert route.calls.last.request.url.params["sort_by"] == "created_at"


@respx.mock
def test_missing_optional_fields_tolerated(client):
    """
    Verify that target responses missing description or reactions default to None or
    empty dictionary.
    """
    minimal = {"id": str(TARGET_ID), "status": "active", "name": "Bare"}
    respx.get(TARGETS_URL).mock(
        return_value=httpx.Response(200, json=page_response([minimal], count=1))
    )

    target = client.targets.list()[0]

    assert target.description is None
    assert target.reactions == {}


@respx.mock
def test_iter_all_walks_pages(client):
    """
    Ensure that iter_all transparently fetches across multiple pages of targets.
    """
    route = respx.get(TARGETS_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=page_response(
                    [target_response(name=f"t{i}") for i in range(2)], 3
                ),
            ),
            httpx.Response(200, json=page_response([target_response(name="t2")], 3)),
        ]
    )

    names = [t.name for t in client.targets.iter_all(page_size=2)]

    assert names == ["t0", "t1", "t2"]
    assert route.call_count == 2


@respx.mock
async def test_async_list(async_client):
    """Verify that the async client correctly lists and parses targets."""
    respx.get(TARGETS_URL).mock(
        return_value=httpx.Response(
            200, json=page_response([target_response()], count=1)
        )
    )

    page = await async_client.targets.list(predictor_id=PREDICTOR_ID)

    assert page[0].id == TARGET_ID
