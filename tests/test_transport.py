"""
Tests for auth, locale, error mapping and retry behaviour.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from guard_client import (
    GuardAPIError,
    GuardAuthError,
    GuardConflictError,
    GuardConnectionError,
    GuardNotFoundError,
    GuardPaymentRequiredError,
    GuardRateLimitError,
    GuardServerError,
    GuardValidationError,
)
from guard_client.transport import DEFAULT_BASE_URL, SyncTransport, TransportConfig

from .conftest import API_KEY, BASE_URL

PATH = "/api/v1/ping"
URL = f"{BASE_URL}{PATH}"


@pytest.fixture
def transport():
    """Provide a SyncTransport instance preconfigured for unit testing."""
    config = TransportConfig(api_key=API_KEY, base_url=BASE_URL, max_retries=0)
    with SyncTransport(config) as t:
        yield t


@respx.mock
def test_sends_bearer_token(transport):
    """
    Verify that every request automatically carries the Authorization Bearer header.
    """
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    assert transport.request("GET", PATH) == {"ok": True}
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {API_KEY}"


@respx.mock
def test_appends_lang_param(transport):
    """
    Verify that requests append the default lang query parameter for localized
    responses.
    """
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))

    transport.request("GET", PATH)
    assert route.calls.last.request.url.params["lang"] == "en"


@respx.mock
def test_locale_is_configurable():
    """
    Verify that setting a custom locale correctly updates the lang query parameter.
    """
    config = TransportConfig(
        api_key=API_KEY, base_url=BASE_URL, locale="de", max_retries=0
    )
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))

    with SyncTransport(config) as t:
        t.request("GET", PATH)

    assert route.calls.last.request.url.params["lang"] == "de"


@respx.mock
def test_drops_none_params(transport):
    """
    Verify that query parameters with None values are automatically stripped from the
    request.
    """
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))

    transport.request("GET", PATH, params={"skip": 0, "space_id": None})

    params = route.calls.last.request.url.params
    assert "space_id" not in params
    assert params["skip"] == "0"


@respx.mock
def test_returns_none_for_204(transport):
    """Verify that receiving a 204 No Content response returns None."""
    respx.get(URL).mock(return_value=httpx.Response(204))
    assert transport.request("GET", PATH) is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, GuardAPIError),
        (401, GuardAuthError),
        (402, GuardPaymentRequiredError),
        (403, GuardAuthError),
        (404, GuardNotFoundError),
        (409, GuardConflictError),
        (422, GuardValidationError),
        (429, GuardRateLimitError),
        (500, GuardServerError),
        (503, GuardServerError),
    ],
)
@respx.mock
def test_maps_status_to_exception(transport, status, expected):
    """
    Verify that HTTP error status codes are correctly mapped to specific Guard
    exceptions.
    """
    respx.get(URL).mock(return_value=httpx.Response(status, json={"detail": "boom"}))

    with pytest.raises(expected) as exc_info:
        transport.request("GET", PATH)

    assert exc_info.value.status_code == status
    assert "boom" in str(exc_info.value)


@respx.mock
def test_prefers_detail_message(transport):
    """Verify that error messages prioritize the detail field from the JSON response."""
    respx.get(URL).mock(
        return_value=httpx.Response(400, json={"detail": "File exceeds the limit"})
    )

    with pytest.raises(GuardAPIError, match="File exceeds the limit"):
        transport.request("GET", PATH)


@respx.mock
def test_parses_422_detail_list(transport):
    """
    Verify that 422 error details structured as lists are formatted into readable error
    messages.
    """
    respx.get(URL).mock(
        return_value=httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "loc": ["body", "media_size"],
                        "msg": "field required",
                        "type": "missing",
                    }
                ]
            },
        )
    )

    with pytest.raises(GuardValidationError) as exc_info:
        transport.request("GET", PATH)

    assert "media_size: field required" in str(exc_info.value)
    assert exc_info.value.errors[0]["type"] == "missing"


@respx.mock
def test_falls_back_on_non_json_body(transport):
    """
    Verify that non-JSON error response bodies fall back gracefully to status line
    messages.
    """
    respx.get(URL).mock(return_value=httpx.Response(500, text="<html>gateway</html>"))

    with pytest.raises(GuardServerError, match="API Error: 500"):
        transport.request("GET", PATH)


@respx.mock
def test_captures_request_id(transport):
    """
    Verify that x-request-id headers on error responses are captured in the exception.
    """
    respx.get(URL).mock(
        return_value=httpx.Response(
            500, json={"detail": "x"}, headers={"x-request-id": "req-9"}
        )
    )

    with pytest.raises(GuardServerError) as exc_info:
        transport.request("GET", PATH)

    assert exc_info.value.request_id == "req-9"


@respx.mock
def test_rate_limit_exposes_retry_after(transport):
    """
    Verify that rate limit exceptions parse and expose the retry-after header value.
    """
    respx.get(URL).mock(
        return_value=httpx.Response(
            429, json={"detail": "slow down"}, headers={"retry-after": "7"}
        )
    )

    with pytest.raises(GuardRateLimitError) as exc_info:
        transport.request("GET", PATH)

    assert exc_info.value.retry_after == 7.0


@respx.mock
def test_wraps_connection_failure(transport):
    """
    Verify that underlying HTTP connection errors are wrapped in GuardConnectionError.
    """
    respx.get(URL).mock(side_effect=httpx.ConnectError("no route"))

    with pytest.raises(GuardConnectionError, match="no route"):
        transport.request("GET", PATH)


@respx.mock
def test_retries_server_errors_then_succeeds(monkeypatch):
    """
    Verify that transient server errors trigger retries up to the configured limit
    before succeeding.
    """
    monkeypatch.setattr("guard_client.transport.time.sleep", lambda _: None)
    config = TransportConfig(api_key=API_KEY, base_url=BASE_URL, max_retries=3)
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(503, json={"detail": "unavailable"}),
            httpx.Response(503, json={"detail": "unavailable"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    with SyncTransport(config) as t:
        assert t.request("GET", PATH) == {"ok": True}

    assert route.call_count == 3


@respx.mock
def test_gives_up_after_max_retries(monkeypatch):
    """Verify that retries halt and raise an exception once max_retries is reached."""
    monkeypatch.setattr("guard_client.transport.time.sleep", lambda _: None)
    config = TransportConfig(api_key=API_KEY, base_url=BASE_URL, max_retries=2)
    route = respx.get(URL).mock(
        return_value=httpx.Response(503, json={"detail": "down"})
    )

    with SyncTransport(config) as t, pytest.raises(GuardServerError):
        t.request("GET", PATH)

    assert route.call_count == 3  # initial attempt + 2 retries


@respx.mock
def test_does_not_retry_post_by_default(monkeypatch):
    """
    Verify that POST requests are not retried by default to prevent non-idempotent
    duplicate operations.
    """
    monkeypatch.setattr("guard_client.transport.time.sleep", lambda _: None)
    config = TransportConfig(api_key=API_KEY, base_url=BASE_URL, max_retries=3)
    route = respx.post(URL).mock(
        return_value=httpx.Response(503, json={"detail": "down"})
    )

    with SyncTransport(config) as t, pytest.raises(GuardServerError):
        t.request("POST", PATH)

    assert route.call_count == 1


@respx.mock
def test_retries_post_when_opted_in(monkeypatch):
    """Verify that POST requests are retried when retry=True is explicitly passed."""
    monkeypatch.setattr("guard_client.transport.time.sleep", lambda _: None)
    config = TransportConfig(api_key=API_KEY, base_url=BASE_URL, max_retries=2)
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(503, json={"detail": "down"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    with SyncTransport(config) as t:
        assert t.request("POST", PATH, retry=True) == {"ok": True}

    assert route.call_count == 2


@respx.mock
def test_retry_false_suppresses_retries_on_idempotent_method(monkeypatch):
    """
    Verify that passing retry=False disables retries even for naturally idempotent
    HTTP methods.
    """
    monkeypatch.setattr("guard_client.transport.time.sleep", lambda _: None)
    config = TransportConfig(api_key=API_KEY, base_url=BASE_URL, max_retries=3)
    route = respx.delete(URL).mock(
        return_value=httpx.Response(503, json={"detail": "down"})
    )

    with SyncTransport(config) as t, pytest.raises(GuardServerError):
        t.request("DELETE", PATH, retry=False)

    assert route.call_count == 1


@respx.mock
def test_retry_none_keeps_method_default(monkeypatch):
    """
    Verify that passing retry=None preserves default method-based idempotency retry
    rules.
    """
    monkeypatch.setattr("guard_client.transport.time.sleep", lambda _: None)
    config = TransportConfig(api_key=API_KEY, base_url=BASE_URL, max_retries=2)
    route = respx.delete(URL).mock(
        return_value=httpx.Response(503, json={"detail": "down"})
    )

    with SyncTransport(config) as t, pytest.raises(GuardServerError):
        t.request("DELETE", PATH, retry=None)

    assert route.call_count == 3  # DELETE is idempotent by default


@respx.mock
def test_retry_false_suppresses_connection_retries(monkeypatch):
    """Verify that passing retry=False suppresses retries on connection failures."""
    monkeypatch.setattr("guard_client.transport.time.sleep", lambda _: None)
    config = TransportConfig(api_key=API_KEY, base_url=BASE_URL, max_retries=3)
    route = respx.delete(URL).mock(side_effect=httpx.ConnectError("boom"))

    with SyncTransport(config) as t, pytest.raises(GuardConnectionError):
        t.request("DELETE", PATH, retry=False)

    assert route.call_count == 1


@respx.mock
def test_does_not_retry_client_errors(monkeypatch):
    """Verify that 4xx client errors are never retried regardless of retry settings."""
    monkeypatch.setattr("guard_client.transport.time.sleep", lambda _: None)
    config = TransportConfig(api_key=API_KEY, base_url=BASE_URL, max_retries=3)
    route = respx.get(URL).mock(
        return_value=httpx.Response(404, json={"detail": "nope"})
    )

    with SyncTransport(config) as t, pytest.raises(GuardNotFoundError):
        t.request("GET", PATH)

    assert route.call_count == 1


def test_config_ignores_environment(monkeypatch):
    """
    Verify that TransportConfig operates as a pure data container without reading
    os.environ.
    """
    monkeypatch.setenv("GUARD_API_KEY", "from-env")
    monkeypatch.setenv("GUARD_BASE_URL", "http://localhost:8000")

    config = TransportConfig()

    assert config.api_key is None
    assert config.base_url == DEFAULT_BASE_URL


def test_defaults_to_prod():
    """Verify that TransportConfig defaults to the production base URL."""
    config = TransportConfig(api_key="k")
    assert config.base_url == DEFAULT_BASE_URL == "https://api.elhio.com"


def test_strips_trailing_slash():
    """
    Verify that TransportConfig strips trailing slashes from base_url during
    initialization.
    """
    assert TransportConfig(api_key="k", base_url="https://x.invalid/").base_url == (
        "https://x.invalid"
    )


@respx.mock
def test_upload_omits_auth_and_lang(transport):
    """
    Verify that presigned storage uploads omit Authorization headers and lang query
    parameters.
    """
    route = respx.post("https://s3.test.invalid/bucket").mock(
        return_value=httpx.Response(204)
    )

    transport.upload(
        "https://s3.test.invalid/bucket", {"key": "uploads/a"}, "a.png", b"\x89PNG"
    )

    request = route.calls.last.request
    assert "Authorization" not in request.headers
    assert "lang" not in request.url.params


@respx.mock
def test_upload_raises_on_rejection(transport):
    """Verify that non-2xx storage responses during uploads raise a GuardUploadError."""
    from guard_client import GuardUploadError

    respx.post("https://s3.test.invalid/bucket").mock(
        return_value=httpx.Response(403, text="AccessDenied")
    )

    with pytest.raises(GuardUploadError) as exc_info:
        transport.upload("https://s3.test.invalid/bucket", {}, "a.png", b"x")

    assert exc_info.value.status_code == 403
