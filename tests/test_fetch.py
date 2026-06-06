"""Behavior tests for the httpx fetch boundary (TASK-016).

Network-free: every client is built over an ``httpx.MockTransport`` so the
success / redirect / status-error / transport-error paths are exercised without
touching the network.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

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
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    for client in _client(handler):
        with pytest.raises(FetchError) as excinfo:
            fetch("https://example.com/missing", client=client)

    assert excinfo.value.status == 404


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
