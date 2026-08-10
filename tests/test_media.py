"""
Tests for media resolution: bytes, paths, file objects and MIME detection.
"""

from __future__ import annotations

import io

import pytest

from guard_client import MediaType, UnsupportedMediaTypeError
from guard_client.media import SUPPORTED_MEDIA_TYPES, resolve_media

from .conftest import jpeg_bytes, png_bytes


def test_resolves_path(png):
    """Verify that media is correctly resolved from a pathlib.Path object."""
    data, media_type, name = resolve_media(png)

    assert data == png_bytes()
    assert media_type is MediaType.PNG
    assert name == "sample.png"


def test_resolves_path_as_string(png):
    """Verify that media is correctly resolved from a string file path."""
    _, media_type, _ = resolve_media(str(png))
    assert media_type is MediaType.PNG


def test_resolves_raw_bytes_by_sniffing():
    """Ensure detection falls back to magic bytes when no filename is provided."""
    _, media_type, name = resolve_media(png_bytes())

    assert media_type is MediaType.PNG
    assert name.endswith(".png")


def test_resolves_file_object(png):
    """Verify that an open binary file object is correctly read and resolved."""
    with open(png, "rb") as handle:
        data, media_type, name = resolve_media(handle)

    assert data == png_bytes()
    assert media_type is MediaType.PNG
    assert name == "sample.png"


def test_resolves_bytesio_with_explicit_filename():
    """
    Verify that a BytesIO stream is resolved using the explicitly provided filename.
    """
    stream = io.BytesIO(jpeg_bytes())
    _, media_type, name = resolve_media(stream, filename="photo.jpg")

    assert media_type is MediaType.JPEG
    assert name == "photo.jpg"


def test_explicit_media_type_skips_detection():
    """
    Ensure an explicit type overrides detection even if the bytes suggest otherwise.
    """
    _, media_type, _ = resolve_media(png_bytes(), media_type="image/webp")
    assert media_type is MediaType.WEBP


def test_explicit_media_type_accepts_enum():
    """Ensure that an explicit MediaType enum is correctly accepted and applied."""
    _, media_type, _ = resolve_media(png_bytes(), media_type=MediaType.JPEG)
    assert media_type is MediaType.JPEG


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("clip.mp4", MediaType.MP4),
        ("clip.webm", MediaType.WEBM),
        ("clip.mov", MediaType.QUICKTIME),
        ("photo.heic", MediaType.HEIC),
        ("photo.webp", MediaType.WEBP),
        ("photo.gif", MediaType.GIF),
    ],
)
def test_extension_detection(filename, expected):
    """Verify that media types are correctly inferred from standard file extensions."""
    _, media_type, _ = resolve_media(b"\x00" * 32, filename=filename)
    assert media_type is expected


def test_sniffs_webp_riff_container():
    """
    Ensure that WEBP files are correctly identified via their RIFF container magic
    bytes.
    """
    data = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16
    _, media_type, _ = resolve_media(data)
    assert media_type is MediaType.WEBP


def test_sniffs_mp4_ftyp_box():
    """Ensure that MP4 files are correctly identified via their ftyp box."""
    data = b"\x00\x00\x00\x18" + b"ftyp" + b"isom" + b"\x00" * 16
    _, media_type, _ = resolve_media(data)
    assert media_type is MediaType.MP4


def test_sniffs_heic_by_brand():
    """Ensure that HEIC files are correctly identified via their specific ftyp brand."""
    data = b"\x00\x00\x00\x18" + b"ftyp" + b"heic" + b"\x00" * 16
    _, media_type, _ = resolve_media(data)
    assert media_type is MediaType.HEIC


def test_rejects_unsupported_type():
    """Verify that an unsupported file extension raises an appropriate error."""
    with pytest.raises(UnsupportedMediaTypeError, match="Unsupported media type"):
        resolve_media(b"%PDF-1.4 fake", filename="doc.pdf")


def test_rejects_undetectable_bytes():
    """Verify that unrecognizable raw bytes raise an appropriate error."""
    with pytest.raises(UnsupportedMediaTypeError, match="Could not determine"):
        resolve_media(b"\x01\x02\x03\x04 not a known format")


def test_rejects_empty_input():
    """Verify that empty byte inputs are cleanly rejected."""
    with pytest.raises(UnsupportedMediaTypeError, match="empty"):
        resolve_media(b"")


def test_rejects_text_mode_file(tmp_path):
    """Ensure that files opened in text mode are rejected with a clear error message."""
    path = tmp_path / "note.png"
    path.write_text("not binary")
    # text mode is the mistake under test
    with (
        open(path) as handle,
        pytest.raises(UnsupportedMediaTypeError, match="binary mode"),
    ):
        resolve_media(handle)


def test_missing_file_raises_filenotfound(tmp_path):
    """
    Verify that providing a non-existent file path naturally raises a FileNotFoundError.
    """
    with pytest.raises(FileNotFoundError):
        resolve_media(tmp_path / "absent.png")


def test_supported_types_match_enum():
    """
    Ensure the supported types frozenset stays perfectly synchronized with the MediaType
    enum.
    """
    assert {m.value for m in MediaType} == SUPPORTED_MEDIA_TYPES
