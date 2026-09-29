"""错误脱敏：token / headers / cookies / authenticate 不进入错误消息。"""

from __future__ import annotations

import pytest

from astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser import (
    CloudflareAPIError,
)


async def test_http_error_redacts_api_token(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(
        401,
        {
            "success": False,
            "errors": [
                {"code": 10000, "message": "Authentication error: tok-secret-123"}
            ],
        },
    )
    with pytest.raises(CloudflareAPIError) as exc_info:
        await runtime.page_markdown(url="https://example.com")
    message = str(exc_info.value)
    assert "tok-secret-123" not in message
    assert "HTTP 401" in message
    assert "api_token" in message or "Token" in message


async def test_non_json_error_redacts_bearer_scheme(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(
        500,
        "gateway error: Authorization: Bearer tok-secret-123 leaked",
    )
    with pytest.raises(CloudflareAPIError) as exc_info:
        await runtime.page_markdown(url="https://example.com")
    message = str(exc_info.value)
    assert "tok-secret-123" not in message
    assert "Bearer" in message


async def test_extra_headers_secret_redacted(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(
        400,
        {
            "success": False,
            "errors": [{"message": "bad header value: header-secret-99"}],
        },
    )
    with pytest.raises(CloudflareAPIError) as exc_info:
        await runtime.page_markdown(
            url="https://example.com",
            set_extra_http_headers={"X-Internal": "header-secret-99"},
        )
    assert "header-secret-99" not in str(exc_info.value)


async def test_cookies_and_authenticate_redacted(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(
        500,
        {
            "success": False,
            "errors": [
                {"message": "rejecting cookie-secret-7 and pw-secret-8 together"}
            ],
        },
    )
    with pytest.raises(CloudflareAPIError) as exc_info:
        await runtime.crawl_start(
            "https://example.com",
            cookies=[{"name": "sid", "value": "cookie-secret-7"}],
            authenticate={"username": "u", "password": "pw-secret-8"},
        )
    message = str(exc_info.value)
    assert "cookie-secret-7" not in message
    assert "pw-secret-8" not in message


async def test_network_failure_redacts_token(make_runtime, fake_http, monkeypatch):
    import aiohttp

    runtime = make_runtime()

    async def explode(*args, **kwargs):
        raise OSError("connect failed with Bearer tok-secret-123")

    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda **kwargs: _ExplodingSession(explode())
    )
    with pytest.raises(CloudflareAPIError) as exc_info:
        await runtime.page_markdown(url="https://example.com")
    assert "tok-secret-123" not in str(exc_info.value)


class _ExplodingSession:
    def __init__(self, awaitable) -> None:
        self._awaitable = awaitable

    async def __aenter__(self):
        return await self._awaitable

    async def __aexit__(self, *exc):
        return False
