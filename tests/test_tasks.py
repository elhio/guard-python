"""
Tests for the tasks resource.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from guard_client import GuardError, TaskOrder, TaskStatus

from .conftest import (
    BASE_URL,
    ORG_ID,
    PREDICTOR_ID,
    TASK_ID,
    page_response,
    task_response,
)

TASKS_URL = f"{BASE_URL}/api/v1/tasks/"


@respx.mock
def test_list_parses_tasks(client):
    """
    Verify that listing tasks correctly parses all fields including reaction dictionary
    key types.
    """
    respx.get(TASKS_URL).mock(
        return_value=httpx.Response(200, json=page_response([task_response()], count=4))
    )

    page = client.tasks.list()

    assert page.count == 4
    task = page[0]
    assert task.id == TASK_ID
    assert task.name == "Deepfake"
    assert task.status is TaskStatus.ACTIVE
    assert task.description == "Detects synthetic media"
    # keys arrive as JSON strings but are coerced to the ints the API means them as
    assert task.reactions == {1: "Real photo", 2: "AI generated"}
    assert all(isinstance(key, int) for key in task.reactions)


@respx.mock
def test_list_filters_by_predictor(client):
    """
    Ensure that filtering tasks by predictor_id sends the expected query parameter.
    """
    route = respx.get(TASKS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.tasks.list(predictor_id=PREDICTOR_ID, sort_by="name", limit=50)

    params = route.calls.last.request.url.params
    assert params["predictor_id"] == str(PREDICTOR_ID)
    assert params["sort_by"] == "name"
    assert params["limit"] == "50"


@respx.mock
def test_list_omits_unset_filters(client):
    """Verify that unset task filters are omitted from query parameters."""
    route = respx.get(TASKS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.tasks.list()

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
def test_list_accepts_both_owner_filters(client):
    """
    Ensure that passing both user_id and organization_id filters is permitted for
    tasks.
    """
    route = respx.get(TASKS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.tasks.list(user_id=TASK_ID, organization_id=ORG_ID)

    assert route.called


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
    route = respx.get(TASKS_URL).mock(return_value=httpx.Response(200))

    with pytest.raises(GuardError, match=message):
        client.tasks.list(**kwargs)

    assert not route.called


@respx.mock
def test_enum_member_accepted(client):
    """
    Verify that TaskOrder enum members are accepted directly as sort_by parameters.
    """
    route = respx.get(TASKS_URL).mock(
        return_value=httpx.Response(200, json=page_response([]))
    )

    client.tasks.list(sort_by=TaskOrder.CREATED_AT)

    assert route.calls.last.request.url.params["sort_by"] == "created_at"


@respx.mock
def test_missing_optional_fields_tolerated(client):
    """
    Verify that task responses missing description or reactions default to None or empty
    dictionary.
    """
    minimal = {"id": str(TASK_ID), "status": "active", "name": "Bare"}
    respx.get(TASKS_URL).mock(
        return_value=httpx.Response(200, json=page_response([minimal], count=1))
    )

    task = client.tasks.list()[0]

    assert task.description is None
    assert task.reactions == {}


@respx.mock
def test_iter_all_walks_pages(client):
    """
    Ensure that iter_all transparently fetches across multiple pages of tasks.
    """
    route = respx.get(TASKS_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=page_response([task_response(name=f"t{i}") for i in range(2)], 3),
            ),
            httpx.Response(200, json=page_response([task_response(name="t2")], 3)),
        ]
    )

    names = [t.name for t in client.tasks.iter_all(page_size=2)]

    assert names == ["t0", "t1", "t2"]
    assert route.call_count == 2


@respx.mock
async def test_async_list(async_client):
    """Verify that the async client correctly lists and parses tasks."""
    respx.get(TASKS_URL).mock(
        return_value=httpx.Response(200, json=page_response([task_response()], count=1))
    )

    page = await async_client.tasks.list(predictor_id=PREDICTOR_ID)

    assert page[0].id == TASK_ID
