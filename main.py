"""AstrBot 插件入口：Cloudflare 云抓取。"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.star import Context, Star

from .plugin_service import CloudflareBrowserService
from .tools.cloudflare_browser import (
    PLUGIN_NAME,
    TOOL_NAMES,
    CloudflareBrowserRuntime,
    build_tools,
    get_plugin_data_dir,
)


class CloudflareBrowserRunPlugin(Star):
    """负责将 Cloudflare 抓取工具注册到 AstrBot，并公开 SDK v1 服务门面。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self._runtime: CloudflareBrowserRuntime | None = None
        self._registered_tool_names: list[str] = []
        self._service = CloudflareBrowserService(self.config, self._ensure_runtime)
        self._register_tools()

    def get_service(self, api_version: int = 1) -> CloudflareBrowserService:
        """获取公开服务；同次加载返回同一实例，调用前可用 get_status()/wait_ready() 确认。"""
        return self._service.for_api_version(api_version)

    def _ensure_runtime(self) -> CloudflareBrowserRuntime:
        """返回 Main、所有 Tool 与 SDK 共用的同一 Runtime 实例（惰性创建）。"""
        if self._runtime is None:
            self._runtime = CloudflareBrowserRuntime(self.config, get_plugin_data_dir())
        return self._runtime

    def _register_tools(self) -> None:
        """根据配置注册启用的 LLM Tool。"""
        self._remove_tools()
        tools, names = build_tools(self.config, runtime=self._ensure_runtime())
        self._registered_tool_names = names

        if tools:
            self.context.add_llm_tools(*tools)
            logger.info(
                f"[{PLUGIN_NAME}] registered LLM tools: "
                f"{', '.join(self._registered_tool_names)}"
            )
        else:
            logger.info(f"[{PLUGIN_NAME}] all LLM tools are disabled by configuration")

    def _remove_tools(self) -> None:
        """卸载本插件注册过的工具，避免重载后重复。"""
        try:
            tool_mgr = self.context.get_llm_tool_manager()
        except Exception:
            return
        for name in TOOL_NAMES:
            try:
                tool_mgr.remove_func(name)
            except Exception:
                try:
                    tool_mgr.remove_tool(name)
                except Exception:
                    pass

    async def initialize(self) -> None:
        """AstrBot 加载完成钩子：标记服务就绪（缺配置时状态为 unavailable）。"""
        self._service.mark_initialized()

    async def terminate(self) -> None:
        self._service.begin_close()
        self._remove_tools()
        self._service.mark_closed()
