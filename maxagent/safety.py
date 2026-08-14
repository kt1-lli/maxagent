#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安全护栏：场景级自动备份与逃生舱静态扫描。

职责：
1. 在危险/写入操作前自动保存临时 Max 文件，作为场景级逃生舱。
2. 对 Python 逃生舱代码做轻量 AST 黑名单扫描。
3. 对 MaxScript 逃生舱做关键字/正则拦截。

注意：这不是真正的沙箱，只是降低误操作损害的保险层。
"""

from __future__ import absolute_import
from __future__ import print_function

import ast
import os
import re
import threading
import time
from datetime import datetime
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from .config import get_config_dir
from .logger import get_logger
from .runtime_helpers import IN_MAX
from .runtime_helpers import run_on_main
from .runtime_helpers import rt


logger = get_logger(__name__)


# ---------------------------------------------------------------------- #
# 场景级自动备份
# ---------------------------------------------------------------------- #

# 最近 N 次备份保留，避免无限增长
_MAX_AUTOSAVE_FILES = 10

# 同一次对话/批次内，两次备份最小间隔（秒），防止高频操作刷爆磁盘
_MIN_BACKUP_INTERVAL_SEC = 5.0

_last_backup_at = 0.0
_backup_lock = threading.Lock()


def _ensure_autosave_dir() -> str:
    """返回并创建备份目录。"""
    path = os.path.join(get_config_dir(), "autosave")
    os.makedirs(path, exist_ok=True)
    return path


def _cleanup_old_autosaves(directory: str, keep: int = _MAX_AUTOSAVE_FILES) -> None:
    """只保留最近的 keep 个备份文件，按修改时间排序。"""
    try:
        files = [
            os.path.join(directory, f)
            for f in os.listdir(directory)
            if f.startswith("maxagent_") and f.endswith(".max")
        ]
        files.sort(key=os.path.getmtime, reverse=True)
        for old in files[keep:]:
            try:
                os.remove(old)
            except OSError:
                pass
    except OSError:
        pass


def _save_temp_max_file_main() -> Optional[str]:
    """在主线程执行 saveTempMaxFile 并返回路径。"""
    if not IN_MAX or rt is None:
        logger.warning("非 Max 环境，跳过场景自动备份")
        return None
    try:
        directory = _ensure_autosave_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        path = os.path.join(directory, "maxagent_{}.max".format(timestamp))
        # saveTempMaxFile 是 MaxScript 全局函数，保存当前场景的临时副本
        rt.saveTempMaxFile(path)
        return path
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("场景自动备份失败: %s", exc)
        return None


def backup_scene_if_needed(
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    force: bool = False,
) -> Optional[str]:
    """必要时对当前场景做一次临时备份。

    :param tool_name: 触发备份的工具名（用于日志）
    :param arguments: 工具参数
    :param force: 忽略时间间隔强制备份
    :returns: 备份文件路径，未备份或失败返回 None
    """
    global _last_backup_at  # pylint: disable=global-statement

    if not IN_MAX:
        return None

    with _backup_lock:
        now = time.time()
        if not force and (now - _last_backup_at) < _MIN_BACKUP_INTERVAL_SEC:
            return None

        path = run_on_main(_save_temp_max_file_main, _timeout=60.0)
        if path:
            _last_backup_at = now
            _cleanup_old_autosaves(os.path.dirname(path))
            logger.info(
                "场景已自动备份: tool=%s path=%s", tool_name, path,
            )
        return path


# ---------------------------------------------------------------------- #
# 风险判定：哪些工具触发自动备份
# ---------------------------------------------------------------------- #

# 只读前缀：不会修改场景，无需备份
_READONLY_PREFIXES = (
    'list_', 'get_', 'query_', 'count_', 'find_',
    'is_', 'has_', 'check_', 'describe_', 'build_scene_snapshot',
    'diff_snapshots', 'read_', 'search_', 'inspect_',
    'capture_',
)

# 高风险工具名：即使单次也备份
_HIGH_RISK_NAMES = frozenset({
    'delete_objects',
    'save_max_file',
    'load_max_file',
    'merge_max_file',
    'import_file',
    'run_python',
    'run_maxscript',
    'clear_scene',
    'reset_max',
})


def classify_risk_for_backup(tool_name):
    # type: (str) -> str
    """判断工具操作风险等级，用于决定是否触发场景自动备份。

    :returns: 'read' / 'write' / 'high_risk'
    """
    if not tool_name:
        return 'write'
    if tool_name in _HIGH_RISK_NAMES:
        return 'high_risk'
    for pref in _READONLY_PREFIXES:
        if tool_name.startswith(pref):
            return 'read'
    return 'write'


# ---------------------------------------------------------------------- #
# 能力策略（Capability Profile）
# ---------------------------------------------------------------------- #

class CapabilityProfile(object):
    """定义一组允许/禁止的能力。"""

    def __init__(
        self,
        allow_file_delete: bool = False,
        allow_shell: bool = False,
        allow_network: bool = False,
        allow_dotnet_reflection: bool = False,
        allow_python_os_sys: bool = False,
        allow_scene_reset: bool = False,
    ):
        self.allow_file_delete = allow_file_delete
        self.allow_shell = allow_shell
        self.allow_network = allow_network
        self.allow_dotnet_reflection = allow_dotnet_reflection
        self.allow_python_os_sys = allow_python_os_sys
        self.allow_scene_reset = allow_scene_reset


# 默认保守策略：只保留核心场景操作
DEFAULT_CAPABILITY = CapabilityProfile()

# 用户显式开启"允许所有"时的宽松策略
PERMISSIVE_CAPABILITY = CapabilityProfile(
    allow_file_delete=True,
    allow_shell=True,
    allow_network=True,
    allow_dotnet_reflection=True,
    allow_python_os_sys=True,
    allow_scene_reset=True,
)


def get_capability_from_config() -> CapabilityProfile:
    """从 AppConfig 构造当前能力策略。"""
    try:
        from .config import load_config
        cfg = load_config()
        # 只有同时开启逃生舱 + 关闭执行前确认，才给宽松策略
        if cfg.allow_escape_hatch and not cfg.confirm_before_exec:
            return PERMISSIVE_CAPABILITY
    except Exception:  # pylint: disable=broad-except
        pass
    return DEFAULT_CAPABILITY


# ---------------------------------------------------------------------- #
# Python 逃生舱 AST 静态扫描
# ---------------------------------------------------------------------- #

# 禁止的 Python 内置函数/模块名（不区分大小写没有意义，Python 是大小写敏感语言）
_PYTHON_FORBIDDEN_BUILTINS = frozenset({
    "__import__", "open", "exec", "eval", "compile",
})

# 禁止的属性访问链：os.system、sys.exit、socket.connect 等
_PYTHON_FORBIDDEN_ATTRS = frozenset({
    ("os", "system"), ("os", "popen"), ("os", "remove"),
    ("os", "unlink"), ("os", "rmdir"), ("os", "removedirs"),
    ("sys", "exit"), ("sys", "modules"),
    ("subprocess", "call"), ("subprocess", "run"),
    ("subprocess", "Popen"), ("subprocess", "check_output"),
    ("urllib", "urlopen"), ("urllib", "request"),
    ("socket", "connect"), ("socket", "create_connection"),
})


def _is_forbidden_attr(node: ast.AST) -> Optional[Tuple[str, ...]]:
    """识别 node 是否是 forbidden 属性链。"""
    names = []
    current = node
    while isinstance(current, ast.Attribute):
        names.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        names.append(current.id)
    else:
        return None
    names.reverse()
    if len(names) < 2:
        return None
    # 只检查前两级即可拦截 os.system
    return tuple(names[:2]) if tuple(names[:2]) in _PYTHON_FORBIDDEN_ATTRS else None


class _PythonSafetyVisitor(ast.NodeVisitor):
    """遍历 AST，收集危险调用。"""

    def __init__(self, cap: CapabilityProfile):
        self.cap = cap
        self.violations: List[str] = []

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        if self.cap.allow_python_os_sys:
            return
        for alias in node.names:
            if alias.name in ("os", "sys"):
                self.violations.append(
                    "禁止导入模块 '{}'".format(alias.name),
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if self.cap.allow_python_os_sys:
            return
        if node.module in ("os", "sys"):
            self.violations.append(
                "禁止从模块 '{}' 导入".format(node.module),
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in _PYTHON_FORBIDDEN_BUILTINS:
                self.violations.append(
                    "禁止调用内置函数 '{}'".format(func.id),
                )
        elif isinstance(func, ast.Attribute):
            chain = _is_forbidden_attr(func)
            if chain is not None:
                self.violations.append(
                    "禁止调用 '{}.{}'".format(chain[0], chain[1]),
                )
        self.generic_visit(node)


def scan_python_code(
    code: str,
    cap: Optional[CapabilityProfile] = None,
) -> Tuple[bool, str]:
    """对 Python 逃生舱代码做静态安全扫描。

    :returns: (ok, reason) ok=True 表示通过
    """
    if cap is None:
        cap = get_capability_from_config()
    if cap.allow_python_os_sys and cap.allow_shell and cap.allow_network:
        # 全开时跳过扫描，减少开销
        return (True, "")

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return (False, "Python 语法错误: {}".format(exc))
    visitor = _PythonSafetyVisitor(cap)
    visitor.visit(tree)
    if visitor.violations:
        return (False, "；".join(visitor.violations))
    return (True, "")


# ---------------------------------------------------------------------- #
# MaxScript 逃生舱字符串扫描
# ---------------------------------------------------------------------- #

# MaxScript 危险关键字正则（大小写不敏感）
_MAXSCRIPT_DANGEROUS_PATTERNS = [
    (re.compile(r"\bDOSCommand\b", re.IGNORECASE), "DOSCommand"),
    (re.compile(r"\bShellLaunch\b", re.IGNORECASE), "ShellLaunch"),
    (re.compile(r"\bdeleteFile\b", re.IGNORECASE), "deleteFile"),
    (re.compile(r"\bdeleteAllChangeHandlers\b", re.IGNORECASE), "deleteAllChangeHandlers"),
    (re.compile(r"\bShellExecute\b", re.IGNORECASE), "ShellExecute"),
    (re.compile(r"\bdotNetClass\b", re.IGNORECASE), "dotNetClass"),
    (re.compile(r"\bdotNetObject\b", re.IGNORECASE), "dotNetObject"),
    (re.compile(r"\bdotNetControl\b", re.IGNORECASE), "dotNetControl"),
    (re.compile(r"\bLoadDll\b", re.IGNORECASE), "LoadDll"),
    (re.compile(r"\bfreeSceneBitmaps\b", re.IGNORECASE), "freeSceneBitmaps"),
    (re.compile(r"\bresetMaxFile\b", re.IGNORECASE), "resetMaxFile"),
]


def scan_maxscript_code(
    code: str,
    cap: Optional[CapabilityProfile] = None,
) -> Tuple[bool, str]:
    """对 MaxScript 逃生舱代码做字符串级危险扫描。

    :returns: (ok, reason) ok=True 表示通过
    """
    if cap is None:
        cap = get_capability_from_config()
    if cap.allow_shell and cap.allow_file_delete and cap.allow_dotnet_reflection:
        return (True, "")

    found = []
    for pattern, keyword in _MAXSCRIPT_DANGEROUS_PATTERNS:
        if pattern.search(code):
            # 根据 capability 决定是否报错
            if keyword in ("DOSCommand", "ShellLaunch", "ShellExecute") and cap.allow_shell:
                continue
            if keyword == "deleteFile" and cap.allow_file_delete:
                continue
            if keyword.startswith("dotNet") and cap.allow_dotnet_reflection:
                continue
            if keyword == "resetMaxFile" and cap.allow_scene_reset:
                continue
            found.append(keyword)

    if found:
        return (
            False,
            "MaxScript 包含高风险调用：{}".format(", ".join(sorted(set(found)))),
        )
    return (True, "")


# ---------------------------------------------------------------------- #
# 统一校验入口
# ---------------------------------------------------------------------- #

def validate_escape_hatch(
    tool_name: str,
    code: str,
    cap: Optional[CapabilityProfile] = None,
) -> Tuple[bool, str]:
    """根据工具名选择对应的扫描器。

    :param tool_name: 'run_python' 或 'run_maxscript'
    :param code: 要执行的代码字符串
    :returns: (ok, reason)
    """
    if cap is None:
        cap = get_capability_from_config()
    if tool_name == "run_python":
        return scan_python_code(code, cap)
    if tool_name == "run_maxscript":
        return scan_maxscript_code(code, cap)
    return (True, "")