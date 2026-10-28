"""聚合 LLM 工具的 schema、mode/action 契约与高级参数展开。"""

from __future__ import annotations

import json

from astrbot.core.agent.tool import ToolSet

from astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser import (
    CloudflareCrawlTool,
    CloudflareFetchTool,
    build_tools,
)


def test_merged_tool_schemas_are_compact_and_provider_compatible(make_runtime):
    runtime = make_runtime()
    tools, names = build_tools(runtime.config, runtime=runtime)
    assert names == ["cf_browser_fetch", "cf_browser_crawl"]
    assert len(tools) == 2
    for conversion in ("openai_schema", "anthropic_schema", "google_schema"):
        getattr(ToolSet(tools), conversion)()

    fetch_properties = tools[0].parameters["properties"]
    assert "advanced_options" in fetch_properties
    assert "cookies" not in fetch_properties
    assert "authenticate" not in fetch_properties
    schema_chars = sum(
        len(json.dumps(tool.parameters, ensure_ascii=False)) for tool in tools
    )
    assert schema_chars < 20000


async def test_fetch_rejects_invalid_mode_and_cross_mode_fields(
    make_runtime, fake_http
):
    tool = CloudflareFetchTool(runtime=make_runtime())
    assert "mode" in await tool.call(None, mode="unknown", url="https://example.com")
    assert "未知参数" in await tool.call(
        None,
        mode="markdown",
        url="https://example.com",
        elements=[{"selector": "h1"}],
    )
    assert fake_http.calls == []


async def test_fetch_expands_advanced_options(make_runtime, fake_http):
    runtime = make_runtime()
    tool = CloudflareFetchTool(runtime=runtime)
    fake_http.enqueue(200, {"result": {"markdown": "# ok"}})
    await tool.call(
        None,
        url="https://example.com",
        advanced_options={"viewport": {"width": 1280, "height": 720}},
    )
    assert fake_http.calls[0]["json"]["viewport"] == {"width": 1280, "height": 720}

    error = await tool.call(
        None,
        url="https://example.com",
        viewport={"width": 800},
        advanced_options={"viewport": {"width": 1280}},
    )
    assert "advanced_options" in error
    assert len(fake_http.calls) == 1


async def test_crawl_action_validation_and_cancel(make_runtime, fake_http):
    runtime = make_runtime()
    tool = CloudflareCrawlTool(runtime=runtime)
    assert "action" in await tool.call(None, job_id="job-1")
    assert "不支持参数" in await tool.call(
        None, action="cancel", job_id="job-1", url="https://example.com"
    )
    assert fake_http.calls == []

    fake_http.enqueue(200, {"result": {"id": "job-1"}})
    result = await tool.call(None, action="cancel", job_id="job-1")
    assert json.loads(result)["id"] == "job-1"
    assert fake_http.calls[0]["method"] == "DELETE"


async def test_crawl_start_returns_poll_guidance(make_runtime, fake_http):
    tool = CloudflareCrawlTool(runtime=make_runtime())
    fake_http.enqueue(200, {"result": "job-1"})
    result = json.loads(
        await tool.call(None, action="start", url="https://example.com")
    )
    assert result["next_action"] == {
        "tool": "cf_browser_crawl",
        "after_seconds": 5,
        "args": {"action": "status", "job_id": "job-1", "limit": 1, "cache_ttl": 0},
        "message": "Poll with limit=1 while running; fetch full records after completion.",
    }


async def test_crawl_accepts_direct_advanced_options(make_runtime, fake_http):
    tool = CloudflareCrawlTool(runtime=make_runtime())
    fake_http.enqueue(200, {"result": "job-1"})
    await tool.call(
        None,
        action="start",
        url="https://example.com",
        viewport={"width": 1280, "height": 720},
    )
    assert fake_http.calls[0]["json"]["viewport"] == {
        "width": 1280,
        "height": 720,
    }


async def test_missing_credentials_are_not_retryable(make_runtime, fake_http):
    runtime = make_runtime()
    runtime.config["connection_settings"]["api_token"] = ""
    tool = CloudflareFetchTool(runtime=runtime)
    result = json.loads(await tool.call(None, url="https://example.com"))
    assert result["error"]["code"] == "cloudflare_api_error"
    assert result["retryable"] is False
    assert fake_http.calls == []
