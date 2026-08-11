"""
Wrapper around the optional on-device detection engine.

The engine itself lives in a separate repo and ships as the `guard-local-detector`
package (import name `guard_local`). This package is AGPL-3.0 licensed. It is therefore
an opt-in extra and is imported lazily inside the call. Importing `guard_client` must
never require it.

This module manages the adaptation from the engine's raw output to `DetectionResult`.
This ensures the rest of the package never touches `guard_local` types.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Protocol, Type, Union
from uuid import UUID, uuid5

from pydantic import ValidationError

from .exceptions import (
    GuardError,
    GuardLocalEngineError,
    GuardLocalModelError,
    GuardMediaDecodeError,
    LocalEngineNotInstalledError,
    UnsupportedMediaTypeError,
)
from .media import MediaSource, resolve_media
from .models import (
    ActivityResultItem,
    DetectionMatch,
    DetectionResult,
    Engine,
    MediaType,
)

__all__ = ["LocalEngine", "LocalRunner"]

#: The hint shown when the local engine is requested but not installed.
_INSTALL_HINT = (
    "The local engine is an optional extra. Install it with:\n"
    '    pip install "guard-client[local]"'
)

#: A fixed UUID namespace used to derive stable task IDs for local runs. Local runs have
#: no server-assigned task IDs, so we derive them from the label. A fixed namespace
#: keeps them reproducible across processes and releases.
_LOCAL_TASK_NAMESPACE = UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

#: Maps the exception name from `guard_local.exceptions` to the `GuardError` raised in
#: its place. The engine's hierarchy derives from nothing here, so without this map an
#: `except GuardError` would miss every local failure. `UnsupportedMediaError` maps onto
#: the type the cloud path already raises for the same mistake, rather than a local-only
#: twin of it.
_LOCAL_ERROR_MAP: Dict[str, Type[GuardError]] = {
    "UnsupportedMediaError": UnsupportedMediaTypeError,
    "MediaDecodeError": GuardMediaDecodeError,
    "ModelLoadError": GuardLocalModelError,
    "GuardLocalError": GuardLocalEngineError,
}


def _map_local_error(exc: BaseException) -> Optional[GuardError]:
    """
    Find the `GuardError` standing in for an engine exception.

    This matches on the MRO by module and class name rather than with `isinstance`.
    Importing `guard_local` to get the classes would break the lazy-import guarantee for
    every caller who never asked for the local engine. It would also stop the tests that
    drive fakes.

    Args:
        exc: Whatever escaped the engine.

    Returns:
        The replacement exception to raise, or `None` when `exc` did not come from
        `guard_local` and should propagate untouched. MRO order ensures the most
        specific match wins, so a `MediaDecodeError` resolves before its
        `GuardLocalError` base class.
    """
    for klass in type(exc).__mro__:
        # Name alone is too weak. If an engine re-raises some other library's
        # ModelLoadError, it must not be relabelled as ours
        if klass.__module__.split(".")[0] != "guard_local":
            continue
        target = _LOCAL_ERROR_MAP.get(klass.__name__)
        if target is not None:
            return target(str(exc))
    return None


@contextmanager
def _mapped_local_errors() -> Iterator[None]:
    """
    Translate engine exceptions into `GuardError` for the enclosed block.

    Yields:
        Nothing. This context manager exists only for its `except` clause.

    Raises:
        GuardLocalEngineError: Or one of its subclasses in place of the engine's own
            exception, which is kept as `__cause__`. Anything not raised by
            `guard_local` propagates unchanged. A bug in the engine should surface as
            the bug it is rather than being disguised as a client error.
    """
    try:
        yield
    except Exception as exc:
        mapped = _map_local_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


class LocalEngine(Protocol):
    """
    The contract this package expects of the local engine.

    `guard-local-detector` is still in development. Coding against this protocol
    keeps the adapter stable while that repo settles. It also lets the tests
    substitute a fake engine so they can run without the ONNX runtime installed.
    """

    def analyze(self, data: bytes, media_type: str) -> Dict[str, Any]:
        """
        Score media bytes.

        Args:
            data: The media to analyse.
            media_type: Its MIME type.

        Returns:
            Raw engine output that will be adapted by this module into the shared result
            shape.
        """
        ...

    async def analyze_async(self, data: bytes, media_type: str) -> Dict[str, Any]:
        """
        Score media bytes without blocking the event loop.

        Args:
            data: The media to analyse.
            media_type: Its MIME type.

        Returns:
            Raw engine output, the same as what `analyze` returns.
        """
        ...


def _load_engine(model_path: Optional[str] = None) -> Any:
    """
    Import and construct the local engine.

    Args:
        model_path: Where the ONNX model lives when the engine requires it.

    Returns:
        A constructed engine satisfying the `LocalEngine` protocol.

    Raises:
        LocalEngineNotInstalledError: If the package is absent, one of its dependencies
            is absent, or it is installed but unimportable. The error message
            distinguishes these scenarios because telling a user to install the extra
            is unhelpful advice if it is already installed.
    """
    try:
        import guard_local
    except ModuleNotFoundError as exc:
        # engine package itself is absent: the common case, fixed by the extra
        if exc.name == "guard_local":
            raise LocalEngineNotInstalledError(
                f"{_INSTALL_HINT}\n\n(original error: {exc})"
            ) from exc
        # something the engine depends on is missing: its install is incomplete
        raise LocalEngineNotInstalledError(
            f"guard-local-detector is installed but its dependency {exc.name!r} is "
            f"not. Reinstall it with: "
            f'pip install --force-reinstall "guard-client[local]"'
            f"\n\n(original error: {exc})"
        ) from exc
    except ImportError as exc:
        # package exists but failed to import
        raise LocalEngineNotInstalledError(
            "guard-local-detector is installed but could not be imported. "
            f'Reinstall it with: pip install --force-reinstall "guard-client[local]"'
            f"\n\n(original error: {exc})"
        ) from exc

    factory = getattr(guard_local, "LocalDetectorEngine", None)
    if factory is None:
        raise LocalEngineNotInstalledError(
            "The installed guard-local-detector does not expose LocalDetectorEngine. "
            "Upgrade it with: pip install --upgrade 'guard-client[local]'"
        )
    return factory(model_path) if model_path is not None else factory()


def _score_to_int(value: Any) -> int:
    """
    Normalize an engine score to the 0-100 integer scale of the API.

    The engine reports a 0-1 probability while the cloud API reports 0-100. A value that
    fits in the unit interval is rescaled. Anything already above 1 is assumed to be on
    the 0-100 scale and is simply clamped.

    Args:
        value: The raw score from the engine.

    Returns:
        An integer between 0 and 100.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0
    if 0.0 <= numeric <= 1.0:
        numeric *= 100.0
    return max(0, min(100, round(numeric)))


def _matches(value: Any) -> Optional[List[DetectionMatch]]:
    """
    Read the evidence list from the engine while tolerating invalid entries.

    Args:
        value: Whatever the engine provided under the `matches` key.

    Returns:
        The parsed evidence, or `None` when the engine reported no evidence. Entries
        that do not parse are skipped rather than raising an error. Partial evidence is
        still worth more than a failed analysis, which mirrors how the rest of `_adapt`
        treats malformed data.
    """
    if not isinstance(value, (list, tuple)):
        return None
    matches = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        try:
            matches.append(DetectionMatch.model_validate(entry))
        except ValidationError:
            continue
    return matches


def _adapt(raw: Any) -> List[ActivityResultItem]:
    """
    Turn raw engine output into the items the cloud API returns.

    This function accepts either a single mapping or a sequence of them because the
    engine output shape is not yet frozen. The `detected` and `matches` fields are
    carried through when the engine reports them and left as `None` when it does not,
    matching the behavior of the cloud path.

    Args:
        raw: The raw output returned by the engine.

    Returns:
        A list of standardized `ActivityResultItem` objects.
    """
    if raw is None:
        return []
    entries = raw if isinstance(raw, (list, tuple)) else [raw]

    items: List[ActivityResultItem] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or entry.get("status") or "unknown")
        raw_task_id = entry.get("task_id")
        try:
            task_id = (
                UUID(str(raw_task_id))
                if raw_task_id
                else uuid5(_LOCAL_TASK_NAMESPACE, label)
            )
        except ValueError:
            task_id = uuid5(_LOCAL_TASK_NAMESPACE, label)

        detected = entry.get("detected")
        items.append(
            ActivityResultItem(
                task_id=task_id,
                score=_score_to_int(entry.get("score")),
                label=label,
                description=entry.get("description"),
                media_key=entry.get("media_key"),
                media_url=entry.get("media_url"),
                detected=detected if isinstance(detected, bool) else None,
                matches=_matches(entry.get("matches")),
            )
        )
    return items


class LocalRunner:
    """
    Lazily loads and caches one engine instance, then adapts its output.

    The ONNX session load is an expensive operation. To mitigate this, the engine is
    constructed on its first use and reused for the lifetime of the client that owns
    this runner.
    """

    def __init__(
        self, *, model_path: Optional[str] = None, engine: Optional[Any] = None
    ) -> None:
        """
        Prepare a runner without loading anything yet.

        Args:
            model_path: Where the ONNX model lives. This is passed through to the
                engine.
            engine: An already-constructed engine. This is mainly used for tests which
                substitute a fake engine so the test suite runs without the ONNX runtime
                installed.
        """
        self._model_path = model_path
        self._engine = engine

    def _ensure_engine(self) -> Any:
        """
        Load the engine on first use and reuse it subsequently.

        Returns:
            The cached engine. It is constructed if this is the first call.

        Raises:
            LocalEngineNotInstalledError: If the engine could not be loaded.
        """
        if self._engine is None:
            self._engine = _load_engine(self._model_path)
        return self._engine

    def analyze(
        self,
        source: MediaSource,
        *,
        media_type: Optional[Union[MediaType, str]] = None,
        filename: Optional[str] = None,
    ) -> DetectionResult:
        """
        Run on-device detection.

        Args:
            source: A path, raw `bytes`, or an open binary file.
            media_type: Skips MIME detection when you already know the type.
            filename: Used for detection and error messages.

        Returns:
            A `DetectionResult` with `engine=LOCAL` and no `activity_id` because nothing
            was created server-side.

        Raises:
            LocalEngineNotInstalledError: The optional engine is unavailable.
            UnsupportedMediaTypeError: The media type is not one the API accepts or is
                one the engine itself cannot score.
            GuardMediaDecodeError: The bytes could not be decoded.
            GuardLocalModelError: The detection model could not be loaded.
            GuardLocalEngineError: Any other failure that occurs inside the engine.
        """
        data, resolved_type, _ = resolve_media(
            source, media_type=media_type, filename=filename
        )
        with _mapped_local_errors():
            engine = self._ensure_engine()
            raw = engine.analyze(data, resolved_type.value)
        return DetectionResult(
            engine=Engine.LOCAL, results=_adapt(raw), activity_id=None
        )

    async def analyze_async(
        self,
        source: MediaSource,
        *,
        media_type: Optional[Union[MediaType, str]] = None,
        filename: Optional[str] = None,
    ) -> DetectionResult:
        """
        Run on-device detection without blocking the event loop.

        Args:
            source: A path, raw `bytes`, or an open binary file.
            media_type: Skips MIME detection when you already know the type.
            filename: Used for detection and error messages.

        Returns:
            A `DetectionResult` with `engine=LOCAL`.

        Raises:
            LocalEngineNotInstalledError: The optional engine is unavailable.
            UnsupportedMediaTypeError: The media type is not one the API accepts or is
                one the engine itself cannot score.
            GuardMediaDecodeError: The bytes could not be decoded.
            GuardLocalModelError: The detection model could not be loaded.
            GuardLocalEngineError: Any other failure that occurs inside the engine.

        Note:
            If an engine offers no async entry point, the work is offloaded to a thread.
            This ensures that inference never stalls the event loop.
        """
        data, resolved_type, _ = resolve_media(
            source, media_type=media_type, filename=filename
        )
        with _mapped_local_errors():
            engine = self._ensure_engine()

            analyze_async = getattr(engine, "analyze_async", None)
            if analyze_async is not None:
                raw = await analyze_async(data, resolved_type.value)
            else:
                # engine is sync-only: offload so inference does not stall the loop
                raw = await asyncio.to_thread(engine.analyze, data, resolved_type.value)

        return DetectionResult(
            engine=Engine.LOCAL, results=_adapt(raw), activity_id=None
        )
