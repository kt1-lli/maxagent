#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具调用调度器。

职责：
1. 根据 LLM 返回的 tool_calls，定位到 ToolSpec 并执行。
2. 自动 marshal 到 Max 主线程（如果工具声明 run_on_main_thread=True）。
3. 自动包 undo 上下文（如果工具声明 wrap_undo=True 且全局允许）。
4. 危险工具执行前调用 confirm_callback 进行人机确认。
5. 异常兜底：工具异常不会让 agent 崩溃，会以结构化错误返回给 LLM 让它继续推理。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import traceback
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional

from ..runtime_helpers import IN_MAX
from ..runtime_helpers import run_on_main
from ..runtime_helpers import undo_block
from .registry import get_tool


class ToolExecutionError(Exception):
    """工具执行异常。"""


class ToolDispatcher(object):
    """工具调度器。"""

    def __init__(
        self,
        wrap_undo=True,                 # type: bool
        confirm_callback=None,          # type: Optional[Callable[[str, Dict], bool]]
        timeout=120.0,                  # type: float
    ):
        """
        :param wrap_undo: 全局 undo 开关
        :param confirm_callback: dangerous 工具执行前的确认回调，
            签名 (tool_name, arguments) -> bool；返回 False 则取消执行
        :param timeout: 单次工具执行的主线程超时（秒）
        """
        self._wrap_undo = wrap_undo
        self._confirm_cb = confirm_callback
        self._timeout = timeout

    # ------------------------------------------------------------------ #
    # 公共接口
    # ------------------------------------------------------------------ #

    def dispatch(self, tool_name, arguments):
        # type: (str, Dict[str, Any]) -> Dict[str, Any]
        """执行单个工具调用。

        :param tool_name: 工具名
        :param arguments: 参数字典（已是 dict，由 LLMClient 解析过 JSON）
        :returns: 标准化结果 dict：
                  成功: {"ok": True, "result": <任意可序列化对象>}
                  失败: {"ok": False, "error": "...", "type": "..."}
        """
        spec = get_tool(tool_name)
        if spec is None:
            return _err("未知工具: {}".format(tool_name), "unknown_tool")

        if not isinstance(arguments, dict):
            return _err(
                "参数必须是对象，收到: {}".format(type(arguments).__name__),
                "bad_arguments",
            )

        # 1. 危险工具确认
        if spec.dangerous and self._confirm_cb is not None:
            try:
                if not self._confirm_cb(tool_name, arguments):
                    return _err("用户已取消执行", "user_cancelled")
            except Exception as exc:  # pylint: disable=broad-except
                return _err(
                    "确认回调异常: {}".format(exc), "confirm_error",
                )

        # 2. 实际执行
        try:
            result = self._invoke(spec, arguments)
        except TimeoutError as exc:
            return _err(str(exc), "timeout")
        except Exception as exc:  # pylint: disable=broad-except
            tb = traceback.format_exc()
            print("[maxagent] 工具 {} 执行异常:\n{}".format(tool_name, tb))
            return _err(
                "{}: {}".format(type(exc).__name__, exc),
                "exec_error",
            )

        # 3. 序列化兜底：保证返回值可被 json.dumps
        return {"ok": True, "result": _safe_serialize(result)}

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _invoke(self, spec, arguments):
        """根据 spec 决定如何调用 func。"""
        do_undo = self._wrap_undo and spec.wrap_undo

        def _call_with_undo():
            if do_undo:
                with undo_block("agent: " + spec.name):
                    return spec.func(**arguments)
            return spec.func(**arguments)

        if spec.run_on_main_thread and IN_MAX:
            return run_on_main(_call_with_undo, _timeout=self._timeout)
        return _call_with_undo()


# ---------------------------------------------------------------------- #
# 辅助
# ---------------------------------------------------------------------- #

def _err(msg, kind):
    return {"ok": False, "error": msg, "type": kind}


def _safe_serialize(obj):
    """把 pymxs 返回的对象转成可 JSON 序列化的纯 Python 类型。

    pymxs 的 Point3 / Color / 节点对象等不是原生 Python 类型，必须降级。
    """
    if obj is None:
        return None
    if isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(x) for x in obj]
    # 尝试 dict / list 化
    if hasattr(obj, "_asdict"):
        try:
            return _safe_serialize(obj._asdict())
        except Exception:  # pylint: disable=broad-except
            pass
    # pymxs Point3 / Color 等通常有 .x .y .z 或可索引
    for attrs in (("x", "y", "z"), ("r", "g", "b", "a")):
        if all(hasattr(obj, a) for a in attrs):
            return [getattr(obj, a) for a in attrs]
    # 兜底：转字符串，避免抛出
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        try:
            return str(obj)
        except Exception:  # pylint: disable=broad-except
            return "<unserializable>"
