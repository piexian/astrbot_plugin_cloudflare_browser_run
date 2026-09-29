"""真实 Main 生命周期：注册/卸载工具、initialize/terminate 与服务状态衔接。"""

from __future__ import annotations

from astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser import TOOL_NAMES


def test_main_registers_all_tools(make_plugin):
    plugin = make_plugin()
    assert [tool.name for tool in plugin.context.added_tools] == list(TOOL_NAMES)
    assert plugin._registered_tool_names == list(TOOL_NAMES)


async def test_terminate_removes_tools_and_closes_service(make_plugin):
    plugin = make_plugin()
    service = plugin.get_service()
    await plugin.initialize()
    await plugin.terminate()
    # 初始化注册前与 terminate 时各执行一次卸载，共两轮
    removed = plugin.context.tool_manager.removed
    assert len(removed) == 2 * len(TOOL_NAMES)
    assert removed[-len(TOOL_NAMES) :] == list(TOOL_NAMES)
    assert service.get_status()["state"] == "closed"


async def test_re_register_uses_same_runtime_instance(make_plugin):
    plugin = make_plugin()
    runtime = plugin._ensure_runtime()
    plugin._register_tools()
    assert plugin._runtime is runtime
    assert [
        tool.name for tool in plugin.context.added_tools[-len(TOOL_NAMES) :]
    ] == list(TOOL_NAMES)
    for tool in plugin.context.added_tools[-len(TOOL_NAMES) :]:
        assert tool.runtime is runtime


def test_tool_manager_failure_is_tolerated(tmp_path, monkeypatch):
    """卸载工具时的异常不应阻断 terminate。"""
    from astrbot_plugin_cloudflare_browser_run import main as plugin_main

    class _BrokenManager:
        def remove_func(self, name):
            raise RuntimeError("boom")

        def remove_tool(self, name):
            raise RuntimeError("boom")

    class _Context:
        def get_llm_tool_manager(self):
            return _BrokenManager()

        def add_llm_tools(self, *tools):
            pass

    monkeypatch.setattr(
        plugin_main, "get_plugin_data_dir", lambda: tmp_path / "plugin_data"
    )
    plugin = plugin_main.CloudflareBrowserRunPlugin(_Context(), None)
    assert plugin.config == {}
    # 配置为空时状态为 unavailable（凭据缺失）
    assert plugin.get_service().get_status()["state"] == "initializing"
