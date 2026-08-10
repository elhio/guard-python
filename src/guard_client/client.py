"""
The user-facing Guard clients.

`GuardClient` and `AsyncGuardClient` expose the same surface in blocking and
`async`/`await` form. Both share `_ClientBase` for client construction to guarantee
identical behavior.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union

import httpx

from .activities import (
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_TIMEOUT,
    Activities,
    AsyncActivities,
)
from .env import DEFAULT_ENV_FILE, EnvFile, EnvSource
from .exceptions import GuardError
from .filters import IdLike
from .local import LocalRunner
from .media import MediaSource, resolve_media
from .models import DetectionResult, Engine, MediaType, coerce_enum
from .predictors import AsyncPredictors, Predictors
from .probe import probe_media
from .reactions import AsyncReactions, Reactions
from .runners import AsyncRunners, Runners
from .shares import AsyncShares, Shares
from .spaces import AsyncSpaces, Spaces
from .tasks import AsyncTasks, Tasks
from .tokens import TokenEstimate, estimate_tokens, frames_for
from .transport import DEFAULT_BASE_URL, AsyncTransport, SyncTransport, TransportConfig

__all__ = ["AsyncGuardClient", "GuardClient"]

#: A type alias representing a valid engine configuration.
EngineLike = Union[Engine, str]

# Effective defaults. They live here rather than in the signatures because every
# env-backed parameter defaults to None, meaning "not provided" — that is what lets an
# explicit argument be told apart from an omitted one.

#: The default engine to use for analysis when none is specified.
DEFAULT_ENGINE = Engine.CLOUD

#: The default locale for API responses.
DEFAULT_LOCALE = "en"

#: The default timeout in seconds for API requests.
DEFAULT_TIMEOUT = 30.0

#: The default maximum number of retries for failed requests.
DEFAULT_MAX_RETRIES = 3


def _coerce_engine(engine: EngineLike) -> Engine:
    """
    Validate an engine name.

    Args:
        engine: An `Engine` member or its string value.

    Returns:
        The matching member.

    Raises:
        GuardError: The value is neither `"cloud"` nor `"local"`.
    """
    return coerce_enum(engine, Engine, field="engine")


class _ClientBase:
    """
    Configuration and validation shared by both clients.

    This resolves every setting from arguments, the environment, and `.env` exactly
    once. This ensures the synchronous and asynchronous clients cannot disagree about
    what they were told.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        space_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        base_url: Optional[str] = None,
        engine: Optional[EngineLike] = None,
        locale: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        local_model_path: Optional[str] = None,
        env_file: EnvFile = DEFAULT_ENV_FILE,
    ) -> None:
        """
        Resolve every setting from arguments, the environment, and `.env`.

        Args:
            api_key: `GUARD_API_KEY`. Required for every cloud request. A client built
                without one can only run `engine="local"` detection. Any API call it
                makes raises an error rather than going out unauthenticated.
            space_id: `GUARD_SPACE_ID`. Required for cloud calls.
            organization_id: `GUARD_ORGANIZATION_ID`. Default owner for new spaces, and
                the organization runners are scoped to.
            base_url: `GUARD_BASE_URL`. Defaults to `https://api.elhio.com`.
            engine: `GUARD_ENGINE`. `"cloud"` (default) or `"local"`.
            locale: `GUARD_LOCALE`. Defaults to `"en"`.
            timeout: `GUARD_TIMEOUT`. Per-request HTTP timeout. Defaults to 30.0.
            max_retries: `GUARD_MAX_RETRIES`. Defaults to 3.
            local_model_path: `GUARD_LOCAL_MODEL_PATH`. Only used by the local engine.
            env_file: Which env file to read. `None` disables `.env` entirely.

        Raises:
            GuardError: Cloud mode without an API key, or an unknown engine.

        Note:
            Every argument defaults to `None` meaning it is not provided. This is what
            lets an explicit value be told apart from an omitted one. The effective
            defaults are listed above.
        """
        env = EnvSource(env_file)

        resolved_engine = (
            engine if engine is not None else env.get_optional("GUARD_ENGINE")
        )
        self._engine = _coerce_engine(
            DEFAULT_ENGINE if resolved_engine is None else resolved_engine
        )
        self._space_id: Optional[IdLike] = (
            space_id if space_id is not None else env.get_optional("GUARD_SPACE_ID")
        )
        self._organization_id: Optional[IdLike] = (
            organization_id
            if organization_id is not None
            else env.get_optional("GUARD_ORGANIZATION_ID")
        )
        self._config = TransportConfig(
            api_key=env.get_optional("GUARD_API_KEY", api_key),
            base_url=env.get_str("GUARD_BASE_URL", base_url, DEFAULT_BASE_URL),
            locale=env.get_str("GUARD_LOCALE", locale, DEFAULT_LOCALE),
            timeout=env.get_float("GUARD_TIMEOUT", timeout, DEFAULT_TIMEOUT),
            max_retries=env.get_int(
                "GUARD_MAX_RETRIES", max_retries, DEFAULT_MAX_RETRIES
            ),
        )
        if self._engine is Engine.CLOUD and not self._config.api_key:
            raise GuardError(
                "An API key is required for cloud detection. Pass api_key=..., set the "
                "GUARD_API_KEY environment variable, or put it in a .env file (see "
                ".env.example). To run fully on-device, use "
                'GuardClient(engine="local").'
            )
        self._local = LocalRunner(
            model_path=env.get_optional("GUARD_LOCAL_MODEL_PATH", local_model_path)
        )

    @property
    def engine(self) -> Engine:
        """
        The default engine for `analyze`.

        Returns:
            Whichever engine was configured. Individual calls may override it.
        """
        return self._engine

    @property
    def base_url(self) -> str:
        """
        The API root this client talks to.

        Returns:
            The resolved base URL, without a trailing slash.
        """
        return self._config.base_url

    def _effective_engine(self, engine: Optional[EngineLike]) -> Engine:
        """
        Pick the engine for one call.

        Args:
            engine: A per-call override, or `None` to use the client default.

        Returns:
            The engine to use.

        Raises:
            GuardError: The override is not a known engine.
        """
        return self._engine if engine is None else _coerce_engine(engine)

    @staticmethod
    def _resolve_estimate_inputs(
        *,
        source: Optional[MediaSource],
        frames: Optional[int],
        width: Optional[int],
        height: Optional[int],
        duration_seconds: Optional[float],
        media_type: Optional[MediaType] = None,
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fill in whatever was not supplied by probing `source`.

        Probing is skipped entirely when every value is already known. This ensures the
        pure calculation never touches the filesystem.
        """
        needs_probe = (
            width is None
            or height is None
            or (frames is None and duration_seconds is None)
        )

        if needs_probe:
            if source is None:
                raise GuardError(
                    "Nothing to estimate from. Pass a file as the first argument, or "
                    "supply frames=, width= and height= directly."
                )
            info = probe_media(source, media_type=media_type, filename=filename)
            width = info.width if width is None else width
            height = info.height if height is None else height
            if duration_seconds is None:
                duration_seconds = info.duration_seconds

        if duration_seconds is None:
            duration_seconds = 0.0
        # Always derive from the *effective* duration, so an explicit duration_seconds
        # still wins over whatever the probe found.
        if frames is None:
            frames = frames_for(duration_seconds)

        if width is None or height is None:
            raise GuardError(
                "Dimensions are required. Pass width= and height=, or a file to probe."
            )
        return {
            "frames": frames,
            "width": width,
            "height": height,
            "duration_seconds": duration_seconds,
        }


class GuardClient(_ClientBase):
    """
    Synchronous Guard client.

    Every argument below falls back to the matching `GUARD_*` environment variable,
    then to a `.env` file, then to the default shown. See `guard_client.env`.

    Args:
        api_key: `GUARD_API_KEY`. Required for every cloud request. A client built
            without one can only run `engine="local"` detection. Any API call it makes
            raises an error rather than going out unauthenticated.
        space_id: `GUARD_SPACE_ID`. Required for cloud calls.
        organization_id: `GUARD_ORGANIZATION_ID`. Default owner for `Spaces.create` and
            the organization `Runners.list` scopes to.
        base_url: `GUARD_BASE_URL`. Defaults to `https://api.elhio.com`.
        engine: `GUARD_ENGINE`. `"cloud"` (default) or `"local"`.
        locale: `GUARD_LOCALE`. Defaults to `"en"`.
        timeout: `GUARD_TIMEOUT`. Per-request HTTP timeout, defaults to `30.0`.
        max_retries: `GUARD_MAX_RETRIES`. Defaults to `3`.
        local_model_path: `GUARD_LOCAL_MODEL_PATH`. Only used by the local engine.
        env_file: Which env file to read. `None` disables `.env` entirely.
            `GUARD_ENV_FILE` overrides the default of `.env`.
        http_client: Supply your own `httpx.Client` to control pooling or proxies.

    Example:
        ```python
        with GuardClient(api_key=KEY, space_id=SPACE) as client:
            result = client.analyze("photo.jpg")
            print(result.max_score)
        ```
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        space_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        base_url: Optional[str] = None,
        engine: Optional[EngineLike] = None,
        locale: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        local_model_path: Optional[str] = None,
        env_file: EnvFile = DEFAULT_ENV_FILE,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        """
        Build a synchronous client.

        Args:
            api_key: `GUARD_API_KEY`. Required for every cloud request. A client built
                without one can only run `engine="local"` detection. Any API call it
                makes raises an error rather than going out unauthenticated.
            space_id: `GUARD_SPACE_ID`. Required for cloud calls.
            organization_id: `GUARD_ORGANIZATION_ID`. Default owner for new spaces,
                and the organization runners are scoped to.
            base_url: `GUARD_BASE_URL`. Defaults to `https://api.elhio.com`.
            engine: `GUARD_ENGINE`. `"cloud"` (default) or `"local"`.
            locale: `GUARD_LOCALE`. Defaults to `"en"`.
            timeout: `GUARD_TIMEOUT`. Per-request HTTP timeout, defaults to `30.0`.
            max_retries: `GUARD_MAX_RETRIES`. Defaults to `3`.
            local_model_path: `GUARD_LOCAL_MODEL_PATH`. Only used by the local engine.
            env_file: Which env file to read. `None` disables `.env` entirely.
            http_client: Supply your own `httpx.Client` to control pooling or proxies.
                When given, closing this client leaves it open.

        Raises:
            GuardError: Cloud mode without an API key, or an unknown engine.

        Note:
            Every argument defaults to `None` meaning it is not provided. This is what
            lets an explicit value be told apart from an omitted one. The effective
            defaults are listed above.
        """
        super().__init__(
            api_key,
            space_id=space_id,
            organization_id=organization_id,
            base_url=base_url,
            engine=engine,
            locale=locale,
            timeout=timeout,
            max_retries=max_retries,
            local_model_path=local_model_path,
            env_file=env_file,
        )
        self._transport = SyncTransport(self._config, http_client=http_client)
        # The resolved ids, not the raw arguments. They may have come from the
        # environment.
        self.activities = Activities(self._transport, default_space_id=self._space_id)
        self.spaces = Spaces(
            self._transport, default_organization_id=self._organization_id
        )
        self.runners = Runners(
            self._transport, default_organization_id=self._organization_id
        )
        self.predictors = Predictors(self._transport)
        self.tasks = Tasks(self._transport)
        self.reactions = Reactions(self._transport)
        self.shares = Shares(self._transport)

    def analyze(
        self,
        source: MediaSource,
        *,
        space_id: Optional[IdLike] = None,
        engine: Optional[EngineLike] = None,
        media_type: Optional[Union[MediaType, str]] = None,
        filename: Optional[str] = None,
        user_id: Optional[IdLike] = None,
        account_id: Optional[IdLike] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_POLL_TIMEOUT,
    ) -> DetectionResult:
        """
        Run the full detection lifecycle for one piece of media.

        In cloud mode this creates an activity, uploads the bytes, confirms the upload,
        polls until processing finishes, and returns the result. In local mode it runs
        on-device and makes no network calls.

        Args:
            source: A file path, raw `bytes`, or an open binary file object.
            space_id: Overrides the client-level default for this call.
            engine: Overrides the client-level default engine for this call.
            media_type: Skips MIME detection when you already know the type.
            filename: Used for detection and for naming the uploaded file.
            user_id: Owning user, for a user-owned activity.
            account_id: Owning service account.
            poll_interval: Seconds between status polls.
            timeout: Seconds to wait for processing before giving up.

        Returns:
            A `DetectionResult`, identical in shape for either engine.

        Raises:
            ActivityFailedError: Processing ended as `failed` or `canceled`.
            GuardTimeoutError: Processing did not finish within `timeout`.
            UnsupportedMediaTypeError: The media type is not accepted.
            LocalEngineNotInstalledError: Local was requested without the extra.
            GuardLocalEngineError: The local engine failed. Subclasses distinguish a
                model that would not load from media that would not decode.

        Example:
            ```python
            with GuardClient() as client:
                result = client.analyze("photo.jpg")
                for item in result.results:
                    print(item.label, item.score)
            ```
        """
        data, resolved_type, name = resolve_media(
            source, media_type=media_type, filename=filename
        )

        if self._effective_engine(engine) is Engine.LOCAL:
            return self._local.analyze(data, media_type=resolved_type, filename=name)

        activity = self.activities.create(
            media_type=resolved_type,
            media_size=len(data),
            space_id=space_id,
            user_id=user_id,
            account_id=account_id,
        )
        self.activities.upload(
            activity.upload_data, data, media_type=resolved_type, filename=name
        )
        self.activities.confirm(activity.id)
        self.activities.wait_until_done(
            activity.id, interval=poll_interval, timeout=timeout
        )

        detail = self.activities.get(activity.id)
        results = detail.result_payload.results if detail.result_payload else []
        return DetectionResult(
            engine=Engine.CLOUD, results=results, activity_id=activity.id
        )

    def estimate_tokens(
        self,
        source: Optional[MediaSource] = None,
        *,
        space_id: Optional[IdLike] = None,
        multiplier: Optional[int] = None,
        frames: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        duration_seconds: Optional[float] = None,
        media_type: Optional[MediaType] = None,
        filename: Optional[str] = None,
    ) -> TokenEstimate:
        """
        Project what analyzing this media will cost before creating an activity.

        `tokens = frames x resolution_cost x multiplier`. Anything not given is read
        from `source`. Anything given overrides the probe, so you can correct a single
        value without supplying the rest.

        Args:
            source: A path, `bytes` or file object to probe. Omit it and supply
                `frames`, `width` and `height` for a pure calculation.
            space_id: Whose `predictor_multiplier` to use. Defaults to the client space.
            multiplier: Skips the space lookup entirely, making this fully offline.
            frames: Overrides the frame count derived from the duration.
            width: Overrides the probed width, in pixels.
            height: Overrides the probed height, in pixels.
            duration_seconds: Overrides the probed duration, and hence the frames.
            media_type: Skips MIME detection when you already know the type.
            filename: Helps identify raw bytes, and names the file in errors.

        Returns:
            A `TokenEstimate` carrying the breakdown as well as the total.

        Raises:
            GuardError: Values are missing or out of range, or the resolution exceeds
                the top tier.

        Note:
            This is an estimate. The API currently reserves only the minimum possible
            cost when an activity is created, and `payed_tokens` on the finished
            activity is authoritative. Expect the two to differ.
        """
        resolved = self._resolve_estimate_inputs(
            source=source,
            frames=frames,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            media_type=media_type,
            filename=filename,
        )
        if multiplier is None:
            multiplier = self._fetch_multiplier(space_id)
        return estimate_tokens(multiplier=multiplier, **resolved)

    def _fetch_multiplier(self, space_id: Optional[IdLike]) -> int:
        """
        Read `predictor_multiplier` off the space.

        This is the only network call `estimate_tokens` makes, and it is skipped
        entirely when `multiplier` is supplied.

        Args:
            space_id: The space to read, or `None` to use the client default.

        Returns:
            The token multiplier of the space.

        Raises:
            GuardError: No space id is available, or the space reports no multiplier.
            GuardNotFoundError: Unknown space, or you cannot see it.
        """
        effective = space_id if space_id is not None else self._space_id
        if effective is None:
            raise GuardError(
                "A multiplier is required to estimate tokens. Pass multiplier=... to "
                "skip the lookup, or space_id=... (or set one on the client) so it can "
                "be read from the space."
            )
        detail = self.spaces.get(effective)
        if detail.predictor_multiplier is None:
            raise GuardError(
                f"Space {effective} reports no predictor_multiplier. "
                f"Pass multiplier=... explicitly."
            )
        return detail.predictor_multiplier

    def close(self) -> None:
        """
        Release the underlying HTTP connection pool.

        This is unnecessary when the client is used as a context manager, which closes
        it for you.
        """
        self._transport.close()

    def __enter__(self) -> GuardClient:
        """
        Enter a context manager.

        Returns:
            This client, unchanged.
        """
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """
        Leave a context manager, closing the connection pool.

        Args:
            *exc_info: Exception details. These are ignored because closing happens
                either way.
        """
        self.close()


class AsyncGuardClient(_ClientBase):
    """
    Asynchronous Guard client. Mirrors `GuardClient`.

    Takes the same arguments and the same `GUARD_*` and `.env` fallbacks. See
    `GuardClient` for the full list.

    Example:
        ```python
        async with AsyncGuardClient(api_key=KEY, space_id=SPACE) as client:
            result = await client.analyze("photo.jpg")
        ```
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        space_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        base_url: Optional[str] = None,
        engine: Optional[EngineLike] = None,
        locale: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        local_model_path: Optional[str] = None,
        env_file: EnvFile = DEFAULT_ENV_FILE,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        Build an asynchronous client.

        Args:
            api_key: `GUARD_API_KEY`. Required for every cloud request. A client
                built without one can only run `engine="local"` detection. Any API
                call it makes raises an error rather than going out unauthenticated.
            space_id: `GUARD_SPACE_ID`. Required for cloud calls.
            organization_id: `GUARD_ORGANIZATION_ID`. Default owner for new spaces,
                and the organization runners are scoped to.
            base_url: `GUARD_BASE_URL`. Defaults to `https://api.elhio.com`.
            engine: `GUARD_ENGINE`. `"cloud"` (default) or `"local"`.
            locale: `GUARD_LOCALE`. Defaults to `"en"`.
            timeout: `GUARD_TIMEOUT`. Per-request HTTP timeout, defaults to `30.0`.
            max_retries: `GUARD_MAX_RETRIES`. Defaults to `3`.
            local_model_path: `GUARD_LOCAL_MODEL_PATH`. Only used by the local engine.
            env_file: Which env file to read. `None` disables `.env` entirely.
            http_client: Supply your own `httpx.AsyncClient` to control pooling or
                proxies. When given, closing this client leaves it open.

        Raises:
            GuardError: Cloud mode without an API key, or an unknown engine.

        Note:
            Every argument defaults to `None` meaning it is not provided. This is what
            lets an explicit value be told apart from an omitted one. The effective
            defaults are listed above.
        """
        super().__init__(
            api_key,
            space_id=space_id,
            organization_id=organization_id,
            base_url=base_url,
            engine=engine,
            locale=locale,
            timeout=timeout,
            max_retries=max_retries,
            local_model_path=local_model_path,
            env_file=env_file,
        )
        self._transport = AsyncTransport(self._config, http_client=http_client)
        # The resolved ids, not the raw arguments: they may have come from the
        # environment.
        self.activities = AsyncActivities(
            self._transport, default_space_id=self._space_id
        )
        self.spaces = AsyncSpaces(
            self._transport, default_organization_id=self._organization_id
        )
        self.runners = AsyncRunners(
            self._transport, default_organization_id=self._organization_id
        )
        self.predictors = AsyncPredictors(self._transport)
        self.tasks = AsyncTasks(self._transport)
        self.reactions = AsyncReactions(self._transport)
        self.shares = AsyncShares(self._transport)

    async def analyze(
        self,
        source: MediaSource,
        *,
        space_id: Optional[IdLike] = None,
        engine: Optional[EngineLike] = None,
        media_type: Optional[Union[MediaType, str]] = None,
        filename: Optional[str] = None,
        user_id: Optional[IdLike] = None,
        account_id: Optional[IdLike] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_POLL_TIMEOUT,
    ) -> DetectionResult:
        """
        Run the full detection lifecycle for one piece of media.

        Returns:
            A `DetectionResult`. See `GuardClient.analyze` for every argument and the
            errors it can raise.
        """
        data, resolved_type, name = resolve_media(
            source, media_type=media_type, filename=filename
        )

        if self._effective_engine(engine) is Engine.LOCAL:
            return await self._local.analyze_async(
                data, media_type=resolved_type, filename=name
            )

        activity = await self.activities.create(
            media_type=resolved_type,
            media_size=len(data),
            space_id=space_id,
            user_id=user_id,
            account_id=account_id,
        )
        await self.activities.upload(
            activity.upload_data, data, media_type=resolved_type, filename=name
        )
        await self.activities.confirm(activity.id)
        await self.activities.wait_until_done(
            activity.id, interval=poll_interval, timeout=timeout
        )

        detail = await self.activities.get(activity.id)
        results = detail.result_payload.results if detail.result_payload else []
        return DetectionResult(
            engine=Engine.CLOUD, results=results, activity_id=activity.id
        )

    async def estimate_tokens(
        self,
        source: Optional[MediaSource] = None,
        *,
        space_id: Optional[IdLike] = None,
        multiplier: Optional[int] = None,
        frames: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        duration_seconds: Optional[float] = None,
        media_type: Optional[MediaType] = None,
        filename: Optional[str] = None,
    ) -> TokenEstimate:
        """
        Project what analyzing this media will cost before creating an activity.

        Returns:
            A `TokenEstimate`. See `GuardClient.estimate_tokens` for every argument.

        Warning:
            This is an estimate, not a quote. `payed_tokens` on the finished activity
            is authoritative.
        """
        resolved = self._resolve_estimate_inputs(
            source=source,
            frames=frames,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            media_type=media_type,
            filename=filename,
        )
        if multiplier is None:
            multiplier = await self._fetch_multiplier(space_id)
        return estimate_tokens(multiplier=multiplier, **resolved)

    async def _fetch_multiplier(self, space_id: Optional[IdLike]) -> int:
        """
        Read `predictor_multiplier` off the space.

        This is the only network call `estimate_tokens` makes, and it is skipped
        entirely when `multiplier` is supplied.

        Args:
            space_id: The space to read, or `None` to use the client default.

        Returns:
            The token multiplier of the space.

        Raises:
            GuardError: No space id is available, or the space reports no multiplier.
            GuardNotFoundError: Unknown space, or you cannot see it.
        """
        effective = space_id if space_id is not None else self._space_id
        if effective is None:
            raise GuardError(
                "A multiplier is required to estimate tokens. Pass multiplier=... to "
                "skip the lookup, or space_id=... (or set one on the client) so it can "
                "be read from the space."
            )
        detail = await self.spaces.get(effective)
        if detail.predictor_multiplier is None:
            raise GuardError(
                f"Space {effective} reports no predictor_multiplier. "
                f"Pass multiplier=... explicitly."
            )
        return detail.predictor_multiplier

    async def aclose(self) -> None:
        """
        Release the underlying HTTP connection pool.

        This is unnecessary when the client is used as an async context manager.
        """
        await self._transport.aclose()

    async def __aenter__(self) -> AsyncGuardClient:
        """
        Enter an async context manager.

        Returns:
            This client, unchanged.
        """
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        """
        Leave an async context manager, closing the connection pool.

        Args:
            *exc_info: Exception details. These are ignored because closing happens
                either way.
        """
        await self.aclose()
