"""
The contract that the guard-python package depends on.
"""

import asyncio
import inspect
import struct
import zlib
from typing import Any, Dict, List, Union

import pytest

import guard_local

SUPPORTED = [
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "image/heic", "video/mp4", "video/webm", "video/quicktime",
]


def _png() -> bytes:
    """Generate a real, decodable 1x1 PNG so the test requires no external fixture files."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


PNG = _png()


def test_factory_is_exported_from_the_package_root() -> None:
    """Ensure the local engine can be imported directly from the package root."""
    assert hasattr(guard_local, "LocalDetectorEngine")


def test_constructs_with_no_arguments() -> None:
    """Ensure a bare constructor call works since the model path environment variable is usually unset."""
    guard_local.LocalDetectorEngine()


def test_model_path_is_accepted_positionally(tmp_path: Any) -> None:
    """Verify that the constructor accepts the model path as a positional argument."""
    sig = inspect.signature(guard_local.LocalDetectorEngine.__init__)
    first = list(sig.parameters.values())[1]
    assert first.default is not inspect.Parameter.empty
    assert first.kind in (first.POSITIONAL_ONLY, first.POSITIONAL_OR_KEYWORD)


def test_analyze_takes_bytes_and_a_media_type() -> None:
    """
    Verify that analysis takes raw bytes and a media type.

    It must not require a path or filename because the client has already read the
    source media into memory by the time this is called.
    """
    raw = guard_local.LocalDetectorEngine().analyze(PNG, "image/png")

    entries = raw if isinstance(raw, list) else [raw]
    assert entries and all(isinstance(e, dict) for e in entries)


def test_every_entry_has_a_label_and_a_unit_interval_score() -> None:
    """Verify that scores are returned as floats between 0.0 and 1.0.

    This is critical because the Guard client rescales by value. Returning an integer 1
    would be incorrectly rescaled to 100.
    """
    raw = guard_local.LocalDetectorEngine().analyze(PNG, "image/png")

    for entry in raw if isinstance(raw, list) else [raw]:
        assert isinstance(entry.get("label"), str) and entry["label"]
        assert 0.0 <= float(entry["score"]) <= 1.0


def test_labels_are_stable_across_calls() -> None:
    """
    Ensure labels remain entirely stable across multiple calls.

    Local task IDs are generated using a UUID5 hash of the label. Because
    of this, silently changing a label would inadvertently change the ID.
    """
    def labels(raw: Union[Dict[str, Any], List[Dict[str, Any]]]) -> List[str]:
        return sorted(e["label"] for e in (raw if isinstance(raw, list) else [raw]))

    engine = guard_local.LocalDetectorEngine()

    assert labels(engine.analyze(PNG, "image/png")) == labels(engine.analyze(PNG, "image/png"))


@pytest.mark.parametrize("media_type", SUPPORTED)
def test_every_supported_media_type_is_handled_or_cleanly_rejected(media_type: str) -> None:
    """
    Verify that the engine handles or cleanly rejects every supported media type.

    The client forwards all eight media types to the engine, and the engine must not
    fail opaquely on any of them.
    """
    engine = guard_local.LocalDetectorEngine()
    try:
        engine.analyze(PNG, media_type)
    except guard_local.GuardLocalError:
        pass  # an explicit, documented rejection is fine


def test_async_entry_point_if_present_is_a_coroutine() -> None:
    """
    Verify that the async entry point is a coroutine if it is present.

    This method is optional. If it is absent, the client safely falls back to offloading
    the synchronous work to a background thread.
    """
    engine = guard_local.LocalDetectorEngine()
    analyze_async = getattr(engine, "analyze_async", None)
    if analyze_async is None:
        pytest.skip("sync-only engine; client offloads to a thread")

    raw = asyncio.run(analyze_async(PNG, "image/png"))

    assert isinstance(raw, (dict, list))
