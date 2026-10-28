"""Cloudflare Browser Run 工具与共享 Runtime。"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
from pydantic import Field
from pydantic.dataclasses import dataclass

try:
    from astrbot.api.star import StarTools
except Exception:  # pragma: no cover - compatibility with older AstrBot builds
    StarTools = None
try:
    from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
except Exception:  # pragma: no cover - compatibility with older AstrBot builds
    get_astrbot_plugin_data_path = None
try:
    from astrbot.core.computer.computer_client import get_booter
except Exception:  # pragma: no cover - compatibility with older AstrBot builds
    get_booter = None

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

PLUGIN_NAME = "astrbot_plugin_cloudflare_browser_run"
API_BASE = "https://api.cloudflare.com/client/v4"
RESULTS_DIR_NAME = "cloudflare_browser_results"

TOOL_NAMES = (
    "cf_browser_fetch",
    "cf_browser_crawl",
)
LEGACY_TOOL_NAMES = (
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
    "result_retention_hours": ("output_settings", "result_retention_hours"),
    "max_stored_results": ("output_settings", "max_stored_results"),
}

CONFIG_DEFAULTS = {
    "account_id": "",
    "api_token": "",
    "timeout_seconds": 120,
    "default_cache_ttl": 5,
    "default_render": False,
    "max_crawl_limit": 100,
    "max_output_chars": 12000,
    "result_retention_hours": 24,
    "max_stored_results": 50,
}

IDEMPOTENT_RETRIES = 2
MAX_RETRY_AFTER_SECONDS = 30.0
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
CRAWL_CONTENT_USES = ["reference", "full"]
SNAPSHOT_FORMATS = ["content", "screenshot", "markdown", "accessibilityTree"]
SNAPSHOT_DEFAULT_FORMATS = ["markdown", "accessibilityTree"]
CRAWL_STATUSES = [
    "queued",
    "errored",
    "completed",
    "disallowed",
    "skipped",
    "cancelled",
]
CRAWL_DEFAULT_LIMIT = 10
CRAWL_DEFAULT_FORMATS = ["markdown"]
CRAWL_DEFAULT_RENDER = False

CF_KEY_MAP = {
    "action_timeout": "actionTimeout",
    "add_script_tag": "addScriptTag",
    "add_style_tag": "addStyleTag",
    "allow_request_pattern": "allowRequestPattern",
    "allow_resource_types": "allowResourceTypes",
    "best_attempt": "bestAttempt",
    "cache_ttl": "cacheTTL",
    "crawl_purposes": "crawlPurposes",
    "content_use": "contentUse",
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
    "interesting_only": "interestingOnly",
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


class CloudflareParamError(ValueError):
    """本地参数校验失败；消息与 LLM Tool 的错误展示保持一致（以“错误：”开头）。"""


class _RetryRequest(Exception):
    def __init__(self, delay: float) -> None:
        super().__init__(delay)
        self.delay = delay


def missing_credentials(config: dict[str, Any]) -> str | None:
    """返回缺失的凭据键名；配置齐全时返回 None。只读内存配置，无任何副作用。"""
    for key in ("account_id", "api_token"):
        if not str(_cfg(config, key) or "").strip():
            return key
    return None


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


def _tool_error(exc: Exception) -> str:
    """将 LLM 工具错误转为稳定、可自恢复的结构。"""
    if isinstance(exc, CloudflareParamError):
        return _json_dumps(
            {
                "error": {"code": "invalid_parameters", "message": str(exc)},
                "retryable": False,
            }
        )
    message = str(exc)
    status_match = re.search(r"HTTP (\d{3})", message)
    status = int(status_match.group(1)) if status_match else None
    error: dict[str, Any] = {"code": "cloudflare_api_error", "message": message}
    if status is not None:
        error["http_status"] = status
    retryable = status is None or status == 429 or status >= 500
    if "未配置 Cloudflare" in message:
        retryable = False
    return _json_dumps({"error": error, "retryable": retryable})


def _local_plugin_data_fallback() -> Path:
    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        if parent.name == "plugins" and parent.parent.name == "data":
            return parent.parent / "plugin_data" / PLUGIN_NAME
    return module_path.parents[1] / "data" / "plugin_data" / PLUGIN_NAME


def get_plugin_data_dir() -> Path:
    """获取插件持久化目录，优先使用 AstrBot 标准目录。"""
    if StarTools is not None:
        try:
            data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        except Exception:
            pass
        else:
            data_dir.mkdir(parents=True, exist_ok=True)
            return data_dir.resolve(strict=False)

    if get_astrbot_plugin_data_path is not None:
        try:
            data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        except Exception:
            pass
        else:
            data_dir.mkdir(parents=True, exist_ok=True)
            return data_dir.resolve(strict=False)

    data_dir = _local_plugin_data_fallback()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir.resolve(strict=False)


def _result_file_stem(payload: dict[str, Any]) -> str:
    result_type = str(payload.get("type") or "result")
    safe_type = re.sub(r"[^A-Za-z0-9_.-]+", "-", result_type).strip("-._")
    if not safe_type:
        safe_type = "result"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return f"{safe_type}_{timestamp}_{uuid4().hex[:8]}"


def _format_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _crawl_markdown(payload: dict[str, Any]) -> str:
    """将 Crawl 记录展开为可按行读取的 Markdown 报告。"""
    lines = ["# Cloudflare Crawl Result", ""]
    for key in ("job_id", "status", "total", "finished", "cursor", "next_cursor"):
        if key in payload:
            lines.append(f"- {key}: {payload[key]}")
    records = payload.get("result")
    if not isinstance(records, list):
        return json.dumps(payload, ensure_ascii=False, default=str, indent=2)
    for index, record in enumerate(records, 1):
        lines.extend(["", f"## Record {index}", ""])
        if not isinstance(record, dict):
            lines.append(json.dumps(record, ensure_ascii=False, default=str, indent=2))
            continue
        for key, value in record.items():
            if key not in {"markdown", "html", "json"} and (
                value is None or isinstance(value, (str, int, float, bool))
            ):
                lines.append(f"- {key}: {value}")
        for format_name in ("markdown", "html", "json"):
            content = record.get(format_name)
            if not _is_present(content):
                continue
            lines.extend(["", f"### {format_name}", ""])
            if format_name == "json" or not isinstance(content, str):
                lines.append(
                    json.dumps(content, ensure_ascii=False, default=str, indent=2)
                )
            else:
                lines.append(content)
    return "\n".join(lines)


def _readable_payload(payload: dict[str, Any]) -> tuple[str, str]:
    """返回适合文件工具分段读取的扩展名和内容。"""
    result = payload.get("result")
    if payload.get("type") == "markdown" and isinstance(result, dict):
        markdown = result.get("markdown")
        if isinstance(markdown, str):
            return ".md", markdown
    if payload.get("type") == "content" and isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, str):
            return ".html", content
    if payload.get("type") == "crawl_status" and isinstance(
        payload.get("result"), list
    ):
        return ".md", _crawl_markdown(payload)
    if payload.get("type") == "snapshot" and isinstance(result, dict):
        lines = ["# Cloudflare Browser Snapshot", ""]
        for format_name in ("markdown", "content", "accessibilityTree"):
            content = result.get(format_name)
            if not _is_present(content):
                continue
            lines.extend(["", f"## {format_name}", ""])
            lines.append(
                content
                if isinstance(content, str)
                else json.dumps(content, ensure_ascii=False, default=str, indent=2)
            )
        if _is_present(result.get("screenshot")):
            lines.extend(["", "## screenshot", "", "截图见 raw_json_file_path。"])
        return ".md", "\n".join(lines)
    return ".json", json.dumps(payload, ensure_ascii=False, default=str, indent=2)


_RESULT_FILE_PATTERN = re.compile(
    r"^(?P<stem>[A-Za-z0-9_.-]+_\d{8}_\d{6}_\d{6}_[0-9a-f]{8})\.(json|md|html)$"
)


def _cleanup_result_files(
    results_dir: Path, retention_hours: int, max_stored_results: int
) -> None:
    """按结果组清理插件生成的文件，不触碰其他文件或目录。"""
    groups: dict[str, list[Path]] = {}
    if not results_dir.is_dir():
        return
    for path in results_dir.iterdir():
        if not path.is_file():
            continue
        match = _RESULT_FILE_PATTERN.fullmatch(path.name)
        if match:
            groups.setdefault(match.group("stem"), []).append(path)

    now = time.time()
    cutoff = now - retention_hours * 3600

    def latest_mtime(paths: list[Path]) -> float:
        mtimes = []
        for path in paths:
            try:
                mtimes.append(path.stat().st_mtime)
            except FileNotFoundError:
                pass
        return max(mtimes, default=0.0)

    groups_by_age = sorted(groups.items(), key=lambda item: latest_mtime(item[1]))
    expired = 0
    while (
        expired < len(groups_by_age)
        and latest_mtime(groups_by_age[expired][1]) < cutoff
    ):
        expired += 1
    remove_count = max(expired, len(groups_by_age) - max_stored_results + 1)
    for _, paths in groups_by_age[:remove_count]:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _write_large_payload(
    payload: dict[str, Any],
    data_dir: Path,
    retention_hours: int,
    max_stored_results: int,
) -> tuple[Path, int, Path | None]:
    results_dir = data_dir / RESULTS_DIR_NAME
    results_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_result_files(results_dir, retention_hours, max_stored_results)
    stem = _result_file_stem(payload)
    raw_json = json.dumps(payload, ensure_ascii=False, default=str, indent=2)
    suffix, readable = _readable_payload(payload)
    if suffix == ".json":
        path = results_dir / f"{stem}.json"
        path.write_text(raw_json, encoding="utf-8")
        return path.resolve(strict=False), path.stat().st_size, None

    raw_path = results_dir / f"{stem}.json"
    path = results_dir / f"{stem}{suffix}"
    raw_path.write_text(raw_json, encoding="utf-8")
    path.write_text(readable, encoding="utf-8")
    return (
        path.resolve(strict=False),
        path.stat().st_size,
        raw_path.resolve(strict=False),
    )


def _computer_use_runtime(context: Any) -> str:
    """读取当前会话的 Computer Use 运行时配置，异常时按 local 处理。"""
    try:
        umo = context.context.event.unified_msg_origin
        cfg = context.context.context.get_config(umo=umo)
        runtime = str(
            cfg.get("provider_settings", {}).get("computer_use_runtime", "local")
        )
    except Exception:
        return "local"
    return runtime if runtime in {"local", "sandbox", "none"} else "local"


async def _result_data_dir(context: Any, fallback: Path) -> Path:
    """Local 结果优先放入当前会话可读的工作区。"""
    try:
        from astrbot.core.tools.computer_tools.util import workspace_root_for_context

        workspace = await workspace_root_for_context(context)
    except Exception:
        workspace = None
    if workspace is not None:
        return Path(workspace).expanduser().resolve(strict=False)
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_system_tmp_path

        return Path(get_astrbot_system_tmp_path()) / PLUGIN_NAME
    except Exception:
        return fallback


async def _upload_result_to_sandbox(context: Any, file_path: Path) -> str:
    """把结果文件上传到当前会话沙盒并返回沙盒侧路径，失败抛异常由调用方回退。"""
    if get_booter is None:
        raise RuntimeError("当前 AstrBot 版本不支持沙盒文件上传。")
    umo = context.context.event.unified_msg_origin
    booter = await get_booter(context.context.context, umo)
    result = await booter.upload_file(str(file_path), file_path.name)
    if not result.get("success", False):
        raise RuntimeError(
            str(result.get("error") or result.get("message") or "unknown error")
        )
    return str(result.get("file_path") or file_path.name)


def _file_suggested_tools(path: str) -> list[dict[str, Any]]:
    return [
        {
            "name": "astrbot_grep_tool",
            "args": {"path": path, "pattern": "要搜索的关键词"},
        },
        {
            "name": "astrbot_file_read_tool",
            "args": {"path": path, "offset": 0, "limit": 120},
        },
    ]


async def _tool_payload(
    payload: dict[str, Any],
    max_chars: int,
    data_dir: Path,
    context: ContextWrapper[AstrAgentContext] | None = None,
    retention_hours: int = 24,
    max_stored_results: int = 50,
) -> str:
    max_chars = max(1000, int(max_chars or CONFIG_DEFAULTS["max_output_chars"]))
    dumped = _json_dumps(payload)
    if len(dumped) <= max_chars:
        return dumped

    runtime = _computer_use_runtime(context) if context is not None else "local"
    result_data_dir = (
        await _result_data_dir(context, data_dir)
        if context is not None and runtime == "local"
        else data_dir
    )
    try:
        file_path, file_size_bytes, raw_json_path = _write_large_payload(
            payload, result_data_dir, retention_hours, max_stored_results
        )
    except Exception as exc:
        preview_limit = max(200, max_chars - 260)
        compact = {
            "saved_to_file": False,
            "type": payload.get("type"),
            "url": payload.get("url"),
            "content_chars": len(dumped),
            "max_output_chars": max_chars,
            "error": f"结果超过最大直接返回长度，但写入本地文件失败：{exc}",
            "preview": dumped[:preview_limit],
        }
        return _json_dumps(compact)

    compact: dict[str, Any] = {
        "saved_to_file": True,
        "type": payload.get("type"),
        "url": payload.get("url"),
        "file_size_bytes": file_size_bytes,
        "file_size": _format_bytes(file_size_bytes),
        "content_chars": len(dumped),
        "max_output_chars": max_chars,
    }

    sandbox_path: str | None = None
    raw_sandbox_path: str | None = None
    sandbox_error: str | None = None
    raw_sandbox_error: str | None = None
    if runtime == "sandbox":
        try:
            sandbox_path = await _upload_result_to_sandbox(context, file_path)
        except Exception as exc:
            sandbox_error = str(exc)
        else:
            if raw_json_path is not None:
                try:
                    raw_sandbox_path = await _upload_result_to_sandbox(
                        context, raw_json_path
                    )
                except Exception as exc:
                    raw_sandbox_error = str(exc)

    if sandbox_path is not None:
        compact["sandbox_synced"] = True
        compact["file_path"] = sandbox_path
        compact["host_file_path"] = str(file_path)
        if raw_sandbox_path is not None:
            compact["raw_json_file_path"] = raw_sandbox_path
        if raw_sandbox_error is not None:
            compact["raw_json_sync_error"] = raw_sandbox_error[:200]
        compact["message"] = (
            "结果超过最大直接返回长度，已保存为可读内容文件并同步到当前会话沙盒。"
            "file_path 为沙盒内路径，请使用 astrbot_grep_tool 搜索，"
            "或使用 astrbot_file_read_tool 按行分段读取。"
        )
        compact["suggested_tools"] = _file_suggested_tools(sandbox_path)
        return _json_dumps(compact)

    compact["file_path"] = str(file_path)
    if raw_json_path is not None:
        compact["raw_json_file_path"] = str(raw_json_path)
    if runtime == "local":
        compact["message"] = (
            "结果超过最大直接返回长度，已保存到当前会话可读目录。"
            "请使用 astrbot_grep_tool 在 file_path 中搜索关键词，"
            "或使用 astrbot_file_read_tool 按行分段读取；raw_json_file_path 保留原始结构。"
        )
        compact["suggested_tools"] = _file_suggested_tools(str(file_path))
        return _json_dumps(compact)

    # 沙盒同步失败或未启用文件工具运行时：附内容预览兜底，避免 LLM 拿不到任何内容
    compact["sandbox_synced"] = False
    if runtime == "sandbox":
        compact["message"] = (
            "结果超过最大直接返回长度，已保存到宿主结果目录，"
            f"但同步到当前会话沙盒失败：{str(sandbox_error)[:200]}。"
            "file_path 为宿主机路径，沙盒内的文件工具无法直接访问；"
            "可请管理员先用 astrbot_upload_file 将 file_path 传入沙盒，"
            "或直接参考 preview 中的内容预览。"
        )
    else:
        compact["message"] = (
            "结果超过最大直接返回长度，已保存到宿主结果目录。"
            "当前未启用 Computer Use 运行时，文件搜索/读取工具不可用；"
            "请直接参考 preview 中的内容预览，或启用本地/沙盒运行时后按 file_path 读取。"
        )

    compact["preview"] = ""
    budget = max(0, max_chars - len(_json_dumps(compact)))
    compact["preview"] = dumped[:budget]
    while compact["preview"] and len(_json_dumps(compact)) > max_chars:
        budget = max(0, budget - 200)
        compact["preview"] = dumped[:budget]
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


def _all_common_properties() -> dict[str, Any]:
    resource_schema = {
        "type": "array",
        "items": {"type": "string", "enum": RESOURCE_TYPES},
        "description": "Resource type filter, e.g. document, image, script.",
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
                "wait_until": {
                    "type": "string",
                    "enum": WAIT_UNTIL,
                    "description": "When to consider navigation done.",
                },
                "timeout": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 60000,
                    "description": "Navigation timeout in ms.",
                },
                "referer": {"type": "string", "description": "Referer header."},
                "referrer_policy": {
                    "type": "string",
                    "description": "Referrer policy.",
                },
            },
        },
        "wait_for_selector": {
            "type": "object",
            "description": "Wait for a CSS selector to appear/disappear.",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector."},
                "visible": {
                    "type": "boolean",
                    "description": "Wait until visible.",
                },
                "hidden": {
                    "type": "boolean",
                    "description": "Wait until hidden.",
                },
                "timeout": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 60000,
                    "description": "Wait timeout in ms.",
                },
            },
        },
        "wait_for_timeout": {
            "type": "integer",
            "minimum": 0,
            "maximum": 120000,
            "description": "Wait a fixed duration in ms before action.",
        },
        "viewport": {
            "type": "object",
            "description": "Browser viewport settings.",
            "properties": {
                "width": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Width in px.",
                },
                "height": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Height in px.",
                },
                "device_scale_factor": {
                    "type": "number",
                    "description": "Device pixel ratio.",
                },
                "is_mobile": {"type": "boolean", "description": "Mobile emulation."},
                "has_touch": {"type": "boolean", "description": "Touch emulation."},
                "is_landscape": {
                    "type": "boolean",
                    "description": "Landscape orientation.",
                },
            },
        },
        "action_timeout": {
            "type": "integer",
            "minimum": 0,
            "maximum": 120000,
            "description": "Max duration in ms for the browser action after page load.",
        },
        "best_attempt": {
            "type": "boolean",
            "description": "Return best available result on partial failure.",
        },
        "set_javascript_enabled": {
            "type": "boolean",
            "description": "Enable or disable JavaScript on the page.",
        },
        "user_agent": {"type": "string", "description": "Custom User-Agent string."},
        "emulate_media_type": {
            "type": "string",
            "description": "Media type to emulate, e.g. screen or print.",
        },
        "allow_resource_types": resource_schema,
        "reject_resource_types": resource_schema,
        "allow_request_pattern": {
            "type": "array",
            "items": {"type": "string"},
            "description": "URL patterns to allow; mutually exclusive with reject_request_pattern.",
        },
        "reject_request_pattern": {
            "type": "array",
            "items": {"type": "string"},
            "description": "URL patterns to reject; mutually exclusive with allow_request_pattern.",
        },
        "set_extra_http_headers": {
            "type": "object",
            "description": "Extra HTTP headers as key-value pairs (object keys are header names, values are strings).",
        },
        "authenticate": {
            "type": "object",
            "description": "HTTP Basic authentication credentials.",
            "properties": {
                "username": {"type": "string", "description": "Username."},
                "password": {"type": "string", "description": "Password."},
            },
            "required": ["username", "password"],
        },
        "cookies": {
            "type": "array",
            "description": "Cookies to set before navigation.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Cookie name."},
                    "value": {"type": "string", "description": "Cookie value."},
                    "domain": {"type": "string", "description": "Cookie domain."},
                    "path": {"type": "string", "description": "Cookie path."},
                    "url": {"type": "string", "description": "Cookie URL."},
                    "expires": {
                        "type": "number",
                        "description": "Expiry Unix timestamp.",
                    },
                    "http_only": {"type": "boolean", "description": "HttpOnly flag."},
                    "secure": {"type": "boolean", "description": "Secure flag."},
                    "same_site": {
                        "type": "string",
                        "enum": ["Strict", "Lax", "None"],
                        "description": "SameSite attribute.",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["Low", "Medium", "High"],
                        "description": "Cookie priority.",
                    },
                    "partition_key": {
                        "type": "string",
                        "description": "Partition key.",
                    },
                    "same_party": {"type": "boolean", "description": "SameParty flag."},
                    "source_port": {"type": "number", "description": "Source port."},
                    "source_scheme": {
                        "type": "string",
                        "enum": ["Unset", "NonSecure", "Secure"],
                        "description": "Source scheme.",
                    },
                },
                "required": ["name", "value"],
            },
        },
        "add_script_tag": {
            "type": "array",
            "description": "Inject <script> tags into the page.",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Script URL."},
                    "content": {
                        "type": "string",
                        "description": "Inline script content.",
                    },
                    "id": {"type": "string", "description": "Script element ID."},
                    "type": {"type": "string", "description": "Script type attribute."},
                },
            },
        },
        "add_style_tag": {
            "type": "array",
            "description": "Inject <style> tags into the page.",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Stylesheet URL."},
                    "content": {"type": "string", "description": "Inline CSS content."},
                },
            },
        },
    }


_BASIC_PAGE_FIELDS = {
    "url",
    "html",
    "cache_ttl",
    "goto_options",
    "wait_for_selector",
    "wait_for_timeout",
    "set_javascript_enabled",
    "allow_resource_types",
    "reject_resource_types",
    "best_attempt",
}


def _common_properties() -> dict[str, Any]:
    properties = _all_common_properties()
    return {
        key: value for key, value in properties.items() if key in _BASIC_PAGE_FIELDS
    }


def _advanced_options_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": (
            "Optional advanced browser options: viewport, action_timeout, user_agent, "
            "emulate_media_type, request patterns, headers, authenticate, cookies, "
            "script/style tags. Unknown keys are rejected."
        ),
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
        "description": (
            "Provide either url or html. The page is rendered with a headless browser "
            "(JavaScript is executed by default). The tool validates url/html at runtime."
        ),
    }
    if required:
        schema["required"] = required
    return schema


def _retry_delay(headers: Any, attempt: int) -> float | None:
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    if raw is not None:
        try:
            delay = max(0.0, float(raw))
        except (TypeError, ValueError):
            delay = None
        if delay is not None:
            return delay if delay <= MAX_RETRY_AFTER_SECONDS else None
    return min(0.5 * (2**attempt), 2.0)


def _response_metadata(headers: Any) -> dict[str, Any]:
    if not hasattr(headers, "get"):
        return {}
    raw = headers.get("X-Browser-Ms-Used")
    if raw is None:
        return {}
    try:
        value: int | float = int(raw)
    except (TypeError, ValueError):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return {}
    return {"browser_ms_used": value}


class CloudflareBrowserRuntime:
    def __init__(self, config: dict[str, Any], data_dir: Path | str) -> None:
        self.config = config
        self.data_dir = Path(data_dir).expanduser().resolve(strict=False)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def cfg(self, key: str) -> Any:
        return _cfg(self.config, key)

    @property
    def max_output_chars(self) -> int:
        return _to_int(
            self.cfg("max_output_chars"), CONFIG_DEFAULTS["max_output_chars"], 1000
        )

    @property
    def result_retention_hours(self) -> int:
        return _to_int(
            self.cfg("result_retention_hours"),
            CONFIG_DEFAULTS["result_retention_hours"],
            1,
        )

    @property
    def max_stored_results(self) -> int:
        return _to_int(
            self.cfg("max_stored_results"), CONFIG_DEFAULTS["max_stored_results"], 1
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
        missing = missing_credentials(self.config)
        if missing:
            return f"错误：未配置 Cloudflare {missing}。"
        return None

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        retry_safe: bool = False,
        with_metadata: bool = False,
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

        for attempt in range(IDEMPOTENT_RETRIES + 1):
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
                            retry_delay = _retry_delay(
                                getattr(response, "headers", {}), attempt
                            )
                            if (
                                retry_safe
                                and attempt < IDEMPOTENT_RETRIES
                                and (response.status == 429 or response.status >= 500)
                                and retry_delay is not None
                            ):
                                raise _RetryRequest(retry_delay)
                            raise CloudflareAPIError(
                                self._format_error(response.status, data, text, secrets)
                            )
                        if isinstance(data, dict) and data.get("success") is False:
                            raise CloudflareAPIError(
                                self._format_error(response.status, data, text, secrets)
                            )
                        result = (
                            data["result"]
                            if isinstance(data, dict) and "result" in data
                            else data
                        )
                        if with_metadata:
                            return result, _response_metadata(
                                getattr(response, "headers", {})
                            )
                        return result
            except _RetryRequest as retry:
                await asyncio.sleep(retry.delay)
                continue
            except CloudflareAPIError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if retry_safe and attempt < IDEMPOTENT_RETRIES:
                    await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
                    continue
                raise CloudflareAPIError(
                    _redact_text(
                        f"错误：Cloudflare Browser Run 请求失败：{exc}", secrets
                    )
                ) from exc
            except Exception as exc:
                raise CloudflareAPIError(
                    _redact_text(
                        f"错误：Cloudflare Browser Run 请求失败：{exc}", secrets
                    )
                ) from exc
        raise CloudflareAPIError(
            "错误：Cloudflare Browser Run 请求失败：重试次数已用尽。"
        )

    def _format_error(
        self, status: int, data: Any, raw_text: str, secrets: list[str]
    ) -> str:
        errors = data.get("errors") if isinstance(data, dict) else None
        if errors:
            safe_errors = _redact(errors)
            message = _json_dumps(safe_errors)
        else:
            message = raw_text or _json_dumps(data)
        hint = _error_hint(status)
        return _redact_text(
            f"错误：Cloudflare Browser Run API 返回 HTTP {status}：{message}{hint}",
            secrets,
        )

    # ---- 具名共享业务入口：LLM Tool 与 SDK 服务都走这一条路径 ----

    async def page(self, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        """单页抓取，返回 {type, url, result}。"""
        if endpoint not in {
            "markdown",
            "content",
            "links",
            "scrape",
            "json",
            "accessibilityTree",
            "snapshot",
        }:
            raise CloudflareParamError(f"错误：不支持的单页端点：{endpoint}")
        if endpoint == "scrape" and not _is_present(kwargs.get("elements")):
            raise CloudflareParamError("错误：elements 至少需要包含一个 selector。")
        if endpoint == "snapshot":
            formats = _list_from_value(
                kwargs.get("formats") or SNAPSHOT_DEFAULT_FORMATS
            )
            error = _validate_values(
                [str(value) for value in formats], SNAPSHOT_FORMATS, "formats"
            )
            if error:
                raise CloudflareParamError(error)
            kwargs["formats"] = formats
        body, error = _page_body(kwargs, _PAGE_EXTRA_FIELDS.get(endpoint))
        if error:
            raise CloudflareParamError(error)
        _validate_timeout_budget(self, kwargs)
        result, metadata = await self.request(
            "POST",
            endpoint,
            params=_query_params(self, kwargs),
            body=body,
            retry_safe=True,
            with_metadata=True,
        )
        payload = {"type": endpoint, "url": kwargs.get("url"), "result": result}
        payload.update(metadata)
        return payload

    async def page_markdown(
        self, *, url: Any = None, html: Any = None, **options: Any
    ) -> dict[str, Any]:
        return await self.page("markdown", url=url, html=html, **options)

    async def page_content(
        self, *, url: Any = None, html: Any = None, **options: Any
    ) -> dict[str, Any]:
        return await self.page("content", url=url, html=html, **options)

    async def page_links(
        self, *, url: Any = None, html: Any = None, **options: Any
    ) -> dict[str, Any]:
        return await self.page("links", url=url, html=html, **options)

    async def page_scrape(
        self, *, url: Any = None, html: Any = None, elements: Any = None, **options: Any
    ) -> dict[str, Any]:
        return await self.page(
            "scrape", url=url, html=html, elements=elements, **options
        )

    async def page_json(
        self, *, url: Any = None, html: Any = None, **options: Any
    ) -> dict[str, Any]:
        return await self.page("json", url=url, html=html, **options)

    async def page_accessibility_tree(
        self, *, url: Any = None, html: Any = None, **options: Any
    ) -> dict[str, Any]:
        options.setdefault("interesting_only", True)
        return await self.page("accessibilityTree", url=url, html=html, **options)

    async def page_snapshot(
        self,
        *,
        url: Any = None,
        html: Any = None,
        formats: Any = None,
        **options: Any,
    ) -> dict[str, Any]:
        selected_formats = _list_from_value(formats or SNAPSHOT_DEFAULT_FORMATS)
        if "accessibilityTree" in selected_formats:
            options.setdefault("interesting_only", True)
        return await self.page(
            "snapshot", url=url, html=html, formats=selected_formats, **options
        )

    async def crawl_start(self, url: Any = None, **options: Any) -> dict[str, Any]:
        """启动异步 Crawl 任务，返回 {type, job_id, message}。"""
        if not _is_present(url):
            raise CloudflareParamError("错误：必须提供 url。")
        options = {"url": url, **options}

        max_crawl_limit = _to_int(
            self.cfg("max_crawl_limit"), CONFIG_DEFAULTS["max_crawl_limit"], 1
        )
        requested_limit = _validated_int(
            options.get("limit", min(CRAWL_DEFAULT_LIMIT, max_crawl_limit)),
            "limit",
            CRAWL_DEFAULT_LIMIT,
            1,
        )
        if requested_limit > max_crawl_limit:
            raise CloudflareParamError(
                f"错误：limit 超过插件配置的 max_crawl_limit（{max_crawl_limit}）。"
            )

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
            "content_use",
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
        unknown = sorted(set(options) - set(fields) - {"cache_ttl"})
        if unknown:
            raise CloudflareParamError(f"错误：包含未知参数：{', '.join(unknown)}")
        body = {
            field_name: options.get(field_name)
            for field_name in fields
            if field_name in options
        }
        body["limit"] = requested_limit
        body.setdefault(
            "render", _to_bool(self.cfg("default_render"), CRAWL_DEFAULT_RENDER)
        )
        body.setdefault("formats", list(CRAWL_DEFAULT_FORMATS))
        error = _validate_crawl_body(body)
        if error:
            raise CloudflareParamError(error)
        if _to_bool(body.get("render"), CRAWL_DEFAULT_RENDER):
            _validate_timeout_budget(self, options)

        job_id, metadata = await self.request(
            "POST",
            "crawl",
            params=_query_params(self, options),
            body=_to_cf_keys(body),
            with_metadata=True,
        )
        payload = {
            "type": "crawl_start",
            "job_id": job_id,
            "message": "Crawl 任务已启动。",
        }
        payload.update(metadata)
        return payload

    async def crawl_status(self, job_id: Any = None, **options: Any) -> dict[str, Any]:
        """查询 Crawl 任务状态，返回以远端结果合并的业务字典。"""
        job_id = _safe_job_id(job_id)
        status = str(options.get("status") or "").strip()
        if status and status not in CRAWL_STATUSES:
            raise CloudflareParamError(
                f"错误：status 必须是以下值之一：{', '.join(CRAWL_STATUSES)}"
            )
        unknown = sorted(set(options) - {"status", "cursor", "limit", "cache_ttl"})
        if unknown:
            raise CloudflareParamError(f"错误：包含未知参数：{', '.join(unknown)}")
        params = _query_params(self, options)
        if status:
            params["status"] = status
        if _is_present(options.get("cursor")):
            params["cursor"] = _validated_int(options.get("cursor"), "cursor", 0, 0)
        if _is_present(options.get("limit")):
            params["limit"] = _validated_int(options.get("limit"), "limit", 50, 1)
        result, metadata = await self.request(
            "GET",
            f"crawl/{job_id}",
            params=params,
            retry_safe=True,
            with_metadata=True,
        )
        payload = {
            "type": "crawl_status",
            **(result if isinstance(result, dict) else {"result": result}),
        }
        payload.update(metadata)
        return payload

    async def crawl_cancel(self, job_id: Any) -> dict[str, Any]:
        """取消 Crawl 任务，返回以远端结果合并的业务字典。"""
        job_id = _safe_job_id(job_id)
        result, metadata = await self.request(
            "DELETE", f"crawl/{job_id}", retry_safe=True, with_metadata=True
        )
        payload = {
            "type": "crawl_cancel",
            **(result if isinstance(result, dict) else {"result": result}),
        }
        payload.update(metadata)
        return payload


_ERROR_HINTS = {
    401: "（排查：API Token 无效或过期，请检查 api_token 配置。）",
    403: "（排查：API Token 权限不足，需要 Browser Run / Browser Rendering - Edit 权限。）",
    429: "（排查：请求频率超限或浏览器时间额度用尽，请降低调用频率或升级 Workers 计划。）",
    400: "（排查：请求参数有误，请检查 url、selector 或格式是否符合要求。）",
    404: "（排查：请求的资源不存在，crawl 任务 ID 可能已过期或无效。）",
    500: "（排查：Cloudflare 服务端错误，可稍后重试。）",
}


def _error_hint(status: int) -> str:
    """返回常见 HTTP 错误码的排查建议。"""
    return _ERROR_HINTS.get(status, "")


_PAGE_EXTRA_FIELDS = {
    "links": ["exclude_external_links", "visible_links_only"],
    "scrape": ["elements"],
    "json": ["prompt", "response_format", "custom_ai"],
    "accessibilityTree": ["interesting_only", "root"],
    "snapshot": ["formats", "interesting_only", "root"],
}

_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")


def _safe_job_id(value: Any) -> str:
    """校验 job_id 为单个安全路径段，拒绝路径/查询注入。"""
    job_id = str(value if value is not None else "").strip()
    if not job_id:
        raise CloudflareParamError("错误：必须提供 job_id。")
    if not _JOB_ID_PATTERN.fullmatch(job_id) or job_id == "." or ".." in job_id:
        raise CloudflareParamError("错误：job_id 必须是单个安全的路径段。")
    return job_id


def _validated_int(
    value: Any,
    name: str,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if value is None or value == "":
        value = default
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise CloudflareParamError(f"错误：{name} 必须是整数。")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise CloudflareParamError(f"错误：{name} 必须是整数。") from exc
    if minimum is not None and number < minimum:
        raise CloudflareParamError(f"错误：{name} 必须大于等于 {minimum}。")
    if maximum is not None and number > maximum:
        raise CloudflareParamError(f"错误：{name} 必须小于等于 {maximum}。")
    return number


def _validate_timeout_budget(
    runtime: CloudflareBrowserRuntime, kwargs: dict[str, Any]
) -> None:
    """避免独立浏览器计时器总和超过 HTTP 总超时。"""
    total_ms = 0
    for field_name in ("goto_options", "wait_for_selector"):
        options = kwargs.get(field_name)
        if not _is_present(options):
            continue
        if not isinstance(options, dict):
            raise CloudflareParamError(f"错误：{field_name} 必须是对象。")
        if _is_present(options.get("timeout")):
            total_ms += _validated_int(
                options["timeout"], f"{field_name}.timeout", 0, 0
            )
    for field_name in ("wait_for_timeout", "action_timeout"):
        if _is_present(kwargs.get(field_name)):
            total_ms += _validated_int(kwargs[field_name], field_name, 0, 0)
    timeout_seconds = _to_int(
        runtime.cfg("timeout_seconds"), CONFIG_DEFAULTS["timeout_seconds"], 1
    )
    if total_ms > timeout_seconds * 1000:
        raise CloudflareParamError(
            f"错误：浏览器等待参数合计 {total_ms}ms，超过 timeout_seconds="
            f"{timeout_seconds}s；请减少等待或提高请求超时配置。"
        )


def _query_params(
    runtime: CloudflareBrowserRuntime, kwargs: dict[str, Any]
) -> dict[str, Any]:
    cache_ttl = kwargs.get("cache_ttl", runtime.default_cache_ttl)
    return {
        "cacheTTL": _validated_int(
            cache_ttl, "cache_ttl", runtime.default_cache_ttl, 0, 86400
        )
    }


def _page_body(
    kwargs: dict[str, Any], extra_fields: list[str] | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    has_url = _is_present(kwargs.get("url"))
    has_html = _is_present(kwargs.get("html"))
    if not has_url and not has_html:
        return None, "错误：必须提供 url 或 html。"
    if has_url and has_html:
        return None, "错误：url 和 html 只能提供其中一个。"

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
    unknown = sorted(set(kwargs) - set(fields) - {"cache_ttl"})
    if unknown:
        return None, f"错误：包含未知参数：{', '.join(unknown)}"

    body = {
        field_name: kwargs.get(field_name)
        for field_name in fields
        if field_name in kwargs
    }
    if extra_fields and "response_format" in extra_fields:
        if not _is_present(body.get("prompt")) and not _is_present(
            body.get("response_format")
        ):
            return None, "错误：JSON 提取至少需要 prompt 或 response_format。"
    error = _validate_body_enums(body)
    if error:
        return None, error
    return _to_cf_keys(body), None


def _validate_body_enums(body: dict[str, Any]) -> str | None:
    if _is_present(body.get("allow_request_pattern")) and _is_present(
        body.get("reject_request_pattern")
    ):
        return "错误：allow_request_pattern 和 reject_request_pattern 不能同时设置。"
    if _is_present(body.get("allow_resource_types")) and _is_present(
        body.get("reject_resource_types")
    ):
        return "错误：allow_resource_types 和 reject_resource_types 不能同时设置。"

    headers = body.get("set_extra_http_headers")
    if isinstance(headers, dict) and headers:
        non_string = [
            key for key, value in headers.items() if not isinstance(value, str)
        ]
        if non_string:
            return (
                f"错误：set_extra_http_headers 的值必须都是字符串，"
                f"以下键的值不是字符串：{', '.join(non_string)}"
            )

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


@dataclass(config={"arbitrary_types_allowed": True})
class _CloudflareTool(FunctionTool[AstrAgentContext]):
    runtime: CloudflareBrowserRuntime | None = field(default=None, repr=False)

    def _runtime(self) -> CloudflareBrowserRuntime:
        if self.runtime is None:
            raise RuntimeError("工具运行时尚未初始化。")
        return self.runtime

    async def _call_runtime(
        self, coro, context: ContextWrapper[AstrAgentContext] | None, transform=None
    ) -> ToolExecResult:
        """执行共享业务请求，错误返回字符串，成功结果按需转换后桥接。"""
        runtime = self._runtime()
        try:
            payload = await coro
        except (CloudflareParamError, CloudflareAPIError) as exc:
            return _tool_error(exc)
        if transform is not None:
            payload = transform(payload)
        return await _tool_payload(
            payload,
            runtime.max_output_chars,
            runtime.data_dir,
            context,
            runtime.result_retention_hours,
            runtime.max_stored_results,
        )


_FETCH_MODE_PROPERTIES = {
    "exclude_external_links": {
        "type": "boolean",
        "description": "Links mode: exclude external domains.",
    },
    "visible_links_only": {
        "type": "boolean",
        "description": "Links mode: return only visible links.",
    },
    "elements": {
        "type": "array",
        "minItems": 1,
        "description": "Scrape mode: CSS selectors to extract.",
        "items": {
            "type": "object",
            "properties": {"selector": {"type": "string"}},
            "required": ["selector"],
        },
    },
    "prompt": {"type": "string", "description": "JSON mode extraction instruction."},
    "response_format": {
        "type": "object",
        "description": "JSON mode response schema; prompt or response_format is required.",
    },
    "custom_ai": {
        "type": "array",
        "description": "JSON mode custom models, in fallback order.",
        "items": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "authorization": {"type": "string"},
            },
            "required": ["model"],
        },
    },
    "interesting_only": {
        "type": "boolean",
        "description": "Keep only semantically meaningful accessibility nodes.",
    },
    "root": {
        "type": "string",
        "description": "CSS selector anchoring an accessibility subtree.",
    },
    "formats": {
        "type": "array",
        "items": {"type": "string", "enum": SNAPSHOT_FORMATS},
        "description": "Snapshot formats; avoid screenshot unless image data is required.",
    },
}


def _fetch_parameters() -> dict[str, Any]:
    schema = _page_parameters(_FETCH_MODE_PROPERTIES)
    schema["properties"]["mode"] = {
        "type": "string",
        "enum": [
            "markdown",
            "content",
            "links",
            "scrape",
            "json",
            "accessibility_tree",
            "snapshot",
        ],
        "description": "Fetch operation. Default markdown is best for reading pages.",
    }
    schema["properties"]["advanced_options"] = _advanced_options_schema()
    schema["description"] = (
        "Provide url or html, not both. elements is for scrape; prompt/response_format "
        "is for json; formats/root are for snapshot or accessibility_tree."
    )
    return schema


def _crawl_parameters() -> dict[str, Any]:
    properties = {
        key: value for key, value in _common_properties().items() if key != "html"
    }
    properties.update(
        {
            "action": {
                "type": "string",
                "enum": ["start", "status", "cancel"],
                "description": "Crawl operation.",
            },
            "job_id": {"type": "string", "description": "Required for status/cancel."},
            "url": {"type": "string", "description": "Required for start."},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "description": "Start: max pages. Status: max records; use 1 while polling.",
            },
            "depth": {"type": "integer", "minimum": 1},
            "formats": {
                "type": "array",
                "items": {"type": "string", "enum": CRAWL_FORMATS},
            },
            "render": {
                "type": "boolean",
                "description": "Execute JavaScript; slower and consumes browser hours.",
            },
            "source": {"type": "string", "enum": LINK_SOURCES},
            "max_age": {"type": "integer", "minimum": 0, "maximum": 604800},
            "modified_since": {"type": "integer", "minimum": 1},
            "crawl_purposes": {
                "type": "array",
                "items": {"type": "string", "enum": CRAWL_PURPOSES},
            },
            "content_use": {"type": "string", "enum": CRAWL_CONTENT_USES},
            "options": {
                "type": "object",
                "description": "Crawl scope options.",
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
                "description": "Required when formats includes json.",
            },
            "status": {"type": "string", "enum": CRAWL_STATUSES},
            "cursor": {"type": "integer", "minimum": 0},
            "advanced_options": _advanced_options_schema(),
        }
    )
    return {
        "type": "object",
        "properties": properties,
        "required": ["action"],
        "description": (
            "Start returns a job_id. After 5 seconds poll action=status with limit=1 "
            "and cache_ttl=0; fetch full records after completion. Cancel needs job_id only."
        ),
    }


def _expand_advanced_options(
    kwargs: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    advanced = kwargs.pop("advanced_options", None)
    if not _is_present(advanced):
        return kwargs, None
    if not isinstance(advanced, dict):
        return None, "错误：advanced_options 必须是对象。"
    duplicates = sorted(set(kwargs) & set(advanced))
    if duplicates:
        return (
            None,
            f"错误：参数不能同时放在顶层和 advanced_options：{', '.join(duplicates)}",
        )
    return {**kwargs, **advanced}, None


_CRAWL_ACTION_FIELDS = {
    "start": {
        "url",
        "limit",
        "depth",
        "formats",
        "render",
        "source",
        "max_age",
        "modified_since",
        "crawl_purposes",
        "content_use",
        "options",
        "json_options",
        "cache_ttl",
        "advanced_options",
        "goto_options",
        "wait_for_selector",
        "wait_for_timeout",
        "set_javascript_enabled",
        "allow_resource_types",
        "reject_resource_types",
        "best_attempt",
        "viewport",
        "action_timeout",
        "user_agent",
        "emulate_media_type",
        "allow_request_pattern",
        "reject_request_pattern",
        "set_extra_http_headers",
        "authenticate",
        "cookies",
        "add_script_tag",
        "add_style_tag",
    },
    "status": {"job_id", "status", "cursor", "limit", "cache_ttl"},
    "cancel": {"job_id"},
}


@dataclass(config={"arbitrary_types_allowed": True})
class CloudflareFetchTool(_CloudflareTool):
    name: str = "cf_browser_fetch"
    description: str = (
        "Fetch and render a page with Cloudflare Browser Run. Use markdown for reading, "
        "content for raw HTML, links for navigation, scrape with selectors, json for "
        "structured extraction, accessibility_tree for interactive semantics, or snapshot "
        "for multiple formats in one call."
    )
    parameters: dict = Field(default_factory=_fetch_parameters)

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        mode = kwargs.pop("mode", "markdown")
        endpoints = {
            "markdown": "markdown",
            "content": "content",
            "links": "links",
            "scrape": "scrape",
            "json": "json",
            "accessibility_tree": "accessibilityTree",
            "snapshot": "snapshot",
        }
        if mode not in endpoints:
            return _tool_error(
                CloudflareParamError(
                    f"错误：mode 必须是以下值之一：{', '.join(endpoints)}"
                )
            )
        kwargs, error = _expand_advanced_options(kwargs)
        if error:
            return _tool_error(CloudflareParamError(error))
        runtime = self._runtime()
        if mode == "accessibility_tree":
            coro = runtime.page_accessibility_tree(**kwargs)
        elif mode == "snapshot":
            coro = runtime.page_snapshot(**kwargs)
        else:
            coro = runtime.page(endpoints[mode], **kwargs)
        return await self._call_runtime(coro, context)


def _crawl_start_guidance(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = payload.get("job_id")
    if isinstance(job_id, str) and job_id:
        payload["next_action"] = {
            "tool": "cf_browser_crawl",
            "after_seconds": 5,
            "args": {"action": "status", "job_id": job_id, "limit": 1, "cache_ttl": 0},
            "message": "Poll with limit=1 while running; fetch full records after completion.",
        }
    return payload


@dataclass(config={"arbitrary_types_allowed": True})
class CloudflareCrawlTool(_CloudflareTool):
    name: str = "cf_browser_crawl"
    description: str = (
        "Start, poll, or cancel an asynchronous Cloudflare Browser Run crawl. "
        "Default start uses render=false and markdown for low-cost AI ingestion."
    )
    parameters: dict = Field(default_factory=_crawl_parameters)

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        action = kwargs.pop("action", None)
        if action not in _CRAWL_ACTION_FIELDS:
            return _tool_error(
                CloudflareParamError("错误：action 必须是 start、status 或 cancel。")
            )
        unknown = sorted(set(kwargs) - _CRAWL_ACTION_FIELDS[action])
        if unknown:
            return _tool_error(
                CloudflareParamError(
                    f"错误：action={action} 不支持参数：{', '.join(unknown)}"
                )
            )
        if action == "start":
            kwargs, error = _expand_advanced_options(kwargs)
            if error:
                return _tool_error(CloudflareParamError(error))
            coro = self._runtime().crawl_start(**kwargs)
        elif action == "status":
            coro = self._runtime().crawl_status(**kwargs)
        else:
            coro = self._runtime().crawl_cancel(kwargs.get("job_id"))
        return await self._call_runtime(
            coro, context, _crawl_start_guidance if action == "start" else None
        )


def _validate_crawl_body(body: dict[str, Any]) -> str | None:
    error = _validate_body_enums(body)
    if error:
        return error

    checks = [
        ("formats", CRAWL_FORMATS),
        ("crawl_purposes", CRAWL_PURPOSES),
        ("content_use", CRAWL_CONTENT_USES),
    ]
    for key, allowed in checks:
        values = [str(value) for value in _list_from_value(body.get(key))]
        if values:
            error = _validate_values(values, allowed, key)
            if error:
                return error

    if _is_present(body.get("source")) and str(body["source"]) not in LINK_SOURCES:
        return f"错误：source 必须是以下值之一：{', '.join(LINK_SOURCES)}"
    for field_name, minimum, maximum in (
        ("depth", 1, 100000),
        ("max_age", 0, 604800),
        ("modified_since", 1, None),
    ):
        if not _is_present(body.get(field_name)):
            continue
        try:
            value = _validated_int(body[field_name], field_name, 0, minimum, maximum)
        except CloudflareParamError as exc:
            return str(exc)
        body[field_name] = value
        if field_name == "modified_since":
            now = int(time.time())
            one_year_ago = now - 365 * 24 * 60 * 60
            if value < one_year_ago or value > now:
                return "错误：modified_since 必须是最近一年内、且不晚于当前时间的 Unix 秒级时间戳。"
    formats = [str(value) for value in _list_from_value(body.get("formats"))]
    if "json" in formats and not _is_present(body.get("json_options")):
        return "错误：formats 包含 json 时必须提供 json_options。"
    return None


TOOL_CLASSES = (
    CloudflareFetchTool,
    CloudflareCrawlTool,
)


def build_tools(
    config: dict[str, Any], runtime: CloudflareBrowserRuntime | None = None
) -> tuple[list[FunctionTool], list[str]]:
    """创建本插件的全部 LLM Tool；传入 runtime 时与 Main、SDK 共用同一实例。"""
    if runtime is None:
        runtime = CloudflareBrowserRuntime(config, get_plugin_data_dir())
    tools: list[FunctionTool] = []
    names: list[str] = []
    for tool_cls in TOOL_CLASSES:
        tool = tool_cls(runtime=runtime)
        tools.append(tool)
        names.append(tool.name)
    return tools, names
