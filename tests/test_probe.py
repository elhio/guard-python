"""
Tests for dependency-free media header parsing.

Fixtures are built by hand so the expected dimensions are known exactly. The same
parsers were also checked against real ffmpeg-generated media and macOS `sips` during
development. These tests lock in the byte-level layouts.
"""

from __future__ import annotations

import struct

import pytest

from guard_client import GuardError, MediaType, UnsupportedMediaTypeError, probe_media

from .conftest import png_bytes


def jpeg_with_size(width: int, height: int) -> bytes:
    """Build a mock JPEG file where the SOF0 segment is placed after an APP0 segment."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 11)
        + b"\x08"
        + struct.pack(">HH", height, width)
    )
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def gif_with_size(width: int, height: int) -> bytes:
    """Build a mock GIF file with the specified width and height in its header."""
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 10


def webp_lossy(width: int, height: int) -> bytes:
    """Build a mock lossy WEBP file with the given dimensions."""
    # 3-byte frame tag + 3-byte sync code, then the dimensions
    payload = b"\x00" * 6 + struct.pack("<HH", width, height)
    body = b"WEBP" + b"VP8 " + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_lossless(width: int, height: int) -> bytes:
    """Build a mock lossless WEBP file with the given dimensions."""
    bits = (width - 1) | ((height - 1) << 14)
    payload = b"\x2f" + struct.pack("<I", bits)
    body = b"WEBP" + b"VP8L" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_extended(width: int, height: int) -> bytes:
    """Build a mock extended WEBP file with the given dimensions."""
    payload = (
        bytes([0x10, 0, 0, 0])
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )
    body = b"WEBP" + b"VP8X" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _box(box_type: bytes, payload: bytes) -> bytes:
    """Wrap a payload in an ISO-BMFF box structure."""
    return struct.pack(">I", len(payload) + 8) + box_type + payload


def mp4_with(
    width: int, height: int, *, duration: float, timescale: int = 1000
) -> bytes:
    """Build a minimal ISO-BMFF file containing only the boxes the parser reads."""
    mvhd = _box(
        b"mvhd",
        b"\x00\x00\x00\x00"  # version 0 + flags
        + b"\x00" * 8  # creation / modification time
        + struct.pack(">I", timescale)
        + struct.pack(">I", int(duration * timescale))
        + b"\x00" * 80,
    )
    # tkhd v0: width/height are the last 8 bytes of an 84-byte payload, 16.16 fixed
    tkhd_payload = bytearray(b"\x00" * 84)
    tkhd_payload[76:84] = struct.pack(">II", width << 16, height << 16)
    trak = _box(b"trak", _box(b"tkhd", bytes(tkhd_payload)))
    return _box(b"ftyp", b"isom" + b"\x00" * 8) + _box(b"moov", mvhd + trak)


def heic_with(width: int, height: int) -> bytes:
    """Build a mock HEIC file that carries a thumbnail ispe alongside the main one."""
    thumb = _box(b"ispe", b"\x00\x00\x00\x00" + struct.pack(">II", 320, 240))
    full = _box(b"ispe", b"\x00\x00\x00\x00" + struct.pack(">II", width, height))
    ipco = _box(b"ipco", thumb + full)
    iprp = _box(b"iprp", ipco)
    # `meta` is a FullBox: 4 bytes of version+flags before its children.
    meta = _box(b"meta", b"\x00\x00\x00\x00" + iprp)
    return _box(b"ftyp", b"heic" + b"\x00" * 8) + meta


def _ebml(element_id: bytes, payload: bytes) -> bytes:
    """Wrap a payload in an EBML element structure."""
    size = len(payload)
    if size < 0x7F:
        length = bytes([0x80 | size])
    else:
        length = b"\x40" + struct.pack(">H", size)[1:] if size < 0x3FFF else b""
        length = bytes([0x40 | (size >> 8), size & 0xFF])
    return element_id + length + payload


def webm_with(width: int, height: int, *, duration: float) -> bytes:
    """Build a minimal Matroska segment with Info and Tracks elements for parsing."""
    timecode_scale = _ebml(b"\x2a\xd7\xb1", struct.pack(">I", 1_000_000))
    duration_el = _ebml(b"\x44\x89", struct.pack(">d", duration * 1000.0))
    info = _ebml(b"\x15\x49\xa9\x66", timecode_scale + duration_el)

    video = _ebml(
        b"\xe0",
        _ebml(b"\xb0", struct.pack(">H", width))
        + _ebml(b"\xba", struct.pack(">H", height)),
    )
    tracks = _ebml(b"\x16\x54\xae\x6b", _ebml(b"\xae", video))
    return _ebml(b"\x18\x53\x80\x67", info + tracks)


def test_png():
    """Verify the probe correctly extracts dimensions and duration from a PNG file."""
    info = probe_media(png_bytes(), filename="x.png")

    assert info.media_type is MediaType.PNG
    assert (info.width, info.height) == (1, 1)
    assert info.frames == 1
    assert info.duration_seconds == 0.0


def test_jpeg_walks_past_app0():
    """
    Ensure the JPEG parser correctly walks past the APP0 segment to find the SOF0
    dimensions.
    """
    info = probe_media(jpeg_with_size(800, 600), filename="x.jpg")

    assert info.media_type is MediaType.JPEG
    assert (info.width, info.height) == (800, 600)


def test_gif():
    """Verify the probe correctly extracts dimensions from a GIF file header."""
    info = probe_media(gif_with_size(320, 240), filename="x.gif")
    assert (info.width, info.height) == (320, 240)


@pytest.mark.parametrize(
    ("builder", "width", "height"),
    [
        (webp_lossy, 640, 480),
        (webp_lossless, 500, 400),
        (webp_extended, 4000, 3000),
    ],
)
def test_webp_variants(builder, width, height):
    """
    Ensure the probe correctly reads dimensions from all three variants of WEBP files.
    """
    info = probe_media(builder(width, height), filename="x.webp")
    assert (info.width, info.height) == (width, height)


def test_mp4_dimensions_and_duration():
    """
    Verify the probe correctly extracts dimensions and calculates the duration of an
    MP4 file.
    """
    info = probe_media(mp4_with(1280, 720, duration=10.4), filename="x.mp4")

    assert (info.width, info.height) == (1280, 720)
    assert info.duration_seconds == pytest.approx(10.4, abs=0.01)
    assert info.frames == 11  # rounded up


def test_mp4_portrait_keeps_orientation():
    """
    Ensure the probe reads dimensions directly without modifying them for rotation
    metadata.
    """
    info = probe_media(mp4_with(1080, 1920, duration=3.0), filename="x.mp4")

    assert (info.width, info.height) == (1080, 1920)
    assert info.long_side == 1920


def test_quicktime_uses_the_same_parser():
    """
    Verify that QuickTime (MOV) files are parsed successfully using the ISO-BMFF parser.
    """
    info = probe_media(mp4_with(640, 480, duration=2.0), filename="x.mov")

    assert info.media_type is MediaType.QUICKTIME
    assert (info.width, info.height) == (640, 480)


def test_mp4_honours_timescale():
    """
    Ensure MP4 duration is correctly calculated by dividing the tick count by the
    timescale.
    """
    info = probe_media(
        mp4_with(640, 480, duration=5.0, timescale=600), filename="x.mp4"
    )
    assert info.duration_seconds == pytest.approx(5.0, abs=0.01)


def test_heic_takes_the_largest_ispe():
    """
    Verify the HEIC parser selects the largest ispe box to avoid reporting a thumbnail.
    """
    info = probe_media(heic_with(6016, 6016), filename="x.heic")

    assert (info.width, info.height) == (6016, 6016)
    assert info.frames == 1  # a still, despite living in a video container


def test_webm_dimensions_and_duration():
    """Verify the probe correctly extracts dimensions and duration from a WebM file."""
    info = probe_media(webm_with(2560, 1440, duration=5.0), filename="x.webm")

    assert (info.width, info.height) == (2560, 1440)
    assert info.duration_seconds == pytest.approx(5.0, abs=0.01)
    assert info.frames == 5


def test_unparseable_header_names_the_escape_hatch():
    """
    Ensure an unparseable header raises an error pointing the user to explicit
    overrides.
    """
    broken = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40  # PNG magic, no IHDR

    with pytest.raises(GuardError) as exc_info:
        probe_media(broken, filename="broken.png")

    message = str(exc_info.value)
    assert "broken.png" in message
    assert "width=" in message and "height=" in message


def test_truncated_header_raises_rather_than_guessing():
    """
    Verify a truncated media file raises an error rather than guessing the dimensions.
    """
    with pytest.raises(GuardError, match="Could not read"):
        probe_media(b"GIF89a", filename="tiny.gif")


def test_unsupported_media_type_is_rejected_first():
    """
    Ensure an unsupported media type is rejected before any header parsing is attempted.
    """
    with pytest.raises(UnsupportedMediaTypeError):
        probe_media(b"%PDF-1.4 nope", filename="doc.pdf")


def test_zero_dimensions_are_rejected():
    """
    Verify that a media header reporting dimensions of zero is treated as an error.
    """
    with pytest.raises(GuardError, match="0x0 frame"):
        probe_media(gif_with_size(0, 0), filename="empty.gif")
