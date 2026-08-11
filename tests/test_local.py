"""
Tests for the optional local-engine wrapper.

The real engine (`guard-local-detector`) is not required here. Everything is driven
through a fake satisfying the `LocalEngine` protocol, so the suite runs without the
ONNX runtime installed.
"""

from __future__ import annotations

import builtins
import subprocess
import sys

import pytest

from guard_client import (
    Engine,
    GuardClient,
    GuardError,
    GuardLocalEngineError,
    GuardLocalModelError,
    GuardMediaDecodeError,
    LocalEngineNotInstalledError,
    UnsupportedMediaTypeError,
)
from guard_client.local import LocalRunner, _adapt, _score_to_int

from .conftest import png_bytes


def _stand_in(name: str, *bases: type) -> type:
    """
    Forge a guard_local exception class without importing the AGPL engine.

    The mapping matches on the MRO by module and class name, so the stand-ins must claim
    the `guard_local` module to be recognized. Building them here rather than importing
    the real ones allows these tests to run without the `[local]` extra installed.
    """
    klass = type(name, bases or (Exception,), {"__doc__": f"Stand-in for {name}."})
    klass.__module__ = "guard_local.exceptions"
    return klass


StandInGuardLocalError = _stand_in("GuardLocalError")
StandInModelLoadError = _stand_in("ModelLoadError", StandInGuardLocalError)
StandInUnsupportedMediaError = _stand_in(
    "UnsupportedMediaError", StandInGuardLocalError, ValueError
)
StandInMediaDecodeError = _stand_in(
    "MediaDecodeError", StandInGuardLocalError, ValueError
)
StandInUnknownError = _stand_in("SomeFutureError", StandInGuardLocalError)
ImpostorModelLoadError = type("ModelLoadError", (Exception,), {})


class RaisingEngine:
    """An engine whose only behavior is to fail with a chosen exception."""

    def __init__(self, exc: BaseException):
        self.exc = exc

    def analyze(self, data: bytes, media_type: str):
        """Raise the configured exception for a synchronous analysis call."""
        raise self.exc

    async def analyze_async(self, data: bytes, media_type: str):
        """Raise the configured exception for an asynchronous analysis call."""
        raise self.exc


class SyncOnlyRaisingEngine:
    """
    An engine that fails only during synchronous analysis.

    This is used to test failures occurring within a thread offload where
    there is no native async entry point.
    """

    def __init__(self, exc: BaseException):
        self.exc = exc

    def analyze(self, data: bytes, media_type: str):
        """Raise the configured exception for a synchronous analysis call."""
        raise self.exc


class FakeEngine:
    """Minimal stand-in for guard_local.LocalDetectorEngine."""

    def __init__(self, payload=None):
        self.payload = (
            payload if payload is not None else {"status": "safe", "score": 0.87}
        )
        self.calls = []

    def analyze(self, data: bytes, media_type: str):
        """Record the call and return the mocked payload synchronously."""
        self.calls.append((len(data), media_type))
        return self.payload

    async def analyze_async(self, data: bytes, media_type: str):
        """Record the call and return the mocked payload asynchronously."""
        self.calls.append((len(data), media_type))
        return self.payload


class SyncOnlyEngine:
    """An engine that predates the async API to exercise the thread offload."""

    def __init__(self):
        self.calls = 0

    def analyze(self, data: bytes, media_type: str):
        """Record the call and return a fixed analysis payload."""
        self.calls += 1
        return {"label": "violence", "score": 0.4}


def test_analyze_returns_unified_result():
    """
    Verify that a local analysis returns the unified standard `DetectionResult` shape.
    """
    runner = LocalRunner(engine=FakeEngine())

    result = runner.analyze(png_bytes())

    assert result.engine is Engine.LOCAL
    assert result.activity_id is None  # local runs create no activity
    assert len(result.results) == 1
    assert result.results[0].label == "safe"
    assert result.results[0].score == 87


def test_analyze_passes_resolved_media_type():
    """Ensure the resolved MIME type is passed through to the engine."""
    engine = FakeEngine()
    runner = LocalRunner(engine=engine)

    runner.analyze(png_bytes())

    assert engine.calls == [(len(png_bytes()), "image/png")]


def test_engine_is_constructed_once():
    """
    Verify the engine is cached across calls since ONNX session loads are expensive.
    """
    engine = FakeEngine()
    runner = LocalRunner(engine=engine)

    runner.analyze(png_bytes())
    runner.analyze(png_bytes())

    assert runner._engine is engine
    assert len(engine.calls) == 2


async def test_analyze_async_uses_native_async():
    """
    Ensure async analysis utilizes the native async entry point when available.
    """
    runner = LocalRunner(engine=FakeEngine())

    result = await runner.analyze_async(png_bytes())

    assert result.engine is Engine.LOCAL
    assert result.results[0].score == 87


async def test_analyze_async_offloads_sync_engine():
    """
    Verify async analysis safely offloads an older sync-only engine to a separate
    thread.
    """
    engine = SyncOnlyEngine()
    runner = LocalRunner(engine=engine)

    result = await runner.analyze_async(png_bytes())

    assert engine.calls == 1
    assert result.results[0].label == "violence"
    assert result.results[0].score == 40


def test_client_routes_to_local_engine():
    """
    Verify a client configured for the local engine correctly routes analysis calls.
    """
    client = GuardClient(engine="local")
    client._local = LocalRunner(engine=FakeEngine())

    result = client.analyze(png_bytes())

    assert result.engine is Engine.LOCAL
    assert result.activity_id is None


def test_per_call_engine_override_to_local():
    """
    Ensure a cloud-configured client can explicitly request a local analysis without
    network access.
    """
    client = GuardClient(api_key="k", space_id="11111111-1111-1111-1111-111111111111")
    client._local = LocalRunner(engine=FakeEngine())

    result = client.analyze(png_bytes(), engine="local")

    assert result.engine is Engine.LOCAL


async def test_async_client_routes_to_local_engine():
    """
    Verify an async client configured for the local engine routes correctly.
    """
    from guard_client import AsyncGuardClient

    client = AsyncGuardClient(engine="local")
    client._local = LocalRunner(engine=FakeEngine())

    result = await client.analyze(png_bytes())

    assert result.engine is Engine.LOCAL


def test_missing_extra_raises_actionable_error(monkeypatch):
    """
    Ensure that missing the local extra package raises an error with installation
    instructions.
    """
    monkeypatch.setitem(sys.modules, "guard_local", None)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "guard_local":
            raise ImportError("No module named 'guard_local'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    runner = LocalRunner()
    with pytest.raises(LocalEngineNotInstalledError, match=r"guard-client\[local\]"):
        runner.analyze(png_bytes())


def test_broken_install_is_distinguished_from_missing(monkeypatch):
    """
    Ensure an installed but unimportable engine raises a clear error rather than
    reporting it missing.
    """
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "guard_local":
            raise ImportError(
                "cannot import name 'analyze_file' from 'guard_local.engine'"
            )
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    runner = LocalRunner()
    with pytest.raises(LocalEngineNotInstalledError, match="could not be imported"):
        runner.analyze(png_bytes())


def test_missing_transitive_dependency_is_reported(monkeypatch):
    """
    Verify a missing onnxruntime is reported as an incomplete install, not a missing
    extra.
    """
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "guard_local":
            raise ModuleNotFoundError(
                "No module named 'onnxruntime'", name="onnxruntime"
            )
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    runner = LocalRunner()
    with pytest.raises(LocalEngineNotInstalledError, match="'onnxruntime' is not"):
        runner.analyze(png_bytes())


def test_engine_without_factory_reports_upgrade(monkeypatch):
    """
    Ensure using an older guard_local missing the required entry point prompts the user
    to upgrade.
    """
    import types

    stub = types.ModuleType("guard_local")
    monkeypatch.setitem(sys.modules, "guard_local", stub)

    runner = LocalRunner()
    with pytest.raises(
        LocalEngineNotInstalledError, match="does not expose LocalDetectorEngine"
    ):
        runner.analyze(png_bytes())


def test_import_guard_client_does_not_import_guard_local():
    """
    Verify that merely importing the guard_client package never pulls in the AGPL local
    engine.

    Checked in a subprocess, not against this process's sys.modules. With the [local]
    extra installed, test_contract.py imports guard_local at collection time, so an
    in-process assertion would report whatever the rest of the session did rather than
    what importing guard_client does.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, guard_client; "
            "assert 'guard_local' not in sys.modules, sorted(sys.modules)",
        ],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0.0, 0),
        (0.5, 50),
        (1.0, 100),
        (0.874, 87),
        (87, 87),  # already on the 0-100 scale
        (100, 100),
        (250, 100),  # clamped
        (-5, 0),  # clamped
        ("nonsense", 0),
        (None, 0),
    ],
)
def test_score_normalisation(raw, expected):
    """
    Check that varying raw engine score formats correctly normalize to a 0-100 integer
    range.
    """
    assert _score_to_int(raw) == expected


def test_adapt_handles_list_of_entries():
    """Verify the adapter correctly processes a sequence of analysis entries."""
    items = _adapt([{"label": "a", "score": 0.1}, {"label": "b", "score": 0.9}])

    assert [i.label for i in items] == ["a", "b"]
    assert [i.score for i in items] == [10, 90]


def test_adapt_derives_stable_task_ids():
    """
    Ensure derived local task IDs are consistent and reproducible so callers can key off
    them.
    """
    first = _adapt({"label": "deepfake", "score": 0.5})
    second = _adapt({"label": "deepfake", "score": 0.9})

    assert first[0].task_id == second[0].task_id


def test_adapt_honours_supplied_task_id():
    """
    Verify the adapter preserves an explicit task ID if one is supplied by the engine.
    """
    task_id = "44444444-4444-4444-4444-444444444444"
    items = _adapt({"label": "x", "score": 1, "task_id": task_id})

    assert str(items[0].task_id) == task_id


def test_adapt_tolerates_empty_and_junk():
    """Check that the adapter gracefully skips empty, invalid, or malformed entries."""
    assert _adapt(None) == []
    assert _adapt([]) == []
    assert _adapt(["not a dict"]) == []


MAPPINGS = [
    (StandInUnsupportedMediaError, UnsupportedMediaTypeError),
    (StandInMediaDecodeError, GuardMediaDecodeError),
    (StandInModelLoadError, GuardLocalModelError),
    (StandInGuardLocalError, GuardLocalEngineError),
    # anything the engine adds later still lands on the catch-all base
    (StandInUnknownError, GuardLocalEngineError),
]

MAPPING_IDS = [raised.__name__ for raised, _ in MAPPINGS]


@pytest.mark.parametrize(("raised", "expected"), MAPPINGS, ids=MAPPING_IDS)
def test_engine_errors_are_mapped(raised, expected):
    """
    Ensure every internal guard_local failure translates into the correct public
    GuardError.
    """
    runner = LocalRunner(engine=RaisingEngine(raised("boom")))

    with pytest.raises(expected):
        runner.analyze(png_bytes())


@pytest.mark.parametrize(("raised", "expected"), MAPPINGS, ids=MAPPING_IDS)
async def test_engine_errors_are_mapped_async(raised, expected):
    """
    Ensure internal guard_local failures translate correctly during async operations.
    """
    runner = LocalRunner(engine=RaisingEngine(raised("boom")))

    with pytest.raises(expected):
        await runner.analyze_async(png_bytes())


@pytest.mark.parametrize(("raised", "expected"), MAPPINGS, ids=MAPPING_IDS)
async def test_engine_errors_are_mapped_through_the_thread_offload(raised, expected):
    """
    Ensure error translations survive when a sync engine fails inside a thread offload.
    """
    runner = LocalRunner(engine=SyncOnlyRaisingEngine(raised("boom")))

    with pytest.raises(expected):
        await runner.analyze_async(png_bytes())


def test_mapped_errors_are_all_guard_errors():
    """
    Verify that all mapped errors correctly derive from the base GuardError.

    This ensures a single except clause safely catches both cloud and local failures.
    """
    for raised, _ in MAPPINGS:
        runner = LocalRunner(engine=RaisingEngine(raised("boom")))

        with pytest.raises(GuardError):
            runner.analyze(png_bytes())


def test_bad_input_errors_stay_value_errors():
    """
    Verify undecodable or unscoreable media exceptions correctly subclass ValueError.
    """
    for raised in (StandInMediaDecodeError, StandInUnsupportedMediaError):
        runner = LocalRunner(engine=RaisingEngine(raised("boom")))

        with pytest.raises(ValueError):
            runner.analyze(png_bytes())


def test_mapping_preserves_the_original_as_cause():
    """
    Ensure that translated exceptions preserve the original engine error as their
    `__cause__`.
    """
    runner = LocalRunner(engine=RaisingEngine(StandInModelLoadError("model.onnx")))

    with pytest.raises(GuardLocalModelError) as excinfo:
        runner.analyze(png_bytes())

    assert isinstance(excinfo.value.__cause__, StandInModelLoadError)
    assert "model.onnx" in str(excinfo.value)


def test_non_engine_exceptions_propagate_unchanged():
    """
    Ensure generic Python bugs like a RuntimeError pass through untouched without
    wrapping.
    """
    runner = LocalRunner(engine=RaisingEngine(RuntimeError("segfault-ish")))

    with pytest.raises(RuntimeError, match="segfault-ish"):
        runner.analyze(png_bytes())


def test_a_same_named_error_from_elsewhere_is_not_mapped():
    """
    Verify that matching relies on module paths so another library's exception is not
    falsely relabeled.
    """
    runner = LocalRunner(engine=RaisingEngine(ImpostorModelLoadError("not ours")))

    with pytest.raises(ImpostorModelLoadError):
        runner.analyze(png_bytes())


def test_constructor_failures_are_mapped_too(monkeypatch):
    """
    Ensure exception mapping functions properly when the error occurs during engine
    initialization.
    """
    import types

    stub = types.ModuleType("guard_local")

    def factory(*args, **kwargs):
        raise StandInModelLoadError("no model on disk")

    stub.LocalDetectorEngine = factory
    monkeypatch.setitem(sys.modules, "guard_local", stub)

    runner = LocalRunner()
    with pytest.raises(GuardLocalModelError, match="no model on disk"):
        runner.analyze(png_bytes())


MATCH = {
    "id": "c2pa.generative",
    "category": "aiGenerated",
    "label": "Generative tool in manifest",
    "description": "The signed manifest names an AI tool.",
    "confidence": 92,
    "kind": None,
    "evidence": "c2pa.actions: com.adobe.firefly",
    "source": "c2pa",
}


def test_adapt_carries_the_engines_evidence():
    """
    Verify the adapter successfully extracts and forwards rich matching evidence from
    the engine.

    The evidence is the primary reason to run locally. Dropping it would waste the
    engine's value.
    """
    items = _adapt(
        {"label": "AI-Generated", "score": 0.92, "detected": True, "matches": [MATCH]}
    )

    assert items[0].detected is True
    assert len(items[0].matches) == 1
    assert items[0].matches[0].id == "c2pa.generative"
    assert items[0].matches[0].source == "c2pa"
    assert items[0].matches[0].confidence == 92


def test_adapt_leaves_evidence_unset_when_the_engine_reports_none():
    """
    Ensure that older engine outputs without evidence result in `None` rather than
    fabricated structures.
    """
    items = _adapt({"label": "Violence", "score": 0.1})

    assert items[0].detected is None
    assert items[0].matches is None


def test_adapt_distinguishes_no_evidence_from_no_answer():
    """
    Verify an empty list signifies an evaluated negative outcome while `None` means
    omitted entirely.
    """
    items = _adapt(
        {"label": "Explicit", "score": 0.0, "detected": False, "matches": []}
    )

    assert items[0].detected is False
    assert items[0].matches == []


def test_adapt_tolerates_junk_evidence():
    """
    Check that the adapter skips over malformed evidence entries while preserving valid
    ones.
    """
    items = _adapt(
        {
            "label": "AI-Generated",
            "score": 0.5,
            "detected": "yes please",
            "matches": ["not a dict", {"missing": "everything"}, MATCH],
        }
    )

    assert items[0].detected is None  # not a bool, so no verdict is claimed
    assert [m.id for m in items[0].matches] == ["c2pa.generative"]


def test_adapt_ignores_matches_that_are_not_a_list():
    """
    Ensure the adapter safely ignores a `matches` structure if it is not formed as a
    list.
    """
    items = _adapt({"label": "Violence", "score": 0.5, "matches": "nonsense"})

    assert items[0].matches is None


def test_client_surfaces_evidence_end_to_end():
    """
    Verify evidence extracted by the local engine remains accessible end-to-end on the
    final object.
    """
    payload = [
        {"label": "AI-Generated", "score": 0.92, "detected": True, "matches": [MATCH]}
    ]
    client = GuardClient(engine="local")
    client._local = LocalRunner(engine=FakeEngine(payload))

    result = client.analyze(png_bytes())

    assert result.results[0].matches[0].evidence.endswith("firefly")


def test_missing_extra_is_still_a_local_engine_error():
    """
    Ensure an uninstalled engine correctly subclasses the full exception hierarchy.

    Reparenting must not accidentally narrow the scope of what the original except
    clauses caught.
    """
    assert issubclass(LocalEngineNotInstalledError, GuardLocalEngineError)
    assert issubclass(LocalEngineNotInstalledError, ImportError)
    assert issubclass(LocalEngineNotInstalledError, GuardError)
