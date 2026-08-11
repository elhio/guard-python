"""Tests for show() and save().

The viewer launcher is always monkeypatched. No test may actually open an application.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from guard_client import (
    ActivityResultItem,
    DetectionResult,
    Engine,
    GuardError,
    MediaType,
    Share,
    UnsupportedMediaTypeError,
    load_media,
    save,
    show,
)
from guard_client import display as display_module

from .conftest import TASK_ID, png_bytes, share_response

MEDIA_URL = "https://s3.test.invalid/app/results/solution.png"


@pytest.fixture
def viewer(monkeypatch):
    """Capture viewer launches instead of actually performing them."""
    opened = []

    def fake_open(data, media_type, filename):
        path = Path(filename)
        opened.append((data, media_type, path))
        return path

    monkeypatch.setattr(display_module, "_open_in_viewer", fake_open)
    return opened


@pytest.fixture
def displayed(monkeypatch):
    """Capture inline renders while simulating a Jupyter kernel environment."""
    rendered = []
    monkeypatch.setattr(display_module, "_in_notebook", lambda: True)
    monkeypatch.setattr(
        display_module,
        "_display_inline",
        lambda data, media_type, width: rendered.append((data, media_type, width)),
    )
    return rendered


def result_item(media_url=MEDIA_URL) -> ActivityResultItem:
    """Create a mock ActivityResultItem for testing purposes."""
    return ActivityResultItem(
        task_id=TASK_ID, score=87, label="Deepfake", media_url=media_url
    )


def test_load_media_from_path(png):
    """Verify load_media correctly loads data from a file path."""
    data, media_type, name = load_media(png)

    assert data == png_bytes()
    assert media_type is MediaType.PNG
    assert name == "sample.png"


def test_load_media_from_bytes():
    """Verify load_media correctly processes raw bytes."""
    data, media_type, _ = load_media(png_bytes(), filename="x.png")
    assert (data, media_type) == (png_bytes(), MediaType.PNG)


def test_load_media_from_file_object(png):
    """Verify load_media correctly reads from an open binary file object."""
    with open(png, "rb") as handle:
        data, media_type, name = load_media(handle)

    assert data == png_bytes()
    assert name == "sample.png"


@respx.mock
def test_load_media_from_url():
    """Verify load_media successfully downloads media from a URL."""
    respx.get(MEDIA_URL).mock(return_value=httpx.Response(200, content=png_bytes()))

    data, media_type, name = load_media(MEDIA_URL)

    assert data == png_bytes()
    assert media_type is MediaType.PNG
    assert name == "solution.png"


@respx.mock
def test_load_media_from_result_item():
    """Verify load_media successfully extracts media from an ActivityResultItem."""
    respx.get(MEDIA_URL).mock(return_value=httpx.Response(200, content=png_bytes()))

    data, _, _ = load_media(result_item())

    assert data == png_bytes()


@respx.mock
def test_load_media_from_share():
    """Verify load_media successfully extracts media from a Share object."""
    share = Share.model_validate(share_response(media_url=MEDIA_URL))
    respx.get(MEDIA_URL).mock(return_value=httpx.Response(200, content=png_bytes()))

    data, _, _ = load_media(share)

    assert data == png_bytes()


@respx.mock
def test_fetch_sends_no_authorization_header():
    """Ensure load_media strips the API key when fetching from external URLs."""
    route = respx.get(MEDIA_URL).mock(
        return_value=httpx.Response(200, content=png_bytes())
    )

    load_media(MEDIA_URL)

    headers = route.calls.last.request.headers
    assert "Authorization" not in headers
    assert "x-guest-id" not in headers


def test_result_without_media_raises():
    """Verify load_media raises an error when passed a result missing media."""
    with pytest.raises(GuardError, match="no media_url"):
        load_media(result_item(media_url=None))


@respx.mock
def test_failed_download_raises():
    """Verify load_media raises an error if the media download fails."""
    respx.get(MEDIA_URL).mock(return_value=httpx.Response(404))

    with pytest.raises(GuardError, match="Could not download"):
        load_media(MEDIA_URL)


@respx.mock
def test_url_with_query_string_keeps_a_usable_name():
    """
    Ensure load_media properly parses a filename from a URL containing a query string.
    """
    url = f"{MEDIA_URL}?signature=abc"
    respx.get(url).mock(return_value=httpx.Response(200, content=png_bytes()))

    _, _, name = load_media(url)

    assert name == "solution.png"


def test_unsupported_type_is_rejected():
    """Verify load_media raises an error when given an unsupported media type."""
    with pytest.raises(UnsupportedMediaTypeError):
        load_media(b"%PDF-1.4 nope", filename="doc.pdf")


def test_show_renders_inline_in_a_notebook(png, displayed, viewer):
    """
    Ensure show() renders inline and avoids launching an external viewer in notebooks.
    """
    show(png, width=400)

    assert len(displayed) == 1
    data, media_type, width = displayed[0]
    assert (data, media_type, width) == (png_bytes(), MediaType.PNG, 400)
    assert not viewer  # never launches an application in a notebook


def test_show_uses_the_viewer_without_ipython(png, monkeypatch, viewer):
    """Ensure show() launches the external viewer outside of a Jupyter notebook."""
    monkeypatch.setattr(display_module, "_in_notebook", lambda: False)

    show(png)

    assert len(viewer) == 1
    assert viewer[0][0] == png_bytes()


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [("x.heic", MediaType.HEIC), ("x.mov", MediaType.QUICKTIME)],
)
def test_heic_and_quicktime_use_the_viewer_even_in_a_notebook(
    displayed, viewer, capsys, filename, media_type
):
    """
    Verify show() uses the external viewer for media types that browsers cannot render.
    """
    show(b"\x00" * 32, media_type=media_type, filename=filename)

    assert not displayed
    assert len(viewer) == 1
    assert "cannot be rendered inline" in capsys.readouterr().out


def test_browser_renderable_set_excludes_heic_and_quicktime():
    """Ensure the BROWSER_RENDERABLE set accurately reflects browser capabilities."""
    from guard_client import BROWSER_RENDERABLE

    assert MediaType.HEIC not in BROWSER_RENDERABLE
    assert MediaType.QUICKTIME not in BROWSER_RENDERABLE
    assert MediaType.PNG in BROWSER_RENDERABLE
    assert MediaType.MP4 in BROWSER_RENDERABLE


def test_open_viewer_false_launches_nothing(png, monkeypatch, viewer):
    """Verify show() respects the open_viewer=False flag by launching nothing."""
    monkeypatch.setattr(display_module, "_in_notebook", lambda: False)

    show(png, open_viewer=False)

    assert not viewer


@respx.mock
def test_show_detection_result_renders_each_item(displayed, viewer):
    """Ensure show() processes every item containing media within a DetectionResult."""
    respx.get(MEDIA_URL).mock(return_value=httpx.Response(200, content=png_bytes()))
    result = DetectionResult(
        engine=Engine.CLOUD,
        results=[
            ActivityResultItem(
                task_id=TASK_ID, score=1, label="A", media_url=MEDIA_URL
            ),
            ActivityResultItem(
                task_id=TASK_ID, score=2, label="B", media_url=MEDIA_URL
            ),
        ],
    )

    show(result)

    assert len(displayed) == 2
    assert not viewer


@respx.mock
def test_show_detection_result_skips_items_without_media(displayed, capsys):
    """Verify show() safely skips results missing a media_url and prints a warning."""
    result = DetectionResult(
        engine=Engine.LOCAL,
        results=[ActivityResultItem(task_id=TASK_ID, score=1, label="A")],
    )

    show(result)

    assert not displayed
    assert "No result in this detection carries an image" in capsys.readouterr().out


def test_not_a_notebook_without_ipython(monkeypatch):
    """Ensure notebook detection correctly returns False when IPython is absent."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "IPython":
            raise ImportError("No module named 'IPython'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert display_module._in_notebook() is False


def test_terminal_ipython_is_not_a_notebook(monkeypatch):
    """Verify that a terminal IPython session does not count as a notebook."""
    import IPython

    class TerminalInteractiveShell:
        pass

    monkeypatch.setattr(IPython, "get_ipython", lambda: TerminalInteractiveShell())

    assert display_module._in_notebook() is False


def test_zmq_shell_is_a_notebook(monkeypatch):
    """Ensure that a ZMQInteractiveShell is correctly identified as a notebook."""
    import IPython

    class ZMQInteractiveShell:
        pass

    monkeypatch.setattr(IPython, "get_ipython", lambda: ZMQInteractiveShell())

    assert display_module._in_notebook() is True


def test_plain_python_is_not_a_notebook():
    """
    Verify that get_ipython returning None correctly flags that we are not in a
    notebook.
    """
    assert display_module._in_notebook() is False


def test_display_inline_builds_a_real_image(monkeypatch):
    """Ensure _display_inline constructs a proper IPython Image object."""
    import IPython.display

    captured = []
    monkeypatch.setattr(IPython.display, "display", captured.append)

    display_module._display_inline(png_bytes(), MediaType.PNG, 300)

    (obj,) = captured
    assert isinstance(obj, IPython.display.Image)
    assert obj.data == png_bytes()
    assert obj.width == 300


def test_display_inline_builds_a_real_video(monkeypatch):
    """Ensure _display_inline constructs a proper IPython Video object."""
    import IPython.display

    captured = []
    monkeypatch.setattr(IPython.display, "display", captured.append)

    display_module._display_inline(b"\x00" * 32, MediaType.MP4, None)

    (obj,) = captured
    assert isinstance(obj, IPython.display.Video)
    # Embedded rather than linked, so a shared notebook keeps working.
    assert obj.embed is True
    assert obj.mimetype == "video/mp4"


def test_save_writes_bytes(tmp_path, png, viewer):
    """
    Verify save() writes media bytes to the specified path without opening a viewer.
    """
    target = save(png, tmp_path / "out.png")

    assert target.read_bytes() == png_bytes()
    assert not viewer  # save never opens anything


def test_save_appends_the_extension(tmp_path, png):
    """Ensure save() automatically appends the correct extension if none is provided."""
    target = save(png, tmp_path / "out")

    assert target.suffix == ".png"
    assert target.exists()


def test_save_into_a_directory(tmp_path, png):
    """Verify save() properly names the file when given a directory path."""
    target = save(png, tmp_path)

    assert target == tmp_path / "sample.png"
    assert target.read_bytes() == png_bytes()


def test_save_creates_missing_parents(tmp_path, png):
    """Ensure save() automatically creates any missing parent directories."""
    target = save(png, tmp_path / "nested" / "deep" / "out.png")
    assert target.exists()


def test_save_refuses_to_overwrite_when_asked(tmp_path, png):
    """Verify save() refuses to overwrite an existing file if overwrite=False."""
    target = tmp_path / "out.png"
    target.write_bytes(b"existing")

    with pytest.raises(GuardError, match="already exists"):
        save(png, target, overwrite=False)

    assert target.read_bytes() == b"existing"


def test_save_overwrites_by_default(tmp_path, png):
    """Ensure save() silently overwrites an existing file by default."""
    target = tmp_path / "out.png"
    target.write_bytes(b"existing")

    save(png, target)

    assert target.read_bytes() == png_bytes()


@respx.mock
def test_save_a_result_item(tmp_path, viewer):
    """Verify save() properly downloads and writes an ActivityResultItem."""
    respx.get(MEDIA_URL).mock(return_value=httpx.Response(200, content=png_bytes()))

    target = save(result_item(), tmp_path / "solution.png")

    assert target.read_bytes() == png_bytes()
    assert not viewer


def test_save_heic_never_opens_a_viewer(tmp_path, viewer):
    """Ensure save() handles HEIC files properly without triggering a viewer launch."""
    target = save(b"\x00" * 32, tmp_path / "x.heic", media_type=MediaType.HEIC)

    assert target.exists()
    assert not viewer
