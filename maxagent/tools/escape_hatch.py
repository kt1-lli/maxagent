#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脚本逃生舱：让 agent 在预定义工具不够用时，直接执行 MaxScript 或 Python。

这是覆盖"尽可能多的 Max 操作"的关键 —— LLM 可现场写脚本完成任何 Max 能做的事。

⚠️ 安全：
- 这两个工具被标记为 dangerous=True，dispatcher 会调用 confirm_callback 弹窗确认；
- 上层应根据 AppConfig.allow_escape_hatch / confirm_before_exec 决定是否注册。
"""

from __future__ import absolute_import
from __future__ import print_function

import io
import sys
import traceback
from typing import Any
from typing import Dict

from ..runtime_helpers import IN_MAX
from ..runtime_helpers import rt
from .registry import tool


# ---------------------------------------------------------------------- #
# MaxScript 逃生舱
# ---------------------------------------------------------------------- #

@tool(
    name="run_maxscript",
    description=(
        "在 3ds Max 中执行任意 MaxScript 代码字符串，返回 MaxScript 表达式的结果。"
        "适用于预定义工具未覆盖的场景，例如复杂的修改器栈操作、特殊插件调用、"
        "查询冷门属性等。注意：写出的代码会作为 rt.execute() 的字符串执行，"
        "请注意 MaxScript 字符串转义。"
    ),
    category="escape_hatch",
    dangerous=True,
    wrap_undo=True,
    run_on_main_thread=True,
)
def run_maxscript(code):
    """执行 MaxScript 代码。

    :param code: 完整的 MaxScript 源码。可以是单行表达式（如 ``$.position``）
                 或多行语句块（如 ``for o in objects do ( ... )``）。
                 多行时建议用 MaxScript 的 ``(`` ``)`` 包裹。
    :returns: dict: {"value": 字符串化的返回值, "success": True}
    """
    if not IN_MAX:
        return {"success": False, "error": "非 3ds Max 环境"}
    try:
        result = rt.execute(code)
    except Exception as exc:  # pylint: disable=broad-except
        return {
            "success": False,
            "error": "{}: {}".format(type(exc).__name__, exc),
            "traceback": traceback.format_exc(),
        }
    # MaxScript 返回值类型多样，统一转字符串供 LLM 阅读
    try:
        text = str(result)
    except Exception:  # pylint: disable=broad-except
        text = "<unprintable>"
    return {"success": True, "value": text}


# ---------------------------------------------------------------------- #
# Python (pymxs) 逃生舱
# ---------------------------------------------------------------------- #

@tool(
    name="run_python",
    description=(
        "在 3ds Max 进程内执行任意 Python 代码（已注入 pymxs.runtime 为 rt）。"
        "适用于希望直接用 Python 操作 Max 的场景，比 MaxScript 更易写复杂逻辑。"
        "执行环境中已可用：pymxs、rt（=pymxs.runtime）。"
        "可以通过 print() 输出，所有 stdout 会被捕获并返回。"
        "若需要返回值，请把结果赋给变量 result。"
    ),
    category="escape_hatch",
    dangerous=True,
    wrap_undo=True,
    run_on_main_thread=True,
)
def run_python(code):
    """执行 Python 代码。

    :param code: 完整的 Python 源码。如希望返回值，请设置变量 ``result``，
                 例如：``result = len(rt.objects)``。所有 print 输出会被捕获。
    :returns: dict: {"success": True, "stdout": "...", "result": <值的字符串>}
    """
    if not IN_MAX:
        return {"success": False, "error": "非 3ds Max 环境"}

    # 准备执行环境，注入 rt 与 pymxs
    try:
        import pymxs as _pymxs  # type: ignore  # pylint: disable=import-error
    except ImportError as exc:
        return {"success": False, "error": "pymxs 不可用: {}".format(exc)}

    sandbox_globals = {
        "__builtins__": __builtins__,
        "pymxs": _pymxs,
        "rt": _pymxs.runtime,
    }  # type: Dict[str, Any]
    sandbox_locals = {}  # type: Dict[str, Any]

    # 重定向 stdout 以便捕获 print
    old_stdout = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        exec(code, sandbox_globals, sandbox_locals)  # pylint: disable=exec-used
    except Exception as exc:  # pylint: disable=broad-except
        sys.stdout = old_stdout
        return {
            "success": False,
            "error": "{}: {}".format(type(exc).__name__, exc),
            "traceback": traceback.format_exc(),
            "stdout": buf.getvalue(),
        }
    finally:
        sys.stdout = old_stdout

    result_val = sandbox_locals.get("result", None)
    try:
        result_str = repr(result_val)
    except Exception:  # pylint: disable=broad-except
        result_str = "<unrepr>"

    return {
        "success": True,
        "stdout": buf.getvalue(),
        "result": result_str,
    }


# ---------------------------------------------------------------------- #
# 注册控制：上层可根据用户配置决定是否注册逃生舱
# ---------------------------------------------------------------------- #

def unregister_escape_hatch():
    """从全局注册表移除逃生舱（用于安全模式）。"""
    from .registry import _REGISTRY  # pylint: disable=import-outside-toplevel
    for name in ("run_maxscript", "run_python"):
        _REGISTRY.pop(name, None)
