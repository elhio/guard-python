"""
Turns whatever the caller passed into bytes and a media type.

Every entry point that touches media like uploading, probing, and displaying funnels
through `resolve_media` so they all agree on what a file is. A path, raw `bytes`, and
an open binary file are treated alike.

Detection tries the filename first and magic bytes second. An extension is cheap and
usually right, while the bytes are authoritative when there is no name to go on.
Anything the API would not accept is rejected here rather than at upload time.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import IO, Optional, Tuple, Union

from .exceptions import UnsupportedMediaTypeError
from .models import MediaType

__all__ = ["MediaSource", "SUPPORTED_MEDIA_TYPES", "resolve_media"]

#: Every MIME type the backend accepts. This is derived from the enum so there is one
#: source of truth.
SUPPORTED_MEDIA_TYPES = frozenset(item.value for item in MediaType)

#: A type alias for anything `resolve_media` knows how to turn into bytes.
MediaSource = Union[str, "os.PathLike[str]", bytes, bytearray, IO[bytes]]

#: Magic-byte prefixes checked when there is no filename to go on. These are ordered
#: longest-first where prefixes would otherwise collide.
_MAGIC_PREFIXES: Tuple[Tuple[bytes, MediaType], ...] = (
    (b"\xff\xd8\xff", MediaType.JPEG),
    (b"\x89PNG\r\n\x1a\n", MediaType.PNG),
    (b"GIF87a", MediaType.GIF),
    (b"GIF89a", MediaType.GIF),
    (b"\x1a\x45\xdf\xa3", MediaType.WEBM),
)

#: HEIC and the MP4 family share the ISO-BMFF container. They are told apart by the
#: major brand that follows the "ftyp" box at offset 4.
_FTYP_BRANDS: Tuple[Tuple[bytes, MediaType], ...] = (
    (b"heic", MediaType.HEIC),
    (b"heix", MediaType.HEIC),
    (b"heif", MediaType.HEIC),
    (b"mif1", MediaType.HEIC),
    (b"qt  ", MediaType.QUICKTIME),
)

#: Extensions to fall back on when a media type has no `mimetypes` entry. The
#: `mimetypes` module does not know these on every platform, so we pin the ones we care
#: about.
_EXTENSION_OVERRIDES = {
    ".heic": MediaType.HEIC,
    ".heif": MediaType.HEIC,
    ".webp": MediaType.WEBP,
    ".webm": MediaType.WEBM,
    ".mov": MediaType.QUICKTIME,
}


def _coerce(mime: Optional[str], *, hint: str) -> MediaType:
    """
    Turn a MIME string into a `MediaType` or explain why it cannot be.

    Args:
        mime: The detected MIME type, or `None` when detection found nothing.
        hint: How to refer to the media in an error message.

    Returns:
        The matching media type enum member.

    Raises:
        UnsupportedMediaTypeError: If detection failed or the type is one the API does
            not accept. The message lists what is supported.
    """
    if mime is None:
        raise UnsupportedMediaTypeError(
            f"Could not determine the media type of {hint}. "
            f"Pass media_type= explicitly. Supported: {sorted(SUPPORTED_MEDIA_TYPES)}"
        )
    try:
        return MediaType(mime)
    except ValueError as exc:
        raise UnsupportedMediaTypeError(
            f"Unsupported media type {mime!r} for {hint}. "
            f"Supported: {sorted(SUPPORTED_MEDIA_TYPES)}"
        ) from exc


def _sniff(data: bytes) -> Optional[str]:
    """
    Guess a MIME type from magic bytes.

    This is used when there is no filename to go on, and it serves as the authority when
    the filename provides an incorrect extension.

    Args:
        data: The start of the file. Twelve bytes is enough for every format evaluated
            here.

    Returns:
        The detected MIME type, or `None` when nothing matches.

    Note:
        HEIC and the MP4 family share the ISO-BMFF container, so they are told apart by
        the brand following the `ftyp` box rather than by a fixed prefix.
    """
    for prefix, media_type in _MAGIC_PREFIXES:
        if data.startswith(prefix):
            return media_type.value

    if len(data) >= 12:
        if data[4:8] == b"ftyp":
            brand = data[8:12]
            for candidate, media_type in _FTYP_BRANDS:
                if brand == candidate:
                    return media_type.value
            # every other ISO-BMFF brand (isom, mp42, avc1, ...) is an MP4
            return MediaType.MP4.value
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return MediaType.WEBP.value

    return None


def _from_filename(name: str) -> Optional[str]:
    """
    Guess a MIME type from a filename while honoring extension overrides.

    Args:
        name: A filename or path. Only the suffix is consulted.

    Returns:
        The detected MIME type, or `None` when the extension is unknown.

    Note:
        A handful of extensions are pinned rather than left to `mimetypes` because the
        standard module does not know `.heic` or `.webp` on every platform.
    """
    suffix = Path(name).suffix.lower()
    if suffix in _EXTENSION_OVERRIDES:
        return _EXTENSION_OVERRIDES[suffix].value
    guessed, _ = mimetypes.guess_type(name)
    return guessed


def resolve_media(
    source: MediaSource,
    *,
    media_type: Optional[Union[MediaType, str]] = None,
    filename: Optional[str] = None,
) -> Tuple[bytes, MediaType, str]:
    """
    Read `source` into bytes and determine its media type and filename.

    This function accepts a filesystem path, raw bytes, or an open binary file object.
    An explicit `media_type` short-circuits detection. Otherwise, the filename is tried
    first and magic-byte sniffing second.

    Args:
        source: The input media as a path, raw bytes, or an open file object.
        media_type: An optional explicit media type to skip detection.
        filename: An optional explicit filename to use for detection and naming.

    Returns:
        A tuple containing the raw data bytes, the resolved `MediaType`, and the
        filename.

    Raises:
        UnsupportedMediaTypeError: If the type could not be determined or is not
            accepted.
        FileNotFoundError: If `source` is a path that does not exist.
    """
    name = filename
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        hint = "the provided bytes"
    elif hasattr(source, "read"):
        stream: IO[bytes] = source  # type: ignore[assignment]
        data = stream.read()
        if not isinstance(data, bytes):
            raise UnsupportedMediaTypeError(
                "File object must be opened in binary mode (e.g. open(path, 'rb'))"
            )
        # `.name` is the full path on a real file handle; only the basename belongs
        # in the multipart filename
        stream_name = getattr(stream, "name", None)
        if name is None and isinstance(stream_name, str) and stream_name:
            name = Path(stream_name).name
        hint = f"file object {name!r}" if name else "the provided file object"
    else:
        path = Path(os.fspath(source))
        data = path.read_bytes()
        name = name or path.name
        hint = str(path)

    if not data:
        raise UnsupportedMediaTypeError(f"{hint} is empty")

    if media_type is not None:
        resolved = _coerce(
            media_type.value if isinstance(media_type, MediaType) else media_type,
            hint=hint,
        )
    else:
        guessed = (_from_filename(name) if name else None) or _sniff(data)
        resolved = _coerce(guessed, hint=hint)

    if not name:
        name = f"upload{mimetypes.guess_extension(resolved.value) or ''}"

    return data, resolved, name
