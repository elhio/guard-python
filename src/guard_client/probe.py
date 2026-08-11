"""
Reads dimensions and duration straight out of media headers.

This is deliberately dependency-free. The client ships with no image or video library,
and adding one just for a cost estimate would be a heavy price. Each parser reads only
the bytes it needs, so probing a large video does not load it into memory.

Anything that cannot be parsed raises a `GuardError` naming the file rather than
guessing. A wrong dimension here becomes a wrong cost estimate.
"""

from __future__ import annotations

import struct
from typing import Iterator, Optional, Tuple

from pydantic import BaseModel, ConfigDict

from .exceptions import GuardError
from .media import MediaSource, resolve_media
from .models import MediaType
from .tokens import frames_for

__all__ = ["MediaInfo", "probe_media"]

#: How much of a file to read. Metadata lives near the start in every format handled
#: here except MOV files that place `moov` last. That is why the QuickTime parser
#: includes a tail read.
_HEAD_BYTES = 512 * 1024

#: Media types that share the ISO-BMFF container format.
_ISO_BMFF_TYPES = frozenset({MediaType.MP4, MediaType.QUICKTIME, MediaType.HEIC})


class MediaInfo(BaseModel):
    """
    What a probe could determine about a piece of media.

    Attributes:
        media_type: The detected MIME type.
        width: Width in pixels.
        height: Height in pixels.
        duration_seconds: Length of a video. Zero for a still image.
        frames: Billable frames, already derived from the duration.
    """

    model_config = ConfigDict(extra="ignore")

    media_type: MediaType
    width: int
    height: int
    duration_seconds: float = 0.0
    frames: int = 1

    @property
    def long_side(self) -> int:
        """
        The dimension the resolution tier is based on.

        Returns:
            The larger of width and height. This makes the resolution tier and the
            resulting cost independent of orientation.
        """
        return max(self.width, self.height)


def _fail(hint: str, reason: str) -> GuardError:
    """
    Build the error raised when a header cannot be read.

    Args:
        hint: How to name the media in the message.
        reason: What specifically went wrong.

    Returns:
        A `GuardError` naming the media and providing the explicit arguments to pass
        instead. Guessing a dimension here would become a wrong cost estimate, so the
        message points at the escape hatch rather than apologizing.
    """
    return GuardError(
        f"Could not read the dimensions of {hint}: {reason}. Pass width=, height= and "
        f"frames= (or duration_seconds=) explicitly instead."
    )


def _png(data: bytes) -> Optional[Tuple[int, int]]:
    """
    Read dimensions from a PNG header.

    Args:
        data: The start of the file.

    Returns:
        A tuple of `(width, height)`, or `None` when the IHDR box is not where it should
        be.
    """
    # 8-byte signature, then an IHDR box whose payload starts at 16
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _gif(data: bytes) -> Optional[Tuple[int, int]]:
    """
    Read dimensions from a GIF header.

    Args:
        data: The start of the file.

    Returns:
        A tuple of `(width, height)`, or `None` when the header is truncated.
    """
    # logical screen descriptor, little-endian, immediately after the 6-byte header
    if len(data) < 10:
        return None
    width, height = struct.unpack("<HH", data[6:10])
    return width, height


def _jpeg(data: bytes) -> Optional[Tuple[int, int]]:
    """
    Read dimensions from a JPEG by walking its segment chain.

    Dimensions live in a start-of-frame segment which sits behind a variable number of
    metadata segments. The chain has to be followed rather than indexed into directly.

    Args:
        data: The start of the file.

    Returns:
        A tuple of `(width, height)`, or `None` when no start-of-frame marker was found.
    """
    index = 2  # skip SOI
    end = len(data)
    while index + 9 < end:
        if data[index] != 0xFF:
            index += 1  # resynchronise on padding
            continue

        marker = data[index + 1]
        # standalone markers carry no length
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if marker == 0xFF:
            index += 1
            continue

        (length,) = struct.unpack(">H", data[index + 2 : index + 4])
        # SOF0-SOF15, excluding the non-frame markers in that range
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return width, height
        if length < 2:
            return None
        index += 2 + length
    return None


def _webp(data: bytes) -> Optional[Tuple[int, int]]:
    """
    Read dimensions from any of the three WebP variants.

    Args:
        data: The start of the file.

    Returns:
        A tuple of `(width, height)`, or `None` when the chunk type is unrecognized.

    Note:
        Lossy, lossless, and extended WebP store their dimensions differently and at
        different offsets. A minimal lossless file is shorter than a lossy one, so each
        branch checks its own length rather than sharing one guard.
    """
    # only enough to identify the variant; each branch checks what it actually needs,
    # since a minimal lossless file is shorter than a lossy one
    if len(data) < 16 or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]

    if chunk == b"VP8 ":
        # lossy: chunk payload starts at 20 with a 3-byte frame tag and 3-byte sync
        # code, so the 14-bit dimensions land at 26
        if len(data) < 30:
            return None
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF

    if chunk == b"VP8L":
        # lossless: a 0x2F signature at 20, then 14+14 bits each stored minus one
        if len(data) < 25 or data[20] != 0x2F:
            return None
        bits = struct.unpack("<I", data[21:25])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1

    if chunk == b"VP8X":
        # extended: 24-bit canvas dimensions, each stored minus one
        if len(data) < 30:
            return None
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height

    return None


def _iso_boxes(data: bytes, start: int, end: int) -> Iterator[Tuple[bytes, int, int]]:
    """
    Walk the ISO-BMFF boxes in a byte range.

    A box is defined by a length, a four-character type, and its payload. Sizes of 1 and
    0 are special. A size of 1 means a 64-bit length follows the type. A size of 0 means
    the box runs to the end of its container.

    Args:
        data: The buffer to read.
        start: Where to begin.
        end: One past the last byte to consider.

    Yields:
        A tuple of `(type, payload_start, payload_end)` per box. It stops early on a
        malformed length rather than reading past the buffer.
    """
    index = start
    while index + 8 <= end:
        (size,) = struct.unpack(">I", data[index : index + 4])
        box_type = data[index + 4 : index + 8]
        header = 8

        if size == 1:  # 64-bit extended size
            if index + 16 > end:
                return
            (size,) = struct.unpack(">Q", data[index + 8 : index + 16])
            header = 16
        elif size == 0:  # extends to the end of the container
            size = end - index

        if size < header:
            return
        yield box_type, index + header, min(index + size, end)
        index += size


#: Container boxes that are FullBoxes. Their 4-byte version/flags field sits before the
#: child boxes. Missing this is why a naive walk never finds anything inside `meta`
_ISO_FULLBOX_CONTAINERS = frozenset({b"meta"})


def _iso_find_all(
    data: bytes, path: Tuple[bytes, ...], start: int, end: int
) -> Iterator[Tuple[int, int]]:
    """
    Find every box matching a nested path.

    Args:
        data: The buffer to read.
        path: Box types to descend through, such as `(b"moov", b"mvhd")`.
        start: Where to begin.
        end: One past the last byte to consider.

    Yields:
        A tuple of `(payload_start, payload_end)` for each match.

    Note:
        A HEIC file usually holds several `ispe` boxes containing thumbnails as well as
        the primary image, so callers need all of them rather than just the first.
        Container boxes that are FullBoxes, such as `meta`, carry four bytes of version
        and flags before their children. Missing that offset is why a naive walk finds
        nothing inside them.
    """
    head, rest = path[0], path[1:]
    for box_type, box_start, box_end in _iso_boxes(data, start, end):
        if box_type != head:
            continue
        if not rest:
            yield box_start, box_end
        else:
            child_start, child_end = box_start, box_end
            if head in _ISO_FULLBOX_CONTAINERS:
                child_start = min(child_start + 4, child_end)
            yield from _iso_find_all(data, rest, child_start, child_end)


def _iso_find(
    data: bytes, path: Tuple[bytes, ...], start: int, end: int
) -> Optional[Tuple[int, int]]:
    """
    Find the first box matching a nested path.

    Args:
        data: The buffer to read.
        path: Box types to descend through, such as `(b"moov", b"mvhd")`.
        start: Where to begin.
        end: One past the last byte to consider.

    Returns:
        A tuple of `(payload_start, payload_end)`, or `None` when the path is absent.
    """
    return next(_iso_find_all(data, path, start, end), None)


def _iso_duration(data: bytes) -> float:
    """
    Read a video duration from its movie header.

    The header stores a tick count and a timescale rather than seconds, so the duration
    is their quotient.

    Args:
        data: The buffer to search.

    Returns:
        Duration in seconds. Returns zero when there is no movie header, such as in a
        HEIC still, or when the file declares its duration unknown.
    """
    found = _iso_find(data, (b"moov", b"mvhd"), 0, len(data))
    if not found:
        return 0.0
    start, end = found
    if end - start < 4:
        return 0.0

    version = data[start]
    if version == 1:
        if end - start < 28:
            return 0.0
        timescale, duration = struct.unpack(">IQ", data[start + 20 : start + 32])
    else:
        if end - start < 16:
            return 0.0
        timescale, duration = struct.unpack(">II", data[start + 12 : start + 20])

    if not timescale:
        return 0.0
    # 0xFFFFFFFF is the "unknown duration" sentinel
    if duration in (0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
        return 0.0
    return float(duration) / float(timescale)


def _iso_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    """
    Read display dimensions from a track header or a HEIC spatial extents box.

    Args:
        data: The buffer to search.

    Returns:
        A tuple of `(width, height)`, or `None` when neither source is present.

    Note:
        Video stores dimensions as 16.16 fixed point in `tkhd`. HEIC has no such box and
        keeps them in `ispe` instead. There is one per stored image, so thumbnails
        appear alongside the real thing and the largest is taken. Resolving the true
        primary item would mean following `pitm` through `ipma` associations, and
        over-reporting is the safer error for a cost estimate.
    """
    found = _iso_find(data, (b"moov", b"trak", b"tkhd"), 0, len(data))
    if found:
        start, end = found
        version = data[start]
        # width/height are the last 8 bytes, as 16.16 fixed point
        offset = start + (96 if version == 1 else 84) - 8
        if offset + 8 <= end:
            width, height = struct.unpack(">II", data[offset : offset + 8])
            width, height = width >> 16, height >> 16
            if width and height:
                return width, height

    # HEIC keeps dimensions in image-spatial-extents boxes instead. There is usually one
    # per stored image, so thumbnails appear alongside the real thing; take the largest.
    # Resolving the true primary item would mean following `pitm` through `ipma`
    # associations, and over-reporting is the safer error for a cost estimate.
    largest: Optional[Tuple[int, int]] = None
    for start, end in _iso_find_all(
        data, (b"meta", b"iprp", b"ipco", b"ispe"), 0, len(data)
    ):
        if end - start < 12:
            continue
        width, height = struct.unpack(">II", data[start + 4 : start + 12])
        if (
            width
            and height
            and (largest is None or width * height > largest[0] * largest[1])
        ):
            largest = (width, height)
    return largest


def _ebml_number(data: bytes, index: int, *, keep_marker: bool) -> Tuple[int, int]:
    """
    Read an EBML variable-length integer.

    The leading zero bits encode the width, and the first set bit is a marker that is
    part of an element ID but not of a length.

    Args:
        data: The buffer to read.
        index: Where the number starts.
        keep_marker: Set to `True` for element IDs which include the marker bit, and
            `False` for lengths where it must be stripped.

    Returns:
        A tuple of `(value, next_index)`.

    Raises:
        ValueError: If the buffer ends mid-number or the width descriptor is invalid.
    """
    if index >= len(data):
        raise ValueError("truncated")
    first = data[index]
    if first == 0:
        raise ValueError("invalid length descriptor")
    length = 8 - first.bit_length() + 1
    if index + length > len(data):
        raise ValueError("truncated")

    value = int.from_bytes(data[index : index + length], "big")
    if not keep_marker:
        value &= (1 << (7 * length)) - 1  # strip the leading marker bit
    return value, index + length


def _webm_elements(data: bytes, start: int, end: int) -> Iterator[Tuple[int, int, int]]:
    """
    Walk the EBML elements in a byte range.

    Args:
        data: The buffer to read.
        start: Where to begin.
        end: One past the last byte to consider.

    Yields:
        A tuple of `(id, payload_start, payload_end)` per element, stopping early on a
        malformed number. An element declaring an unknown size runs to the end of its
        parent.
    """
    index = start
    while index < end:
        try:
            element_id, index = _ebml_number(data, index, keep_marker=True)
            size, index = _ebml_number(data, index, keep_marker=False)
        except ValueError:
            return
        # an unknown-size element runs to the end of its parent
        stop = end if size >= (1 << 56) - 1 else min(index + size, end)
        yield element_id, index, stop
        index = stop


def _webm_uint(data: bytes, start: int, end: int) -> int:
    """
    Read an EBML unsigned integer of any width.

    Args:
        data: The buffer to read.
        start: First byte of the value.
        end: One past the last byte.

    Returns:
        The integer value, or 0 for an empty range.
    """
    return int.from_bytes(data[start:end], "big") if end > start else 0


def _webm_float(data: bytes, start: int, end: int) -> float:
    """
    Read an EBML float.

    Args:
        data: The buffer to read.
        start: First byte of the value.
        end: One past the last byte.

    Returns:
        The float value, or 0.0 for a width Matroska does not define.
    """
    width = end - start
    if width == 4:
        return float(struct.unpack(">f", data[start:end])[0])
    if width == 8:
        return float(struct.unpack(">d", data[start:end])[0])
    return 0.0


def _webm(data: bytes) -> Optional[Tuple[int, int, float]]:
    """
    Read dimensions and duration from a Matroska or WebM header.

    Args:
        data: The start of the file.

    Returns:
        A tuple of `(width, height, duration_seconds)`, or `None` when no video track
        was found.

    Note:
        Duration is stored in timecode ticks scaled by `TimecodeScale` nanoseconds. This
        defaults to a millisecond when the file omits it.
    """
    segment = None
    for element_id, start, end in _webm_elements(data, 0, len(data)):
        if element_id == 0x18538067:  # Segment
            segment = (start, end)
            break
    if segment is None:
        return None

    width = height = 0
    timecode_scale = 1_000_000.0  # nanoseconds per tick, the Matroska default
    raw_duration = 0.0

    for element_id, start, end in _webm_elements(data, *segment):
        if element_id == 0x1549A966:  # Info
            for sub_id, sub_start, sub_end in _webm_elements(data, start, end):
                if sub_id == 0x2AD7B1:  # TimecodeScale
                    timecode_scale = (
                        float(_webm_uint(data, sub_start, sub_end)) or timecode_scale
                    )
                elif sub_id == 0x4489:  # Duration
                    raw_duration = _webm_float(data, sub_start, sub_end)
        elif element_id == 0x1654AE6B:  # Tracks
            for track_id, track_start, track_end in _webm_elements(data, start, end):
                if track_id != 0xAE:  # TrackEntry
                    continue
                for field_id, f_start, f_end in _webm_elements(
                    data, track_start, track_end
                ):
                    if field_id != 0xE0:  # Video
                        continue
                    for v_id, v_start, v_end in _webm_elements(data, f_start, f_end):
                        if v_id == 0xB0:  # PixelWidth
                            width = _webm_uint(data, v_start, v_end)
                        elif v_id == 0xBA:  # PixelHeight
                            height = _webm_uint(data, v_start, v_end)

    if not width or not height:
        return None
    return width, height, raw_duration * timecode_scale / 1_000_000_000.0


def probe_media(
    source: MediaSource,
    *,
    media_type: Optional[MediaType] = None,
    filename: Optional[str] = None,
) -> MediaInfo:
    """
    Read dimensions and duration from a file without decoding it.

    Args:
        source: A path, raw `bytes`, or an open binary file object.
        media_type: Skips MIME detection when you already know the type.
        filename: Used for MIME detection and error messages.

    Returns:
        A `MediaInfo` object with `frames` already derived from the duration.

    Raises:
        GuardError: If the format is unsupported or the header could not be read. The
            message lists the explicit arguments to pass instead.
        UnsupportedMediaTypeError: If the media type is not one the API accepts.
    """
    data, resolved_type, name = resolve_media(
        source, media_type=media_type, filename=filename
    )
    head = data[:_HEAD_BYTES]

    dimensions: Optional[Tuple[int, int]] = None
    duration = 0.0

    if resolved_type is MediaType.PNG:
        dimensions = _png(head)
    elif resolved_type is MediaType.JPEG:
        dimensions = _jpeg(head)
    elif resolved_type is MediaType.GIF:
        dimensions = _gif(head)
    elif resolved_type is MediaType.WEBP:
        dimensions = _webp(head)
    elif resolved_type is MediaType.WEBM:
        parsed = _webm(head)
        if parsed:
            dimensions = (parsed[0], parsed[1])
            duration = parsed[2]
    elif resolved_type in _ISO_BMFF_TYPES:
        dimensions = _iso_dimensions(head)
        duration = _iso_duration(head)
        if dimensions is None and len(data) > _HEAD_BYTES:
            # QuickTime often writes `moov` at the end of the file
            tail = data[-_HEAD_BYTES:]
            dimensions = _iso_dimensions(tail)
            duration = _iso_duration(tail)

    if dimensions is None:
        raise _fail(name, f"unrecognised {resolved_type.value} header")

    width, height = dimensions
    if width <= 0 or height <= 0:
        raise _fail(name, f"header reported a {width}x{height} frame")

    return MediaInfo(
        media_type=resolved_type,
        width=width,
        height=height,
        duration_seconds=duration,
        frames=frames_for(duration),
    )
