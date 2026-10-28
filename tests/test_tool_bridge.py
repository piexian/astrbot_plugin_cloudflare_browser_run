"""Tool 限长、可读文件桥接和沙箱同步；SDK 结果保持完整且不落文件。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from astrbot_plugin_cloudflare_browser_run.plugin_service import (
    CloudflareBrowserService,
)
from astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser import (
    CloudflareFetchTool,
    CloudflareCrawlTool,
)
from conftest import make_config

BIG_MARKDOWN = "# Example\n\n" + "x" * 40000


def _enqueue_markdown(fake_http, content):
    fake_http.enqueue(200, {"success": True, "result": {"markdown": content}})


async def test_tool_long_result_bridges_to_file(make_runtime, fake_http):
    runtime = make_runtime()
    _enqueue_markdown(fake_http, BIG_MARKDOWN)
    tool = CloudflareFetchTool(runtime=runtime)
    out = await tool.call(None, url="https://example.com")
    data = json.loads(out)
    assert data["saved_to_file"] is True
    assert data["content_chars"] > 12000
    assert len(out) <= 12000
    assert BIG_MARKDOWN not in out

    path = Path(data["file_path"])
    assert path.suffix == ".md"
    assert path.read_text(encoding="utf-8") == BIG_MARKDOWN
    raw_path = Path(data["raw_json_file_path"])
    stored = json.loads(raw_path.read_text(encoding="utf-8"))
    assert stored["result"]["markdown"] == BIG_MARKDOWN


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
    tool = CloudflareFetchTool(runtime=runtime)
    out = await tool.call(_ContextStub("sandbox"), url="https://example.com")
    data = json.loads(out)
    assert data["sandbox_synced"] is True
    assert data["file_path"].startswith("/sandbox/")
    assert data["host_file_path"] == str(uploads[0][0])
    assert uploads[0][1].endswith(".md")
    assert uploads[1][1].endswith(".json")
    assert data["raw_json_file_path"] == f"/sandbox/{uploads[1][1]}"
    assert len(uploads) == 2
    assert "preview" not in data


async def test_tool_sandbox_raw_json_failure_keeps_readable_file(
    make_runtime, fake_http, monkeypatch
):
    import astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser as cf

    async def fake_booter(context, umo):
        class _Booter:
            async def upload_file(self, path, name):
                if name.endswith(".json"):
                    return {"success": False, "error": "raw upload rejected"}
                return {"success": True, "file_path": f"/sandbox/{name}"}

        return _Booter()

    monkeypatch.setattr(cf, "get_booter", fake_booter)
    runtime = make_runtime()
    _enqueue_markdown(fake_http, BIG_MARKDOWN)
    tool = CloudflareFetchTool(runtime=runtime)
    out = await tool.call(_ContextStub("sandbox"), url="https://example.com")
    data = json.loads(out)
    assert data["sandbox_synced"] is True
    assert data["file_path"].endswith(".md")
    assert data["raw_json_sync_error"] == "raw upload rejected"
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
    tool = CloudflareFetchTool(runtime=runtime)
    out = await tool.call(_ContextStub("sandbox"), url="https://example.com")
    data = json.loads(out)
    assert data["sandbox_synced"] is False
    assert "sandbox offline" in data["message"]
    assert "x" * 100 in data["preview"]


async def test_tool_local_runtime_reports_host_file(make_runtime, fake_http):
    runtime = make_runtime()
    _enqueue_markdown(fake_http, BIG_MARKDOWN)
    tool = CloudflareFetchTool(runtime=runtime)
    out = await tool.call(None, url="https://example.com")
    data = json.loads(out)
    assert data["file_path"].startswith(str(runtime.data_dir))
    assert "astrbot_file_read_tool" in data["message"]


async def test_tool_api_error_returns_redacted_string(make_runtime, fake_http):
    runtime = make_runtime()
    for _ in range(3):
        fake_http.enqueue(429, {"errors": [{"message": "tok-secret-123 exhausted"}]})
    tool = CloudflareFetchTool(runtime=runtime)
    out = await tool.call(None, url="https://example.com")
    assert isinstance(out, str)
    assert "HTTP 429" in out
    assert "tok-secret-123" not in out


async def test_tool_local_param_error_is_structured(make_runtime):
    runtime = make_runtime()
    tool = CloudflareFetchTool(runtime=runtime)
    result = json.loads(await tool.call(None))
    assert result["error"]["code"] == "invalid_parameters"
    assert result["error"]["message"] == "错误：必须提供 url 或 html。"
    assert result["retryable"] is False
    crawl = CloudflareCrawlTool(runtime=runtime)
    out = await crawl.call(None, action="start", url="https://example.com", limit=999)
    assert json.loads(out)["error"]["message"].startswith("错误：limit 超过")


async def test_tool_local_runtime_uses_session_workspace(
    make_runtime, fake_http, monkeypatch, tmp_path
):
    import astrbot.core.tools.computer_tools.util as workspace

    workspace_root = tmp_path / "workspace"

    async def fake_workspace_root(context):
        return workspace_root

    monkeypatch.setattr(workspace, "workspace_root_for_context", fake_workspace_root)
    runtime = make_runtime()
    _enqueue_markdown(fake_http, BIG_MARKDOWN)
    tool = CloudflareFetchTool(runtime=runtime)
    out = await tool.call(_ContextStub("local"), url="https://example.com")
    data = json.loads(out)
    path = Path(data["file_path"])
    assert path.is_relative_to(workspace_root)
    assert path.read_text(encoding="utf-8") == BIG_MARKDOWN


async def test_crawl_result_has_line_readable_report(make_runtime, fake_http):
    runtime = make_runtime()
    markdown = "# Page\n\n" + "y" * 40000
    fake_http.enqueue(
        200,
        {
            "success": True,
            "result": {
                "status": "completed",
                "result": [{"url": "https://example.com", "markdown": markdown}],
            },
        },
    )
    tool = CloudflareCrawlTool(runtime=runtime)
    out = await tool.call(None, action="status", job_id="job-1")
    data = json.loads(out)
    report = Path(data["file_path"]).read_text(encoding="utf-8")
    assert "## Record 1" in report
    assert markdown in report
    stored = json.loads(Path(data["raw_json_file_path"]).read_text(encoding="utf-8"))
    assert stored["result"][0]["markdown"] == markdown


async def test_result_cleanup_preserves_unrelated_files(make_runtime, fake_http):
    runtime = make_runtime(
        make_config(
            output_settings={
                "max_output_chars": 12000,
                "result_retention_hours": 1,
                "max_stored_results": 10,
            }
        )
    )
    results_dir = runtime.data_dir / "cloudflare_browser_results"
    results_dir.mkdir(parents=True)
    old_json = results_dir / "markdown_20000101_000000_000000_deadbeef.json"
    old_markdown = results_dir / "markdown_20000101_000000_000000_deadbeef.md"
    old_json.write_text("{}")
    old_markdown.write_text("old")
    os.utime(old_json, (0, 0))
    os.utime(old_markdown, (0, 0))
    unrelated = results_dir / "notes.txt"
    unrelated.write_text("keep")

    _enqueue_markdown(fake_http, BIG_MARKDOWN)
    tool = CloudflareFetchTool(runtime=runtime)
    await tool.call(None, url="https://example.com")
    assert not old_json.exists()
    assert not old_markdown.exists()
    assert unrelated.read_text() == "keep"
