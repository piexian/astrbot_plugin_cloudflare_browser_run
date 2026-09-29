"""状态查询零副作用：不创建 Runtime、不读写文件、不联网。"""

from __future__ import annotations


from conftest import make_config


class _ExplodingGetter:
    """任何获取 Runtime 的尝试都视为副作用失败。"""

    def __call__(self, *args, **kwargs):
        raise AssertionError("状态查询不得创建或获取 CloudflareBrowserRuntime")


def test_status_queries_never_touch_runtime_or_network(fake_http):
    from astrbot_plugin_cloudflare_browser_run.plugin_service import (
        CloudflareBrowserService,
    )

    service = CloudflareBrowserService(make_config(), _ExplodingGetter())
    for _ in range(3):
        status = service.get_status()
        assert status["state"] == "initializing"
        assert service.capabilities()["api_version"] == 1
    assert fake_http.calls == []


def test_status_missing_config_reports_unavailable_without_side_effects(fake_http):
    from astrbot_plugin_cloudflare_browser_run.plugin_service import (
        CloudflareBrowserService,
    )

    service = CloudflareBrowserService(
        make_config(connection_settings={"account_id": "", "api_token": ""}),
        _ExplodingGetter,
    )
    status = service.get_status()
    assert status["state"] == "initializing"
    assert status["ready"] is False
    assert fake_http.calls == []


async def test_main_status_does_not_construct_runtime(
    make_plugin, monkeypatch, fake_http
):
    """状态查询若试图新建 Runtime（会 mkdir）则直接失败。"""
    from astrbot_plugin_cloudflare_browser_run import main as plugin_main

    def _boom(*args, **kwargs):
        raise AssertionError("状态查询不得新建 CloudflareBrowserRuntime")

    plugin = make_plugin()
    monkeypatch.setattr(plugin_main, "CloudflareBrowserRuntime", _boom)
    await plugin.initialize()
    service = plugin.get_service()
    assert service.get_status()["state"] == "ready"
    assert service.capabilities()["features"]
    assert fake_http.calls == []


async def test_main_status_writes_no_files(make_plugin, tmp_path, fake_http):
    plugin = make_plugin()
    await plugin.initialize()
    service = plugin.get_service()
    data_dir = tmp_path / "plugin_data"
    before = sorted(str(path) for path in data_dir.rglob("*"))
    for _ in range(3):
        service.get_status()
        service.capabilities()
    after = sorted(str(path) for path in data_dir.rglob("*"))
    assert before == after
    assert fake_http.calls == []


async def test_closed_service_status_still_local(make_plugin, fake_http):
    plugin = make_plugin()
    service = plugin.get_service()
    await plugin.terminate()
    for _ in range(2):
        status = service.get_status()
        assert status["state"] == "closed"
        service.capabilities()
    assert fake_http.calls == []
