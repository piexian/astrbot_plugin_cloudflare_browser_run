"""日志规约：插件源码的 logger 一律来自 astrbot.api，禁止自建 logging。"""

from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# 允许自建 logger 的模块白名单；保持为空，新增需给出无法使用 astrbot logger 的理由
SELF_LOGGER_WHITELIST: set[str] = set()

MODULE_FILES = [
    "main.py",
    "plugin_service.py",
    *sorted(
        p.relative_to(PLUGIN_ROOT).as_posix()
        for p in (PLUGIN_ROOT / "tools").glob("*.py")
    ),
]


def test_no_self_built_logger():
    for rel in MODULE_FILES:
        if rel in SELF_LOGGER_WHITELIST:
            continue
        source = (PLUGIN_ROOT / rel).read_text(encoding="utf-8")
        assert "import logging" not in source, (
            f"{rel}: 禁止自建 logging，logger 只能从 astrbot.api 导入"
        )
        assert "getLogger" not in source, f"{rel}: 禁止 getLogger"


def test_logger_usage_imports_from_astrbot():
    for rel in MODULE_FILES:
        source = (PLUGIN_ROOT / rel).read_text(encoding="utf-8")
        if "logger." not in source:
            continue
        assert "from astrbot.api import logger" in source.replace("\n", " "), (
            f"{rel}: 使用 logger 前需 from astrbot.api import logger"
        )
