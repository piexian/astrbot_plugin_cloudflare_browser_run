"""OAuth 与 Runtime/Main 的集成路径：凭据校验、用量累计、会话化登录流程。"""

from __future__ import annotations

import time

from astrbot_plugin_cloudflare_browser_run import main as plugin_main
from astrbot_plugin_cloudflare_browser_run.tools.oauth import (
    OAUTH_STORE_FILENAME,
    OAuthError,
    OAuthToken,
)
from astrbot_plugin_cloudflare_browser_run.tools.oauth import (
    oauth_user_message as _oauth_user_message,
)

from conftest import make_config


class _FakeEvent:
    """最小事件替身：会话过滤只依赖 umo、sender 与 message_str。"""

    def __init__(
        self, text: str = "", sender: str = "admin-1", umo: str = "umo-1"
    ) -> None:
        self.message_str = text
        self.unified_msg_origin = umo
        self._sender = sender
        self.sent: list[str] = []

    def get_sender_id(self) -> str:
        return self._sender

    def plain_result(self, text: str):
        self.sent.append(text)
        return ("plain", text)


def _save_oauth_token(runtime, token: OAuthToken) -> None:
    runtime.oauth._store.save(token)


def make_oauth_token(**overrides) -> OAuthToken:
    fields = {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "expires_at": time.time() + 7200,
        "account_id": "acct-oauth",
        "account_name": "我的账号",
    }
    fields.update(overrides)
    return OAuthToken(**fields)


# ---------- Runtime 凭据与用量 ----------


def test_validate_prefers_api_token(make_runtime):
    runtime = make_runtime()
    assert runtime.validate_credentials() is None


def test_validate_oauth_without_api_token(make_runtime):
    runtime = make_runtime(make_config())
    runtime.config["connection_settings"]["api_token"] = ""
    runtime.config["connection_settings"]["account_id"] = ""
    # 未登录：给引导信息
    message = runtime.validate_credentials()
    assert message is not None and "/cfrun login" in message
    # 登录但未绑定账号：提示补账号
    _save_oauth_token(runtime, make_oauth_token(account_id=""))
    message = runtime.validate_credentials()
    assert message is not None and "Account ID" in message
    # 登录且已绑定：通过
    _save_oauth_token(runtime, make_oauth_token())
    assert runtime.validate_credentials() is None


def test_usage_accounting_accumulates_by_utc_day(make_runtime):
    runtime = make_runtime()
    headers = {"X-Browser-Ms-Used": "1500"}
    runtime._record_usage(headers)
    runtime._record_usage(headers)
    date_label, seconds = runtime.usage_today()
    assert seconds == 3.0
    assert date_label == time.strftime("%Y-%m-%d", time.gmtime())
    # 重启后续算
    runtime2 = make_runtime()
    runtime2._load_usage()
    assert runtime2.usage_today()[1] == 3.0


def test_usage_ignores_missing_header(make_runtime):
    runtime = make_runtime()
    runtime._record_usage({})
    runtime._record_usage({"X-Browser-Ms-Used": "not-a-number"})
    assert runtime.usage_today()[1] == 0.0


# ---------- 会话化登录流程（替换 session_waiter） ----------


def install_waiter_stub(monkeypatch, replies: list[str]):
    """把 session_waiter 换成顺序回放的替身，模拟用户粘贴授权码。"""

    def fake_session_waiter(timeout=None, record_history_chains=False):
        def decorator(fn):
            async def wrapper(event, *args, **kwargs):
                class _Controller:
                    def stop(self):
                        pass

                    def keep(self, *a, **k):
                        pass

                for text in replies:
                    await fn(_Controller(), _FakeEvent(text))

            return wrapper

        return decorator

    monkeypatch.setattr(plugin_main, "session_waiter", fake_session_waiter)


async def test_login_flow_success_auto_fills_account(make_plugin, monkeypatch):
    plugin = make_plugin()
    runtime = plugin._ensure_runtime()
    install_waiter_stub(monkeypatch, ["paste-code-123"])
    monkeypatch.setattr(
        type(runtime.oauth),
        "complete_login",
        lambda self, state, code: _fake_complete(self, state, code),
    )

    results = [result async for result in plugin._run_login_flow(_FakeEvent())]
    saved = runtime.oauth.load_token()
    assert saved is not None and saved.account_id == "acct-oauth"
    joined = "\n".join(str(r) for r in results)
    assert "授权成功" in joined
    assert "已自动回填" in joined or "自动回填" in joined


async def _fake_complete(manager, state, code):
    token = make_oauth_token()
    manager._store.save(token)
    return token


async def test_login_flow_cancel(make_plugin, monkeypatch):
    plugin = make_plugin()
    install_waiter_stub(monkeypatch, ["取消"])

    results = [result async for result in plugin._run_login_flow(_FakeEvent())]
    joined = "\n".join(str(r) for r in results)
    assert "已取消授权" in joined
    assert plugin._ensure_runtime().oauth.load_token() is None


async def test_login_flow_invalid_code_reports_relogin(make_plugin, monkeypatch):
    plugin = make_plugin()
    runtime = plugin._ensure_runtime()
    install_waiter_stub(monkeypatch, ["bad-code"])

    from astrbot_plugin_cloudflare_browser_run.tools.oauth import (
        OAuthError as _OAuthError,
    )

    async def raise_invalid_grant(self, state, code):
        raise _OAuthError("invalid_grant")

    monkeypatch.setattr(type(runtime.oauth), "complete_login", raise_invalid_grant)

    results = [result async for result in plugin._run_login_flow(_FakeEvent())]
    joined = "\n".join(str(r) for r in results)
    assert "/cfrun login" in joined and "重新授权" in joined


def test_oauth_file_probe_reflects_store(make_plugin):
    plugin = make_plugin()
    assert plugin._oauth_file_exists() is False
    runtime = plugin._ensure_runtime()
    runtime.oauth._store.save(make_oauth_token())
    assert plugin._oauth_file_exists() is True


def test_store_filename_shared_between_probe_and_manager(make_plugin):
    from astrbot_plugin_cloudflare_browser_run.tools.cloudflare_browser import (
        OAUTH_STORE_FILENAME as _RUNTIME_NAME,
    )

    assert _RUNTIME_NAME == OAUTH_STORE_FILENAME
    assert _oauth_user_message(OAuthError("not_logged_in")).startswith("尚未登录")
