"""
Low-level bindings for the activity endpoints.

`Activities` and `AsyncActivities` provide synchronous and asynchronous interfaces for
the API. Both share `_ActivitiesBase` for request building to guarantee identical
behavior.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict, Iterator, Optional, Sequence, Union
from uuid import UUID

from .exceptions import ActivityFailedError, GuardError, GuardTimeoutError
from .filters import (
    MAX_LIMIT,
    DateLike,
    IdLike,
    add_datetime,
    add_ids,
    add_sort,
    add_statuses,
    reject_too_old,
    validate_pagination,
)
from .media import MediaSource, resolve_media
from .models import (
    Activity,
    ActivityCreateResponse,
    ActivityDetail,
    ActivityOrder,
    ActivityPage,
    ActivityStatus,
    ActivityStatusResponse,
    MediaType,
    PresignedUploadData,
    SortOrder,
)
from .transport import AsyncTransport, SyncTransport

__all__ = ["Activities", "AsyncActivities", "IdLike"]

#: The base URL path for activity endpoints.
_BASE = "/api/v1/activities/"

#: Default seconds to wait between status checks when polling an activity.
DEFAULT_POLL_INTERVAL = 2

#: Default maximum seconds to wait for an activity to complete before raising a timeout
#: error.
DEFAULT_POLL_TIMEOUT = 90.0


class _ActivitiesBase:
    """
    Handles request and response shaping without performing network I/O.

    All logic that does not touch the network lives here. This ensures that the
    synchronous and asynchronous clients cannot drift in how they build or validate
    requests.
    """

    def __init__(self, default_space_id: Optional[IdLike] = None) -> None:
        """
        Remember the space to fall back on.

        Args:
            default_space_id: Used when a call omits `space_id`. It can be set once on
                the client instead of on every call.
        """
        self._default_space_id = default_space_id

    def _resolve_space_id(self, space_id: Optional[IdLike]) -> str:
        """
        Pick the space for this call.

        Args:
            space_id: The per-call value, or `None` to use the client default.

        Returns:
            The space id as a string.

        Raises:
            GuardError: Neither source supplied a space id. The message names every way
                to set it, including the `GUARD_SPACE_ID` environment variable.
        """
        effective = space_id if space_id is not None else self._default_space_id
        if effective is None:
            raise GuardError(
                "space_id is required. Pass it to this call or set it on the client: "
                "GuardClient(api_key=..., space_id=...)"
            )
        return str(effective)

    def _create_payload(
        self,
        *,
        media_type: Union[MediaType, str],
        media_size: int,
        space_id: Optional[IdLike],
        user_id: Optional[IdLike],
        account_id: Optional[IdLike],
    ) -> Dict[str, Any]:
        """
        Build the body for creating an activity.

        Returns:
            The request body, omitting every owner id that was not supplied.

        Raises:
            GuardError: No space id is available from the call or the client default.
        """
        resolved_type = (
            media_type.value if isinstance(media_type, MediaType) else media_type
        )
        payload: Dict[str, Any] = {
            "space_id": self._resolve_space_id(space_id),
            "media_type": resolved_type,
            "media_size": media_size,
        }
        for key, value in (
            ("user_id", user_id),
            ("account_id", account_id),
        ):
            if value is not None:
                payload[key] = str(value)
        return payload

    @staticmethod
    def _list_params(
        *,
        user_id: Optional[IdLike],
        organization_id: Optional[IdLike],
        space_id: Optional[IdLike],
        start_date: Optional[DateLike],
        end_date: Optional[DateLike],
        statuses: Optional[Sequence[Union[ActivityStatus, str]]],
        sort_by: Optional[Union[ActivityOrder, str]],
        sort_order: Optional[Union[SortOrder, str]],
        skip: int,
        limit: int,
    ) -> Dict[str, Any]:
        """
        Build the query parameters for a list request.

        Returns:
            The query dict, omitting every unset filter.

        Raises:
            GuardError: A filter value is invalid.
        """
        validate_pagination(skip, limit)
        reject_too_old(start_date, field="start_date")

        params: Dict[str, Any] = {"skip": skip, "limit": limit}
        add_ids(
            params,
            user_id=user_id,
            organization_id=organization_id,
            space_id=space_id,
        )
        add_datetime(params, "start_date", start_date)
        add_datetime(params, "end_date", end_date)
        add_statuses(params, statuses, ActivityStatus)
        add_sort(params, sort_by, sort_order, ActivityOrder)
        return params

    @staticmethod
    def _check_terminal(
        status: ActivityStatusResponse, activity_id: IdLike
    ) -> Optional[ActivityStatusResponse]:
        """
        Return the activity status if valid, raising on failure.
        """
        if status.status is ActivityStatus.COMPLETED:
            return status
        if status.status in (ActivityStatus.FAILED, ActivityStatus.CANCELED):
            raise ActivityFailedError(
                f"Activity {activity_id} {status.status.value}",
                status=status.status.value,
                activity_id=_as_uuid(activity_id),
            )
        return None


def _as_uuid(value: IdLike) -> Optional[UUID]:
    """
    Return as UUID.

    Args:
        value: A UUID or its string form.

    Returns:
        The UUID, or `None` when the value is not a valid UUID. An exception attribute
        is not worth failing a request over.
    """
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None


class Activities(_ActivitiesBase):
    """
    Synchronous activity endpoints.

    This is reached through the client rather than constructed directly, and it shares
    the client's connection pool.
    """

    def __init__(
        self, transport: SyncTransport, *, default_space_id: Optional[IdLike] = None
    ) -> None:
        """
        Bind this resource to a transport with an optional default space id.

        Args:
            transport: The client's transport, whose connection pool is shared.
            default_space_id: Used when a call omits it. The id can be set once on the
                client instead of on every call.
        """
        super().__init__(default_space_id)
        self._transport = transport

    def create(
        self,
        *,
        media_type: Union[MediaType, str],
        media_size: int,
        space_id: Optional[IdLike] = None,
        user_id: Optional[IdLike] = None,
        account_id: Optional[IdLike] = None,
    ) -> ActivityCreateResponse:
        """
        Create an activity and get back its presigned upload target.

        Args:
            media_type: MIME type of the media you will upload.
            media_size: Its size in bytes.
            space_id: Overrides the client default space id.
            user_id: Owning user, for a user-owned activity.
            account_id: Owning service account.

        Returns:
            The created activity, including the one-time `upload_data`.

        Raises:
            GuardError: No space id is available.
            GuardAPIError: The API rejected the request.

        Note:
            This request is safe to replay and is the only POST the client retries.
            A duplicate create leaves an unused activity rather than performing an
            action twice.
        """
        payload = self._create_payload(
            media_type=media_type,
            media_size=media_size,
            space_id=space_id,
            user_id=user_id,
            account_id=account_id,
        )
        data = self._transport.request("POST", _BASE, json=payload, retry=True)
        return ActivityCreateResponse.model_validate(data)

    def upload(
        self,
        upload_data: PresignedUploadData,
        source: MediaSource,
        *,
        media_type: Optional[Union[MediaType, str]] = None,
        filename: Optional[str] = None,
    ) -> None:
        """
        Upload the media bytes to the presigned storage target.

        Args:
            upload_data: The target returned from `create`.
            source: A path, raw `bytes`, or an open binary file.
            media_type: Skips MIME detection when you already know the type.
            filename: Name for the multipart file part.

        Raises:
            GuardUploadError: Storage rejected the upload.
            UnsupportedMediaTypeError: The media type is not one the API accepts.
        """
        data, _, name = resolve_media(source, media_type=media_type, filename=filename)
        self._transport.upload(upload_data.url, upload_data.fields, name, data)

    def confirm(self, activity_id: IdLike) -> Activity:
        """
        Confirm the upload, which moves the activity into processing.

        Args:
            activity_id: The activity whose media has been uploaded.

        Returns:
            The activity, which is now processing.

        Raises:
            GuardNotFoundError: Unknown activity, or not owned by you.
        """
        data = self._transport.request("POST", f"{_BASE}{activity_id}/confirm")
        return Activity.model_validate(data)

    def get_status(self, activity_id: IdLike) -> ActivityStatusResponse:
        """
        Read just enough of an activity to know its current state.

        This response is deliberately smaller than `get` so polling an activity does
        not repeatedly transfer its results.

        Args:
            activity_id: The activity to check.

        Returns:
            The id and current status of the activity.

        Raises:
            GuardNotFoundError: Unknown activity, or not owned by you.
        """
        data = self._transport.request("GET", f"{_BASE}{activity_id}/status")
        return ActivityStatusResponse.model_validate(data)

    def get(self, activity_id: IdLike) -> ActivityDetail:
        """
        Read an activity in full, including its results.

        Args:
            activity_id: The activity to fetch.

        Returns:
            The activity. `result_payload` is `None` until processing finishes, and
            `payed_tokens` carries the actual cost.

        Raises:
            GuardNotFoundError: Unknown activity, or not owned by you.
        """
        data = self._transport.request("GET", f"{_BASE}{activity_id}")
        return ActivityDetail.model_validate(data)

    def list(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        space_id: Optional[IdLike] = None,
        start_date: Optional[DateLike] = None,
        end_date: Optional[DateLike] = None,
        statuses: Optional[Sequence[Union[ActivityStatus, str]]] = None,
        sort_by: Optional[Union[ActivityOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> ActivityPage:
        """
        List activities matching the given filters.

        Args:
            user_id: Only this user's activities. This may be combined with
                `organization_id`, and the API will apply both.
            organization_id: Only this organization's activities.
            space_id: Only activities in this space.
            start_date: A `datetime`, `date`, or an ISO-8601 string. The API keeps only
                one year of history. Anything older is rejected locally before the
                request is sent. Omitting it defaults to exactly one year ago. A naive
                datetime is interpreted as UTC to match the server.
            end_date: Accepts the same types as start_date. Defaults to now.
            statuses: Keep only activities with these statuses.
            sort_by: Valid options include `"created_at"`. Server default: `created_at`.
            sort_order: `"asc"` or `"desc"`. Server default for activities: `desc`.
            skip: Offset, 0 or greater.
            limit: Page size, 1-100.

        Returns:
            An `ActivityPage`. You can iterate it like a list, or read `.count` for the
            total matching the filter across all pages.
        """
        params = self._list_params(
            user_id=user_id,
            organization_id=organization_id,
            space_id=space_id,
            start_date=start_date,
            end_date=end_date,
            statuses=statuses,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        data = self._transport.request("GET", _BASE, params=params)
        return ActivityPage.model_validate(data)

    def iter_all(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        space_id: Optional[IdLike] = None,
        start_date: Optional[DateLike] = None,
        end_date: Optional[DateLike] = None,
        statuses: Optional[Sequence[Union[ActivityStatus, str]]] = None,
        sort_by: Optional[Union[ActivityOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> Iterator[Activity]:
        """
        Yield every matching activity by fetching pages as needed.

        Yields:
            Each matching activity, starting with the oldest page first.

        Note:
            Pages are fetched lazily. Breaking out of the loop early stops the requests
            rather than paying for the whole set.
        """
        skip = 0
        while True:
            page = self.list(
                user_id=user_id,
                organization_id=organization_id,
                space_id=space_id,
                start_date=start_date,
                end_date=end_date,
                statuses=statuses,
                sort_by=sort_by,
                sort_order=sort_order,
                skip=skip,
                limit=page_size,
            )
            yield from page.data

            skip += len(page.data)
            # A short page means the end; the length check also guarantees termination
            # if `count` is stale or wrong.
            if not page.data or len(page.data) < page_size or skip >= page.count:
                return

    def wait_until_done(
        self,
        activity_id: IdLike,
        *,
        interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_POLL_TIMEOUT,
    ) -> ActivityStatusResponse:
        """
        Poll until the activity completes.

        Args:
            activity_id: The activity to check.
            interval: Seconds to wait between polling requests.
            timeout: Maximum seconds to wait before raising an error.

        Raises:
            ActivityFailedError: The activity ended as `failed` or `canceled`.
            GuardTimeoutError: The specified timeout seconds elapsed without reaching a
                terminal status.
        """
        deadline = time.monotonic() + timeout
        while True:
            status = self.get_status(activity_id)
            done = self._check_terminal(status, activity_id)
            if done is not None:
                return done
            if time.monotonic() + interval >= deadline:
                raise GuardTimeoutError(
                    f"Activity {activity_id} did not complete within {timeout}s "
                    f"(last status: {status.status.value})",
                    activity_id=_as_uuid(activity_id),
                )
            time.sleep(interval)


class AsyncActivities(_ActivitiesBase):
    """
    Asynchronous activity endpoints.

    This is reached through the client rather than constructed directly, and it shares
    the client's connection pool. It mirrors `Activities` method for method. Review the
    synchronous methods for argument details.
    """

    def __init__(
        self, transport: AsyncTransport, *, default_space_id: Optional[IdLike] = None
    ) -> None:
        """
        Bind this resource to a transport with an optional default space id.

        Args:
            transport: The client's transport, whose connection pool is shared.
            default_space_id: Used when a call omits it. The id can be set once on the
                client instead of on every call.
        """
        super().__init__(default_space_id)
        self._transport = transport

    async def create(
        self,
        *,
        media_type: Union[MediaType, str],
        media_size: int,
        space_id: Optional[IdLike] = None,
        user_id: Optional[IdLike] = None,
        account_id: Optional[IdLike] = None,
    ) -> ActivityCreateResponse:
        """
        Create an activity and get back its presigned upload target.

        Args:
            media_type: MIME type of the media you will upload.
            media_size: Its size in bytes.
            space_id: Overrides the client default space id.
            user_id: Owning user, for a user-owned activity.
            account_id: Owning service account.

        Returns:
            The created activity, including the one-time `upload_data`.

        Raises:
            GuardError: No space id is available.
            GuardAPIError: The API rejected the request.

        Note:
            This request is safe to replay and is the only POST the client retries. A
            duplicate create leaves an unused activity rather than performing an action
            twice.
        """
        payload = self._create_payload(
            media_type=media_type,
            media_size=media_size,
            space_id=space_id,
            user_id=user_id,
            account_id=account_id,
        )
        data = await self._transport.request("POST", _BASE, json=payload, retry=True)
        return ActivityCreateResponse.model_validate(data)

    async def upload(
        self,
        upload_data: PresignedUploadData,
        source: MediaSource,
        *,
        media_type: Optional[Union[MediaType, str]] = None,
        filename: Optional[str] = None,
    ) -> None:
        """
        Upload the media bytes to the presigned storage target.

        Args:
            upload_data: The target returned from `create`.
            source: A path, raw `bytes`, or an open binary file.
            media_type: Skips MIME detection when you already know the type.
            filename: Name for the multipart file part.

        Raises:
            GuardUploadError: Storage rejected the upload.
            UnsupportedMediaTypeError: The media type is not one the API accepts.
        """
        data, _, name = resolve_media(source, media_type=media_type, filename=filename)
        await self._transport.upload(upload_data.url, upload_data.fields, name, data)

    async def confirm(self, activity_id: IdLike) -> Activity:
        """
        Confirm the upload, which moves the activity into processing.

        Args:
            activity_id: The activity whose media has been uploaded.

        Returns:
            The activity, which is now processing.

        Raises:
            GuardNotFoundError: Unknown activity, or not owned by you.
        """
        data = await self._transport.request("POST", f"{_BASE}{activity_id}/confirm")
        return Activity.model_validate(data)

    async def get_status(self, activity_id: IdLike) -> ActivityStatusResponse:
        """
        Read just enough of an activity to know its current state.

        This response is deliberately smaller than `get` so polling an activity does not
        repeatedly transfer its results.

        Args:
            activity_id: The activity to check.

        Returns:
            The id and current status of the activity.

        Raises:
            GuardNotFoundError: Unknown activity, or not owned by you.
        """
        data = await self._transport.request("GET", f"{_BASE}{activity_id}/status")
        return ActivityStatusResponse.model_validate(data)

    async def get(self, activity_id: IdLike) -> ActivityDetail:
        """
        Read an activity in full, including its results.

        Args:
            activity_id: The activity to fetch.

        Returns:
            The activity. `result_payload` is `None` until processing finishes, and
            `payed_tokens` carries the actual cost.

        Raises:
            GuardNotFoundError: Unknown activity, or not owned by you.
        """
        data = await self._transport.request("GET", f"{_BASE}{activity_id}")
        return ActivityDetail.model_validate(data)

    async def list(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        space_id: Optional[IdLike] = None,
        start_date: Optional[DateLike] = None,
        end_date: Optional[DateLike] = None,
        statuses: Optional[Sequence[Union[ActivityStatus, str]]] = None,
        sort_by: Optional[Union[ActivityOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> ActivityPage:
        """
        List activities matching the given filters.

        Args:
            user_id: Only this user's activities. This may be combined with
                `organization_id`, and the API will apply both.
            organization_id: Only this organization's activities.
            space_id: Only activities in this space.
            start_date: A `datetime`, `date`, or an ISO-8601 string. The API keeps only
                one year of history. Anything older is rejected locally before the
                request is sent. Omitting it defaults to exactly one year ago. A naive
                datetime is interpreted as UTC to match the server.
            end_date: Accepts the same types as start_date. Defaults to now.
            statuses: Keep only activities with these statuses.
            sort_by: Valid options include `"created_at"`. Server default: `created_at`.
            sort_order: `"asc"` or `"desc"`. Server default for activities: `desc`.
            skip: Offset, 0 or greater.
            limit: Page size, 1-100.

        Returns:
            An `ActivityPage`. You can iterate it like a list, or read `.count` for the
            total matching the filter across all pages.
        """
        params = self._list_params(
            user_id=user_id,
            organization_id=organization_id,
            space_id=space_id,
            start_date=start_date,
            end_date=end_date,
            statuses=statuses,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        data = await self._transport.request("GET", _BASE, params=params)
        return ActivityPage.model_validate(data)

    async def iter_all(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        space_id: Optional[IdLike] = None,
        start_date: Optional[DateLike] = None,
        end_date: Optional[DateLike] = None,
        statuses: Optional[Sequence[Union[ActivityStatus, str]]] = None,
        sort_by: Optional[Union[ActivityOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> AsyncIterator[Activity]:
        """
        Yield every matching activity by fetching pages as needed.

        Args:
            user_id: Only this user's activities. This may be combined with
                `organization_id`, and the API will apply both.
            organization_id: Only this organization's activities.
            space_id: Only activities in this space.
            start_date: A `datetime`, `date`, or an ISO-8601 string. The API keeps only
                one year of history. Anything older is rejected locally before the
                request is sent. Omitting it defaults to exactly one year ago. A naive
                datetime is interpreted as UTC to match the server.
            end_date: Accepts the same types as start_date. Defaults to now.
            statuses: Keep only activities with these statuses.
            sort_by: Valid options include `"created_at"`. Server default: `created_at`.
            sort_order: `"asc"` or `"desc"`. Server default for activities: `desc`.
            page_size: Page size, 1-100.

        Yields:
            Each matching activity, starting with the oldest page first.

        Note:
            Pages are fetched lazily. Breaking out of the loop early stops the requests
            rather than paying for the whole set.
        """
        skip = 0
        while True:
            page = await self.list(
                user_id=user_id,
                organization_id=organization_id,
                space_id=space_id,
                start_date=start_date,
                end_date=end_date,
                statuses=statuses,
                sort_by=sort_by,
                sort_order=sort_order,
                skip=skip,
                limit=page_size,
            )
            for activity in page.data:
                yield activity

            skip += len(page.data)
            if not page.data or len(page.data) < page_size or skip >= page.count:
                return

    async def wait_until_done(
        self,
        activity_id: IdLike,
        *,
        interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_POLL_TIMEOUT,
    ) -> ActivityStatusResponse:
        """
        Poll until the activity completes.

        Args:
            activity_id: The activity to check.
            interval: Seconds to wait between polling requests.
            timeout: Maximum seconds to wait before raising an error.

        Returns:
            The terminal status. Check `Activities.wait_until_done` for reference.

        Raises:
            ActivityFailedError: The activity ended as `failed` or `canceled`.
            GuardTimeoutError: The deadline passed while it was still running.

        Note:
            This method sleeps with `asyncio.sleep`, ensuring that waiting never blocks
            the event loop.
        """
        deadline = time.monotonic() + timeout
        while True:
            status = await self.get_status(activity_id)
            done = self._check_terminal(status, activity_id)
            if done is not None:
                return done
            if time.monotonic() + interval >= deadline:
                raise GuardTimeoutError(
                    f"Activity {activity_id} did not complete within {timeout}s "
                    f"(last status: {status.status.value})",
                    activity_id=_as_uuid(activity_id),
                )
            await asyncio.sleep(interval)
