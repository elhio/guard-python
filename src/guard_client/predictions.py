"""
Low-level bindings for the prediction endpoints.

`Predictions` and `AsyncPredictions` provide synchronous and asynchronous interfaces for
the API. Both share `_PredictionsBase` for request building to guarantee identical
behavior.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict, Iterator, Optional, Sequence, Union
from uuid import UUID

from .exceptions import GuardError, GuardTimeoutError, PredictionFailedError
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
    MediaType,
    Prediction,
    PredictionCreateResponse,
    PredictionDetail,
    PredictionOrder,
    PredictionPage,
    PredictionStatus,
    PredictionStatusResponse,
    PresignedUploadData,
    SortOrder,
)
from .transport import AsyncTransport, SyncTransport

__all__ = ["Predictions", "AsyncPredictions", "IdLike"]

#: The base URL path for prediction endpoints.
_BASE = "/api/v1/predictions"

#: Default seconds to wait between status checks when polling a prediction.
DEFAULT_POLL_INTERVAL = 2

#: Default maximum seconds to wait for a prediction to complete before raising a timeout
#: error.
DEFAULT_POLL_TIMEOUT = 90.0


class _PredictionsBase:
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
        width: Optional[int] = None,
        height: Optional[int] = None,
        duration_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Build the body for creating a prediction.

        Returns:
            The request body, omitting every owner id and dimension that was not
            supplied.

        Raises:
            GuardError: No space id is available from the call or the client default,
                or a dimension is invalid.
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
        payload.update(_dimensions(width, height, duration_seconds))
        return payload

    @staticmethod
    def _list_params(
        *,
        user_id: Optional[IdLike],
        organization_id: Optional[IdLike],
        space_id: Optional[IdLike],
        start_date: Optional[DateLike],
        end_date: Optional[DateLike],
        statuses: Optional[Sequence[Union[PredictionStatus, str]]],
        sort_by: Optional[Union[PredictionOrder, str]],
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
        add_statuses(params, statuses, PredictionStatus)
        add_sort(params, sort_by, sort_order, PredictionOrder)
        return params

    @staticmethod
    def _check_terminal(
        status: PredictionStatusResponse, prediction_id: IdLike
    ) -> Optional[PredictionStatusResponse]:
        """
        Return the prediction status if valid, raising on failure.
        """
        if status.status is PredictionStatus.COMPLETED:
            return status
        if status.status in (PredictionStatus.FAILED, PredictionStatus.CANCELED):
            raise PredictionFailedError(
                f"Prediction {prediction_id} {status.status.value}",
                status=status.status.value,
                prediction_id=_as_uuid(prediction_id),
            )
        return None


def _dimensions(
    width: Optional[int], height: Optional[int], duration_seconds: Optional[float]
) -> Dict[str, Any]:
    """
    Validate the declared media dimensions.

    The API reserves tokens from these and rejects media whose long side has no price
    before anything is uploaded. Without them it reserves the smallest possible
    prediction and only learns the real cost from the runner.

    Args:
        width: Width in pixels, or `None` when unknown.
        height: Height in pixels, or `None` when unknown.
        duration_seconds: Video length in seconds, or `None` for a still image or when
            unknown.

    Returns:
        The body fields for the values that were supplied.

    Raises:
        GuardError: Only one of `width` and `height` was given, a side is not a
            positive integer, or the duration is negative.
    """
    if (width is None) != (height is None):
        raise GuardError("width and height must be given together")
    fields: Dict[str, Any] = {}
    if width is not None and height is not None:
        for name, side in (("width", width), ("height", height)):
            if isinstance(side, bool) or not isinstance(side, int) or side <= 0:
                raise GuardError(f"Invalid {name}={side!r}. Expected a positive int")
        fields["width"] = width
        fields["height"] = height
    if duration_seconds is not None:
        if isinstance(duration_seconds, bool) or duration_seconds < 0:
            raise GuardError(
                f"Invalid duration_seconds={duration_seconds!r}. Expected 0 or greater"
            )
        fields["duration_seconds"] = float(duration_seconds)
    return fields


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


class Predictions(_PredictionsBase):
    """
    Synchronous prediction endpoints.

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
        width: Optional[int] = None,
        height: Optional[int] = None,
        duration_seconds: Optional[float] = None,
    ) -> PredictionCreateResponse:
        """
        Create a prediction and get back its presigned upload target.

        Args:
            media_type: MIME type of the media you will upload.
            media_size: Its size in bytes.
            space_id: Overrides the client default space id.
            user_id: Owning user, for a user-owned prediction.
            account_id: Owning service account.
            width: Media width in pixels. Give it with `height` so the API reserves
                the real cost rather than the smallest possible one.
            height: Media height in pixels.
            duration_seconds: Video length in seconds.

        Returns:
            The created prediction, including the one-time `upload_data`.

        Raises:
            GuardError: No space id is available, or a dimension is invalid.
            GuardAPIError: The API rejected the request, for example with a 413 when
                the media is larger than the space allows or its long side has no
                price.

        Note:
            This request is safe to replay and is the only POST the client retries.
            A duplicate create leaves an unused prediction rather than performing an
            action twice.
        """
        payload = self._create_payload(
            media_type=media_type,
            media_size=media_size,
            space_id=space_id,
            user_id=user_id,
            account_id=account_id,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
        )
        data = self._transport.request("POST", _BASE, json=payload, retry=True)
        return PredictionCreateResponse.model_validate(data)

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

    def confirm(self, prediction_id: IdLike) -> Prediction:
        """
        Confirm the upload, which moves the prediction into processing.

        Args:
            prediction_id: The prediction whose media has been uploaded.

        Returns:
            The prediction, which is now processing.

        Raises:
            GuardNotFoundError: Unknown prediction, or not owned by you.
        """
        data = self._transport.request("POST", f"{_BASE}/{prediction_id}/confirm")
        return Prediction.model_validate(data)

    def get_status(self, prediction_id: IdLike) -> PredictionStatusResponse:
        """
        Read just enough of a prediction to know its current state.

        This response is deliberately smaller than `get` so polling a prediction does
        not repeatedly transfer its results.

        Args:
            prediction_id: The prediction to check.

        Returns:
            The id and current status of the prediction.

        Raises:
            GuardNotFoundError: Unknown prediction, or not owned by you.
        """
        data = self._transport.request("GET", f"{_BASE}/{prediction_id}/status")
        return PredictionStatusResponse.model_validate(data)

    def get(self, prediction_id: IdLike) -> PredictionDetail:
        """
        Read a prediction in full, including its results.

        Args:
            prediction_id: The prediction to fetch.

        Returns:
            The prediction. `result_payload` is `None` until processing finishes, and
            `payed_tokens` carries the actual cost.

        Raises:
            GuardNotFoundError: Unknown prediction, or not owned by you.
        """
        data = self._transport.request("GET", f"{_BASE}/{prediction_id}")
        return PredictionDetail.model_validate(data)

    def list(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        space_id: Optional[IdLike] = None,
        start_date: Optional[DateLike] = None,
        end_date: Optional[DateLike] = None,
        statuses: Optional[Sequence[Union[PredictionStatus, str]]] = None,
        sort_by: Optional[Union[PredictionOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> PredictionPage:
        """
        List predictions matching the given filters.

        Args:
            user_id: Only this user's predictions. This may be combined with
                `organization_id`, and the API will apply both.
            organization_id: Only this organization's predictions.
            space_id: Only predictions in this space.
            start_date: A `datetime`, `date`, or an ISO-8601 string. The API keeps only
                one year of history. Anything older is rejected locally before the
                request is sent. Omitting it defaults to exactly one year ago. A naive
                datetime is interpreted as UTC to match the server.
            end_date: Accepts the same types as start_date. Defaults to now.
            statuses: Keep only predictions with these statuses.
            sort_by: Valid options include `"created_at"`. Server default: `created_at`.
            sort_order: `"asc"` or `"desc"`. Server default for predictions: `desc`.
            skip: Offset, 0 or greater.
            limit: Page size, 1-100.

        Returns:
            A `PredictionPage`. You can iterate it like a list, or read `.count` for the
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
        return PredictionPage.model_validate(data)

    def iter_all(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        space_id: Optional[IdLike] = None,
        start_date: Optional[DateLike] = None,
        end_date: Optional[DateLike] = None,
        statuses: Optional[Sequence[Union[PredictionStatus, str]]] = None,
        sort_by: Optional[Union[PredictionOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> Iterator[Prediction]:
        """
        Yield every matching prediction by fetching pages as needed.

        Args:
            user_id: Only this user's predictions. This may be combined with
                `organization_id`, and the API will apply both.
            organization_id: Only this organization's predictions.
            space_id: Only predictions in this space.
            start_date: A `datetime`, `date`, or an ISO-8601 string. The API keeps only
                one year of history. Anything older is rejected locally before the
                request is sent. Omitting it defaults to exactly one year ago. A naive
                datetime is interpreted as UTC to match the server.
            end_date: Accepts the same types as start_date. Defaults to now.
            statuses: Keep only predictions with these statuses.
            sort_by: Valid options include `"created_at"`. Server default: `created_at`.
            sort_order: `"asc"` or `"desc"`. Server default for predictions: `desc`.
            page_size: Page size, 1-100.

        Yields:
            Each matching prediction, starting with the oldest page first.

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
        prediction_id: IdLike,
        *,
        interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_POLL_TIMEOUT,
    ) -> PredictionStatusResponse:
        """
        Poll until the prediction completes.

        Args:
            prediction_id: The prediction to check.
            interval: Seconds to wait between polling requests.
            timeout: Maximum seconds to wait before raising an error.

        Returns:
            The terminal status, which is always `completed`. A prediction that ended as
            `failed` or `canceled` raises rather than returning, so a value coming back
            from here needs no further checking.

        Raises:
            PredictionFailedError: The prediction ended as `failed` or `canceled`.
            GuardTimeoutError: The specified timeout seconds elapsed without reaching a
                terminal status.
        """
        deadline = time.monotonic() + timeout
        while True:
            status = self.get_status(prediction_id)
            done = self._check_terminal(status, prediction_id)
            if done is not None:
                return done
            if time.monotonic() + interval >= deadline:
                raise GuardTimeoutError(
                    f"Prediction {prediction_id} did not complete within {timeout}s "
                    f"(last status: {status.status.value})",
                    prediction_id=_as_uuid(prediction_id),
                )
            time.sleep(interval)


class AsyncPredictions(_PredictionsBase):
    """
    Asynchronous prediction endpoints.

    This is reached through the client rather than constructed directly, and it shares
    the client's connection pool. It mirrors `Predictions` method for method. Review the
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
        width: Optional[int] = None,
        height: Optional[int] = None,
        duration_seconds: Optional[float] = None,
    ) -> PredictionCreateResponse:
        """
        Create a prediction and get back its presigned upload target.

        Args:
            media_type: MIME type of the media you will upload.
            media_size: Its size in bytes.
            space_id: Overrides the client default space id.
            user_id: Owning user, for a user-owned prediction.
            account_id: Owning service account.
            width: Media width in pixels. Give it with `height` so the API reserves
                the real cost rather than the smallest possible one.
            height: Media height in pixels.
            duration_seconds: Video length in seconds.

        Returns:
            The created prediction, including the one-time `upload_data`.

        Raises:
            GuardError: No space id is available, or a dimension is invalid.
            GuardAPIError: The API rejected the request, for example with a 413 when
                the media is larger than the space allows or its long side has no
                price.

        Note:
            This request is safe to replay and is the only POST the client retries. A
            duplicate create leaves an unused prediction rather than performing an
            action twice.
        """
        payload = self._create_payload(
            media_type=media_type,
            media_size=media_size,
            space_id=space_id,
            user_id=user_id,
            account_id=account_id,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
        )
        data = await self._transport.request("POST", _BASE, json=payload, retry=True)
        return PredictionCreateResponse.model_validate(data)

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

    async def confirm(self, prediction_id: IdLike) -> Prediction:
        """
        Confirm the upload, which moves the prediction into processing.

        Args:
            prediction_id: The prediction whose media has been uploaded.

        Returns:
            The prediction, which is now processing.

        Raises:
            GuardNotFoundError: Unknown prediction, or not owned by you.
        """
        data = await self._transport.request("POST", f"{_BASE}/{prediction_id}/confirm")
        return Prediction.model_validate(data)

    async def get_status(self, prediction_id: IdLike) -> PredictionStatusResponse:
        """
        Read just enough of a prediction to know its current state.

        This response is deliberately smaller than `get` so polling a prediction does
        not repeatedly transfer its results.

        Args:
            prediction_id: The prediction to check.

        Returns:
            The id and current status of the prediction.

        Raises:
            GuardNotFoundError: Unknown prediction, or not owned by you.
        """
        data = await self._transport.request("GET", f"{_BASE}/{prediction_id}/status")
        return PredictionStatusResponse.model_validate(data)

    async def get(self, prediction_id: IdLike) -> PredictionDetail:
        """
        Read a prediction in full, including its results.

        Args:
            prediction_id: The prediction to fetch.

        Returns:
            The prediction. `result_payload` is `None` until processing finishes, and
            `payed_tokens` carries the actual cost.

        Raises:
            GuardNotFoundError: Unknown prediction, or not owned by you.
        """
        data = await self._transport.request("GET", f"{_BASE}/{prediction_id}")
        return PredictionDetail.model_validate(data)

    async def list(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        space_id: Optional[IdLike] = None,
        start_date: Optional[DateLike] = None,
        end_date: Optional[DateLike] = None,
        statuses: Optional[Sequence[Union[PredictionStatus, str]]] = None,
        sort_by: Optional[Union[PredictionOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> PredictionPage:
        """
        List predictions matching the given filters.

        Args:
            user_id: Only this user's predictions. This may be combined with
                `organization_id`, and the API will apply both.
            organization_id: Only this organization's predictions.
            space_id: Only predictions in this space.
            start_date: A `datetime`, `date`, or an ISO-8601 string. The API keeps only
                one year of history. Anything older is rejected locally before the
                request is sent. Omitting it defaults to exactly one year ago. A naive
                datetime is interpreted as UTC to match the server.
            end_date: Accepts the same types as start_date. Defaults to now.
            statuses: Keep only predictions with these statuses.
            sort_by: Valid options include `"created_at"`. Server default: `created_at`.
            sort_order: `"asc"` or `"desc"`. Server default for predictions: `desc`.
            skip: Offset, 0 or greater.
            limit: Page size, 1-100.

        Returns:
            A `PredictionPage`. You can iterate it like a list, or read `.count` for the
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
        return PredictionPage.model_validate(data)

    async def iter_all(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        space_id: Optional[IdLike] = None,
        start_date: Optional[DateLike] = None,
        end_date: Optional[DateLike] = None,
        statuses: Optional[Sequence[Union[PredictionStatus, str]]] = None,
        sort_by: Optional[Union[PredictionOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> AsyncIterator[Prediction]:
        """
        Yield every matching prediction by fetching pages as needed.

        Args:
            user_id: Only this user's predictions. This may be combined with
                `organization_id`, and the API will apply both.
            organization_id: Only this organization's predictions.
            space_id: Only predictions in this space.
            start_date: A `datetime`, `date`, or an ISO-8601 string. The API keeps only
                one year of history. Anything older is rejected locally before the
                request is sent. Omitting it defaults to exactly one year ago. A naive
                datetime is interpreted as UTC to match the server.
            end_date: Accepts the same types as start_date. Defaults to now.
            statuses: Keep only predictions with these statuses.
            sort_by: Valid options include `"created_at"`. Server default: `created_at`.
            sort_order: `"asc"` or `"desc"`. Server default for predictions: `desc`.
            page_size: Page size, 1-100.

        Yields:
            Each matching prediction, starting with the oldest page first.

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
            for prediction in page.data:
                yield prediction

            skip += len(page.data)
            if not page.data or len(page.data) < page_size or skip >= page.count:
                return

    async def wait_until_done(
        self,
        prediction_id: IdLike,
        *,
        interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_POLL_TIMEOUT,
    ) -> PredictionStatusResponse:
        """
        Poll until the prediction completes.

        Args:
            prediction_id: The prediction to check.
            interval: Seconds to wait between polling requests.
            timeout: Maximum seconds to wait before raising an error.

        Returns:
            The terminal status. Check `Predictions.wait_until_done` for reference.

        Raises:
            PredictionFailedError: The prediction ended as `failed` or `canceled`.
            GuardTimeoutError: The deadline passed while it was still running.

        Note:
            This method sleeps with `asyncio.sleep`, ensuring that waiting never blocks
            the event loop.
        """
        deadline = time.monotonic() + timeout
        while True:
            status = await self.get_status(prediction_id)
            done = self._check_terminal(status, prediction_id)
            if done is not None:
                return done
            if time.monotonic() + interval >= deadline:
                raise GuardTimeoutError(
                    f"Prediction {prediction_id} did not complete within {timeout}s "
                    f"(last status: {status.status.value})",
                    prediction_id=_as_uuid(prediction_id),
                )
            await asyncio.sleep(interval)
