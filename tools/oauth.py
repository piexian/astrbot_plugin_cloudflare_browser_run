"""Cloudflare 自管 OAuth 客户端（Authorization Code + PKCE S256）。

无状态公开客户端：token 与自动发现的 account_id 以 0600 JSON 落在插件
持久化目录；后台循环在临期前自动刷新，不依赖请求失败触发。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp

from astrbot.api import logger


OAUTH_CLIENT_ID = "9f211403f438578c72d299494764ff22"
OAUTH_REDIRECT_URI = "https://browser-run.alxy.app/callback"
OAUTH_AUTHORIZATION_URL = "https://dash.cloudflare.com/oauth2/auth"
OAUTH_TOKEN_URL = "https://dash.cloudflare.com/oauth2/token"
OAUTH_REVOKE_URL = "https://dash.cloudflare.com/oauth2/revoke"
ACCOUNTS_URL = "https://api.cloudflare.com/client/v4/accounts"
OAUTH_SCOPES = "browser-rendering.write account-settings.read offline_access"
OAUTH_STORE_FILENAME = "oauth.json"

#: 临期阈值：剩余寿命低于该值即刷新（保活循环与请求路径共用）。
REFRESH_MARGIN_SECONDS = 600
#: 保活循环检查间隔。
KEEPALIVE_INTERVAL_SECONDS = 60
#: 授权码粘贴等待时长。
LOGIN_TIMEOUT_SECONDS = 600


class OAuthError(Exception):
    """OAuth 协议/流程错误；code 对齐上游（invalid_grant、network_error 等）。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or f"OAuth 请求被拒绝 ({code})")
        self.code = code


def oauth_user_message(exc: OAuthError) -> str:
    """把 OAuthError 转成面向用户的中文提示。"""
    if exc.code == "not_logged_in":
        return "尚未登录 Cloudflare：请发送 /cfrun login 完成授权，或在配置中填写 api_token。"
    if exc.code in {"invalid_grant", "unauthorized_client"}:
        return "OAuth 登录已失效（令牌可能被撤销）：请发送 /cfrun login 重新授权。"
    if exc.code == "invalid_state":
        return str(exc)
    return f"授权失败：{exc}"


def generate_pkce() -> tuple[str, str]:
    """生成 PKCE code_verifier 与 S256 code_challenge。"""
    verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    )
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(state: str, code_challenge: str) -> str:
    """构建授权页链接；offline_access 是协议 scope（不在 403 清单），换发 refresh_token 必需。"""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": OAUTH_CLIENT_ID,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "scope": OAUTH_SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{OAUTH_AUTHORIZATION_URL}?{query}"


@dataclass
class OAuthAccount:
    account_id: str
    account_name: str


@dataclass
class OAuthToken:
    """访问令牌及登录时自动发现的账号信息。"""

    access_token: str
    refresh_token: str
    expires_at: float
    scopes: list[str] = field(default_factory=list)
    account_id: str = ""
    account_name: str = ""
    account_count: int = 1

    def is_expired(self, margin: float = 60.0) -> bool:
        return time.time() >= self.expires_at - margin

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scopes": self.scopes,
            "account_id": self.account_id,
            "account_name": self.account_name,
            "account_count": self.account_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OAuthToken | None":
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        expires_at = data.get("expires_at")
        if not access or not refresh or not isinstance(expires_at, (int, float)):
            return None
        scopes = data.get("scopes")
        return cls(
            access_token=str(access),
            refresh_token=str(refresh),
            expires_at=float(expires_at),
            scopes=[str(s) for s in scopes] if isinstance(scopes, list) else [],
            account_id=str(data.get("account_id") or ""),
            account_name=str(data.get("account_name") or ""),
            account_count=int(data.get("account_count") or 1),
        )


def _parse_token_grant(
    body: dict[str, Any], previous_refresh_token: str = ""
) -> OAuthToken:
    """解析 token 端点响应；refresh_token 缺省时继承旧值（轮换语义）。"""
    access_token = str(body.get("access_token") or "").strip()
    refresh_token = (
        str(body.get("refresh_token") or "").strip() or previous_refresh_token
    )
    token_type = str(body.get("token_type") or "").strip().lower()
    expires_in = body.get("expires_in")
    missing = []
    if not access_token:
        missing.append("access_token")
    if token_type != "bearer":
        missing.append(f"token_type={token_type or '空'}")
    if not isinstance(expires_in, (int, float)) or expires_in <= 0:
        missing.append("expires_in")
    if missing:
        raise OAuthError(
            "invalid_token_response",
            f"OAuth 响应缺少必要字段：{'、'.join(missing)}（响应字段：{'、'.join(sorted(body))}）",
        )
    if not refresh_token:
        # 未授予 offline_access 时无 refresh_token：令牌到期后需重新授权
        logger.warning(
            "Cloudflare OAuth 未返回 refresh_token，令牌到期后需重新 /cfrun login"
        )
        refresh_token = previous_refresh_token
    scopes_value = body.get("scope")
    scopes = (
        scopes_value.split()
        if isinstance(scopes_value, str)
        else ([str(s) for s in scopes_value] if isinstance(scopes_value, list) else [])
    )
    return OAuthToken(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=time.time() + float(expires_in),
        scopes=scopes,
    )


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    """原子写入仅属主可读写（0600）的 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        fp.write(json.dumps(payload, ensure_ascii=False, indent=2))
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class TokenStore:
    """OAuth 令牌与账号信息持久化（oauth.json，0600）。"""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> OAuthToken | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return OAuthToken.from_dict(data)

    def save(self, token: OAuthToken) -> None:
        _write_private_json(self._path, token.to_dict())

    def clear(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass


class OAuthClient:
    """Cloudflare OAuth token/revoke/accounts 端点客户端（无 client secret）。"""

    def __init__(self, timeout: float = 30.0, session: Any = None) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> Any:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()

    async def _post_form(self, url: str, values: dict[str, str]) -> dict[str, Any]:
        """POST 表单并解析 JSON；业务 error 字段或非 2xx 抛 OAuthError。"""
        session = await self._get_session()
        try:
            async with session.post(
                url,
                data=values,
                headers={"Accept": "application/json"},
            ) as response:
                text = await response.text()
                status = response.status
        except aiohttp.ClientError as exc:
            raise OAuthError("network_error", f"OAuth 网络请求失败: {exc}") from exc
        try:
            body = json.loads(text) if text else {}
        except ValueError as exc:
            raise OAuthError(
                "invalid_response", f"OAuth 响应不是 JSON (HTTP {status})"
            ) from exc
        if not isinstance(body, dict):
            raise OAuthError("invalid_response", f"OAuth 响应格式异常 (HTTP {status})")
        error = str(body.get("error") or "").strip()
        if error or status >= 400:
            raise OAuthError(
                error or f"http_{status}", str(body.get("error_description") or "")
            )
        return body

    async def exchange_code(self, code: str, code_verifier: str) -> OAuthToken:
        """授权码换令牌（公共客户端，仅 client_id + PKCE）。"""
        body = await self._post_form(
            OAUTH_TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "client_id": OAUTH_CLIENT_ID,
                "code_verifier": code_verifier,
            },
        )
        return _parse_token_grant(body)

    async def refresh(self, refresh_token: str) -> OAuthToken:
        """刷新访问令牌；新响应不含 refresh_token 时沿用旧值。"""
        body = await self._post_form(
            OAUTH_TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OAUTH_CLIENT_ID,
            },
        )
        return _parse_token_grant(body, previous_refresh_token=refresh_token)

    async def revoke(self, refresh_token: str) -> None:
        """撤销 refresh_token；失败不抛错（本地仍会清除）。"""
        try:
            await self._post_form(
                OAUTH_REVOKE_URL,
                {
                    "token": refresh_token,
                    "token_type_hint": "refresh_token",
                    "client_id": OAUTH_CLIENT_ID,
                },
            )
        except OAuthError:
            pass

    async def fetch_accounts(self, access_token: str) -> list[OAuthAccount]:
        """列出用户可见账号（用于自动回填 account_id）。"""
        session = await self._get_session()
        try:
            async with session.get(
                ACCOUNTS_URL,
                params={"per_page": "50"},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            ) as response:
                text = await response.text()
                status = response.status
        except aiohttp.ClientError as exc:
            raise OAuthError("network_error", f"获取账号列表失败: {exc}") from exc
        try:
            body = json.loads(text) if text else {}
        except ValueError as exc:
            raise OAuthError(
                "invalid_response", f"账号列表响应不是 JSON (HTTP {status})"
            ) from exc
        result = body.get("result") if isinstance(body, dict) else None
        if status >= 400 or not isinstance(result, list):
            raise OAuthError(
                "accounts_request_failed", f"获取账号列表失败 (HTTP {status})"
            )
        accounts = []
        for item in result:
            if isinstance(item, dict) and item.get("id"):
                accounts.append(
                    OAuthAccount(
                        account_id=str(item["id"]),
                        account_name=str(item.get("name") or ""),
                    )
                )
        return accounts


class OAuthManager:
    """令牌生命周期：登录流程、临期定时刷新、撤销与清除。"""

    def __init__(self, store: TokenStore, client: OAuthClient) -> None:
        self._store = store
        self._client = client
        self._lock = asyncio.Lock()
        self._keepalive_task: asyncio.Task | None = None
        self._pending: dict[str, str] = {}  # state -> code_verifier

    def load_token(self) -> OAuthToken | None:
        return self._store.load()

    def has_token(self) -> bool:
        return self._store.load() is not None

    def begin_login(self) -> tuple[str, str]:
        """生成授权链接与 state；verifier 留存在 pending 等待回填。"""
        state = secrets.token_hex(16)
        verifier, challenge = generate_pkce()
        self._pending[state] = verifier
        return build_authorize_url(state, challenge), state

    async def complete_login(self, state: str, code: str) -> OAuthToken:
        """用粘贴的授权码完成换发，自动发现账号并启动保活。"""
        verifier = self._pending.pop(state, None)
        if not verifier:
            raise OAuthError(
                "invalid_state", "授权流程状态不匹配或已过期，请重新发送 /cfrun login"
            )
        token = await self._client.exchange_code(code, verifier)
        try:
            accounts = await self._client.fetch_accounts(token.access_token)
        except OAuthError:
            accounts = []
        token.account_count = max(1, len(accounts))
        if accounts:
            token.account_id = accounts[0].account_id
            token.account_name = accounts[0].account_name
        self._store.save(token)
        self.ensure_keepalive()
        return token

    async def get_access_token(self) -> str:
        """返回可用 access_token；临期先刷新。"""
        token = self._store.load()
        if token is None:
            raise OAuthError("not_logged_in")
        if token.is_expired(REFRESH_MARGIN_SECONDS):
            token = await self.refresh_token(token)
        return token.access_token

    async def refresh_token(self, token: OAuthToken | None = None) -> OAuthToken:
        """单飞刷新：并发调用共用一次远端请求。"""
        async with self._lock:
            current = self._store.load()
            if current is None:
                raise OAuthError("not_logged_in")
            if current.is_expired(REFRESH_MARGIN_SECONDS):
                try:
                    refreshed = await self._client.refresh(current.refresh_token)
                except OAuthError:
                    raise
                refreshed.account_id = current.account_id
                refreshed.account_name = current.account_name
                refreshed.account_count = current.account_count
                self._store.save(refreshed)
                current = refreshed
            return current

    def ensure_keepalive(self) -> None:
        """有登录态时启动保活循环（已在跑则忽略）。"""
        if self._keepalive_task is not None and not self._keepalive_task.done():
            return
        if self._store.load() is None:
            return
        self._keepalive_task = asyncio.get_running_loop().create_task(
            self._keepalive_loop()
        )

    async def _keepalive_loop(self) -> None:
        """周期检查临期并提前刷新；失效（invalid_grant）即停止等待重登。"""
        try:
            while True:
                await asyncio.sleep(KEEPALIVE_INTERVAL_SECONDS)
                token = self._store.load()
                if token is None:
                    return
                if not token.is_expired(REFRESH_MARGIN_SECONDS):
                    continue
                try:
                    await self.refresh_token(token)
                except OAuthError as exc:
                    if exc.code in {"invalid_grant", "unauthorized_client"}:
                        return
                    # 网络类临时故障：下一轮重试
        except asyncio.CancelledError:
            pass

    async def logout(self) -> OAuthToken | None:
        """撤销远端授权并清除本地令牌，返回被清除的令牌（未登录返回 None）。"""
        self._stop_keepalive()
        token = self._store.load()
        if token is not None:
            await self._client.revoke(token.refresh_token)
            self._store.clear()
        return token

    def _stop_keepalive(self) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None

    async def close(self) -> None:
        self._stop_keepalive()
        await self._client.close()
