"""Cloudflare Browser Rendering / Browser Run 工具实现。"""

from __future__ import annotations

import json
import re
from dataclasses import field
from typing import Any

import aiohttp
from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

PLUGIN_NAME = "astrbot_plugin_cloudflare_browser_run"
API_BASE = "https://api.cloudflare.com/client/v4"

TOOL_NAMES = (
    "cf_browser_markdown",
    "cf_browser_content",
    "cf_browser_links",
    "cf_browser_scrape",
    "cf_browser_json",
    "cf_browser_crawl_start",
    "cf_browser_crawl_status",
    "cf_browser_crawl_cancel",
)

CONFIG_PATHS = {
    "account_id": ("connection_settings", "account_id"),
    "api_token": ("connection_settings", "api_token"),
    "timeout_seconds": ("request_settings", "timeout_seconds"),
    "default_cache_ttl": ("request_settings", "default_cache_ttl"),
    "default_render": ("request_settings", "default_render"),
    "max_crawl_limit": ("request_settings", "max_crawl_limit"),
    "max_output_chars": ("output_settings", "max_output_chars"),
    "enable_markdown": ("tool_settings", "enable_markdown"),
    "enable_content": ("tool_settings", "enable_content"),
    "enable_links": ("tool_settings", "enable_links"),
    "enable_scrape": ("tool_settings", "enable_scrape"),
    "enable_json": ("tool_settings", "enable_json"),
    "enable_crawl_start": ("tool_settings", "enable_crawl_start"),
    "enable_crawl_status": ("tool_settings", "enable_crawl_status"),
    "enable_crawl_cancel": ("tool_settings", "enable_crawl_cancel"),
}

CONFIG_DEFAULTS = {
    "account_id": "",
    "api_token": "",
    "timeout_seconds": 120,
    "default_cache_ttl": 5,
    "default_render": False,
    "max_crawl_limit": 100,
    "max_output_chars": 12000,
    "enable_markdown": True,
    "enable_content": True,
    "enable_links": True,
    "enable_scrape": True,
    "enable_json": True,
    "enable_crawl_start": True,
    "enable_crawl_status": True,
    "enable_crawl_cancel": True,
}

RESOURCE_TYPES = [
    "document",
    "stylesheet",
    "image",
    "media",
    "font",
    "script",
    "texttrack",
    "xhr",
    "fetch",
    "prefetch",
    "eventsource",
    "websocket",
    "manifest",
    "signedexchange",
    "ping",
    "cspviolationreport",
    "preflight",
    "other",
]
WAIT_UNTIL = ["load", "domcontentloaded", "networkidle0", "networkidle2"]
LINK_SOURCES = ["all", "sitemaps", "links"]
CRAWL_FORMATS = ["html", "markdown", "json"]
CRAWL_PURPOSES = ["search", "ai-input", "ai-train"]
CRAWL_STATUSES = [
    "queued",
    "errored",
    "completed",
    "disallowed",
    "skipped",
    "cancelled",
]

CF_KEY_MAP = {
    "action_timeout": "actionTimeout",
    "add_script_tag": "addScriptTag",
    "add_style_tag": "addStyleTag",
    "allow_request_pattern": "allowRequestPattern",
    "allow_resource_types": "allowResourceTypes",
    "best_attempt": "bestAttempt",
    "cache_ttl": "cacheTTL",
    "crawl_purposes": "crawlPurposes",
    "default_cache_ttl": "defaultCacheTTL",
    "device_scale_factor": "deviceScaleFactor",
    "emulate_media_type": "emulateMediaType",
    "exclude_external_links": "excludeExternalLinks",
    "exclude_patterns": "excludePatterns",
    "full_page": "fullPage",
    "goto_options": "gotoOptions",
    "has_touch": "hasTouch",
    "http_only": "httpOnly",
    "include_external_links": "includeExternalLinks",
    "include_patterns": "includePatterns",
    "include_subdomains": "includeSubdomains",
    "is_landscape": "isLandscape",
    "is_mobile": "isMobile",
    "json_options": "jsonOptions",
    "max_age": "maxAge",
    "modified_since": "modifiedSince",
    "reject_request_pattern": "rejectRequestPattern",
    "reject_resource_types": "rejectResourceTypes",
    "referrer_policy": "referrerPolicy",
    "same_party": "sameParty",
    "same_site": "sameSite",
    "set_extra_http_headers": "setExtraHTTPHeaders",
    "set_javascript_enabled": "setJavaScriptEnabled",
    "source_port": "sourcePort",
    "source_scheme": "sourceScheme",
    "user_agent": "userAgent",
    "visible_links_only": "visibleLinksOnly",
    "wait_for_selector": "waitForSelector",
    "wait_for_timeout": "waitForTimeout",
    "wait_until": "waitUntil",
}
KEEP_SNAKE_KEYS = {"custom_ai", "response_format", "json_schema"}
SENSITIVE_KEYS = {
    "api_token",
    "authorization",
    "authenticate",
    "password",
    "cookies",
    "cookie",
    "setExtraHTTPHeaders",
    "set_extra_http_headers",
    "headers",
}


class CloudflareAPIError(Exception):
    """Cloudflare API 请求失败。"""


def _is_present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _cfg(config: dict[str, Any], key: str) -> Any:
    path = CONFIG_PATHS[key]
    section = config.get(path[0], {})
    if isinstance(section, dict) and path[1] in section:
        return section[path[1]]
    return config.get(key, CONFIG_DEFAULTS[key])


def _to_int(
    value: Any, default: int, minimum: int | None = None, maximum: int | None = None
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _cf_key(key: str) -> str:
    if key in KEEP_SNAKE_KEYS:
        return key
    return CF_KEY_MAP.get(key, key)


def _to_cf_keys(value: Any) -> Any:
    if isinstance(value, list):
        return [_to_cf_keys(item) for item in value]
    if not isinstance(value, dict):
        return value
    converted: dict[str, Any] = {}
    for key, item in value.items():
        if not _is_present(item) and item is not False and item != 0:
            continue
        converted[_cf_key(str(key))] = _to_cf_keys(item)
    return converted


def _redact(value: Any) -> Any:
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key) in SENSITIVE_KEYS:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact(item)
        return redacted
    return value


def _redact_text(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***REDACTED***")
    redacted = re.sub(
        r"Bearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer ***REDACTED***",
        redacted,
        flags=re.IGNORECASE,
    )
    return redacted


def _collect_sensitive_values(value: Any, parent_key: str = "") -> list[str]:
    values: list[str] = []
    if isinstance(value, list):
        for item in value:
            values.extend(_collect_sensitive_values(item, parent_key))
        return values
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text in SENSITIVE_KEYS or parent_key in SENSITIVE_KEYS:
                values.extend(_string_values(item))
            else:
                values.extend(_collect_sensitive_values(item, key_text))
        return values
    return values


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_string_values(item))
        return result
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_string_values(item))
        return result
    return []


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _tool_payload(payload: dict[str, Any], max_chars: int) -> str:
    max_chars = max(1000, int(max_chars or CONFIG_DEFAULTS["max_output_chars"]))
    dumped = _json_dumps(payload)
    if len(dumped) <= max_chars:
        return dumped
    preview_limit = max(200, max_chars - 180)
    compact = {
        "truncated": True,
        "type": payload.get("type"),
        "url": payload.get("url"),
        "preview": dumped[:preview_limit],
    }
    return _json_dumps(compact)


def _validate_values(values: list[str], allowed: list[str], name: str) -> str | None:
    invalid = [value for value in values if value not in allowed]
    if invalid:
        return f"错误：{name} 包含不支持的值：{', '.join(invalid)}"
    return None


def _parse_jsonish(value: Any, name: str) -> tuple[Any | None, str | None]:
    if value is None or value == "":
        return None, None
    if isinstance(value, (dict, list)):
        return value, None
    if isinstance(value, str):
        try:
            return json.loads(value), None
        except json.JSONDecodeError as exc:
            return None, f"错误：{name} 必须是合法 JSON：{exc}"
    return None, f"错误：{name} 必须是对象、数组或 JSON 字符串。"


def _list_from_value(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]


def _common_properties() -> dict[str, Any]:
    resource_schema = {
        "type": "array",
        "items": {"type": "string", "enum": RESOURCE_TYPES},
    }
    return {
        "url": {"type": "string", "description": "URL to navigate to."},
        "html": {
            "type": "string",
            "description": "Raw HTML to render instead of a URL.",
        },
        "cache_ttl": {
            "type": "integer",
            "description": "Cloudflare cache TTL in seconds. Default is plugin default.",
            "minimum": 0,
            "maximum": 86400,
        },
        "goto_options": {
            "type": "object",
            "description": "Navigation options.",
            "properties": {
                "wait_until": {"type": "string", "enum": WAIT_UNTIL},
                "timeout": {"type": "integer", "minimum": 0, "maximum": 60000},
                "referer": {"type": "string"},
                "referrer_policy": {"type": "string"},
            },
        },
        "wait_for_selector": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "visible": {"type": "boolean"},
                "hidden": {"type": "boolean"},
                "timeout": {"type": "integer", "minimum": 0},
            },
        },
        "wait_for_timeout": {"type": "integer", "minimum": 0, "maximum": 120000},
        "viewport": {
            "type": "object",
            "properties": {
                "width": {"type": "integer", "minimum": 1},
                "height": {"type": "integer", "minimum": 1},
                "device_scale_factor": {"type": "number"},
                "is_mobile": {"type": "boolean"},
                "has_touch": {"type": "boolean"},
                "is_landscape": {"type": "boolean"},
            },
        },
        "action_timeout": {"type": "integer", "minimum": 0, "maximum": 120000},
        "best_attempt": {"type": "boolean"},
        "set_javascript_enabled": {"type": "boolean"},
        "user_agent": {"type": "string"},
        "emulate_media_type": {"type": "string"},
        "allow_resource_types": resource_schema,
        "reject_resource_types": resource_schema,
        "allow_request_pattern": {"type": "array", "items": {"type": "string"}},
        "reject_request_pattern": {"type": "array", "items": {"type": "string"}},
        "set_extra_http_headers": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Extra headers sent while loading the target page.",
        },
        "authenticate": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "password": {"type": "string"},
            },
            "required": ["username", "password"],
        },
        "cookies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "string"},
                    "domain": {"type": "string"},
                    "path": {"type": "string"},
                    "url": {"type": "string"},
                    "expires": {"type": "number"},
                    "http_only": {"type": "boolean"},
                    "secure": {"type": "boolean"},
                    "same_site": {"type": "string", "enum": ["Strict", "Lax", "None"]},
                    "priority": {"type": "string", "enum": ["Low", "Medium", "High"]},
                    "partition_key": {"type": "string"},
                    "same_party": {"type": "boolean"},
                    "source_port": {"type": "number"},
                    "source_scheme": {
                        "type": "string",
                        "enum": ["Unset", "NonSecure", "Secure"],
                    },
                },
                "required": ["name", "value"],
            },
        },
        "add_script_tag": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "content": {"type": "string"},
                    "id": {"type": "string"},
                    "type": {"type": "string"},
                },
            },
        },
        "add_style_tag": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
    }


def _page_parameters(
    extra: dict[str, Any] | None = None, required: list[str] | None = None
) -> dict[str, Any]:
    properties = _common_properties()
    if extra:
        properties.update(extra)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "anyOf": [{"required": ["url"]}, {"required": ["html"]}],
    }
    if required:
        schema["required"] = required
    return schema


class CloudflareBrowserRuntime:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def cfg(self, key: str) -> Any:
        return _cfg(self.config, key)

    @property
    def max_output_chars(self) -> int:
        return _to_int(
            self.cfg("max_output_chars"), CONFIG_DEFAULTS["max_output_chars"], 1000
        )

    @property
    def default_cache_ttl(self) -> int:
        return _to_int(
            self.cfg("default_cache_ttl"),
            CONFIG_DEFAULTS["default_cache_ttl"],
            0,
            86400,
        )

    def validate_credentials(self) -> str | None:
        if not str(self.cfg("account_id") or "").strip():
            return "错误：未配置 Cloudflare account_id。"
        if not str(self.cfg("api_token") or "").strip():
            return "错误：未配置 Cloudflare api_token。"
        return None

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        credential_error = self.validate_credentials()
        if credential_error:
            raise CloudflareAPIError(credential_error)

        account_id = str(self.cfg("account_id")).strip()
        api_token = str(self.cfg("api_token")).strip()
        url = (
            f"{API_BASE}/accounts/{account_id}/browser-rendering/{endpoint.lstrip('/')}"
        )
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        secrets = [api_token]
        if body:
            secrets.extend(_collect_sensitive_values(body))
        timeout_seconds = _to_int(
            self.cfg("timeout_seconds"),
            CONFIG_DEFAULTS["timeout_seconds"],
            1,
        )

        try:
            async with aiohttp.ClientSession(
                trust_env=True,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as session:
                async with session.request(
                    method,
                    url,
                    params=params,
                    json=body,
                    headers=headers,
                ) as response:
                    text = await response.text()
                    try:
                        data = json.loads(text) if text else {}
                    except json.JSONDecodeError:
                        data = {"raw": text}

                    if response.status >= 400:
                        raise CloudflareAPIError(
                            self._format_error(response.status, data, text, secrets)
                        )
                    if isinstance(data, dict) and data.get("success") is False:
                        raise CloudflareAPIError(
                            self._format_error(response.status, data, text, secrets)
                        )
                    if isinstance(data, dict) and "result" in data:
                        return data["result"]
                    return data
        except CloudflareAPIError:
            raise
        except Exception as exc:
            raise CloudflareAPIError(
                _redact_text(
                    f"错误：Cloudflare Browser Rendering 请求失败：{exc}", secrets
                )
            ) from exc

    def _format_error(
        self, status: int, data: Any, raw_text: str, secrets: list[str]
    ) -> str:
        errors = data.get("errors") if isinstance(data, dict) else None
        if errors:
            safe_errors = _redact(errors)
            message = _json_dumps(safe_errors)
        else:
            message = raw_text or _json_dumps(data)
        return _redact_text(
            f"错误：Cloudflare Browser Rendering API 返回 HTTP {status}：{message}",
            secrets,
        )


def _query_params(
    runtime: CloudflareBrowserRuntime, kwargs: dict[str, Any]
) -> dict[str, Any]:
    cache_ttl = kwargs.get("cache_ttl", runtime.default_cache_ttl)
    return {"cacheTTL": _to_int(cache_ttl, runtime.default_cache_ttl, 0, 86400)}


def _page_body(
    kwargs: dict[str, Any], extra_fields: list[str] | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    if not _is_present(kwargs.get("url")) and not _is_present(kwargs.get("html")):
        return None, "错误：必须提供 url 或 html。"

    fields = [
        "url",
        "html",
        "goto_options",
        "wait_for_selector",
        "wait_for_timeout",
        "viewport",
        "action_timeout",
        "best_attempt",
        "set_javascript_enabled",
        "user_agent",
        "emulate_media_type",
        "allow_resource_types",
        "reject_resource_types",
        "allow_request_pattern",
        "reject_request_pattern",
        "set_extra_http_headers",
        "authenticate",
        "cookies",
        "add_script_tag",
        "add_style_tag",
    ]
    if extra_fields:
        fields.extend(extra_fields)

    body = {
        field_name: kwargs.get(field_name)
        for field_name in fields
        if field_name in kwargs
    }
    error = _validate_body_enums(body)
    if error:
        return None, error
    return _to_cf_keys(body), None


def _validate_body_enums(body: dict[str, Any]) -> str | None:
    for field_name in ("allow_resource_types", "reject_resource_types"):
        values = [str(value) for value in _list_from_value(body.get(field_name))]
        error = _validate_values(values, RESOURCE_TYPES, field_name)
        if error:
            return error
    goto_options = body.get("goto_options")
    if isinstance(goto_options, dict) and _is_present(goto_options.get("wait_until")):
        values = _list_from_value(goto_options.get("wait_until"))
        error = _validate_values(
            [str(value) for value in values], WAIT_UNTIL, "goto_options.wait_until"
        )
        if error:
            return error
    return None


async def _call_page_endpoint(
    runtime: CloudflareBrowserRuntime,
    endpoint: str,
    result_type: str,
    kwargs: dict[str, Any],
    extra_fields: list[str] | None = None,
) -> str:
    body, error = _page_body(kwargs, extra_fields)
    if error:
        return error
    assert body is not None
    try:
        result = await runtime.request(
            "POST",
            endpoint,
            params=_query_params(runtime, kwargs),
            body=body,
        )
    except CloudflareAPIError as exc:
        return str(exc)
    return _tool_payload(
        {
            "type": result_type,
            "url": kwargs.get("url"),
            "result": result,
        },
        runtime.max_output_chars,
    )


@dataclass(config={"arbitrary_types_allowed": True})
class _CloudflareTool(FunctionTool[AstrAgentContext]):
    runtime: CloudflareBrowserRuntime | None = field(default=None, repr=False)

    def _runtime(self) -> CloudflareBrowserRuntime:
        if self.runtime is None:
            raise RuntimeError("工具运行时尚未初始化。")
        return self.runtime


@dataclass(config={"arbitrary_types_allowed": True})
class CloudflareMarkdownTool(_CloudflareTool):
    name: str = "cf_browser_markdown"
    description: str = (
        "Fetch a URL or HTML through Cloudflare Browser Rendering and return Markdown."
    )
    parameters: dict = Field(default_factory=_page_parameters)

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        return await _call_page_endpoint(
            self._runtime(), "markdown", "markdown", kwargs
        )


@dataclass(config={"arbitrary_types_allowed": True})
class CloudflareContentTool(_CloudflareTool):
    name: str = "cf_browser_content"
    description: str = "Fetch a URL or HTML through Cloudflare Browser Rendering and return rendered HTML content."
    parameters: dict = Field(default_factory=_page_parameters)

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        return await _call_page_endpoint(self._runtime(), "content", "content", kwargs)


@dataclass(config={"arbitrary_types_allowed": True})
class CloudflareLinksTool(_CloudflareTool):
    name: str = "cf_browser_links"
    description: str = (
        "Extract links from a URL or HTML through Cloudflare Browser Rendering."
    )
    parameters: dict = Field(
        default_factory=lambda: _page_parameters(
            {
                "exclude_external_links": {"type": "boolean", "default": False},
                "visible_links_only": {"type": "boolean", "default": False},
            }
        )
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        return await _call_page_endpoint(
            self._runtime(),
            "links",
            "links",
            kwargs,
            ["exclude_external_links", "visible_links_only"],
        )


@dataclass(config={"arbitrary_types_allowed": True})
class CloudflareScrapeTool(_CloudflareTool):
    name: str = "cf_browser_scrape"
    description: str = "Scrape specific page elements using CSS selectors through Cloudflare Browser Rendering."
    parameters: dict = Field(
        default_factory=lambda: _page_parameters(
            {
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {"selector": {"type": "string"}},
                        "required": ["selector"],
                    },
                }
            },
            required=["elements"],
        )
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if not _is_present(kwargs.get("elements")):
            return "错误：elements 至少需要包含一个 selector。"
        return await _call_page_endpoint(
            self._runtime(), "scrape", "scrape", kwargs, ["elements"]
        )


@dataclass(config={"arbitrary_types_allowed": True})
class CloudflareJsonTool(_CloudflareTool):
    name: str = "cf_browser_json"
    description: str = "Extract structured JSON from a URL or HTML using Cloudflare Browser Rendering JSON extraction."
    parameters: dict = Field(
        default_factory=lambda: _page_parameters(
            {
                "prompt": {
                    "type": "string",
                    "description": "Instruction for JSON extraction.",
                },
                "response_format": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "json_schema": {"type": "object"},
                    },
                    "required": ["type"],
                },
                "custom_ai": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "model": {"type": "string"},
                            "authorization": {"type": "string"},
                        },
                        "required": ["model"],
                    },
                },
            }
        )
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        return await _call_page_endpoint(
            self._runtime(),
            "json",
            "json",
            kwargs,
            ["prompt", "response_format", "custom_ai"],
        )


@dataclass(config={"arbitrary_types_allowed": True})
class CloudflareCrawlStartTool(_CloudflareTool):
    name: str = "cf_browser_crawl_start"
    description: str = (
        "Start an asynchronous Cloudflare Browser Run crawl job for a website."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                **_common_properties(),
                "url": {"type": "string", "description": "Starting URL for the crawl."},
                "limit": {"type": "integer", "minimum": 1},
                "depth": {"type": "integer", "minimum": 1, "maximum": 100000},
                "formats": {
                    "type": "array",
                    "items": {"type": "string", "enum": CRAWL_FORMATS},
                },
                "render": {"type": "boolean"},
                "source": {"type": "string", "enum": LINK_SOURCES},
                "max_age": {"type": "integer", "minimum": 0, "maximum": 604800},
                "modified_since": {"type": "integer", "minimum": 0},
                "crawl_purposes": {
                    "type": "array",
                    "items": {"type": "string", "enum": CRAWL_PURPOSES},
                },
                "options": {
                    "type": "object",
                    "properties": {
                        "include_external_links": {"type": "boolean"},
                        "include_subdomains": {"type": "boolean"},
                        "include_patterns": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "exclude_patterns": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                "json_options": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "response_format": {"type": "object"},
                        "custom_ai": {"type": "array"},
                    },
                },
            },
            "required": ["url"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        runtime = self._runtime()
        if not _is_present(kwargs.get("url")):
            return "错误：必须提供 url。"

        max_crawl_limit = _to_int(
            runtime.cfg("max_crawl_limit"), CONFIG_DEFAULTS["max_crawl_limit"], 1
        )
        requested_limit = _to_int(kwargs.get("limit", min(10, max_crawl_limit)), 10, 1)
        if requested_limit > max_crawl_limit:
            return f"错误：limit 超过插件配置的 max_crawl_limit（{max_crawl_limit}）。"

        fields = [
            "url",
            "limit",
            "depth",
            "formats",
            "render",
            "source",
            "max_age",
            "modified_since",
            "crawl_purposes",
            "options",
            "json_options",
            "goto_options",
            "wait_for_selector",
            "wait_for_timeout",
            "viewport",
            "action_timeout",
            "best_attempt",
            "set_javascript_enabled",
            "emulate_media_type",
            "allow_resource_types",
            "reject_resource_types",
            "allow_request_pattern",
            "reject_request_pattern",
            "set_extra_http_headers",
            "authenticate",
            "cookies",
            "add_script_tag",
            "add_style_tag",
        ]
        body = {
            field_name: kwargs.get(field_name)
            for field_name in fields
            if field_name in kwargs
        }
        body["limit"] = requested_limit
        body.setdefault("render", _to_bool(runtime.cfg("default_render"), False))
        body.setdefault("formats", ["markdown"])
        error = _validate_crawl_body(body)
        if error:
            return error

        try:
            job_id = await runtime.request(
                "POST",
                "crawl",
                params=_query_params(runtime, kwargs),
                body=_to_cf_keys(body),
            )
        except CloudflareAPIError as exc:
            return str(exc)
        return _tool_payload(
            {"type": "crawl_start", "job_id": job_id, "message": "Crawl 任务已启动。"},
            runtime.max_output_chars,
        )


def _validate_crawl_body(body: dict[str, Any]) -> str | None:
    error = _validate_body_enums(body)
    if error:
        return error

    checks = [
        ("formats", CRAWL_FORMATS),
        ("crawl_purposes", CRAWL_PURPOSES),
    ]
    for key, allowed in checks:
        values = [str(value) for value in _list_from_value(body.get(key))]
        if values:
            error = _validate_values(values, allowed, key)
            if error:
                return error

    if _is_present(body.get("source")) and str(body["source"]) not in LINK_SOURCES:
        return f"错误：source 必须是以下值之一：{', '.join(LINK_SOURCES)}"
    return None


@dataclass(config={"arbitrary_types_allowed": True})
class CloudflareCrawlStatusTool(_CloudflareTool):
    name: str = "cf_browser_crawl_status"
    description: str = (
        "Get status and paginated records for a Cloudflare Browser Run crawl job."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "status": {"type": "string", "enum": CRAWL_STATUSES},
                "cursor": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1},
                "cache_ttl": {"type": "integer", "minimum": 0, "maximum": 86400},
            },
            "required": ["job_id"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        runtime = self._runtime()
        job_id = str(kwargs.get("job_id") or "").strip()
        if not job_id:
            return "错误：必须提供 job_id。"
        status = str(kwargs.get("status") or "").strip()
        if status and status not in CRAWL_STATUSES:
            return f"错误：status 必须是以下值之一：{', '.join(CRAWL_STATUSES)}"

        params = _query_params(runtime, kwargs)
        if status:
            params["status"] = status
        if _is_present(kwargs.get("cursor")):
            params["cursor"] = _to_int(kwargs.get("cursor"), 0, 0)
        if _is_present(kwargs.get("limit")):
            params["limit"] = _to_int(kwargs.get("limit"), 50, 1)

        try:
            result = await runtime.request("GET", f"crawl/{job_id}", params=params)
        except CloudflareAPIError as exc:
            return str(exc)
        payload = {
            "type": "crawl_status",
            **(result if isinstance(result, dict) else {"result": result}),
        }
        return _tool_payload(payload, runtime.max_output_chars)


@dataclass(config={"arbitrary_types_allowed": True})
class CloudflareCrawlCancelTool(_CloudflareTool):
    name: str = "cf_browser_crawl_cancel"
    description: str = "Cancel a Cloudflare Browser Run crawl job."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        runtime = self._runtime()
        job_id = str(kwargs.get("job_id") or "").strip()
        if not job_id:
            return "错误：必须提供 job_id。"
        try:
            result = await runtime.request("DELETE", f"crawl/{job_id}")
        except CloudflareAPIError as exc:
            return str(exc)
        payload = {
            "type": "crawl_cancel",
            **(result if isinstance(result, dict) else {"result": result}),
        }
        return _tool_payload(payload, runtime.max_output_chars)


TOOL_CLASSES = {
    "enable_markdown": CloudflareMarkdownTool,
    "enable_content": CloudflareContentTool,
    "enable_links": CloudflareLinksTool,
    "enable_scrape": CloudflareScrapeTool,
    "enable_json": CloudflareJsonTool,
    "enable_crawl_start": CloudflareCrawlStartTool,
    "enable_crawl_status": CloudflareCrawlStatusTool,
    "enable_crawl_cancel": CloudflareCrawlCancelTool,
}


def build_tools(config: dict[str, Any]) -> tuple[list[FunctionTool], list[str]]:
    """按插件配置创建需要注册的 LLM Tool。"""
    runtime = CloudflareBrowserRuntime(config)
    tools: list[FunctionTool] = []
    names: list[str] = []
    for config_key, tool_cls in TOOL_CLASSES.items():
        if _to_bool(_cfg(config, config_key), True):
            tool = tool_cls(runtime=runtime)
            tools.append(tool)
            names.append(tool.name)
    return tools, names
