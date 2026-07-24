"""Behavior tests for the httpx fetch boundary.

Network-free: every client is built over an ``httpx.MockTransport`` so the
success / redirect / status-error / transport-error paths are exercised without
touching the network.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from feed_filter.fetch import FetchError, build_client, fetch


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> Iterator[httpx.Client]:
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        yield client


def test_success_returns_body_and_metadata() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello", headers={"content-type": "text/html"})

    for client in _client(handler):
        result = fetch("https://example.com/", client=client)

    assert result.text == "hello"
    assert result.content == b"hello"
    assert result.status == 200
    assert "text/html" in result.content_type
    assert result.final_url == "https://example.com/"


def test_redirect_reports_final_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://example.com/new"})
        return httpx.Response(200, text="moved")

    for client in _client(handler):
        result = fetch("https://example.com/old", client=client)

    assert result.final_url == "https://example.com/new"
    assert result.status == 200


def test_404_raises_with_status() -> None:
    waits: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    for client in _client(handler):
        with pytest.raises(FetchError) as excinfo:
            fetch("https://example.com/missing", client=client, sleep=waits.append)

    assert excinfo.value.status == 404
    # A non-retryable status must raise on the first attempt without sleeping.
    assert waits == []


def test_429_then_success_retries() -> None:
    waits: list[float] = []
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "2"}, text="slow down")
        return httpx.Response(200, text="ok")

    for client in _client(handler):
        result = fetch("https://example.com/", client=client, sleep=waits.append)

    assert result.text == "ok"
    assert calls["n"] == 2
    # Honored the integer Retry-After header for the single back-off.
    assert waits == [2.0]


def test_429_exhausts_retries_then_raises() -> None:
    waits: list[float] = []
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, text="slow down")  # no Retry-After header

    for client in _client(handler):
        with pytest.raises(FetchError) as excinfo:
            fetch("https://example.com/", client=client, max_retries=3, sleep=waits.append)

    assert excinfo.value.status == 429
    # 1 initial + 3 retries = 4 GETs, with a back-off before each of the 3 retries.
    assert calls["n"] == 4
    # No Retry-After → exponential back-off by attempt index (1, 2, 4).
    assert waits == [1.0, 2.0, 4.0]


def test_negative_max_retries_still_makes_one_attempt() -> None:
    # max_retries < 0 means "no retries", not "no attempt": the loop is clamped to
    # one attempt, so a throttled status raises after a single GET with no back-off
    # (and never falls through to the unreachable terminal assertion).
    waits: list[float] = []
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "1"}, text="slow")

    for client in _client(handler):
        with pytest.raises(FetchError) as excinfo:
            fetch("https://example.com/", client=client, max_retries=-1, sleep=waits.append)

    assert excinfo.value.status == 429
    assert calls["n"] == 1  # exactly one attempt, no retries
    assert waits == []  # no back-off


def test_503_is_retried() -> None:
    waits: list[float] = []
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, headers={"retry-after": "1"}, text="unavailable")
        return httpx.Response(200, text="back")

    for client in _client(handler):
        result = fetch("https://example.com/", client=client, sleep=waits.append)

    assert result.text == "back"
    assert calls["n"] == 2
    assert waits == [1.0]


def test_retry_after_is_clamped_to_max() -> None:
    waits: list[float] = []
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            # A hostile/mis-set header must not stall the run beyond the cap.
            return httpx.Response(429, headers={"retry-after": "86400"}, text="slow")
        return httpx.Response(200, text="ok")

    for client in _client(handler):
        result = fetch("https://example.com/", client=client, sleep=waits.append)

    assert result.text == "ok"
    assert waits == [30.0]  # MAX_RETRY_WAIT


def test_max_retries_zero_disables_retry() -> None:
    waits: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "1"}, text="slow")

    for client in _client(handler):
        with pytest.raises(FetchError) as excinfo:
            fetch("https://example.com/", client=client, max_retries=0, sleep=waits.append)

    assert excinfo.value.status == 429
    assert waits == []


def test_timeout_raises_without_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    for client in _client(handler):
        with pytest.raises(FetchError) as excinfo:
            fetch("https://example.com/", client=client)

    assert excinfo.value.status is None


def test_build_client_sets_user_agent() -> None:
    client = build_client()
    try:
        assert "feed-filter" in client.headers["user-agent"]
    finally:
        client.close()


def test_build_client_verifies_via_os_trust_store(monkeypatch: pytest.MonkeyPatch) -> None:
    # TLS verification is delegated to the OS trust store (truststore) rather
    # than the default certifi bundle, so an incomplete server chain still
    # verifies. Guards against a regression that drops back to certifi (or, far
    # worse, disables verification): the ``verify`` context handed to the client
    # must be a truststore one. Spy on the constructor kwargs rather than httpx
    # internals so the assertion survives httpx version changes.
    import truststore

    from feed_filter import fetch

    captured: dict[str, Any] = {}
    real_client = httpx.Client

    def _spy(**kwargs: Any) -> httpx.Client:
        captured.update(kwargs)
        return real_client(**kwargs)

    monkeypatch.setattr(fetch.httpx, "Client", _spy)
    client = build_client()
    try:
        assert isinstance(captured["verify"], truststore.SSLContext)
    finally:
        client.close()
