"""
Low-level bindings for the `/api/v1/tasks/` endpoints.

A task is one detection a space can run, such as AI-generated, violence, and so on.
Listing them supplies the `enabled_task_ids` accepted by `Spaces.create`.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Iterator, Optional, Union

from .filters import (
    MAX_LIMIT,
    IdLike,
    add_ids,
    add_sort,
    reject_conflicting_owners,
    validate_pagination,
)
from .models import SortOrder, Task, TaskOrder, TaskPage
from .transport import AsyncTransport, SyncTransport

__all__ = ["AsyncTasks", "Tasks"]

#: The base URL path for the tasks endpoints.
_BASE = "/api/v1/tasks/"


class _TasksBase:
    """
    Query and payload construction with no network I/O.

    Everything that does not touch the network lives here. This ensures the synchronous
    and asynchronous resources cannot drift in how they build or validate a request.
    """

    @staticmethod
    def _list_params(
        *,
        user_id: Optional[IdLike],
        organization_id: Optional[IdLike],
        predictor_id: Optional[IdLike],
        sort_by: Optional[Union[TaskOrder, str]],
        sort_order: Optional[Union[SortOrder, str]],
        skip: int,
        limit: int,
    ) -> Dict[str, Any]:
        """
        Build the query parameters for a list request.

        Validating here means a bad filter never reaches the network. The arguments
        mirror `list`, but none are optional here.

        Args:
            user_id: Only tasks available to this user.
            organization_id: Only tasks available to this organization.
            predictor_id: Only tasks supported by this predictor.
            sort_by: The field to sort the results by.
            sort_order: The direction to sort the results.
            skip: Offset of the results. Must be 0 or greater.
            limit: Page size, 1-100.

        Returns:
            The query dict with every unset filter omitted.

        Raises:
            GuardError: If a filter value is invalid or both owner filters were given.
        """
        reject_conflicting_owners(user_id, organization_id)
        validate_pagination(skip, limit)

        params: Dict[str, Any] = {"skip": skip, "limit": limit}
        add_ids(
            params,
            user_id=user_id,
            organization_id=organization_id,
            predictor_id=predictor_id,
        )
        add_sort(params, sort_by, sort_order, TaskOrder)
        return params


class Tasks(_TasksBase):
    """
    Synchronous task endpoints.

    This class is accessed through the client rather than being constructed directly,
    and it shares the client connection pool.
    """

    def __init__(self, transport: SyncTransport) -> None:
        """
        Bind this resource to a transport.

        Args:
            transport: The client transport whose connection pool is shared.
        """
        self._transport = transport

    def list(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        predictor_id: Optional[IdLike] = None,
        sort_by: Optional[Union[TaskOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> TaskPage:
        """
        List the available detection tasks.

        Args:
            user_id: Only tasks available to this user.
            organization_id: Only tasks available to this organization.
            predictor_id: Only tasks the given predictor supports. This is the usual
                filter when picking `enabled_task_ids` for a new space.
            sort_by: Valid options include `"name"` or `"created_at"`. Server default:
                `"name"`.
            sort_order: `"asc"` or `"desc"`. Server default: `"asc"`.
            skip: Offset. 0 or greater.
            limit: Page size, 1-100.

        Returns:
            A `TaskPage`. You can iterate it like a list or read `.count`.

        Raises:
            GuardError: If a filter value is invalid or both owner filters were given.
        """
        params = self._list_params(
            user_id=user_id,
            organization_id=organization_id,
            predictor_id=predictor_id,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        data = self._transport.request("GET", _BASE, params=params)
        return TaskPage.model_validate(data)

    def iter_all(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        predictor_id: Optional[IdLike] = None,
        sort_by: Optional[Union[TaskOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> Iterator[Task]:
        """
        Yield every matching task by fetching pages as needed.

        Args:
            user_id: Only tasks available to this user.
            organization_id: Only tasks available to this organization.
            predictor_id: Only tasks supported by this predictor.
            sort_by: The field to sort the results by.
            sort_order: The direction to sort the results.
            page_size: The number of items to fetch per page. Defaults to `MAX_LIMIT`.

        Yields:
            Each matching task with the oldest page first.

        Note:
            Pages are fetched lazily. Breaking out of the loop early stops the requests
            rather than paying for the whole set.
        """
        skip = 0
        while True:
            page = self.list(
                user_id=user_id,
                organization_id=organization_id,
                predictor_id=predictor_id,
                sort_by=sort_by,
                sort_order=sort_order,
                skip=skip,
                limit=page_size,
            )
            yield from page.data

            skip += len(page.data)
            if not page.data or len(page.data) < page_size or skip >= page.count:
                return


class AsyncTasks(_TasksBase):
    """
    Asynchronous task endpoints.

    This class mirrors `Tasks` method for method. See the synchronous methods for full
    argument details.
    """

    def __init__(self, transport: AsyncTransport) -> None:
        """
        Bind this resource to a transport.

        Args:
            transport: The client transport whose connection pool is shared.
        """
        self._transport = transport

    async def list(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        predictor_id: Optional[IdLike] = None,
        sort_by: Optional[Union[TaskOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> TaskPage:
        """
        List the tasks matching the given filters.

        Args:
            user_id: Only tasks available to this user.
            organization_id: Only tasks available to this organization.
            predictor_id: Only tasks supported by this predictor.
            sort_by: The field to sort the results by.
            sort_order: The direction to sort the results.
            skip: Offset. 0 or greater.
            limit: Page size, 1-100.

        Returns:
            A `TaskPage`. Review `Tasks.list` for full filter details.

        Raises:
            GuardError: If a filter value is invalid or both owner filters were given.
        """
        params = self._list_params(
            user_id=user_id,
            organization_id=organization_id,
            predictor_id=predictor_id,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        data = await self._transport.request("GET", _BASE, params=params)
        return TaskPage.model_validate(data)

    async def iter_all(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        predictor_id: Optional[IdLike] = None,
        sort_by: Optional[Union[TaskOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> AsyncIterator[Task]:
        """
        Yield every matching task by fetching pages as needed.

        Args:
            user_id: Only tasks available to this user.
            organization_id: Only tasks available to this organization.
            predictor_id: Only tasks supported by this predictor.
            sort_by: The field to sort the results by.
            sort_order: The direction to sort the results.
            page_size: The number of items to fetch per page. Defaults to `MAX_LIMIT`.

        Yields:
            Each matching task. Review `Tasks.iter_all` for more context.
        """
        skip = 0
        while True:
            page = await self.list(
                user_id=user_id,
                organization_id=organization_id,
                predictor_id=predictor_id,
                sort_by=sort_by,
                sort_order=sort_order,
                skip=skip,
                limit=page_size,
            )
            for task in page.data:
                yield task

            skip += len(page.data)
            if not page.data or len(page.data) < page_size or skip >= page.count:
                return
