"""
Low-level bindings for the `/api/v1/activities/shares` endpoints.

A share is a public link to one task result for an activity. Three API facts shape this
module: First, the activity must have finished processing. The task must appear in its
results, or the API answers 400 `Invalid task`. Second, only one share is allowed per
activity, and there is no delete endpoint. A link cannot be revoked; it can only be left
to expire. Third, authentication requires a full identity. As everywhere in this client,
an API key is required.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Iterator, Optional, Sequence, Union

from .exceptions import GuardError
from .filters import (
    MAX_LIMIT,
    IdLike,
    add_ids,
    add_sort,
    add_statuses,
    validate_pagination,
)
from .models import (
    ActivityResultItem,
    ResultSource,
    Share,
    ShareOrder,
    SharePage,
    ShareStatus,
    SortOrder,
    activity_id_of,
    result_items_of,
)
from .transport import AsyncTransport, SyncTransport

__all__ = ["AsyncShares", "Shares"]

#: The base URL path for the shares endpoints.
_BASE = "/api/v1/activities/shares/"

#: Server-side minimum bound on `expires_in` (days).
MIN_EXPIRES_IN = 1

#: Server-side maximum bound on `expires_in` (days).
MAX_EXPIRES_IN = 7

#: A type alias for share statuses.
StatusLike = Union[ShareStatus, str]


class _SharesBase:
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
        statuses: Optional[Sequence[StatusLike]],
        sort_by: Optional[Union[ShareOrder, str]],
        sort_order: Optional[Union[SortOrder, str]],
        skip: int,
        limit: int,
    ) -> Dict[str, Any]:
        """
        Build the query parameters for a list request.

        Validating here means a bad filter never reaches the network. The arguments
        mirror `list`, but none are optional here.

        Args:
            user_id: Only shares belonging to this user.
            organization_id: Only shares belonging to this organization.
            statuses: Keep only shares with these statuses.
            sort_by: The field to sort the results by.
            sort_order: The direction to sort the results.
            skip: Offset of the results. Must be 0 or greater.
            limit: Page size, 1-100.

        Returns:
            The query dict with every unset filter omitted.

        Raises:
            GuardError: If a filter value is invalid.
        """
        validate_pagination(skip, limit)

        params: Dict[str, Any] = {"skip": skip, "limit": limit}
        # like /activities/ and unlike /spaces/, this route applies both owner filters
        # independently rather than rejecting the combination
        add_ids(params, user_id=user_id, organization_id=organization_id)
        add_statuses(params, statuses, ShareStatus)
        add_sort(params, sort_by, sort_order, ShareOrder)
        return params

    @staticmethod
    def _create_payload(
        *,
        activity_id: IdLike,
        task_id: IdLike,
        expires_in: Optional[int],
    ) -> Dict[str, Any]:
        """
        Build the JSON body for creating a share.

        The arguments mirror `Shares.create`, but none are optional here.

        Args:
            activity_id: The ID of the activity to share.
            task_id: The ID of the task result to share.
            expires_in: Lifetime in days, 1-7.

        Returns:
            The request body. It omits `expires_in` when unset so the server default of
                seven days applies.

        Raises:
            GuardError: If `expires_in` is not an integer or falls outside 1-7.
        """
        payload: Dict[str, Any] = {
            "activity_id": str(activity_id),
            "task_id": str(task_id),
        }
        if expires_in is not None:
            # bool subclasses int, so True would otherwise pass as 1 day
            if isinstance(expires_in, bool) or not isinstance(expires_in, int):
                raise GuardError(
                    f"Invalid expires_in={expires_in!r}. Expected an integer number "
                    f"of days"
                )
            if not MIN_EXPIRES_IN <= expires_in <= MAX_EXPIRES_IN:
                raise GuardError(
                    f"Invalid expires_in={expires_in}. Expected between "
                    f"{MIN_EXPIRES_IN} and {MAX_EXPIRES_IN} days"
                )
            payload["expires_in"] = expires_in
        return payload

    @staticmethod
    def _resolve_source(source: ResultSource, item: ActivityResultItem) -> IdLike:
        """
        Get the activity id while checking the item really belongs to this result.

        The membership check is the local stand-in for the API 400 `Invalid task` error,
        which it raises when the task is absent from the activity results.

        Args:
            source: A result from `analyze()` or `activities.get()`.
            item: The result item being shared.

        Returns:
            The activity id behind the result.

        Raises:
            GuardError: If the source came from the local engine, meaning no activity
                exists on the server, or if the item belongs to a different activity.
        """
        activity_id = activity_id_of(source)
        if activity_id is None:
            raise GuardError(
                "This result came from the local engine, so no activity exists on the "
                "server to share. Shares apply to cloud results only."
            )

        known = result_items_of(source)
        if not any(existing.task_id == item.task_id for existing in known):
            available = ", ".join(str(existing.task_id) for existing in known) or "none"
            raise GuardError(
                f"task_id {item.task_id} is not part of this activity's results "
                f"(available: {available}). Only a completed activity can be shared."
            )
        return activity_id


class Shares(_SharesBase):
    """
    Synchronous activity-share endpoints.

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
        statuses: Optional[Sequence[StatusLike]] = None,
        sort_by: Optional[Union[ShareOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> SharePage:
        """
        List the activity shares visible to you.

        Args:
            user_id: Only your own ID is accepted. Anything else returns a 404. A
                service account passing this at all gets a 403.
            organization_id: Requires editor or owner membership.
            statuses: Valid options include `"active"` and/or `"expired"`.
            sort_by: Valid options include `"created_at"`. Server default:
                `"created_at"`.
            sort_order: `"asc"` or `"desc"`. Server default for shares: `"desc"`.
            skip: Offset. 0 or greater.
            limit: Page size, 1-100.

        Returns:
            A `SharePage`. You can iterate it like a list or read `.count`.

        Note:
            There is no `activity_id` filter. Shares cannot be looked up by the activity
            they belong to.
        """
        params = self._list_params(
            user_id=user_id,
            organization_id=organization_id,
            statuses=statuses,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        data = self._transport.request("GET", _BASE, params=params)
        return SharePage.model_validate(data)

    def iter_all(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        statuses: Optional[Sequence[StatusLike]] = None,
        sort_by: Optional[Union[ShareOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> Iterator[Share]:
        """
        Yield every matching share by fetching pages as needed.

        Args:
            user_id: Only shares available to this user.
            organization_id: Only shares available to this organization.
            statuses: Valid options include `"active"` and/or `"expired"`.
            sort_by: Valid options include `"created_at"`.
            sort_order: `"asc"` or `"desc"`.
            page_size: The number of items to fetch per page. Defaults to `MAX_LIMIT`.

        Yields:
            Each matching share with the oldest page first.

        Note:
            Pages are fetched lazily. Breaking out of the loop early stops the requests
            rather than paying for the whole set.
        """
        skip = 0
        while True:
            page = self.list(
                user_id=user_id,
                organization_id=organization_id,
                statuses=statuses,
                sort_by=sort_by,
                sort_order=sort_order,
                skip=skip,
                limit=page_size,
            )
            yield from page.data

            skip += len(page.data)
            if not page.data or len(page.data) < page_size or skip >= page.count:
                return

    def get(self, share_id: IdLike) -> Share:
        """
        Read one share.

        Args:
            share_id: The ID of the share to fetch.

        Returns:
            The requested `Share`.

        Raises:
            GuardNotFoundError: If the share is unknown, expired beyond visibility, or
                not yours.
        """
        data = self._transport.request("GET", f"{_BASE}{share_id}")
        return Share.model_validate(data)

    def create(
        self,
        *,
        activity_id: IdLike,
        task_id: IdLike,
        expires_in: Optional[int] = None,
    ) -> Share:
        """
        Create a public link to one task result.

        Args:
            activity_id: The activity to share. Must be your own and completed.
            task_id: Which task result the link shows. It must appear in the activity
                results or the API answers 400.
            expires_in: Lifetime in days, 1-7. Omitting it means the server
                default of 7 applies.

        Returns:
            The created `Share` carrying `share_url`.

        Raises:
            GuardError: If `expires_in` is out of range. Raised before any request.
            GuardNotFoundError: If the activity is unknown, not yours, or its media is
                gone.
            GuardConflictError: If this activity has already been shared.
            GuardAPIError: 400 error if the task is not in the activity results,
                or if the task has no associated media.

        Examples:
            ```python
            share = client.shares.create(activity_id=A, task_id=T, expires_in=1)
            print(share.share_url)
            ```

        Note:
            There is no way to revoke a share. It lives until it expires.
        """
        payload = self._create_payload(
            activity_id=activity_id, task_id=task_id, expires_in=expires_in
        )
        # not retried: a replay would trip the one-share-per-activity guard and report a
        # 409 for a share that actually succeeded
        data = self._transport.request("POST", _BASE, json=payload)
        return Share.model_validate(data)

    def create_for(
        self,
        source: ResultSource,
        item: ActivityResultItem,
        *,
        expires_in: Optional[int] = None,
    ) -> Share:
        """
        Share using the objects you already hold.

        Args:
            source: Either a `DetectionResult` from `GuardClient.analyze` or an
                `ActivityDetail` from `activities.get()`.
            item: The `ActivityResultItem` being shared.
            expires_in: Lifetime in days, 1-7. Omitting it means the server default of 7
                applies.

        Returns:
            The created `Share` carrying `share_url`.

        Raises:
            GuardError: If the source is a local result or the item is not one of its
                results. Both checks happen before any request is sent.
        """
        activity_id = self._resolve_source(source, item)
        return self.create(
            activity_id=activity_id, task_id=item.task_id, expires_in=expires_in
        )


class AsyncShares(_SharesBase):
    """
    Asynchronous activity-share endpoints.

    This class mirrors `Shares` method for method. See the synchronous methods for full
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
        statuses: Optional[Sequence[StatusLike]] = None,
        sort_by: Optional[Union[ShareOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> SharePage:
        """
        List the shares matching the given filters.

        Args:
            user_id: Only your own ID is accepted. Anything else returns a 404. A
                service account passing this at all gets a 403.
            organization_id: Requires editor or owner membership.
            statuses: Valid options include `"active"` and/or `"expired"`.
            sort_by: Valid options include `"created_at"`.
            sort_order: `"asc"` or `"desc"`.
            skip: Offset. 0 or greater.
            limit: Page size, 1-100.

        Returns:
            A `SharePage`. Review `Shares.list` for full filter details.
        """
        params = self._list_params(
            user_id=user_id,
            organization_id=organization_id,
            statuses=statuses,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        data = await self._transport.request("GET", _BASE, params=params)
        return SharePage.model_validate(data)

    async def iter_all(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        statuses: Optional[Sequence[StatusLike]] = None,
        sort_by: Optional[Union[ShareOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> AsyncIterator[Share]:
        """
        Yield every matching share by fetching pages as needed.

        Args:
            user_id: Only shares available to this user.
            organization_id: Only shares available to this organization.
            statuses: Valid options include `"active"` and/or `"expired"`.
            sort_by: Valid options include `"created_at"`.
            sort_order: `"asc"` or `"desc"`.
            page_size: The number of items to fetch per page. Defaults to `MAX_LIMIT`.

        Yields:
            Each matching share. Review `Shares.iter_all` for more context.
        """
        skip = 0
        while True:
            page = await self.list(
                user_id=user_id,
                organization_id=organization_id,
                statuses=statuses,
                sort_by=sort_by,
                sort_order=sort_order,
                skip=skip,
                limit=page_size,
            )
            for share in page.data:
                yield share

            skip += len(page.data)
            if not page.data or len(page.data) < page_size or skip >= page.count:
                return

    async def get(self, share_id: IdLike) -> Share:
        """
        Read one share.

        Args:
            share_id: The ID of the share to fetch.

        Returns:
            The requested `Share`.

        Raises:
            GuardNotFoundError: If the share is unknown or not yours.
        """
        data = await self._transport.request("GET", f"{_BASE}{share_id}")
        return Share.model_validate(data)

    async def create(
        self,
        *,
        activity_id: IdLike,
        task_id: IdLike,
        expires_in: Optional[int] = None,
    ) -> Share:
        """
        Create a public link to one task result.

        Args:
            activity_id: The activity to share. Must be your own and completed.
            task_id: Which task result the link shows. It must appear in the activity
                results or the API answers 400.
            expires_in: Lifetime in days, 1-7. Omitting it means the server default of
                7 applies.

        Returns:
            The created `Share` carrying `share_url`. Review `Shares.create` for more
                details.

        Raises:
            GuardError: If `expires_in` is out of range.
            GuardConflictError: If this activity has already been shared.
            GuardNotFoundError: If the activity is unknown, not yours, or its media is
                gone.
        """
        payload = self._create_payload(
            activity_id=activity_id, task_id=task_id, expires_in=expires_in
        )
        # not retried: see the note on Shares.create
        data = await self._transport.request("POST", _BASE, json=payload)
        return Share.model_validate(data)

    async def create_for(
        self,
        source: ResultSource,
        item: ActivityResultItem,
        *,
        expires_in: Optional[int] = None,
    ) -> Share:
        """
        Share using the objects you already hold.

        Args:
            source: Either a `DetectionResult` from `GuardClient.analyze` or an
                `ActivityDetail` from `activities.get()`.
            item: The `ActivityResultItem` being shared.
            expires_in: Lifetime in days, 1-7. Omitting it means the server default of 7
                applies.

        Returns:
            The created `Share`. Review `Shares.create_for` for more details.

        Raises:
            GuardError: If the source is a local result or the item is not one of its
                results. Both checks happen before any request is sent.
        """
        activity_id = self._resolve_source(source, item)
        return await self.create(
            activity_id=activity_id, task_id=item.task_id, expires_in=expires_in
        )
