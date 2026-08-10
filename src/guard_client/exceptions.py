"""
Exception hierarchy for the Guard client.

Everything raised by this package derives from `GuardError`. This ensures a single
`except GuardError` block catches any client failure. Below that base class, errors
split into two families: those raised locally before a request is sent, and
`GuardAPIError` subclasses carrying a status code the server returned.

Catching specific exceptions is worthwhile when the response dictates a different
action. A `GuardConflictError` usually means the action was already done, which is
often benign, while a `GuardPaymentRequiredError` needs human intervention.

The on-device engine forms a third family under `GuardLocalEngineError`. The
`guard-local-detector` package raises its own hierarchy rooted at
`guard_local.GuardLocalError`, which does not derive from anything in this package. The
`guard_client.local` module translates each of those errors into the appropriate
`GuardLocalEngineError` subclass before it reaches a caller. This keeps the promise
that `except GuardError` will catch everything this client can raise regardless of which
engine ran.

Examples:
    ```python
    from guard_client import (
        ActivityFailedError,
        GuardAuthError,
        GuardError,
        GuardTimeoutError,
    )

    try:
        result = client.analyze("photo.jpg")
    except GuardAuthError:
        pass  # Handle bad or expired API key
    except ActivityFailedError as exc:
        pass  # Processing ended "failed" or "canceled". exc.status says which.
    except GuardTimeoutError:
        pass  # Still processing when the deadline passed
    except GuardError:
        pass  # Anything else from this client
    ```

Note:
    Values are validated locally wherever the rule is knowable without the server. Most
    mistakes raise a plain `GuardError` before any request is sent. This is deliberate
    because a wasted round-trip produces a worse error message than a local check.
"""

from __future__ import annotations

from typing import Any, List, Optional
from uuid import UUID

__all__ = [
    "ActivityFailedError",
    "GuardAPIError",
    "GuardAuthError",
    "GuardConflictError",
    "GuardConnectionError",
    "GuardError",
    "GuardLocalEngineError",
    "GuardLocalModelError",
    "GuardMediaDecodeError",
    "GuardNotFoundError",
    "GuardPaymentRequiredError",
    "GuardRateLimitError",
    "GuardServerError",
    "GuardTimeoutError",
    "GuardUploadError",
    "GuardValidationError",
    "LocalEngineNotInstalledError",
    "UnsupportedMediaTypeError",
]


class GuardError(Exception):
    """
    Base class for every error raised by this package.

    This is raised directly for local validation failures. These failures have no status
    code because no request was made.
    """


class GuardAPIError(GuardError):
    """
    The API returned a non-2xx response.

    This error is subclassed per status code. Catch this to handle any server-side
    failure uniformly.

    Args:
        message: Human-readable summary taken from the server `detail` when present.
        status_code: The HTTP status that produced this error.
        detail: The raw `detail` payload. This is a string for most errors, or a list
            of per-field objects for 422 errors.
        request_id: The server `x-request-id` when it sent one. This is worth quoting
            in a bug report.

    Attributes:
        status_code: The HTTP status that produced this error.
        detail: The raw `detail` payload from the server.
        request_id: The server `x-request-id`, or `None`.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        detail: Any = None,
        request_id: Optional[str] = None,
    ) -> None:
        """
        Store the response metadata alongside the message.

        Args:
            message: Human-readable summary.
            status_code: The HTTP status that produced this error.
            detail: The raw `detail` payload.
            request_id: The server `x-request-id` when present.
        """
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail
        self.request_id = request_id

    def __str__(self) -> str:
        """
        Render the message with its status code prefixed.

        Returns:
            The server message behind a `[404]` style prefix. This ensures that a bare
            print of the exception still indicates which status caused it.
        """
        base = super().__str__()
        return f"[{self.status_code}] {base}" if self.status_code else base


class GuardAuthError(GuardAPIError):
    """
    The API key is missing, invalid, or lacks permission (401/403).

    This is also raised for 403 responses that represent authorization failures, such as
    attempting to use a predictor that is not enabled in your plan.
    """


class GuardNotFoundError(GuardAPIError):
    """
    The requested resource does not exist (404).

    Note:
        Several routes answer 404 for resources that exist but do not belong to you.
        This means a 404 does not strictly prove absence. Reacting to someone else's
        activity reports the same error as reacting to an id that was never issued.
    """


class GuardValidationError(GuardAPIError):
    """
    The request body or parameters failed server-side validation (422).

    Reaching this usually means a value was provided that the client could not check
    locally. Anything knowable in advance is rejected before the request is made.

    Attributes:
        errors: Per-field validation errors.
    """

    @property
    def errors(self) -> List[Any]:
        """
        Per-field validation errors or an empty list.

        Returns:
            One mapping per rejected field, each containing `loc`, `msg`, and `type`.
            Returns an empty list when the server sent a plain string instead.
        """
        return self.detail if isinstance(self.detail, list) else []


class GuardPaymentRequiredError(GuardAPIError):
    """
    The account lacks an active subscription or a plan limit was reached (402).

    This covers both "no subscription" and "you already have as many spaces or runners
    as your plan allows". The API does not distinguish between these scenarios by status
    code.
    """


class GuardConflictError(GuardAPIError):
    """
    The resource clashes with one that already exists (409).

    This is raised when a space or runner name is already taken in the same context,
    when an activity result already has a reaction, or when an activity has already been
    shared.

    Note:
        This error is often benign. Because the client never retries a create operation,
        seeing this means the resource genuinely existed beforehand, not that a request
        was replayed.
    """


class GuardRateLimitError(GuardAPIError):
    """
    Too many requests (429).

    Requests are retried automatically up to `max_retries` while honoring the
    `Retry-After` header. This error surfaces only once those attempts are exhausted.

    Args:
        *args: Forwarded to `GuardAPIError`.
        retry_after: Seconds the server asked us to wait when it provided this
            information.
        **kwargs: Forwarded to `GuardAPIError`.

    Attributes:
        retry_after: Seconds from the `Retry-After` header, or `None`.
    """

    def __init__(
        self, *args: Any, retry_after: Optional[float] = None, **kwargs: Any
    ) -> None:
        """
        Store the server requested wait time alongside the response metadata.

        Args:
            *args: Forwarded to `GuardAPIError`.
            retry_after: Seconds from the `Retry-After` header.
            **kwargs: Forwarded to `GuardAPIError`.
        """
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class GuardServerError(GuardAPIError):
    """
    The API failed to handle the request (5xx).

    These are retried automatically for idempotent calls. Create operations are never
    replayed so this surfaces immediately for them.
    """


class GuardConnectionError(GuardError):
    """
    The request never reached the API (DNS, TLS, socket, or read timeout).

    This is distinct from `GuardServerError` because nothing was processed. A create
    operation that fails this way definitely did not happen.
    """


class GuardUploadError(GuardError):
    """
    The presigned S3 upload was rejected.

    This is separate from `GuardAPIError` because the failing request went to storage
    rather than the API, meaning it carries no `detail` payload.

    Args:
        message: What went wrong.
        status_code: The status storage returned when there was a response at all.

    Attributes:
        status_code: The HTTP status from storage, or `None` for a transport failure.
    """

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        """
        Store the storage response status alongside the message.

        Args:
            message: What went wrong.
            status_code: The status storage returned, if any.
        """
        super().__init__(message)
        self.status_code = status_code


class GuardTimeoutError(GuardError):
    """
    An activity did not reach a terminal status before the polling deadline.

    The activity is still running server-side. Only the waiting process stopped. You
    should fetch it again later rather than resubmitting the media.

    Args:
        message: What timed out, including the last status seen.
        activity_id: The activity that was being polled.

    Attributes:
        activity_id: The activity still in flight so it can be polled again.
    """

    def __init__(self, message: str, *, activity_id: Optional[UUID] = None) -> None:
        """
        Store which activity was still running when the deadline passed.

        Args:
            message: What timed out, including the last status seen.
            activity_id: The activity that was being polled.
        """
        super().__init__(message)
        self.activity_id = activity_id


class ActivityFailedError(GuardError):
    """
    An activity reached a terminal status other than `completed`.

    Args:
        message: What happened, naming the activity and status.
        status: The terminal status reached, either `failed` or `canceled`.
        activity_id: The activity that ended.

    Attributes:
        status: The terminal status reached.
        activity_id: The activity that ended.
    """

    def __init__(
        self, message: str, *, status: str, activity_id: Optional[UUID] = None
    ) -> None:
        """
        Store which activity ended and how.

        Args:
            message: What happened, naming the activity and status.
            status: The terminal status reached.
            activity_id: The activity that ended.
        """
        super().__init__(message)
        self.status = status
        self.activity_id = activity_id


class UnsupportedMediaTypeError(GuardError, ValueError):
    """
    The media MIME type is not one the backend accepts.

    This subclasses `ValueError` because it is a bad argument rather than a service
    failure. It is raised locally before any upload happens.
    """


class GuardLocalEngineError(GuardError):
    """
    The on-device engine failed.

    Every failure raised by `guard-local-detector` is translated into this class or one
    of its subclasses before it leaves `LocalRunner`. The engine's own exceptions do not
    derive from `GuardError`. Without translation, a caller writing `except GuardError`
    would catch every cloud failure but miss every local one.

    Catch this to handle any local-engine problem uniformly. Catch a subclass when the
    remedy differs. For example, a missing extra needs an install but a missing model
    file does not.
    """


class LocalEngineNotInstalledError(GuardLocalEngineError, ImportError):
    """
    Local execution was requested without the optional `[local]` extra.

    This also subclasses `ImportError` so existing error handling for a missing optional
    dependency catches it. The message distinguishes an absent package from one that is
    installed but broken.
    """


class GuardLocalModelError(GuardLocalEngineError):
    """
    The engine is installed but its detection model could not be loaded.

    This is distinct from `LocalEngineNotInstalledError` because the remedy is
    different. The package is present and importable, so telling the user to install
    the extra is wrong advice for a model file that is missing, unreadable, or corrupt.

    Note:
        This is raised on the first call rather than at construction because the engine
        defers loading the model until something needs scoring.
    """


class GuardMediaDecodeError(GuardLocalEngineError, ValueError):
    """
    The engine accepts this media type but could not decode the bytes.

    This covers truncated or corrupt files, as well as videos that yield no decodable
    frames. It also subclasses `ValueError` because unreadable input is a bad argument
    rather than an engine failure. This follows the same reasoning that puts
    `UnsupportedMediaTypeError` under `ValueError`.
    """
