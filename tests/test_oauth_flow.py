"""OAuth 模块：PKCE、令牌换发/继承、存储、单飞刷新、保活循环与登录流程。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time

import pytest

from astrbot_plugin_cloudflare_browser_run.tools.oauth import (
    LOGIN_TIMEOUT_SECONDS,
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
    OAUTH_SCOPES,
    REFRESH_MARGIN_SECONDS,
    OAuthClient,
    OAuthError,
    OAuthManager,
    OAuthToken,
    TokenStore,
    build_authorize_url,
    generate_pkce,
    oauth_user_message,
)


class _FakeResponse:
    def __init__(self, status: int, payload: dict | str) -> None:
        self.status = status
        self._payload = payload

    async def text(self) -> str:
        return (
            self._payload
            if isinstance(self._payload, str)
            else json.dumps(self._payload)
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """aiohttp 会话替身：按 URL 前缀返回预设响应并记录调用。"""

    def __init__(self, responses: list[tuple[str, int, dict]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def _pop(self, url: str) -> tuple[int, dict]:
        assert self.responses, f"意外的请求：{url}"
        for index, (prefix, status, payload) in enumerate(self.responses):
            if prefix in url:
                return self.responses.pop(index)[1:]
        raise AssertionError(f"意外的请求：{url}")

    def post(self, url: str, data=None, headers=None):
        self.calls.append(("POST", dict(data or {})))
        status, payload = self._pop(url)
        return _FakeResponse(status, payload)

    def get(self, url: str, params=None, headers=None):
        self.calls.append(("GET", dict(params or {})))
        status, payload = self._pop(url)
        return _FakeResponse(status, payload)


class _TimeoutSession:
    def post(self, *args, **kwargs):
        raise asyncio.TimeoutError("post timeout")

    def get(self, *args, **kwargs):
        raise asyncio.TimeoutError("get timeout")

def make_client(responses: list[tuple[str, int, dict]]) -> OAuthClient:
    return OAuthClient(session=FakeSession(responses))


def grant_body(expires_in: int = 3600, with_refresh: bool = True) -> dict:
    body = {"access_token": "at-new", "token_type": "bearer", "expires_in": expires_in}
    if with_refresh:
        body["refresh_token"] = "rt-new"
    return body


def make_token(**overrides) -> OAuthToken:
    fields = {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "expires_at": time.time() + 7200,
    }
    fields.update(overrides)
    return OAuthToken(**fields)


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = generate_pkce()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert challenge == expected
    assert 43 <= len(verifier) <= 128


def test_authorize_url_contains_required_params():
    url = build_authorize_url("state-1", "challenge-1")
    assert "response_type=code" in url
    assert "code_challenge_method=S256" in url
    assert "state=state-1" in url
    assert "code_challenge=challenge-1" in url
    assert "browser-rendering.write" in url
    assert "account-settings.read" in url
    assert "offline_access" in url


async def test_exchange_code_sends_pkce_params():
    client = make_client([("oauth2/token", 200, grant_body())])
    token = await client.exchange_code("code-1", "verifier-1")
    assert token.access_token == "at-new"
    assert token.refresh_token == "rt-new"
    post_calls = [c for c in client._session.calls if c[0] == "POST"]
    assert len(post_calls) == 1
    values = post_calls[0][1]
    assert values["grant_type"] == "authorization_code"
    assert values["code"] == "code-1"
    assert values["code_verifier"] == "verifier-1"
    assert values["client_id"] == OAUTH_CLIENT_ID
    assert values["redirect_uri"] == OAUTH_REDIRECT_URI


async def test_timeout_errors_map_to_network_error():
    client = OAuthClient(session=_TimeoutSession())

    with pytest.raises(OAuthError) as exc_info:
        await client.exchange_code("code-1", "verifier-1")
    assert exc_info.value.code == "network_error"

    with pytest.raises(OAuthError) as exc_info:
        await client.fetch_accounts("access-token")
    assert exc_info.value.code == "network_error"


async def test_exchange_tolerates_missing_refresh_token():
    """未授予 offline_access 的客户端无 refresh_token：登录仍成功，降级为一次性令牌。"""
    body = grant_body(with_refresh=False)
    client = make_client([("oauth2/token", 200, body)])
    token = await client.exchange_code("code-1", "verifier-1")
    await client.close()
    assert token.access_token == "at-new"
    assert token.refresh_token == ""


async def test_refresh_inherits_refresh_token_when_absent(tmp_path):
    store = TokenStore(tmp_path / "oauth.json")
    store.save(make_token(expires_at=time.time() + 1))
    client = make_client([("oauth2/token", 200, grant_body(with_refresh=False))])
    manager = OAuthManager(store, client)
    refreshed = await manager.refresh_token()
    await client.close()
    assert refreshed.access_token == "at-new"
    assert refreshed.refresh_token == "rt-1"
    assert store.load().refresh_token == "rt-1"


async def test_token_store_roundtrip_and_permissions(tmp_path):
    store = TokenStore(tmp_path / "sub" / "oauth.json")
    store.save(make_token(account_id="acct-1", account_name="acct"))
    loaded = store.load()
    assert loaded.account_id == "acct-1"
    assert (tmp_path / "sub" / "oauth.json").stat().st_mode & 0o777 == 0o600
    store.clear()
    assert store.load() is None


def test_token_store_rejects_malformed_account_count(tmp_path):
    path = tmp_path / "oauth.json"
    payload = make_token().to_dict()
    payload["account_count"] = "not-a-number"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert TokenStore(path).load() is None

async def test_complete_login_fills_account_and_starts_keepalive(tmp_path):
    store = TokenStore(tmp_path / "oauth.json")
    client = make_client(
        [
            ("oauth2/token", 200, grant_body()),
            (
                "client/v4/accounts",
                200,
                {"result": [{"id": "acct-9", "name": "我的账号"}]},
            ),
        ]
    )
    manager = OAuthManager(store, client)
    url, state = manager.begin_login()
    assert state in manager._pending
    token = await manager.complete_login(state, "code-1")
    assert token.account_id == "acct-9"
    assert token.account_name == "我的账号"
    assert manager._keepalive_task is not None
    await manager.close()
    assert store.load().account_id == "acct-9"


async def test_complete_login_rejects_unknown_state(tmp_path):
    manager = OAuthManager(TokenStore(tmp_path / "oauth.json"), make_client([]))
    manager.begin_login()
    with pytest.raises(OAuthError) as exc_info:
        await manager.complete_login("wrong-state", "code-1")
    assert exc_info.value.code == "invalid_state"
    await manager.close()


async def test_discovery_failure_does_not_fail_login(tmp_path):
    store = TokenStore(tmp_path / "oauth.json")
    client = make_client(
        [
            ("oauth2/token", 200, grant_body()),
            ("client/v4/accounts", 500, {"error": "boom"}),
        ]
    )
    manager = OAuthManager(store, client)
    _, state = manager.begin_login()
    token = await manager.complete_login(state, "code-1")
    await manager.close()
    assert token.access_token == "at-new"
    assert token.account_id == ""
    assert manager.load_token() is not None


async def test_get_access_token_refreshes_near_expiry(tmp_path):
    store = TokenStore(tmp_path / "oauth.json")
    store.save(make_token(expires_at=time.time() + REFRESH_MARGIN_SECONDS - 10))
    client = make_client([("oauth2/token", 200, grant_body())])
    manager = OAuthManager(store, client)
    bearer = await manager.get_access_token()
    await client.close()
    assert bearer == "at-new"


async def test_get_access_token_single_flight(tmp_path):
    store = TokenStore(tmp_path / "oauth.json")
    store.save(make_token(expires_at=time.time() + 1))
    client = make_client([("oauth2/token", 200, grant_body())])
    manager = OAuthManager(store, client)
    results = await asyncio.gather(
        manager.get_access_token(),
        manager.get_access_token(),
        manager.get_access_token(),
    )
    await manager.close()
    assert results == ["at-new", "at-new", "at-new"]
    post_calls = [c for c in client._session.calls if c[0] == "POST"]
    assert len(post_calls) == 1


async def test_invalid_grant_maps_to_relogin_message(tmp_path):
    store = TokenStore(tmp_path / "oauth.json")
    store.save(make_token(expires_at=time.time() - 10))
    client = make_client([("oauth2/token", 400, {"error": "invalid_grant"})])
    manager = OAuthManager(store, client)
    with pytest.raises(OAuthError) as exc_info:
        await manager.get_access_token()
    await client.close()
    assert "重新授权" in oauth_user_message(exc_info.value)
    assert "/cfrun login" in oauth_user_message(exc_info.value)


async def test_logout_revokes_and_clears(tmp_path):
    store = TokenStore(tmp_path / "oauth.json")
    store.save(make_token())
    client = make_client([("oauth2/revoke", 200, {})])
    manager = OAuthManager(store, client)
    token = await manager.logout()
    await client.close()
    assert token is not None
    assert store.load() is None
    revoke_calls = [c for c in client._session.calls if c[0] == "POST"]
    assert len(revoke_calls) == 1


async def test_logout_without_token_is_noop(tmp_path):
    client = make_client([])
    manager = OAuthManager(TokenStore(tmp_path / "oauth.json"), client)
    assert await manager.logout() is None
    await client.close()


def test_login_timeout_constant_is_ten_minutes():
    assert LOGIN_TIMEOUT_SECONDS == 600
    assert (
        OAUTH_SCOPES == "browser-rendering.write account-settings.read offline_access"
    )
