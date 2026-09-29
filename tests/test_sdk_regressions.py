"""Verify readiness gates and Tool/SDK request compatibility."""

import pytest

from astrbot_plugin_cloudflare_browser_run.plugin_service import PluginServiceError
from astrbot_plugin_cloudflare_browser_run.tools import cloudflare_browser as cf
from conftest import make_config


BUSINESS_CALLS = [
    ("fetch", {"url": "https://example.com"}),
    ("markdown", {"url": "https://example.com"}),
    ("content", {"url": "https://example.com"}),
    ("links", {"url": "https://example.com"}),
    ("scrape", {"url": "https://example.com", "elements": [{"selector": "h1"}]}),
    ("json", {"url": "https://example.com", "prompt": "Title"}),
    ("crawl_start", {"url": "https://example.com"}),
    ("crawl_status", {"job_id": "job-1"}),
    ("crawl_cancel", {"job_id": "job-1"}),
]


@pytest.mark.parametrize("method,kwargs", BUSINESS_CALLS)
@pytest.mark.parametrize("initialized", [False, True])
async def test_unready_business_does_not_acquire_runtime(
    make_plugin, fake_http, monkeypatch, method, kwargs, initialized
):
    config = make_config()
    if initialized:
        config["connection_settings"]["api_token"] = ""
    plugin = make_plugin(config)
    if initialized:
        await plugin.initialize()
    service = plugin.get_service()

    def unexpected_runtime():
        raise AssertionError("Unready service acquired its runtime")

    monkeypatch.setattr(service, "_runtime_getter", unexpected_runtime)
    with pytest.raises(PluginServiceError) as error:
        await getattr(service, method)(**kwargs)
    assert error.value.code == "not_ready"
    assert fake_http.calls == []


@pytest.mark.parametrize(
    "method,extras",
    [
        ("markdown", {}),
        ("content", {}),
        ("links", {}),
        ("scrape", {"elements": [{"selector": "h1"}]}),
        ("json", {"prompt": "Title"}),
    ],
)
@pytest.mark.parametrize(
    "page", [{"url": "https://example.com"}, {"html": "<h1>Title</h1>"}]
)
async def test_tool_and_sdk_omit_absent_page_source(
    make_plugin, fake_http, method, extras, page
):
    plugin = make_plugin()
    await plugin.initialize()
    service = plugin.get_service()
    tool = next(
        tool
        for tool in plugin.context.added_tools
        if tool.name == f"cf_browser_{method}"
    )
    fake_http.enqueue(payload={"result": "page result"})
    fake_http.enqueue(payload={"result": "page result"})
    await tool.call(None, **page, **extras)
    await getattr(service, method)(**page, **extras)
    assert len(fake_http.calls) == 2
    for request in fake_http.calls:
        assert request["json"] == {**page, **extras}


@pytest.mark.parametrize(
    "tool_cls", [cf.CloudflareCrawlStatusTool, cf.CloudflareCrawlCancelTool]
)
@pytest.mark.parametrize("kwargs", [{}, {"cache_ttl": 5}])
async def test_crawl_tool_missing_id_stays_a_local_error(
    make_runtime, fake_http, tool_cls, kwargs
):
    tool = tool_cls(runtime=make_runtime())
    assert await tool.call(None, **kwargs) == "错误：必须提供 job_id。"
    assert fake_http.calls == []


@pytest.mark.parametrize("method", ["crawl_status", "crawl_cancel"])
async def test_crawl_rejects_current_directory_path(make_runtime, fake_http, method):
    with pytest.raises(cf.CloudflareParamError):
        await getattr(make_runtime(), method)(".")
    assert fake_http.calls == []


@pytest.mark.parametrize(
    "state", ["missing", "disabled", "no_instance", "old_api", "ready"]
)
async def test_readme_discovery_uses_native_registry(make_plugin, monkeypatch, state):
    import ast
    import importlib
    import re
    from pathlib import Path
    from types import SimpleNamespace

    from astrbot.api.star import Context
    from astrbot.core.star.star import StarMetadata

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    block = next(
        block
        for block in re.findall(r"```python\n(.*?)```", readme, re.S)
        if "def get_browser_service(" in block
    )
    helper = next(
        node for node in ast.parse(block).body if isinstance(node, ast.FunctionDef)
    )
    namespace = {}
    exec(
        compile(ast.Module(body=[helper], type_ignores=[]), "README.md", "exec"),
        namespace,
    )

    plugin = make_plugin()
    await plugin.initialize()
    metadata = StarMetadata(
        name="astrbot_plugin_cloudflare_browser_run", star_cls=plugin
    )
    if state == "disabled":
        metadata.activated = False
    elif state == "no_instance":
        metadata.star_cls = None
    elif state == "old_api":
        metadata.star_cls = SimpleNamespace(get_service=None)
    context_module = importlib.import_module("astrbot.core.star.context")
    monkeypatch.setattr(
        context_module, "star_registry", [] if state == "missing" else [metadata]
    )
    context = object.__new__(Context)
    if state == "ready":
        service = namespace["get_browser_service"](context)
        assert service is plugin.get_service()
        assert service.get_status()["ready"] is True
        await plugin.terminate()
        assert service.get_status()["state"] == "closed"
    else:
        with pytest.raises(RuntimeError):
            namespace["get_browser_service"](context)
