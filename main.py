"""AstrBot 插件入口：Cloudflare 云抓取。"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.star import Context, Star

from .tools.cloudflare_browser import PLUGIN_NAME, TOOL_NAMES, build_tools


class CloudflareBrowserRunPlugin(Star):
    """负责将 Cloudflare 抓取工具注册到 AstrBot。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self._registered_tool_names: list[str] = []
        self._register_tools()

    def _register_tools(self) -> None:
        """根据配置注册启用的 LLM Tool。"""
        self._remove_tools()
        tools, names = build_tools(self.config)
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

    async def terminate(self) -> None:
        self._remove_tools()
