"""
Viewing and saving media for notebooks and scripts alike.

The `show()` function renders inline in Jupyter and falls back to the operating system
viewer anywhere else. `save()` writes bytes to disk and never opens anything.

Both accept whatever source you already have: a local file, raw bytes, a URL, or a
result object carrying a `media_url`. Neither requires a configured client. Result media
lives at plain, unauthenticated URLs, meaning no credentials are involved.
"""

from __future__ import annotations

import mimetypes
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

import httpx

from .exceptions import GuardError
from .media import MediaSource, resolve_media
from .models import MediaType

__all__ = ["load_media", "save", "show"]

#: Types a browser will actually render. HEIC is unsupported in Chrome and Firefox,
#: and `video/quicktime` is poorly supported. Embedding either produces a silently
#: broken element, so they take the system-viewer path instead.
BROWSER_RENDERABLE = frozenset(
    {
        MediaType.JPEG,
        MediaType.PNG,
        MediaType.WEBP,
        MediaType.GIF,
        MediaType.MP4,
        MediaType.WEBM,
    }
)

#: Video media types used to determine inline display behavior.
_VIDEO_TYPES = frozenset({MediaType.MP4, MediaType.WEBM, MediaType.QUICKTIME})

#: Extensions to fall back on when a media type has no `mimetypes` entry.
_EXTENSIONS = {
    MediaType.JPEG: ".jpg",
    MediaType.PNG: ".png",
    MediaType.WEBP: ".webp",
    MediaType.GIF: ".gif",
    MediaType.HEIC: ".heic",
    MediaType.MP4: ".mp4",
    MediaType.WEBM: ".webm",
    MediaType.QUICKTIME: ".mov",
}

#: A type alias for the sources that can be displayed or saved.
DisplaySource = Union[MediaSource, Any]


def _is_url(value: Any) -> bool:
    """
    Decide whether a source is an HTTP or HTTPS URL rather than a path.

    Args:
        value: Any accepted source.

    Returns:
        `True` for a string starting with `http://` or `https://`.
    """
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _fetch(url: str) -> bytes:
    """
    Download media from a plain URL.

    This is deliberately a bare request. Result media lives at unauthenticated URLs,
    and attaching the client API key would hand it to a third-party host.
    """
    try:
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
    except httpx.HTTPError as exc:
        raise GuardError(f"Could not download {url}: {exc}") from exc

    if not response.is_success:
        raise GuardError(
            f"Could not download {url}: {response.status_code} {response.reason_phrase}"
        )
    return response.content


def _media_url_of(source: Any) -> Optional[str]:
    """
    Extract the `media_url` of a result item, share, or anything else exposing one.

    This uses duck typing rather than isinstance checks so new models work without
    requiring changes here.
    """
    return (
        getattr(source, "media_url", None)
        if not isinstance(source, (str, bytes))
        else None
    )


def load_media(
    source: DisplaySource,
    *,
    media_type: Optional[MediaType] = None,
    filename: Optional[str] = None,
) -> Tuple[bytes, MediaType, str]:
    """
    Resolve any supported source to `(data, media_type, filename)`.

    Args:
        source: A path, raw `bytes`, an open binary file, an HTTP or HTTPS URL,
            or an object with a `media_url` such as an `ActivityResultItem` or `Share`.
        media_type: Skips detection when you already know the type.
        filename: Used for detection and for naming a saved file.

    Raises:
        GuardError: A result object carries no media, or a URL could not be fetched.
        UnsupportedMediaTypeError: The media type is not one the API accepts.
    """
    if not _is_url(source):
        url = _media_url_of(source)
        if url is not None:
            source = url
        elif hasattr(source, "media_url"):
            # attribute exists but is None, meaning the result has no image at all
            raise GuardError(
                f"{type(source).__name__} has no media_url, so there is nothing to "
                f"show. Not every result includes an image."
            )

    if isinstance(source, str) and _is_url(source):
        data = _fetch(source)
        filename = filename or Path(source.split("?", 1)[0]).name or None
        return resolve_media(data, media_type=media_type, filename=filename)

    return resolve_media(source, media_type=media_type, filename=filename)


def _result_items(source: Any) -> Optional[List[Any]]:
    """
    Recognize a whole detection result as opposed to a single item.

    Args:
        source: Any accepted source.

    Returns:
        The result items when `source` is a `DetectionResult`, allowing `show()` to
        render each in turn. It returns `None` for anything else, including a single
        result item that carries its own `media_url`.
    """
    items = getattr(source, "results", None)
    if isinstance(items, list) and not hasattr(source, "media_url"):
        return items
    return None


def _in_notebook() -> bool:
    """
    Determine whether we are in a Jupyter kernel that can render rich output.

    Terminal IPython reports a `TerminalInteractiveShell` and cannot display images.
    It deliberately does not count as a notebook environment.
    """
    try:
        import IPython
    except ImportError:
        return False

    # Accessed off the module rather than imported by name: IPython does not re-export
    # get_ipython in a way type checkers recognize
    get_ipython = getattr(IPython, "get_ipython", None)
    if get_ipython is None:
        return False

    shell = get_ipython()
    return shell is not None and type(shell).__name__ == "ZMQInteractiveShell"


def _open_in_viewer(data: bytes, media_type: MediaType, filename: str) -> Path:
    """
    Write to a temporary file and hand it to the operating system.

    The file is not cleaned up. Viewers open asynchronously, so deleting it here would
    race the application that is about to read it. Everything lands in one
    `guard-media-` directory so it is obvious what to purge.
    """
    directory = Path(tempfile.mkdtemp(prefix="guard-media-"))
    suffix = Path(filename).suffix or _EXTENSIONS.get(media_type, "")
    path = directory / (Path(filename).stem or "media")
    path = path.with_suffix(suffix)
    path.write_bytes(data)

    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        elif sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError as exc:  # pragma: no cover, platform dependent
        raise GuardError(f"Could not open a viewer for {path}: {exc}") from exc
    return path


def show(
    source: DisplaySource,
    *,
    media_type: Optional[MediaType] = None,
    filename: Optional[str] = None,
    width: Optional[int] = None,
    open_viewer: bool = True,
) -> None:
    """
    Display media inline in Jupyter or in the system viewer elsewhere.

    Args:
        source: A path, `bytes`, a URL, a result item, a `Share`, or a whole
            `DetectionResult`. A full result shows every item that has an image.
        media_type: Skips detection when you already know the type.
        filename: Used for detection and for naming the temporary file.
        width: Display width in pixels. This is honored only for inline rendering.
        open_viewer: Set `False` to suppress launching an application. This is worth
            doing in CI environments where spawning a viewer on a build agent is
            unwelcome.

    Raises:
        GuardError: The source carries no media or could not be fetched.

    Example:
        ```python
        show("photo.jpg")
        show(result.results[0])
        show(result, width=400)
        ```

    Note:
        HEIC and QuickTime cannot be rendered by most browsers. They will use the system
        viewer even inside a notebook.
    """
    items = _result_items(source)
    if items is not None:
        shown = 0
        for item in items:
            if getattr(item, "media_url", None):
                show(item, width=width, open_viewer=open_viewer)
                shown += 1
        if not shown:
            print("No result in this detection carries an image.")
        return

    data, resolved_type, name = load_media(
        source, media_type=media_type, filename=filename
    )

    if _in_notebook():
        if resolved_type in BROWSER_RENDERABLE:
            _display_inline(data, resolved_type, width)
            return
        print(
            f"{resolved_type.value} cannot be rendered inline by most browsers; "
            f"opening it in the system viewer instead."
        )

    if not open_viewer:
        return
    _open_in_viewer(data, resolved_type, name)


def _display_inline(data: bytes, media_type: MediaType, width: Optional[int]) -> None:
    """
    Embed the bytes in notebook output.

    Embedding rather than linking keeps the output working after the notebook is shared
    or the media expires. It also keeps the media URL out of the saved `.ipynb` file.
    """
    from IPython.display import Image, Video, display

    if media_type in _VIDEO_TYPES:
        display(Video(data=data, embed=True, mimetype=media_type.value, width=width))
    else:
        display(Image(data=data, width=width))


def save(
    source: DisplaySource,
    path: Union[str, os.PathLike[str]],
    *,
    media_type: Optional[MediaType] = None,
    filename: Optional[str] = None,
    overwrite: bool = True,
) -> Path:
    """
    Write media to disk while never opening a viewer.

    Args:
        source: Anything `load_media` accepts.
        path: A file path, or a directory to write into. When writing to a directory,
            the name comes from the source and the extension from its media type.
        media_type: Skips detection when you already know the type.
        filename: Overrides the name used when `path` is a directory.
        overwrite: Set `False` to refuse replacing an existing file.

    Returns:
        The path actually written.

    Raises:
        GuardError: The source carries no media, or the target exists and
            `overwrite` is `False`.
    """
    data, resolved_type, name = load_media(
        source, media_type=media_type, filename=filename
    )

    target = Path(os.fspath(path))
    if target.is_dir() or str(path).endswith((os.sep, "/")):
        target = target / name
    if not target.suffix:
        target = target.with_suffix(
            mimetypes.guess_extension(resolved_type.value)
            or _EXTENSIONS.get(resolved_type, "")
        )

    if target.exists() and not overwrite:
        raise GuardError(f"{target} already exists. Pass overwrite=True to replace it.")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target
