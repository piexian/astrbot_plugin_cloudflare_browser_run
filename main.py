"""AstrBot 插件入口：Cloudflare 云抓取。"""

from __future__ import annotations

import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.api.util import SessionController, session_waiter

from .plugin_service import CloudflareBrowserService
from .tools.cloudflare_browser import (
    PLUGIN_NAME,
    TOOL_NAMES,
    LEGACY_TOOL_NAMES,
    CloudflareBrowserRuntime,
    build_tools,
    get_plugin_data_dir,
)
from .tools.oauth import (
    LOGIN_TIMEOUT_SECONDS,
    OAUTH_STORE_FILENAME,
    TokenStore,
    OAuthError,
    oauth_user_message,
)


@filter.command_group("cfrun")
def cfrun_group() -> None:
    """Cloudflare 授权管理指令组"""


class CloudflareBrowserRunPlugin(Star):
    """负责将 Cloudflare 抓取工具注册到 AstrBot，并公开 SDK v1 服务门面。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self._runtime: CloudflareBrowserRuntime | None = None
        self._registered_tool_names: list[str] = []
        self._service = CloudflareBrowserService(
            self.config,
            self._ensure_runtime,
            oauth_credentials_ready=self._oauth_credentials_ready,
        )
        self._login_sessions: set[str] = set()
        self._register_tools()

    def get_service(self, api_version: int = 1) -> CloudflareBrowserService:
        """获取公开服务；同次加载返回同一实例，调用前可用 get_status()/wait_ready() 确认。"""
        return self._service.for_api_version(api_version)

    def _ensure_runtime(self) -> CloudflareBrowserRuntime:
        """返回 Main、所有 Tool 与 SDK 共用的同一 Runtime 实例（惰性创建）。"""
        if self._runtime is None:
            self._runtime = CloudflareBrowserRuntime(self.config, get_plugin_data_dir())
        return self._runtime

    def _oauth_credentials_ready(self) -> bool:
        """判断 OAuth 登录态是否具备可用的 Account ID。"""
        token = TokenStore(
            get_plugin_data_dir() / OAUTH_STORE_FILENAME
        ).load()
        if token is None:
            return False
        settings = self.config.get("connection_settings")
        if not isinstance(settings, dict):
            return bool(token.account_id.strip())
        configured_account_id = str(settings.get("account_id") or "").strip()
        return bool(token.account_id.strip() or configured_account_id)

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
        for name in (*TOOL_NAMES, *LEGACY_TOOL_NAMES):
            try:
                tool_mgr.remove_func(name)
            except Exception:
                try:
                    tool_mgr.remove_tool(name)
                except Exception:
                    pass

    async def initialize(self) -> None:
        """AstrBot 加载完成钩子：标记服务就绪并按需启动令牌保活。"""
        self._service.mark_initialized()
        self._ensure_runtime().oauth.ensure_keepalive()

    async def terminate(self) -> None:
        self._service.begin_close()
        if self._runtime is not None:
            await self._runtime.oauth.close()
        self._remove_tools()
        self._service.mark_closed()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @cfrun_group.command("login")
    async def cfrun_login(self, event: AstrMessageEvent):
        """登录 Cloudflare 账号（OAuth 授权），自动回填 Account ID。"""
        session_key = getattr(event, "unified_msg_origin", "") or event.message_str
        if session_key in self._login_sessions:
            yield event.plain_result(
                "当前会话已有进行中的授权流程，请先完成或等待超时。"
            )
            return
        self._login_sessions.add(session_key)
        try:
            async for result in self._run_login_flow(event):
                yield result
        finally:
            self._login_sessions.discard(session_key)

    async def _run_login_flow(self, event: AstrMessageEvent):
        """授权码登录：发链接 → 会话内等授权码 → 换发并回填账号。"""
        runtime = self._ensure_runtime()
        authorize_url, state = runtime.oauth.begin_login()
        yield event.plain_result(
            "请完成 Cloudflare 授权：打开下一条消息的链接，登录并同意授权，"
            "然后把回调页上的授权码复制发给我；发送「取消」退出。（10 分钟内有效）"
        )
        yield event.plain_result(authorize_url)
        initiator_id = event.get_sender_id()
        choice: dict[str, str] = {}

        @session_waiter(timeout=LOGIN_TIMEOUT_SECONDS)
        async def code_waiter(controller: SessionController, ev: AstrMessageEvent):
            if ev.get_sender_id() != initiator_id:
                controller.keep(
                    LOGIN_TIMEOUT_SECONDS, reset_timeout=False
                )  # 忽略非发起人消息
                return
            text = ev.message_str.strip()
            if text in {"取消", "c", "cancel"}:
                choice["code"] = ""
            else:
                choice["code"] = text.strip().strip('"').strip("'").replace(" ", "")
            controller.stop()

        try:
            await code_waiter(event)
        except TimeoutError:
            yield event.plain_result("授权已超时取消，请重新发送 /cfrun login。")
            return
        code = choice.get("code") or ""
        if not code:
            yield event.plain_result("已取消授权。")
            return
        try:
            token = await runtime.oauth.complete_login(state, code)
        except OAuthError as exc:
            yield event.plain_result(oauth_user_message(exc))
            return

        if not token.account_id:
            yield event.plain_result(
                "授权成功，但未能自动获取 Account ID（网络异常或多账号）。\n"
                "请稍后重发 /cfrun login，或在插件配置中手动填写 account_id。"
            )
            return
        mins = max(1, int((token.expires_at - time.time()) / 60))
        multi = (
            f"检测到 {token.account_count} 个账号，已绑定第一个；"
            "如需切换，请在插件配置中手动填写 account_id。\n"
            if token.account_count > 1
            else ""
        )
        name = f"（{token.account_name}）" if token.account_name else ""
        renew_note = (
            f"访问令牌约 {mins} 分钟后到期，临期自动续期，无需人工干预。"
            if token.refresh_token
            else (
                f"本次授权未获得续期令牌（refresh token）：约 {mins} 分钟后到期，"
                "届时请重新发送 /cfrun login。"
            )
        )
        yield event.plain_result(
            f"Cloudflare 授权成功{name}。\n"
            f"{multi}"
            "Account ID 已自动回填到插件数据目录，抓取功能现已可用。\n"
            f"{renew_note}"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @cfrun_group.command("status")
    async def cfrun_status(self, event: AstrMessageEvent):
        """查看 Cloudflare 授权状态。"""
        runtime = self._ensure_runtime()
        token = runtime.oauth.load_token()
        if token is not None:
            mins = max(0, int((token.expires_at - time.time()) / 60))
            account = token.account_name or "未知账号"
            extra = (
                "\n已配置的 api_token 转为兜底，仅在登录态失效时使用。"
                if str(runtime.cfg("api_token")).strip()
                else ""
            )
            yield event.plain_result(
                f"当前鉴权：OAuth 登录（{account}）\n"
                f"Account ID：{token.account_id or '未获取'}\n"
                f"访问令牌剩余约 {mins} 分钟，临期自动续期。{extra}"
            )
            return
        if str(runtime.cfg("api_token")).strip():
            yield event.plain_result(
                "当前鉴权：配置的 api_token（OAuth 未登录时的兜底）。\n"
                "如需免维护令牌，请发送 /cfrun login 改用 OAuth 登录。"
            )
            return
        yield event.plain_result(
            "当前未登录：请发送 /cfrun login 完成授权，或在配置中填写 account_id 与 api_token。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @cfrun_group.command("usage")
    async def cfrun_usage(self, event: AstrMessageEvent):
        """查看本插件今日 Browser Run 用量。"""
        runtime = self._ensure_runtime()
        date_label, seconds = runtime.usage_today()
        if seconds <= 0:
            yield event.plain_result(
                "今日（UTC）本插件尚无 Browser Run 用量记录。\n"
                "官方账号总量请看控制台：https://dash.cloudflare.com/?to=/:account/workers/browser-run"
            )
            return
        minutes = seconds / 60
        yield event.plain_result(
            f"今日（UTC，{date_label}）本插件累计消耗 Browser Run 约 {minutes:.1f} 分钟。\n"
            "仅统计本插件发起的请求；账号总量（含其他途径）请看控制台：\n"
            "https://dash.cloudflare.com/?to=/:account/workers/browser-run"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @cfrun_group.command("logout")
    async def cfrun_logout(self, event: AstrMessageEvent):
        """退出 Cloudflare OAuth 登录并清除本地令牌。"""
        runtime = self._ensure_runtime()
        token = await runtime.oauth.logout()
        if token is None:
            yield event.plain_result("当前未登录 Cloudflare。")
            return
        yield event.plain_result(
            "已退出 Cloudflare 登录并清除本地令牌，远端授权已请求撤销。"
        )
