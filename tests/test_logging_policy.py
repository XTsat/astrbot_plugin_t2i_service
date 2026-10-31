"""日志规范的回归测试（上架审核要求）。

审核口径：logger 必须且只能从 ``astrbot.api`` 导入，禁止第三方日志库与标准库
``logging``；而核心层为了能被独立服务复用又不能 import ``astrbot``，所以它只保留
一个可注入的出口，由插件入口把 ``astrbot.api.logger`` 塞进去。这些用例按源码
AST 守住这条线，避免下次改代码又把降级分支加回来。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = REPO_ROOT / "t2i_core"
PLUGIN_DIR = REPO_ROOT / "t2i_plugin"
PLUGIN_ENTRY = REPO_ROOT / "main.py"
STANDALONE_ENTRY = REPO_ROOT / "service" / "main.py"

FORBIDDEN_MODULES = {"logging", "loguru"}


def _python_files(*roots: Path) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend(sorted(root.glob("*.py")) if root.is_dir() else [root])
    return files


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def _uses_logger(path: Path) -> bool:
    return any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "logger"
        for node in ast.walk(_tree(path))
    )


def _imports_logger_from_astrbot_api(path: Path) -> bool:
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.module == "astrbot.api":
            if any(alias.name == "logger" for alias in node.names):
                return True
    return False


def test_no_forbidden_logging_library_is_imported() -> None:
    """插件层、核心层、两个入口都不许用标准库 logging 或第三方日志库。"""
    offenders = {
        str(path.relative_to(REPO_ROOT)): sorted(
            _imported_modules(path) & FORBIDDEN_MODULES
        )
        for path in _python_files(CORE_DIR, PLUGIN_DIR, PLUGIN_ENTRY, STANDALONE_ENTRY)
    }
    assert not {name: hits for name, hits in offenders.items() if hits}


def test_core_layer_does_not_import_astrbot() -> None:
    """核心层要能被独立服务复用，日志只能靠宿主注入。"""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _python_files(CORE_DIR)
        if "astrbot" in _imported_modules(path)
    ]
    assert not offenders


def test_plugin_layer_logs_via_astrbot_api() -> None:
    """插件层每个会打日志的模块都直接用 astrbot.api 的 logger，不留降级分支。"""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _python_files(PLUGIN_DIR) + [PLUGIN_ENTRY]
        if _uses_logger(path) and not _imports_logger_from_astrbot_api(path)
    ]
    assert not offenders


def test_plugin_entry_injects_host_logger_into_core() -> None:
    """插件模式必须把宿主的 logger 注入核心层，否则核心层日志落到兜底出口。"""
    source = PLUGIN_ENTRY.read_text(encoding="utf-8")
    assert "set_logger as set_core_logger" in source
    assert "set_core_logger(logger," in source
