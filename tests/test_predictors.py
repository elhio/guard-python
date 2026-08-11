"""
Tests for the predictors resource.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from guard_client import GuardError, MediaCategory, PredictorOrder, PredictorStatus

from .conftest import (
    BASE_URL,
    ORG_ID,
    PREDICTOR_ID,
    TASK_ID,
    page_response,
    predictor_response,
)

PREDICTORS_URL = f"{BASE_URL}/api/v1/predictors/"


@respx.mock
def test_list_parses_predictors(client):
    """
    Verify that the list endpoint correctly parses predictor attributes from the
    response.
    """
    respx.get(PREDICTORS_URL).mock(
        return_value=httpx.Response(
            200, json=page_response([predictor_response()], count=3)
        )
    )

    page = client.predictors.list()

    assert page.count == 3
    predictor = page[0]
    assert predictor.id == PREDICTOR_ID
    assert predictor.name == "Default Predictor"
    assert predictor.status is PredictorStatus.ACTIVE
    assert predictor.token_multiplier == 1
    assert predictor.supported_media == [MediaCategory.IMAGE, MediaCategory.VIDEO]
    assert predictor.supported_task_ids == [TASK_ID]


@respx.mock
def test_list_passes_filters(client):
    """
    Ensure that all provided filters are correctly formatted and sent in the query
    string.
    """
    route = respx.get(PREDICTORS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.predictors.list(
        organization_id=ORG_ID,
        supported_task_ids=[TASK_ID],
        sort_by="name",
        sort_order="desc",
        skip=5,
        limit=10,
    )

    params = route.calls.last.request.url.params
    assert params["organization_id"] == str(ORG_ID)
    assert params.get_list("supported_task_ids") == [str(TASK_ID)]
    assert params["sort_by"] == "name"
    assert params["sort_order"] == "desc"
    assert params["skip"] == "5"
    assert params["limit"] == "10"


@respx.mock
def test_list_omits_unset_filters(client):
    """
    Check that parameters not explicitly provided are excluded from the request URL.
    """
    route = respx.get(PREDICTORS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.predictors.list()

    params = route.calls.last.request.url.params
    for absent in ("user_id", "organization_id", "supported_task_ids", "sort_by"):
        assert absent not in params


@respx.mock
def test_conflicting_owner_filters_raise_before_any_request(client):
    """
    Verify that filtering by both user and organization simultaneously raises locally,
    before the API answers 400 to that pair.
    """
    route = respx.get(PREDICTORS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    with pytest.raises(GuardError, match="both user_id and organization_id"):
        client.predictors.list(user_id=TASK_ID, organization_id=ORG_ID)

    assert not route.called


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sort_by": "bogus"}, r"Invalid sort_by='bogus'.*'name', 'created_at'"),
        ({"sort_order": "sideways"}, "Invalid sort_order"),
        ({"limit": 0}, "Invalid limit=0"),
        ({"skip": -1}, "Invalid skip=-1"),
    ],
)
@respx.mock
def test_list_validates_before_sending(client, kwargs, message):
    """
    Ensure that invalid query parameters raise an error locally before any network call.
    """
    route = respx.get(PREDICTORS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match=message):
        client.predictors.list(**kwargs)

    assert not route.called


@respx.mock
def test_enum_member_accepted(client):
    """Verify that enum members can be passed directly as filter arguments."""
    route = respx.get(PREDICTORS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.predictors.list(sort_by=PredictorOrder.CREATED_AT)

    assert route.calls.last.request.url.params["sort_by"] == "created_at"


@respx.mock
def test_iter_all_walks_pages(client):
    """Ensure iter_all correctly paginates through multiple pages of predictors."""
    first = [predictor_response(name=f"p{i}") for i in range(2)]
    route = respx.get(PREDICTORS_URL).mock(
        side_effect=[
            httpx.Response(200, json=page_response(first, count=3)),
            httpx.Response(
                200, json=page_response([predictor_response(name="p2")], count=3)
            ),
        ]
    )

    names = [p.name for p in client.predictors.iter_all(page_size=2)]

    assert names == ["p0", "p1", "p2"]
    assert route.call_count == 2


@respx.mock
async def test_async_list(async_client):
    """Verify that the async client can fetch and parse a page of predictors."""
    respx.get(PREDICTORS_URL).mock(
        return_value=httpx.Response(
            200, json=page_response([predictor_response()], count=1)
        )
    )

    page = await async_client.predictors.list()

    assert page[0].id == PREDICTOR_ID


@respx.mock
async def test_async_iter_all(async_client):
    """
    Ensure the async client can correctly paginate through all available predictors.
    """
    route = respx.get(PREDICTORS_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=page_response(
                    [predictor_response(name=f"p{i}") for i in range(2)], 3
                ),
            ),
            httpx.Response(200, json=page_response([predictor_response(name="p2")], 3)),
        ]
    )

    names = [p.name async for p in async_client.predictors.iter_all(page_size=2)]

    assert names == ["p0", "p1", "p2"]
    assert route.call_count == 2
