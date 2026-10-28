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
        await runtime.crawl_start("https://example.com", content_use="everything")
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


async def test_page_rejects_both_url_and_html(make_runtime, fake_http):
    runtime = make_runtime()
    with pytest.raises(CloudflareParamError, match="只能提供其中一个"):
        await runtime.page_markdown(
            url="https://example.com", html="<h1>duplicate</h1>"
        )
    assert fake_http.calls == []


async def test_json_requires_prompt_or_response_format(make_runtime, fake_http):
    runtime = make_runtime()
    with pytest.raises(CloudflareParamError, match="prompt 或 response_format"):
        await runtime.page_json(url="https://example.com")
    assert fake_http.calls == []


async def test_crawl_json_requires_json_options(make_runtime, fake_http):
    runtime = make_runtime()
    with pytest.raises(CloudflareParamError, match="json_options"):
        await runtime.crawl_start("https://example.com", formats=["json"])
    assert fake_http.calls == []


async def test_unknown_parameters_rejected_without_http(make_runtime, fake_http):
    runtime = make_runtime()
    with pytest.raises(CloudflareParamError, match="unknown_option"):
        await runtime.page_markdown(url="https://example.com", unknown_option=True)
    with pytest.raises(CloudflareParamError, match="unknown_option"):
        await runtime.crawl_start("https://example.com", unknown_option=True)
    with pytest.raises(CloudflareParamError, match="unknown_option"):
        await runtime.crawl_status("job-1", unknown_option=True)
    assert fake_http.calls == []


async def test_browser_timeout_budget_rejected_before_http(make_runtime, fake_http):
    runtime = make_runtime(
        make_config(request_settings={"timeout_seconds": 1, "max_crawl_limit": 100})
    )
    with pytest.raises(CloudflareParamError, match="timeout_seconds"):
        await runtime.page_markdown(
            url="https://example.com",
            goto_options={"timeout": 600},
            wait_for_timeout=500,
            action_timeout=100,
        )
    with pytest.raises(CloudflareParamError, match="timeout_seconds"):
        await runtime.crawl_start(
            "https://example.com", render=True, wait_for_timeout=1500
        )
    assert fake_http.calls == []


@pytest.mark.parametrize(
    "field,value", [("depth", 0), ("max_age", 604801), ("max_age", 1.5)]
)
async def test_crawl_numeric_bounds_rejected_without_http(
    make_runtime, fake_http, field, value
):
    runtime = make_runtime()
    with pytest.raises(CloudflareParamError, match=field):
        await runtime.crawl_start("https://example.com", **{field: value})
    assert fake_http.calls == []
