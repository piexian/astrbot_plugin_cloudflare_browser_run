"""SDK 门面：单实例、版本协商、状态机、wait_ready 与关闭语义。"""

from __future__ import annotations

import asyncio

import pytest

from astrbot_plugin_cloudflare_browser_run.plugin_service import (
    PluginServiceError,
)
from conftest import make_config

FEATURES = [
    "web.fetch",
    "browser.markdown",
    "browser.content",
    "browser.links",
    "browser.scrape",
    "browser.json",
    "browser.crawl",
]


def test_get_service_returns_same_instance(make_plugin):
    plugin = make_plugin()
    service = plugin.get_service()
    assert service is plugin.get_service()
    assert service is plugin.get_service(api_version=1)
    assert service.api_version == 1
    assert service.instance_id


def test_get_service_rejects_non_v1(make_plugin):
    plugin = make_plugin()
    for bad in (0, 2, -1, 1.0, True, "1", None):
        with pytest.raises(PluginServiceError) as exc_info:
            plugin.get_service(bad)
        assert exc_info.value.code == "unsupported_version"


def test_status_before_initialize(make_plugin):
    service = make_plugin().get_service()
    status = service.get_status()
    assert status == {
        "api_version": 1,
        "instance_id": service.instance_id,
        "state": "initializing",
        "ready": False,
        "reason": None,
    }


async def test_initialize_ready_and_capabilities(make_plugin):
    plugin = make_plugin()
    service = plugin.get_service()
    await plugin.initialize()
    status = service.get_status()
    assert status["state"] == "ready"
    assert status["ready"] is True
    assert status["reason"] is None
    assert status["instance_id"] == service.instance_id
    assert service.capabilities() == {"api_version": 1, "features": FEATURES}


async def test_unavailable_when_credentials_missing(make_plugin):
    plugin = make_plugin(
        make_config(connection_settings={"account_id": "", "api_token": ""})
    )
    service = plugin.get_service()
    await plugin.initialize()
    status = service.get_status()
    assert status["state"] == "unavailable"
    assert status["ready"] is False
    assert status["reason"] == "not_configured"


async def test_unavailable_only_when_token_missing(make_plugin):
    plugin = make_plugin(
        make_config(connection_settings={"account_id": "acct", "api_token": ""})
    )
    service = plugin.get_service()
    await plugin.initialize()
    status = service.get_status()
    assert status["state"] == "unavailable"
    assert status["reason"] == "not_configured"


async def test_wait_ready_success_and_timeout(make_plugin):
    plugin = make_plugin()
    service = plugin.get_service()
    with pytest.raises(TimeoutError):
        await service.wait_ready(timeout=0.15)
    await plugin.initialize()
    status = await service.wait_ready(timeout=1)
    assert status["ready"] is True
    assert status["state"] == "ready"


async def test_wait_ready_rejects_closed(make_plugin):
    plugin = make_plugin()
    service = plugin.get_service()
    await plugin.initialize()
    await plugin.terminate()
    with pytest.raises(PluginServiceError) as exc_info:
        await service.wait_ready(timeout=1)
    assert exc_info.value.code == "service_closed"


async def test_wait_ready_detects_close_while_waiting(make_plugin):
    plugin = make_plugin()
    service = plugin.get_service()

    async def close_later():
        await asyncio.sleep(0.05)
        await plugin.terminate()

    task = asyncio.create_task(close_later())
    with pytest.raises(PluginServiceError) as exc_info:
        await service.wait_ready(timeout=2)
    assert exc_info.value.code == "service_closed"
    await task


async def test_terminate_rejects_new_business_calls(make_plugin, fake_http):
    plugin = make_plugin()
    service = plugin.get_service()
    await plugin.initialize()
    await plugin.terminate()

    calls = [
        lambda: service.fetch("https://example.com"),
        lambda: service.markdown(url="https://example.com"),
        lambda: service.content(url="https://example.com"),
        lambda: service.links(url="https://example.com"),
        lambda: service.scrape(
            url="https://example.com", elements=[{"selector": "h1"}]
        ),
        lambda: service.json(url="https://example.com"),
        lambda: service.crawl_start("https://example.com"),
        lambda: service.crawl_status("job-ok"),
        lambda: service.crawl_cancel("job-ok"),
    ]
    for call in calls:
        with pytest.raises(PluginServiceError) as exc_info:
            await call()
        assert exc_info.value.code == "service_closed"
    assert fake_http.calls == []

    # 关闭后状态仍可查询，能力声明仍可读取。
    status = service.get_status()
    assert status["state"] == "closed"
    assert status["reason"] == "service_closed"
    assert service.capabilities()["api_version"] == 1


async def test_get_service_after_terminate_returns_same_closed_instance(make_plugin):
    plugin = make_plugin()
    service = plugin.get_service()
    await plugin.terminate()
    assert plugin.get_service() is service
    assert plugin.get_service().get_status()["state"] == "closed"


async def test_reload_creates_new_service_old_stays_closed(make_plugin):
    old = make_plugin()
    await old.initialize()
    old_service = old.get_service()
    await old.terminate()

    new = make_plugin()
    new_service = new.get_service()
    assert new_service is not old_service
    assert new_service.instance_id != old_service.instance_id
    assert old_service.get_status()["state"] == "closed"
    with pytest.raises(PluginServiceError):
        await old_service.fetch("https://example.com")

    await new.initialize()
    assert new_service.get_status()["ready"] is True
