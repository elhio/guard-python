"""
Shared fixtures and payload builders for the test suite.
"""

from __future__ import annotations

import importlib.util
import os
import struct
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import pytest

# `test_contract.py` is shared byte-identically with ../guard-local-python and does a
# bare `import guard_local` at module scope, so without the [local] extra it fails at
# collection rather than skipping. The guard has to live out here: editing the file to
# add one would defeat the point of keeping the two copies identical.
collect_ignore = []
if importlib.util.find_spec("guard_local") is None:
    collect_ignore.append("test_contract.py")

BASE_URL = "https://api.test.invalid"
API_KEY = "test-key"
SPACE_ID = UUID("11111111-1111-1111-1111-111111111111")
ACTIVITY_ID = UUID("22222222-2222-2222-2222-222222222222")
TASK_ID = UUID("33333333-3333-3333-3333-333333333333")
ORG_ID = UUID("44444444-4444-4444-4444-444444444444")
PREDICTOR_ID = UUID("55555555-5555-5555-5555-555555555555")
USER_ID = UUID("77777777-7777-7777-7777-777777777777")
RUNNER_ID = UUID("88888888-8888-8888-8888-888888888888")
REACTION_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SHARE_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
UPLOAD_URL = "https://s3.test.invalid/bucket"


def png_bytes() -> bytes:
    """A real 1x1 PNG, so magic-byte sniffing has something valid to work with."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def jpeg_bytes() -> bytes:
    """Generate basic JPEG magic bytes for testing."""
    return b"\xff\xd8\xff\xe0" + b"\x00" * 16


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def create_response(**overrides: Any) -> Dict[str, Any]:
    """Build a mock response for an activity creation request."""
    payload: Dict[str, Any] = {
        "id": str(ACTIVITY_ID),
        "status": "pending_upload",
        "created_at": _now(),
        "space_id": str(SPACE_ID),
        "user_id": None,
        "account_id": None,
        "guest_id": None,
        "space_name": "Test Space",
        "user_name": None,
        "account_name": None,
        "media_type": "image/png",
        "upload_data": {
            "url": UPLOAD_URL,
            "fields": {
                "key": "uploads/abc",
                "policy": "xyz",
                "Content-Type": "image/png",
            },
        },
    }
    payload.update(overrides)
    return payload


def status_response(status: str = "completed", **overrides: Any) -> Dict[str, Any]:
    """Build a mock status response for an activity."""
    payload: Dict[str, Any] = {
        "id": str(ACTIVITY_ID),
        "status": status,
        "user_id": None,
        "account_id": None,
        "guest_id": None,
    }
    payload.update(overrides)
    return payload


def detail_response(
    status: str = "completed",
    results: Optional[List[Dict[str, Any]]] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    """Build a mock detailed response for an activity."""
    if results is None:
        results = [
            {
                "task_id": str(TASK_ID),
                "score": 87,
                "label": "Deepfake",
                "description": "Likely synthetic",
                "media_url": None,
            }
        ]
    payload: Dict[str, Any] = {
        "id": str(ACTIVITY_ID),
        "status": status,
        "created_at": _now(),
        "updated_at": _now(),
        "space_id": str(SPACE_ID),
        "predictor_id": str(uuid4()),
        "runner_id": None,
        "user_id": None,
        "account_id": None,
        "guest_id": None,
        "media_type": "image/png",
        "media_size": 1024,
        "payed_tokens": 1,
        "result_payload": {"results": results},
        "space_name": "Test Space",
        "predictor_name": "default",
        "runner_name": None,
        "user_name": None,
        "account_name": None,
    }
    payload.update(overrides)
    return payload


def space_response(**overrides: Any) -> Dict[str, Any]:
    """Build a mock response for a space."""
    payload: Dict[str, Any] = {
        "id": str(SPACE_ID),
        "status": "active",
        "created_at": _now(),
        "name": "Test Space",
        "description": "A space for testing",
        "slug": "test-space",
        "url_id": "abc123",
        "is_default": True,
        "is_public": False,
        "user_id": None,
        "user_name": None,
        "organization_id": str(ORG_ID),
        "organization_name": "Test Org",
        "predictor_id": str(PREDICTOR_ID),
        "predictor_name": "default",
        "enabled_media": ["image", "video"],
        "enabled_task_names": ["Deepfake", "Violence"],
    }
    payload.update(overrides)
    return payload


def spaces_page_response(
    count: Optional[int] = None, items: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """A SpacesPublic envelope. Defaults to a single space with count=1."""
    data = [space_response()] if items is None else items
    return {"data": data, "count": len(data) if count is None else count}


def predictor_response(**overrides: Any) -> Dict[str, Any]:
    """Build a mock response for a predictor."""
    payload: Dict[str, Any] = {
        "id": str(PREDICTOR_ID),
        "name": "Default Predictor",
        "status": "active",
        "description": "The standard detection model",
        "token_multiplier": 1,
        "slug": "default-predictor",
        "url_id": "pred123",
        "supported_media": ["image", "video"],
        "supported_task_ids": [str(TASK_ID)],
    }
    payload.update(overrides)
    return payload


def task_response(**overrides: Any) -> Dict[str, Any]:
    """Build a mock response for a task."""
    payload: Dict[str, Any] = {
        "id": str(TASK_ID),
        "status": "active",
        "name": "Deepfake",
        "description": "Detects synthetic media",
        "reactions": {"1": "Real photo", "2": "AI generated"},
    }
    payload.update(overrides)
    return payload


def reaction_response(**overrides: Any) -> Dict[str, Any]:
    """Build a mock response for a reaction."""
    payload: Dict[str, Any] = {
        "id": str(REACTION_ID),
        "created_at": _now(),
        "activity_id": str(ACTIVITY_ID),
        "task_id": str(TASK_ID),
        "is_positive": True,
        "key_value": None,
        "description": None,
    }
    payload.update(overrides)
    return payload


def runner_response(**overrides: Any) -> Dict[str, Any]:
    """Build a mock response for a runner."""
    payload: Dict[str, Any] = {
        "id": str(RUNNER_ID),
        "status": "running",
        "created_at": _now(),
        "terminated_at": None,
        "name": "runner-1",
        "slug": "runner-1",
        "url_id": "run123",
        "predictor_id": str(PREDICTOR_ID),
        "predictor_name": "Default Predictor",
        "organization_id": str(ORG_ID),
        "organization_name": "Test Org",
    }
    payload.update(overrides)
    return payload


def share_response(**overrides: Any) -> Dict[str, Any]:
    """Build a mock response for a share."""
    payload: Dict[str, Any] = {
        "id": str(SHARE_ID),
        "created_at": _now(),
        "expired_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "activity_id": str(ACTIVITY_ID),
        "task_id": str(TASK_ID),
        "expires_in": 7,
        "share_url": "https://elhio.com/s/abc123",
        "media_url": "https://cdn.elhio.com/media/abc.jpg",
        "task_name": "Deepfake",
        "space_name": "Test Space",
        "result": {
            "task_id": str(TASK_ID),
            "score": 87,
            "label": "Deepfake",
            "description": "Likely synthetic",
            "media_url": None,
        },
    }
    payload.update(overrides)
    return payload


def space_detail_response(**overrides: Any) -> Dict[str, Any]:
    """Build a mock detailed response for a space."""
    payload: Dict[str, Any] = {
        "id": str(SPACE_ID),
        "status": "active",
        "created_at": _now(),
        "name": "Test Space",
        "description": "A space for testing",
        "slug": "test-space",
        "url_id": "abc123",
        "is_default": True,
        "is_public": False,
        "user_id": None,
        "user_name": None,
        "organization_id": str(ORG_ID),
        "organization_name": "Test Org",
        "predictor_id": str(PREDICTOR_ID),
        "predictor_name": "default",
        "predictor_multiplier": 3,
        "max_media_size": 52428800,
        "enabled_media": ["image", "video"],
        "enabled_tasks": [task_response()],
        "task_thresholds": [
            {"task_id": str(TASK_ID), "blur_threshold": 50, "hide_threshold": 80}
        ],
    }
    payload.update(overrides)
    return payload


def page_response(
    items: List[Dict[str, Any]], count: Optional[int] = None
) -> Dict[str, Any]:
    """The {data, count} envelope every list endpoint returns."""
    return {"data": items, "count": len(items) if count is None else count}


def confirm_response(**overrides: Any) -> Dict[str, Any]:
    """Build a mock response for confirming an activity."""
    payload = create_response()
    payload.pop("upload_data")
    payload["status"] = "processing"
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def isolate_env(tmp_path_factory, monkeypatch):
    """
    Insulate every test from the developer's real environment.

    Two hazards this closes:

    * exported ``GUARD_*`` variables in the shell running pytest, and
    * a real ``.env`` in the repo — ``find_dotenv`` walks *up* from the cwd, so once a
      developer creates one for the smoke script it would otherwise leak into the suite.

    Tests that want a ``.env`` write one into the cwd this provides.
    """
    for key in [k for k in os.environ if k.startswith("GUARD_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))


@pytest.fixture
def png(tmp_path):
    """A PNG file on disk, returned as a Path."""
    path = tmp_path / "sample.png"
    path.write_bytes(png_bytes())
    return path


@pytest.fixture
def client(isolate_env):
    """A sync client pointed at the mock base URL, with retries off for speed."""
    from guard_client import GuardClient

    with GuardClient(
        api_key=API_KEY, space_id=SPACE_ID, base_url=BASE_URL, max_retries=0
    ) as c:
        yield c


@pytest.fixture
async def async_client(isolate_env):
    """An async client pointed at the mock base URL."""
    from guard_client import AsyncGuardClient

    async with AsyncGuardClient(
        api_key=API_KEY, space_id=SPACE_ID, base_url=BASE_URL, max_retries=0
    ) as c:
        yield c
