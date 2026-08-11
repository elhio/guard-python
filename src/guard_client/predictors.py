"""
Low-level bindings for the `/api/v1/predictors/` endpoints.

A predictor is the model that powers a space, and its ID is required to create one.
Listing predictors is how a caller finds a valid `predictor_id`.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Iterator, Optional, Sequence, Union

from .filters import (
    MAX_LIMIT,
    IdLike,
    add_ids,
    add_sort,
    id_list,
    reject_conflicting_owners,
    validate_pagination,
)
from .models import Predictor, PredictorOrder, PredictorPage, SortOrder
from .transport import AsyncTransport, SyncTransport

__all__ = ["AsyncPredictors", "Predictors"]

#: The base URL path for predictor endpoints.
_BASE = "/api/v1/predictors/"


class _PredictorsBase:
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
        supported_task_ids: Optional[Sequence[IdLike]],
        sort_by: Optional[Union[PredictorOrder, str]],
        sort_order: Optional[Union[SortOrder, str]],
        skip: int,
        limit: int,
    ) -> Dict[str, Any]:
        """
        Build the query parameters for a list request.

        Validating here means a bad filter never reaches the network. The arguments
        mirror `list`, but none are optional here.

        Args:
            user_id: Only predictors available to this user.
            organization_id: Only predictors available to this organization.
            supported_task_ids: Only predictors supporting all of these tasks.
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
        add_ids(params, user_id=user_id, organization_id=organization_id)
        task_ids = id_list(supported_task_ids, field="supported_task_ids")
        if task_ids:
            params["supported_task_ids"] = task_ids
        add_sort(params, sort_by, sort_order, PredictorOrder)
        return params


class Predictors(_PredictorsBase):
    """
    Synchronous predictor endpoints.

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
        supported_task_ids: Optional[Sequence[IdLike]] = None,
        sort_by: Optional[Union[PredictorOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> PredictorPage:
        """
        List the available predictors.

        Args:
            user_id: Only predictors available to this user.
            organization_id: Only predictors available to this organization.
            supported_task_ids: Only predictors supporting all of these tasks.
            sort_by: Valid options include `"name"` or `"created_at"`. Server default:
                `"name"`.
            sort_order: `"asc"` or `"desc"`. Server default: `"asc"`.
            skip: Offset. 0 or greater.
            limit: Page size, 1-100.

        Returns:
            A `PredictorPage`. You can iterate it like a list or read `.count`.

        Raises:
            GuardError: If a filter value is invalid or both owner filters were given.
        """
        params = self._list_params(
            user_id=user_id,
            organization_id=organization_id,
            supported_task_ids=supported_task_ids,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        data = self._transport.request("GET", _BASE, params=params)
        return PredictorPage.model_validate(data)

    def iter_all(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        supported_task_ids: Optional[Sequence[IdLike]] = None,
        sort_by: Optional[Union[PredictorOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> Iterator[Predictor]:
        """
        Yield every matching predictor by fetching pages as needed.

        Args:
            user_id: Only predictors available to this user.
            organization_id: Only predictors available to this organization.
            supported_task_ids: Only predictors supporting all of these tasks.
            sort_by: Valid options include `"name"` or `"created_at"`.
            sort_order: `"asc"` or `"desc"`.
            page_size: The number of items to fetch per page. Defaults to `MAX_LIMIT`.

        Yields:
            Each matching predictor with the oldest page first.

        Note:
            Pages are fetched lazily. Breaking out of the loop early stops the requests
            rather than paying for the whole set.
        """
        skip = 0
        while True:
            page = self.list(
                user_id=user_id,
                organization_id=organization_id,
                supported_task_ids=supported_task_ids,
                sort_by=sort_by,
                sort_order=sort_order,
                skip=skip,
                limit=page_size,
            )
            yield from page.data

            skip += len(page.data)
            if not page.data or len(page.data) < page_size or skip >= page.count:
                return


class AsyncPredictors(_PredictorsBase):
    """
    Asynchronous predictor endpoints.

    This class mirrors `Predictors` method for method. See the synchronous methods for
    full argument details.
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
        supported_task_ids: Optional[Sequence[IdLike]] = None,
        sort_by: Optional[Union[PredictorOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> PredictorPage:
        """
        List the predictors matching the given filters.

        Args:
            user_id: Only predictors available to this user.
            organization_id: Only predictors available to this organization.
            supported_task_ids: Only predictors supporting all of these tasks.
            sort_by: Valid options include `"name"` or `"created_at"`.
            sort_order: `"asc"` or `"desc"`.
            skip: Offset. 0 or greater.
            limit: Page size, 1-100.

        Returns:
            A `PredictorPage`. Review `Predictors.list` for full filter details.

        Raises:
            GuardError: If a filter value is invalid or both owner filters were given.
        """
        params = self._list_params(
            user_id=user_id,
            organization_id=organization_id,
            supported_task_ids=supported_task_ids,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        data = await self._transport.request("GET", _BASE, params=params)
        return PredictorPage.model_validate(data)

    async def iter_all(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        supported_task_ids: Optional[Sequence[IdLike]] = None,
        sort_by: Optional[Union[PredictorOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> AsyncIterator[Predictor]:
        """
        Yield every matching predictor by fetching pages as needed.

        Args:
            user_id: Only predictors available to this user.
            organization_id: Only predictors available to this organization.
            supported_task_ids: Only predictors supporting all of these tasks.
            sort_by: Valid options include `"name"` or `"created_at"`.
            sort_order: `"asc"` or `"desc"`.
            page_size: The number of items to fetch per page. Defaults to `MAX_LIMIT`.

        Yields:
            Each matching predictor. Review `Predictors.iter_all` for more
            context.
        """
        skip = 0
        while True:
            page = await self.list(
                user_id=user_id,
                organization_id=organization_id,
                supported_task_ids=supported_task_ids,
                sort_by=sort_by,
                sort_order=sort_order,
                skip=skip,
                limit=page_size,
            )
            for predictor in page.data:
                yield predictor

            skip += len(page.data)
            if not page.data or len(page.data) < page_size or skip >= page.count:
                return
