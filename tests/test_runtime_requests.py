"""共享 Runtime 的全部八类业务请求构造、参数归一化与上限。"""

from __future__ import annotations

import pytest

from astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser import (
    CloudflareAPIError,
    CloudflareParamError,
)
from conftest import API_BASE, make_config

JOB_ID = "9f1c2a34-5b6d-4e7f-8a9b-0c1d2e3f4a5b"


async def test_markdown_request_construction(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(200, {"success": True, "result": {"markdown": "# hi"}})
    payload = await runtime.page_markdown(
        url="https://example.com",
        goto_options={"wait_until": "load"},
        cache_ttl=60,
    )
    assert len(fake_http.calls) == 1
    call = fake_http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{API_BASE}/accounts/acct-test/browser-rendering/markdown"
    assert call["params"] == {"cacheTTL": 60}
    assert call["json"]["url"] == "https://example.com"
    assert call["json"]["gotoOptions"] == {"waitUntil": "load"}
    assert call["headers"]["Authorization"] == "Bearer tok-secret-123"
    assert payload == {
        "type": "markdown",
        "url": "https://example.com",
        "result": {"markdown": "# hi"},
    }


async def test_content_and_links_request_construction(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(200, {"result": {"content": "<h1>hi</h1>"}})
    payload = await runtime.page_content(html="<h1>hi</h1>", wait_for_timeout=500)
    call = fake_http.calls[0]
    assert call["url"] == f"{API_BASE}/accounts/acct-test/browser-rendering/content"
    assert call["json"] == {"html": "<h1>hi</h1>", "waitForTimeout": 500}
    assert payload == {
        "type": "content",
        "url": None,
        "result": {"content": "<h1>hi</h1>"},
    }

    fake_http.enqueue(200, {"result": {"links": ["https://a.com"]}})
    payload = await runtime.page_links(
        url="https://example.com", visible_links_only=True
    )
    call = fake_http.calls[1]
    assert call["url"] == f"{API_BASE}/accounts/acct-test/browser-rendering/links"
    assert call["json"]["visibleLinksOnly"] is True
    assert payload["type"] == "links"


async def test_scrape_request_construction(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(200, {"result": [{"selector": "h1", "text": "标题"}]})
    payload = await runtime.page_scrape(
        url="https://example.com", elements=[{"selector": "h1"}]
    )
    call = fake_http.calls[0]
    assert call["url"] == f"{API_BASE}/accounts/acct-test/browser-rendering/scrape"
    assert call["json"]["elements"] == [{"selector": "h1"}]
    assert payload["type"] == "scrape"


async def test_json_request_keeps_snake_keys(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(200, {"result": {"title": "t"}})
    await runtime.page_json(
        url="https://example.com",
        prompt="提取标题",
        response_format={"type": "json_schema", "json_schema": {"type": "object"}},
        custom_ai=[{"model": "openai/gpt", "authorization": "sk-custom"}],
    )
    body = fake_http.calls[0]["json"]
    assert body["prompt"] == "提取标题"
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"type": "object"},
    }
    assert body["custom_ai"][0]["model"] == "openai/gpt"
    assert body["custom_ai"][0]["authorization"] == "sk-custom"


async def test_crawl_start_defaults_and_payload(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(200, {"success": True, "result": JOB_ID})
    payload = await runtime.crawl_start("https://example.com")
    assert len(fake_http.calls) == 1
    call = fake_http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{API_BASE}/accounts/acct-test/browser-rendering/crawl"
    assert call["json"] == {
        "url": "https://example.com",
        "limit": 10,
        "render": False,
        "formats": ["markdown"],
    }
    assert payload == {
        "type": "crawl_start",
        "job_id": JOB_ID,
        "message": "Crawl 任务已启动。",
    }


async def test_crawl_start_honors_options_and_small_cap(make_runtime, fake_http):
    runtime = make_runtime(
        make_config(request_settings={"max_crawl_limit": 5, "default_render": True})
    )
    fake_http.enqueue(200, {"result": JOB_ID})
    await runtime.crawl_start(
        "https://example.com",
        depth=2,
        source="sitemaps",
        crawl_purposes=["search"],
        modified_since=None,
    )
    body = fake_http.calls[0]["json"]
    # 默认 limit 取 min(CRAWL_DEFAULT_LIMIT, max_crawl_limit)
    assert body["limit"] == 5
    assert body["render"] is True
    assert body["source"] == "sitemaps"
    assert body["crawlPurposes"] == ["search"]
    # 显式传入的 None 值不进入请求体
    assert "modifiedSince" not in body


async def test_crawl_start_limit_above_config_rejected_without_http(
    make_runtime, fake_http
):
    runtime = make_runtime()
    with pytest.raises(CloudflareParamError) as exc_info:
        await runtime.crawl_start("https://example.com", limit=101)
    assert "max_crawl_limit" in str(exc_info.value)
    assert fake_http.calls == []


async def test_crawl_status_request_construction(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(200, {"result": {"status": "queued", "result": []}})
    payload = await runtime.crawl_status(
        JOB_ID, status="queued", cursor=2, limit=25, cache_ttl=0
    )
    assert len(fake_http.calls) == 1
    call = fake_http.calls[0]
    assert call["method"] == "GET"
    assert (
        call["url"] == f"{API_BASE}/accounts/acct-test/browser-rendering/crawl/{JOB_ID}"
    )
    assert call["params"] == {
        "cacheTTL": 0,
        "status": "queued",
        "cursor": 2,
        "limit": 25,
    }
    assert payload["type"] == "crawl_status"
    assert payload["status"] == "queued"


async def test_crawl_cancel_request_construction(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(200, {"result": {"id": JOB_ID}})
    payload = await runtime.crawl_cancel(JOB_ID)
    assert len(fake_http.calls) == 1
    call = fake_http.calls[0]
    assert call["method"] == "DELETE"
    assert (
        call["url"] == f"{API_BASE}/accounts/acct-test/browser-rendering/crawl/{JOB_ID}"
    )
    assert call["params"] is None
    assert payload == {"type": "crawl_cancel", "id": JOB_ID}


async def test_cache_ttl_clamped_to_allowed_range(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(200, {"result": {}})
    await runtime.page_markdown(url="https://example.com", cache_ttl=999999)
    assert fake_http.calls[0]["params"] == {"cacheTTL": 86400}


async def test_api_error_raises_cloudflare_api_error(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(404, {"errors": [{"code": 7000, "message": "not found"}]})
    with pytest.raises(CloudflareAPIError) as exc_info:
        await runtime.crawl_status(JOB_ID)
    assert "HTTP 404" in str(exc_info.value)


async def test_success_false_raises_api_error(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(200, {"success": False, "errors": [{"message": "nope"}]})
    with pytest.raises(CloudflareAPIError):
        await runtime.page_markdown(url="https://example.com")
