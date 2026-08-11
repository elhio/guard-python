"""
Internal HTTP transport handling authentication, locale, error mapping, and retries.

Two thin wrappers over `httpx`, `SyncTransport` and `AsyncTransport`, share their
configuration and all non-I/O logic via `_TransportBase`.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

import httpx

from .exceptions import (
    GuardAPIError,
    GuardAuthError,
    GuardConflictError,
    GuardConnectionError,
    GuardError,
    GuardNotFoundError,
    GuardPaymentRequiredError,
    GuardRateLimitError,
    GuardServerError,
    GuardUploadError,
    GuardValidationError,
)

__all__ = ["AsyncTransport", "SyncTransport", "TransportConfig", "DEFAULT_BASE_URL"]

#: The Guard API every client talks to unless `base_url` or `GUARD_BASE_URL` says
#: otherwise.
DEFAULT_BASE_URL = "https://api.elhio.com"

#: Methods replayed by default. Individual calls override this with `retry`.
#: `POST /activities/` opts in because a duplicate create only leaves an unused
#: activity, while `DELETE /runners/{id}` opts out because it tears down a live
#: deployment.
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

#: HTTP status codes that indicate a request should be retried.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: The maximum number of seconds to wait before replaying a failed request.
_MAX_BACKOFF = 30.0


@dataclass
class TransportConfig:
    """
    Everything both transports need to construct a request.

    This is a plain data holder. The client resolves each value from arguments, the
    environment, and `.env` before building one of these, so nothing here consults its
    surroundings.

    Attributes:
        api_key: Bearer token sent on every API request. `None` is allowed only for
            local-only use. Any API request made without one raises `GuardError` rather
            than going out unauthenticated. Presigned uploads and media downloads are
            unaffected.
        base_url: API root, without a trailing slash.
        locale: Language for server-rendered labels, sent as `lang`.
        timeout: Per-request HTTP timeout in seconds.
        max_retries: How many times a retryable request may be replayed.
        headers: Extra headers merged into every request.
    """

    api_key: Optional[str] = None
    base_url: str = DEFAULT_BASE_URL
    locale: str = "en"
    timeout: float = 30.0
    max_retries: int = 3
    headers: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """
        Normalize the base URL.

        This strips any trailing slash so joining a path onto it cannot produce a double
        slash that the server would treat as a different route.
        """
        self.base_url = self.base_url.rstrip("/")


def _extract_detail(response: httpx.Response) -> Tuple[str, Any]:
    """
    Pull a human-readable message out of a failed response.

    It prefers the API's `detail` field and falls back to the status line. Note that 422
    errors return `detail` as a list of field errors.

    Args:
        response: The failed HTTP response.

    Returns:
        A tuple containing a human-readable error message and the raw detail payload.
    """
    try:
        payload = response.json()
    except (ValueError, UnicodeDecodeError):
        return f"API Error: {response.status_code} {response.reason_phrase}", None

    if not isinstance(payload, dict):
        return f"API Error: {response.status_code} {response.reason_phrase}", payload

    detail = payload.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail, detail
    if isinstance(detail, list) and detail:
        parts = []
        for entry in detail:
            if isinstance(entry, dict):
                loc = ".".join(str(p) for p in entry.get("loc", []) if p != "body")
                msg = entry.get("msg", "invalid")
                parts.append(f"{loc}: {msg}" if loc else str(msg))
            else:
                parts.append(str(entry))
        return "; ".join(parts), detail

    return f"API Error: {response.status_code} {response.reason_phrase}", detail


def _raise_for_status(response: httpx.Response) -> None:
    """
    Map a non-2xx response onto the exception tree.

    Args:
        response: The response to inspect.

    Raises:
        GuardAuthError: On 401 or 403 status.
        GuardPaymentRequiredError: On 402 status.
        GuardNotFoundError: On 404 status.
        GuardConflictError: On 409 status.
        GuardValidationError: On 422 status.
        GuardRateLimitError: On 429 status.
        GuardServerError: On any 5xx status.
        GuardAPIError: On any other non-2xx status.
    """
    if response.is_success:
        return

    message, detail = _extract_detail(response)
    status = response.status_code
    request_id = response.headers.get("x-request-id")
    kwargs: Dict[str, Any] = {
        "status_code": status,
        "detail": detail,
        "request_id": request_id,
    }

    if status in (401, 403):
        raise GuardAuthError(message, **kwargs)
    if status == 402:
        raise GuardPaymentRequiredError(message, **kwargs)
    if status == 404:
        raise GuardNotFoundError(message, **kwargs)
    if status == 409:
        raise GuardConflictError(message, **kwargs)
    if status == 422:
        raise GuardValidationError(message, **kwargs)
    if status == 429:
        retry_after = response.headers.get("retry-after")
        raise GuardRateLimitError(
            message,
            retry_after=float(retry_after)
            if retry_after and retry_after.isdigit()
            else None,
            **kwargs,
        )
    if status >= 500:
        raise GuardServerError(message, **kwargs)
    raise GuardAPIError(message, **kwargs)


def _backoff_delay(attempt: int, response: Optional[httpx.Response]) -> float:
    """
    Determine how long to wait before replaying a request.

    Args:
        attempt: Zero-based attempt number so the delay doubles each time.
        response: The failed response, consulted for the `Retry-After` header.

    Returns:
        Seconds to sleep, capped at the defined maximum backoff.

    Note:
        Full jitter spreads retries out so a fleet recovering from an outage does not
        stampede the API in lockstep.
    """
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), _MAX_BACKOFF)
    return min(2.0**attempt, _MAX_BACKOFF) * (0.5 + random.random() / 2)


class _TransportBase:
    """
    Request construction and retry bookkeeping shared by both transports.

    This holds everything that does not perform I/O so the sync and async transports
    cannot drift in how they build a request or decide to retry it.
    """

    def __init__(self, config: TransportConfig) -> None:
        """
        Store the resolved configuration.

        Args:
            config: Fully resolved settings. Nothing here reads the environment.
        """
        self._config = config

    @property
    def config(self) -> TransportConfig:
        """
        The configuration this transport was built with.

        Returns:
            The live config object, not a copy.
        """
        return self._config

    def _build_headers(self) -> Dict[str, str]:
        """
        Build the headers every API request carries.

        Returns:
            A dictionary containing `Accept`, any configured extras, and the bearer
            token.

        Raises:
            GuardError: If no API key was configured. Every API request must be
                authenticated, so this refuses before the request is sent rather than
                letting an anonymous one through.

        Note:
            This is only used for API requests. Presigned uploads and media downloads
            deliberately bypass this so the key never reaches a third-party host. This
            is also why they keep working on a keyless client.
        """
        if not self._config.api_key:
            raise GuardError(
                "An API key is required for cloud requests. Pass api_key=..., set the "
                "GUARD_API_KEY environment variable, or put it in a .env file (see "
                ".env.example). This client was built without one, so only "
                'engine="local" detection is available.'
            )
        headers = {"Accept": "application/json", **self._config.headers}
        headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    def _build_params(self, params: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        """
        Merge the caller-supplied query parameters with the locale.

        Args:
            params: Caller-supplied parameters. Entries that are `None` are dropped so
                an unset filter is omitted rather than sent as a null.

        Returns:
            The query dict to send, always carrying `lang`.
        """
        # API localises result labels/descriptions off `lang`
        merged: Dict[str, Any] = {"lang": self._config.locale}
        for key, value in (params or {}).items():
            if value is not None:
                merged[key] = value
        return merged

    def _should_retry(
        self,
        *,
        attempt: int,
        method: str,
        retry: Optional[bool],
        response: Optional[httpx.Response],
    ) -> bool:
        """
        Decide whether to replay a request.

        Args:
            attempt: The current zero-based attempt number.
            method: The HTTP method used for the request.
            retry: Overrides the method-based default. `True` opts a non-idempotent call
                in, `False` opts a nominally-idempotent one out, and `None` leaves the
                decision to the method.
            response: The failed response, or `None` for a transport-level failure.

        Returns:
            `True` if the request should be retried, `False` otherwise.
        """
        if attempt >= self._config.max_retries:
            return False
        if retry is False:
            return False
        if retry is not True and method.upper() not in _IDEMPOTENT_METHODS:
            return False
        if response is None:  # transport-level failure
            return True
        return response.status_code in _RETRY_STATUSES


class SyncTransport(_TransportBase):
    """
    Blocking HTTP transport.

    This owns an `httpx.Client` unless one is supplied, in which case closing is left
    to whoever provided it.
    """

    def __init__(
        self, config: TransportConfig, *, http_client: Optional[httpx.Client] = None
    ) -> None:
        """
        Build a transport, creating a connection pool unless given one.

        Args:
            config: Fully resolved settings.
            http_client: An existing client to borrow. When supplied, `close` leaves it
                open since its lifetime belongs to the caller.
        """
        super().__init__(config)
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=config.timeout)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        data: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        retry: Optional[bool] = None,
    ) -> Any:
        """
        Issue an authenticated request and return the decoded JSON body.

        Args:
            method: HTTP method.
            path: Path below `base_url`, starting with a slash.
            params: Query parameters. `None` values are dropped.
            json: JSON body to send.
            data: Form body to send.
            headers: Extra headers for this request only.
            retry: Overrides the method-based retry policy. `True` opts a non-idempotent
                call in, `False` opts a nominally-idempotent one out.

        Returns:
            The decoded JSON body, or `None` for an empty response.

        Raises:
            GuardAPIError: If the API returned a non-2xx status. Which subclass depends
                on the status code.
            GuardConnectionError: If the request never reached the API.
        """
        url = f"{self._config.base_url}{path}"
        merged_headers = {**self._build_headers(), **(headers or {})}
        merged_params = self._build_params(params)
        last_exc: Optional[Exception] = None

        for attempt in range(self._config.max_retries + 1):
            response: Optional[httpx.Response] = None
            try:
                response = self._client.request(
                    method,
                    url,
                    params=merged_params,
                    json=json,
                    data=data,
                    headers=merged_headers,
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                if not self._should_retry(
                    attempt=attempt, method=method, retry=retry, response=None
                ):
                    raise GuardConnectionError(
                        f"Request to {url} failed: {exc}"
                    ) from exc
                time.sleep(_backoff_delay(attempt, None))
                continue

            if self._should_retry(
                attempt=attempt, method=method, retry=retry, response=response
            ):
                time.sleep(_backoff_delay(attempt, response))
                continue

            _raise_for_status(response)
            return _decode(response)

        raise GuardConnectionError(f"Request to {url} failed after retries: {last_exc}")

    def upload(
        self, url: str, fields: Mapping[str, str], filename: str, data: bytes
    ) -> None:
        """
        POST bytes to a presigned storage target.

        Args:
            url: The presigned endpoint from `upload_data.url`.
            fields: Policy fields from `upload_data.fields`.
            filename: Name for the multipart file part.
            data: The media bytes.

        Raises:
            GuardUploadError: If storage rejected the upload or could not be reached.

        Note:
            This deliberately bypasses `request`. The presigned policy must not receive
            our `Authorization` header because that would hand the API key to a
            third-party host, nor the `lang` parameter, and the policy fields have to
            precede the file part in the multipart body.
        """
        try:
            response = self._client.post(
                url,
                data=dict(fields),
                files={"file": (filename, data)},
            )
        except httpx.HTTPError as exc:
            raise GuardUploadError(f"Media upload to {url} failed: {exc}") from exc

        if not response.is_success:
            raise GuardUploadError(
                f"Media upload failed: {response.status_code} {response.reason_phrase}",
                status_code=response.status_code,
            )

    def close(self) -> None:
        """
        Release the connection pool if this transport owns it.

        A borrowed client is left open since its lifetime belongs to whoever passed it.
        """
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> SyncTransport:
        """
        Enter a context manager.

        Returns:
            This transport, unchanged.
        """
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """
        Leave a context manager, closing the pool.

        Args:
            *exc_info: Exception details, ignored because closing happens either way.
        """
        self.close()


class AsyncTransport(_TransportBase):
    """
    Non-blocking HTTP transport.

    This mirrors `SyncTransport` method for method, sharing all request construction and
    retry logic through `_TransportBase`.
    """

    def __init__(
        self,
        config: TransportConfig,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        Build a transport, creating a connection pool unless given one.

        Args:
            config: Fully resolved settings.
            http_client: An existing client to borrow. When supplied, `aclose` leaves it
                open.
        """
        super().__init__(config)
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=config.timeout)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        data: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        retry: Optional[bool] = None,
    ) -> Any:
        """
        Issue an authenticated request and return the decoded JSON body.

        Args:
            method: HTTP method.
            path: Path below `base_url`, starting with a slash.
            params: Query parameters. `None` values are dropped.
            json: JSON body to send.
            data: Form body to send.
            headers: Extra headers for this request only.
            retry: Overrides the method-based retry policy.

        Returns:
            The decoded JSON body, or `None` for an empty response.

        Raises:
            GuardAPIError: If the API returned a non-2xx status.
            GuardConnectionError: If the request never reached the API.
        """
        url = f"{self._config.base_url}{path}"
        merged_headers = {**self._build_headers(), **(headers or {})}
        merged_params = self._build_params(params)
        last_exc: Optional[Exception] = None

        for attempt in range(self._config.max_retries + 1):
            response: Optional[httpx.Response] = None
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=merged_params,
                    json=json,
                    data=data,
                    headers=merged_headers,
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                if not self._should_retry(
                    attempt=attempt, method=method, retry=retry, response=None
                ):
                    raise GuardConnectionError(
                        f"Request to {url} failed: {exc}"
                    ) from exc
                await asyncio.sleep(_backoff_delay(attempt, None))
                continue

            if self._should_retry(
                attempt=attempt, method=method, retry=retry, response=response
            ):
                await asyncio.sleep(_backoff_delay(attempt, response))
                continue

            _raise_for_status(response)
            return _decode(response)

        raise GuardConnectionError(f"Request to {url} failed after retries: {last_exc}")

    async def upload(
        self, url: str, fields: Mapping[str, str], filename: str, data: bytes
    ) -> None:
        """
        POST bytes to a presigned storage target.

        Args:
            url: The presigned endpoint from `upload_data.url`.
            fields: Policy fields from `upload_data.fields`.
            filename: Name for the multipart file part.
            data: The media bytes.

        Raises:
            GuardUploadError: If storage rejected the upload or could not be reached.

        Note:
            This carries no `Authorization` header for the reason given on
            `SyncTransport.upload`.
        """
        try:
            response = await self._client.post(
                url,
                data=dict(fields),
                files={"file": (filename, data)},
            )
        except httpx.HTTPError as exc:
            raise GuardUploadError(f"Media upload to {url} failed: {exc}") from exc

        if not response.is_success:
            raise GuardUploadError(
                f"Media upload failed: {response.status_code} {response.reason_phrase}",
                status_code=response.status_code,
            )

    async def aclose(self) -> None:
        """
        Release the connection pool if this transport owns it.

        A borrowed client is left open since its lifetime belongs to whoever passed it.
        """
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> AsyncTransport:
        """
        Enter an async context manager.

        Returns:
            This transport, unchanged.
        """
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        """
        Leave an async context manager, closing the pool.

        Args:
            *exc_info: Exception details, ignored because closing happens either way.
        """
        await self.aclose()


def _decode(response: httpx.Response) -> Any:
    """
    Decode a successful response body.

    Args:
        response: A response that already passed `_raise_for_status`.

    Returns:
        The parsed JSON, or `None` for a 204 status or an empty body.

    Raises:
        GuardAPIError: If the body was not JSON despite a success status.
    """
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise GuardAPIError(
            f"Expected a JSON response but got "
            f"{response.headers.get('content-type')!r}",
            status_code=response.status_code,
        ) from exc
