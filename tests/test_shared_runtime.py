"""Main / Tool / SDK 共享同一 Runtime；build_tools 向后兼容。"""

from __future__ import annotations


from astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser import (
    TOOL_NAMES,
    build_tools,
)
from conftest import make_config


def test_main_tools_and_sdk_share_one_runtime(make_plugin):
    plugin = make_plugin()
    service = plugin.get_service()
    runtime = plugin._ensure_runtime()
    assert plugin._runtime is runtime
    assert len(plugin.context.added_tools) == len(TOOL_NAMES)
    for tool in plugin.context.added_tools:
        assert tool.runtime is runtime
    assert service._runtime_getter() is runtime


async def test_sdk_business_flows_through_shared_runtime(make_plugin, fake_http):
    plugin = make_plugin()
    service = plugin.get_service()
    await plugin.initialize()
    fake_http.enqueue(200, {"result": {"markdown": "# ok"}})
    payload = await service.fetch("https://example.com")
    assert payload["result"]["markdown"] == "# ok"
    assert len(fake_http.calls) == 1
    assert fake_http.calls[0]["url"].endswith(
        "/accounts/acct-test/browser-rendering/markdown"
    )


def test_build_tools_accepts_explicit_runtime(make_runtime):
    runtime = make_runtime()
    tools, names = build_tools(make_config(), runtime=runtime)
    assert names == list(TOOL_NAMES)
    for tool in tools:
        assert tool.runtime is runtime


def test_build_tools_backward_compat_creates_own_runtime(tmp_path, monkeypatch):
    import astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser as cf

    monkeypatch.setattr(cf, "get_plugin_data_dir", lambda: tmp_path / "plugin_data")
    tools, names = build_tools(make_config())
    assert names == list(TOOL_NAMES)
    first = tools[0].runtime
    assert first is not None
    tools_again, _ = build_tools(make_config())
    assert tools_again[0].runtime is not first


async def test_shared_runtime_reflects_same_config(make_plugin, fake_http):
    """同一 Runtime 意味着 Tool 与 SDK 使用同一份配置语义。"""
    plugin = make_plugin()
    service = plugin.get_service()
    await plugin.initialize()
    fake_http.enqueue(200, {"result": "job-1"})
    await service.crawl_start("https://example.com")
    fake_http.enqueue(200, {"result": "job-2"})
    crawl_tool = plugin.context.added_tools[5]
    assert crawl_tool.name == "cf_browser_crawl_start"
    await crawl_tool.call(None, url="https://example.com")
    # 两者默认 limit 都是 min(10, max_crawl_limit=100) = 10
    assert fake_http.calls[0]["json"]["limit"] == 10
    assert fake_http.calls[1]["json"]["limit"] == 10
