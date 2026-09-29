"""纯本地参数拒绝：一律不发 HTTP，消息与原 Tool 展示一致。"""

from __future__ import annotations

import pytest

from astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser import (
    CloudflareAPIError,
    CloudflareParamError,
)
from conftest import make_config

INVALID_JOB_IDS = [
    "",
    "   ",
    None,
    "abc/def",
    "../escape",
    "..",
    "a?query=1",
    "a#fragment",
    "a b",
    "a\nb",
    "job" * 50,  # 超长
]


async def test_page_requires_url_or_html(make_runtime, fake_http):
    runtime = make_runtime()
    with pytest.raises(CloudflareParamError) as exc_info:
        await runtime.page_markdown()
    assert str(exc_info.value) == "错误：必须提供 url 或 html。"
    assert fake_http.calls == []


async def test_scrape_requires_elements(make_runtime, fake_http):
    runtime = make_runtime()
    for elements in (None, [], ""):
        with pytest.raises(CloudflareParamError) as exc_info:
            await runtime.page_scrape(url="https://example.com", elements=elements)
        assert str(exc_info.value) == "错误：elements 至少需要包含一个 selector。"
    assert fake_http.calls == []


async def test_mutually_exclusive_options_rejected(make_runtime, fake_http):
    runtime = make_runtime()
    with pytest.raises(CloudflareParamError):
        await runtime.page_markdown(
            url="https://example.com",
            allow_request_pattern=["*a*"],
            reject_request_pattern=["*b*"],
        )
    with pytest.raises(CloudflareParamError):
        await runtime.page_markdown(
            url="https://example.com",
            allow_resource_types=["image"],
            reject_resource_types=["script"],
        )
    assert fake_http.calls == []


async def test_enum_values_rejected(make_runtime, fake_http):
    runtime = make_runtime()
    with pytest.raises(CloudflareParamError):
        await runtime.page_markdown(
            url="https://example.com", goto_options={"wait_until": "whenever"}
        )
    with pytest.raises(CloudflareParamError):
        await runtime.page_markdown(
            url="https://example.com", allow_resource_types=["hologram"]
        )
    with pytest.raises(CloudflareParamError):
        await runtime.crawl_start("https://example.com", formats=["pdf"])
    with pytest.raises(CloudflareParamError):
        await runtime.crawl_start("https://example.com", source="everywhere")
    with pytest.raises(CloudflareParamError):
        await runtime.crawl_start("https://example.com", crawl_purposes=["spam"])
    with pytest.raises(CloudflareParamError):
        await runtime.crawl_status("job-ok", status="flying")
    with pytest.raises(CloudflareParamError):
        await runtime.page_markdown(
            url="https://example.com",
            set_extra_http_headers={"X-Mode": 123},
        )
    assert fake_http.calls == []


async def test_crawl_start_requires_url(make_runtime, fake_http):
    runtime = make_runtime()
    # None/空串被拒绝；纯空白串沿用原有行为（原 Tool 同样放行）
    for url in (None, ""):
        with pytest.raises(CloudflareParamError) as exc_info:
            await runtime.crawl_start(url)
        assert str(exc_info.value) == "错误：必须提供 url。"
    assert fake_http.calls == []


async def test_crawl_modified_since_window(make_runtime, fake_http):
    import time

    runtime = make_runtime()
    too_old = int(time.time()) - 400 * 24 * 60 * 60
    with pytest.raises(CloudflareParamError):
        await runtime.crawl_start("https://example.com", modified_since=too_old)
    with pytest.raises(CloudflareParamError):
        await runtime.crawl_start(
            "https://example.com", modified_since=int(time.time()) + 60
        )
    assert fake_http.calls == []


@pytest.mark.parametrize("job_id", INVALID_JOB_IDS)
async def test_job_id_injection_rejected_without_http(make_runtime, fake_http, job_id):
    runtime = make_runtime()
    with pytest.raises(CloudflareParamError):
        await runtime.crawl_status(job_id)
    with pytest.raises(CloudflareParamError):
        await runtime.crawl_cancel(job_id)
    assert fake_http.calls == []


async def test_param_error_messages_keep_tool_display_format(make_runtime):
    runtime = make_runtime()
    with pytest.raises(CloudflareParamError) as exc_info:
        await runtime.page_content()
    assert str(exc_info.value).startswith("错误：")


async def test_missing_credentials_rejected_before_http(make_runtime, fake_http):
    runtime = make_runtime(
        make_config(connection_settings={"account_id": "", "api_token": "tok"})
    )
    with pytest.raises(CloudflareAPIError) as exc_info:
        await runtime.page_markdown(url="https://example.com")
    assert "account_id" in str(exc_info.value)
    assert fake_http.calls == []

    with pytest.raises(CloudflareAPIError):
        await runtime.crawl_start("https://example.com")
    assert fake_http.calls == []
