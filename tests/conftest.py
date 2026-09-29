"""测试引导与共享替身：加载真实插件包，HTTP 用可计数替身。

- 真实 Runtime / Tool / SDK / Main 全部参与测试，只替换 HTTP 层与宿主设施。
- 插件数据目录一律指向 tmp_path，不触碰仓库 data/ 与宿主 AstrBot 数据。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG_NAME = "astrbot_plugin_cloudflare_browser_run"
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

API_BASE = "https://api.cloudflare.com/client/v4"


def make_config(**overrides):
    """构造嵌套结构的插件配置；overrides 按顶层键覆盖。"""
    config = {
        "connection_settings": {
            "account_id": "acct-test",
            "api_token": "tok-secret-123",
        },
        "request_settings": {
            "timeout_seconds": 30,
            "default_cache_ttl": 5,
            "default_render": False,
            "max_crawl_limit": 100,
        },
        "output_settings": {"max_output_chars": 12000},
    }
    config.update(overrides)
    return config


class _RecordedResponse:
    def __init__(self, status: int, payload) -> None:
        self.status = status
        self._text = payload if isinstance(payload, str) else json.dumps(payload)

    async def text(self) -> str:
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class HttpRecorder:
    """可计数 HTTP 替身：记录每次请求并按队列返回预设响应。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.responses: list[tuple[int, object]] = []

    def enqueue(self, status: int = 200, payload=None) -> None:
        self.responses.append((status, payload if payload is not None else {}))

    def _session(self, **kwargs):
        recorder = self

        class _Session:
            def request(self, method, url, params=None, json=None, headers=None):
                recorder.calls.append(
                    {
                        "method": method,
                        "url": url,
                        "params": params,
                        "json": json,
                        "headers": headers,
                    }
                )
                if not recorder.responses:
                    raise AssertionError(f"意外的 HTTP 请求：{method} {url}")
                status, payload = recorder.responses.pop(0)
                return _RecordedResponse(status, payload)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return _Session()


@pytest.fixture
def fake_http(monkeypatch):
    """把 aiohttp.ClientSession 换成共享记录器；测试结束自动还原。"""
    import aiohttp

    recorder = HttpRecorder()
    monkeypatch.setattr(aiohttp, "ClientSession", recorder._session)
    return recorder


class FakeToolManager:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def remove_func(self, name: str) -> None:
        self.removed.append(name)

    def remove_tool(self, name: str) -> None:
        self.removed.append(name)


class FakeContext:
    def __init__(self) -> None:
        self.tool_manager = FakeToolManager()
        self.added_tools: list = []

    def get_llm_tool_manager(self):
        return self.tool_manager

    def add_llm_tools(self, *tools) -> None:
        self.added_tools.extend(tools)


@pytest.fixture
def make_runtime(tmp_path):
    """构造使用 tmp_path 数据目录的真实 Runtime。"""
    from astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser import (
        CloudflareBrowserRuntime,
    )

    def _make(config=None) -> CloudflareBrowserRuntime:
        return CloudflareBrowserRuntime(
            config if config is not None else make_config(), tmp_path / "plugin_data"
        )

    return _make


@pytest.fixture
def make_plugin(tmp_path, monkeypatch):
    """构造真实 Main 实例；插件数据目录指向 tmp_path。"""
    from astrbot_plugin_cloudflare_browser_run import main as plugin_main

    def _make(config=None):
        monkeypatch.setattr(
            plugin_main, "get_plugin_data_dir", lambda: tmp_path / "plugin_data"
        )
        return plugin_main.CloudflareBrowserRunPlugin(
            FakeContext(), config if config is not None else make_config()
        )

    return _make
