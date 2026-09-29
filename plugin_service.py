"""SDK v1 公开服务门面：供其他插件通过 get_service(api_version=1) 调用。

门面只暴露具名业务方法，不透传插件实例、配置、HTTP 客户端或任意端点；
业务调用经由与 LLM Tool 相同的 CloudflareBrowserRuntime 共享路径，
返回完整结构化 payload（不截断、不落结果文件、不上传沙箱）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable
from uuid import uuid4

from .tools.cloudflare_browser import CloudflareBrowserRuntime, missing_credentials


class PluginServiceError(RuntimeError):
    """SDK v1 基础接口错误，附稳定 code。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class CloudflareBrowserService:
    api_version = 1

    # SDK v1 固定能力标识：声明接口实现支持的能力，不代表账号权限或当前可执行。
    features = (
        "web.fetch",
        "browser.markdown",
        "browser.content",
        "browser.links",
        "browser.scrape",
        "browser.json",
        "browser.crawl",
    )

    _POLL_INTERVAL = 0.1

    def __init__(
        self,
        config: dict[str, Any],
        runtime_getter: Callable[[], CloudflareBrowserRuntime],
    ) -> None:
        self.instance_id = uuid4().hex
        self._config = config
        self._runtime_getter = runtime_getter
        self._state = "initializing"

    # ---- 生命周期（由插件 Main 驱动） ----

    def mark_initialized(self) -> None:
        if self._state == "initializing":
            self._state = "ready"

    def begin_close(self) -> None:
        if self._state not in {"closing", "closed"}:
            self._state = "closing"

    def mark_closed(self) -> None:
        self._state = "closed"

    # ---- SDK v1 基础接口 ----

    def for_api_version(self, api_version: int = 1) -> CloudflareBrowserService:
        """按 SDK 约定协商版本并返回当前服务实例；只接受真正的 int 1。"""
        if type(api_version) is not int or api_version != 1:
            raise PluginServiceError("unsupported_version", "仅支持插件服务接口 v1")
        return self

    def get_status(self) -> dict[str, Any]:
        """同步本地快照：只读取内存状态与配置，不创建 Runtime、不读写文件、不联网。"""
        state, reason = self._state, None
        if state in {"closing", "closed"}:
            reason = "service_closed"
        elif state == "ready" and missing_credentials(self._config):
            state, reason = "unavailable", "not_configured"
        return {
            "api_version": self.api_version,
            "instance_id": self.instance_id,
            "state": state,
            "ready": state == "ready",
            "reason": reason,
        }

    def capabilities(self) -> dict[str, Any]:
        return {"api_version": self.api_version, "features": list(self.features)}

    async def wait_ready(self, timeout: float | None = None) -> dict[str, Any]:
        """等待服务就绪并返回 get_status 同形快照；超时抛 TimeoutError，关闭抛 service_closed。"""
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + max(0.0, float(timeout))
        while True:
            status = self.get_status()
            if status["ready"]:
                return status
            if status["state"] in {"closing", "closed"}:
                raise PluginServiceError("service_closed", "服务实例已关闭，请重新获取")
            if deadline is not None and loop.time() >= deadline:
                raise TimeoutError("等待插件服务就绪超时")
            await asyncio.sleep(self._POLL_INTERVAL)

    # ---- 业务方法 ----

    def _runtime(self) -> CloudflareBrowserRuntime:
        """未就绪或关闭时拒绝业务，不创建或重新激活 Runtime。"""
        status = self.get_status()
        if status["state"] in {"closing", "closed"}:
            raise PluginServiceError("service_closed", "服务实例已关闭，请重新获取")
        if not status["ready"]:
            raise PluginServiceError(
                "not_ready", "服务尚未就绪，请检查初始化状态和配置"
            )
        return self._runtime_getter()

    async def fetch(self, url: str, **options: Any) -> dict[str, Any]:
        """markdown 的便捷入口。"""
        return await self._runtime().page_markdown(url=url, **options)

    async def markdown(
        self, *, url: str | None = None, html: str | None = None, **options: Any
    ) -> dict[str, Any]:
        return await self._runtime().page_markdown(url=url, html=html, **options)

    async def content(
        self, *, url: str | None = None, html: str | None = None, **options: Any
    ) -> dict[str, Any]:
        return await self._runtime().page_content(url=url, html=html, **options)

    async def links(
        self, *, url: str | None = None, html: str | None = None, **options: Any
    ) -> dict[str, Any]:
        return await self._runtime().page_links(url=url, html=html, **options)

    async def scrape(
        self,
        *,
        url: str | None = None,
        html: str | None = None,
        elements: Any = None,
        **options: Any,
    ) -> dict[str, Any]:
        return await self._runtime().page_scrape(
            url=url, html=html, elements=elements, **options
        )

    async def json(
        self, *, url: str | None = None, html: str | None = None, **options: Any
    ) -> dict[str, Any]:
        return await self._runtime().page_json(url=url, html=html, **options)

    async def crawl_start(self, url: str, **options: Any) -> dict[str, Any]:
        """启动异步 Crawl 任务；job_id 是调用方保存的远端句柄。"""
        return await self._runtime().crawl_start(url=url, **options)

    async def crawl_status(self, job_id: str, **options: Any) -> dict[str, Any]:
        return await self._runtime().crawl_status(job_id, **options)

    async def crawl_cancel(self, job_id: str) -> dict[str, Any]:
        return await self._runtime().crawl_cancel(job_id)
