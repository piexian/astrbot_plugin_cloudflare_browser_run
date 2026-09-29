"""旧 Tool 的限长/文件桥接/沙箱同步保持不变；SDK 结果保持完整且不落文件。"""

from __future__ import annotations

import json
from pathlib import Path

from astrbot_plugin_cloudflare_browser_run.plugin_service import (
    CloudflareBrowserService,
)
from astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser import (
    CloudflareMarkdownTool,
    CloudflareCrawlStartTool,
)

BIG_MARKDOWN = "x" * 40000


def _enqueue_markdown(fake_http, content):
    fake_http.enqueue(200, {"success": True, "result": {"markdown": content}})


async def test_tool_long_result_bridges_to_file(make_runtime, fake_http):
    runtime = make_runtime()
    _enqueue_markdown(fake_http, BIG_MARKDOWN)
    tool = CloudflareMarkdownTool(runtime=runtime)
    out = await tool.call(None, url="https://example.com")
    data = json.loads(out)
    assert data["saved_to_file"] is True
    assert data["content_chars"] > 12000
    assert len(out) <= 12000
    assert BIG_MARKDOWN not in out

    path = Path(data["file_path"])
    assert path.exists()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["result"]["markdown"] == BIG_MARKDOWN
    assert data["suggested_tools"][0]["name"] == "astrbot_grep_tool"


async def test_sdk_long_result_stays_complete_without_file(make_runtime, fake_http):
    runtime = make_runtime()
    service = CloudflareBrowserService(runtime.config, lambda: runtime)
    service.mark_initialized()
    _enqueue_markdown(fake_http, BIG_MARKDOWN)
    payload = await service.markdown(url="https://example.com")
    assert payload["type"] == "markdown"
    assert payload["result"]["markdown"] == BIG_MARKDOWN

    results_dir = runtime.data_dir / "cloudflare_browser_results"
    assert not results_dir.exists() or not list(results_dir.iterdir())


class _EventStub:
    unified_msg_origin = "umo:test-session"


class _ConfigStub:
    def __init__(self, runtime_name: str) -> None:
        self._runtime_name = runtime_name

    def get_config(self, umo=None):
        return {"provider_settings": {"computer_use_runtime": self._runtime_name}}


class _InnerStub:
    def __init__(self, runtime_name: str) -> None:
        self.event = _EventStub()
        self.context = _ConfigStub(runtime_name)


class _ContextStub:
    def __init__(self, runtime_name: str) -> None:
        self.context = _InnerStub(runtime_name)


async def test_tool_sandbox_upload_syncs_result_file(
    make_runtime, fake_http, monkeypatch
):
    import astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser as cf

    uploads: list[tuple[str, str]] = []

    async def fake_booter(context, umo):
        class _Booter:
            async def upload_file(self, path, name):
                uploads.append((path, name))
                return {"success": True, "file_path": f"/sandbox/{name}"}

        return _Booter()

    monkeypatch.setattr(cf, "get_booter", fake_booter)
    runtime = make_runtime()
    _enqueue_markdown(fake_http, BIG_MARKDOWN)
    tool = CloudflareMarkdownTool(runtime=runtime)
    out = await tool.call(_ContextStub("sandbox"), url="https://example.com")
    data = json.loads(out)
    assert data["sandbox_synced"] is True
    assert data["file_path"].startswith("/sandbox/")
    assert data["host_file_path"] == str(uploads[0][0])
    assert uploads[0][1].endswith(".json")
    assert "preview" not in data


async def test_tool_sandbox_upload_failure_falls_back_to_preview(
    make_runtime, fake_http, monkeypatch
):
    import astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser as cf

    async def fake_booter(context, umo):
        class _Booter:
            async def upload_file(self, path, name):
                return {"success": False, "error": "sandbox offline"}

        return _Booter()

    monkeypatch.setattr(cf, "get_booter", fake_booter)
    runtime = make_runtime()
    _enqueue_markdown(fake_http, BIG_MARKDOWN)
    tool = CloudflareMarkdownTool(runtime=runtime)
    out = await tool.call(_ContextStub("sandbox"), url="https://example.com")
    data = json.loads(out)
    assert data["sandbox_synced"] is False
    assert "sandbox offline" in data["message"]
    assert "x" * 100 in data["preview"]


async def test_tool_local_runtime_reports_host_file(make_runtime, fake_http):
    runtime = make_runtime()
    _enqueue_markdown(fake_http, BIG_MARKDOWN)
    tool = CloudflareMarkdownTool(runtime=runtime)
    out = await tool.call(None, url="https://example.com")
    data = json.loads(out)
    assert data["file_path"].startswith(str(runtime.data_dir))
    assert "astrbot_file_read_tool" in data["message"]


async def test_tool_api_error_returns_redacted_string(make_runtime, fake_http):
    runtime = make_runtime()
    fake_http.enqueue(429, {"errors": [{"message": "tok-secret-123 exhausted"}]})
    tool = CloudflareMarkdownTool(runtime=runtime)
    out = await tool.call(None, url="https://example.com")
    assert isinstance(out, str)
    assert "HTTP 429" in out
    assert "tok-secret-123" not in out


async def test_tool_local_param_error_string_unchanged(make_runtime):
    runtime = make_runtime()
    tool = CloudflareMarkdownTool(runtime=runtime)
    assert await tool.call(None) == "错误：必须提供 url 或 html。"
    crawl = CloudflareCrawlStartTool(runtime=runtime)
    out = await crawl.call(None, url="https://example.com", limit=999)
    assert out.startswith("错误：limit 超过")
