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
import time
import traceback
from typing import Any
from typing import Dict

from ..runtime_helpers import IN_MAX
from ..runtime_helpers import rt
from ..logger import get_logger
from .registry import tool


logger = get_logger(__name__)


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
        "\n⚠️ 生成 MaxScript 必须遵守系统提示词【MaxScript 专用规则】："
        "所有变量用 local/global 显式声明；函数用 return 显式返回；"
        "if 控制流模板：有 else 用 `if c then (...) else (...)`；"
        "无 else 用 `if c do (...)`；**严禁** `if c do (...) else (...)`，"
        "do 永远不配 else，本工具入口会拦截并拒绝执行；"
        "for 用 in/=...to/collect；数组索引从 1 开始；"
        "标识符用英文 camelCase；注释用中文。"
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

    code_len = len(code) if isinstance(code, str) else 0
    t0 = time.time()

    # 入口校验：拦截已知硬性语法错误（如 if-do-else），让 LLM 自纠
    # 校验器位于 agent 层，避免 tools 反向依赖，这里就近导入
    try:
        from ..agent.coding_rules import validate_maxscript_syntax  # noqa: WPS433
    except ImportError:
        validate_maxscript_syntax = None  # 极端情况下放行，不阻断业务
    if validate_maxscript_syntax is not None:
        ok, hint = validate_maxscript_syntax(code)
        if not ok:
            logger.info(
                "run_maxscript rejected by validator code_len=%d hint=%s",
                code_len, hint,
            )
            return {
                "success": False,
                "error": hint,
                "rejected_by_validator": True,
            }
    t_validate = (time.time() - t0) * 1000

    # 实际执行
    t1 = time.time()
    try:
        result = rt.execute(code)
    except Exception as exc:  # pylint: disable=broad-except
        elapsed = (time.time() - t1) * 1000
        logger.warning(
            "run_maxscript rt.execute raised after %.0fms code_len=%d: %s",
            elapsed, code_len, exc,
        )
        return {
            "success": False,
            "error": "{}: {}".format(type(exc).__name__, exc),
            "traceback": traceback.format_exc(),
        }
    t_exec = (time.time() - t1) * 1000

    # 返回值字符串化
    t2 = time.time()
    try:
        text = str(result)
    except Exception:  # pylint: disable=broad-except
        text = "<unprintable>"
    t_str = (time.time() - t2) * 1000

    # 仅在 DEBUG 或慢调用时打日志，避免 info 级别刷屏
    total = t_validate + t_exec + t_str
    if total >= 500:
        logger.info(
            "run_maxscript total=%.0fms validate=%.0fms exec=%.0fms"
            " str=%.0fms code_len=%d",
            total, t_validate, t_exec, t_str, code_len,
        )
    elif logger.isEnabledFor(10):
        logger.debug(
            "run_maxscript total=%.0fms validate=%.0fms exec=%.0fms"
            " str=%.0fms code_len=%d",
            total, t_validate, t_exec, t_str, code_len,
        )
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
        "\n⚠️ 生成 Python 必须遵守系统提示词【Python+pymxs 专用规则】："
        "from pymxs import runtime as rt（无需重新写，已注入）；"
        "比较 pymxs 对象用 == 不用 is；访问 pymxs 数组用 0-based、"
        "传索引给 MaxScript 函数用 1-based；MaxScript Name 写成 rt.Name('xxx')；"
        "动画/撤销上下文用 with pymxs.animate(True): / with pymxs.undo(True):；"
        "标识符用英文 camelCase；注释用中文。"
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

    code_len = len(code) if isinstance(code, str) else 0
    t0 = time.time()

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
    t_prepare = (time.time() - t0) * 1000

    # 重定向 stdout 以便捕获 print
    old_stdout = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    t1 = time.time()
    try:
        exec(code, sandbox_globals, sandbox_locals)  # pylint: disable=exec-used
    except Exception as exc:  # pylint: disable=broad-except
        sys.stdout = old_stdout
        elapsed = (time.time() - t1) * 1000
        logger.warning(
            "run_python exec raised after %.0fms code_len=%d: %s",
            elapsed, code_len, exc,
        )
        return {
            "success": False,
            "error": "{}: {}".format(type(exc).__name__, exc),
            "traceback": traceback.format_exc(),
            "stdout": buf.getvalue(),
        }
    finally:
        sys.stdout = old_stdout
    t_exec = (time.time() - t1) * 1000

    t2 = time.time()
    result_val = sandbox_locals.get("result", None)
    try:
        result_str = repr(result_val)
    except Exception:  # pylint: disable=broad-except
        result_str = "<unrepr>"
    t_repr = (time.time() - t2) * 1000

    total = t_prepare + t_exec + t_repr
    if total >= 500:
        logger.info(
            "run_python total=%.0fms prepare=%.0fms exec=%.0fms"
            " repr=%.0fms code_len=%d",
            total, t_prepare, t_exec, t_repr, code_len,
        )
    elif logger.isEnabledFor(10):
        logger.debug(
            "run_python total=%.0fms prepare=%.0fms exec=%.0fms"
            " repr=%.0fms code_len=%d",
            total, t_prepare, t_exec, t_repr, code_len,
        )

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
