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
from ..logger import get_logger
from .registry import get_tool

class ToolExecutionError(Exception):
    """工具执行异常。"""

logger = get_logger(__name__)


# 工具结果序列化后超过这个字节数时，会自动截断后再回灌给 LLM，
# 防止单次 scene_query 把上下文窗口直接打爆。可由 Profile 覆盖。
DEFAULT_RESULT_MAX_BYTES = 16 * 1024


class ToolDispatcher(object):
    """工具调度器。"""

    def __init__(
        self,
        wrap_undo=True,                 # type: bool
        confirm_callback=None,          # type: Optional[Callable[[str, Dict], bool]]
        timeout=120.0,                  # type: float
        result_max_bytes=DEFAULT_RESULT_MAX_BYTES,  # type: int
    ):
        """
        :param wrap_undo: 全局 undo 开关
        :param confirm_callback: dangerous 工具执行前的确认回调，
            签名 (tool_name, arguments) -> bool；返回 False 则取消执行
        :param timeout: 单次工具执行的主线程超时（秒）
        :param result_max_bytes: 结果 JSON 序列化后的字节上限。超过时会
            把 ``result`` 字段替换为截断版本，并附加 ``__truncated__``
            元信息让 LLM 知道有省略。0 或负数表示不截断。
        """
        self._wrap_undo = wrap_undo
        self._confirm_cb = confirm_callback
        self._timeout = timeout
        self._result_max_bytes = int(result_max_bytes)

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
            logger.error("工具 %s 执行异常:\n%s", tool_name, tb)
            return _err(
                "{}: {}".format(type(exc).__name__, exc),
                "exec_error",
            )

        # 3. 序列化兜底：保证返回值可被 json.dumps
        safe = _safe_serialize(result)
        out = {"ok": True, "result": safe}
        # 4. 结果体积裁剪：避免 list_scene_objects 这类返回数千项的
        #    工具一次性把上下文打爆。
        if self._result_max_bytes > 0:
            out = _maybe_truncate_result(
                out, self._result_max_bytes, tool_name=tool_name,
            )
        return out

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


def _maybe_truncate_result(out, max_bytes, tool_name=""):
    # type: (Dict[str, Any], int, str) -> Dict[str, Any]
    """如果序列化后超出 max_bytes，把 result 字段替换为截断版本。

    策略：
    - 整体 dump 一次估算大小；不超就原样返回
    - 超出时根据 result 的形状裁剪：
        * list/tuple：保留前 N 项，N 自适应到不超 max_bytes
        * dict：用前 N 个 key（按字典顺序，保证可重现）
        * str：直接按字节截断
        * 其他：转 str 后截断
    - 在结果里加上 ``__truncated__`` 字段告诉 LLM 此处被裁剪了，
      方便 LLM 决定要不要带 ``limit/offset/filter`` 重试。
    """
    try:
        body = json.dumps(out, ensure_ascii=False)
    except Exception:  # pylint: disable=broad-except
        return out
    raw_size = len(body.encode("utf-8", errors="replace"))
    if raw_size <= max_bytes:
        return out

    result = out.get("result")
    truncated_info = {
        "tool": tool_name,
        "original_bytes": raw_size,
        "max_bytes": max_bytes,
        "hint": (
            "结果过大已被自动截断。如需完整数据，请用 limit/offset/filter "
            "等参数缩小范围后重试。"
        ),
    }

    if isinstance(result, list):
        new_result, kept = _truncate_list(result, max_bytes)
        truncated_info["original_count"] = len(result)
        truncated_info["kept_count"] = kept
        out["result"] = new_result
    elif isinstance(result, dict):
        new_result, kept = _truncate_dict(result, max_bytes)
        truncated_info["original_keys"] = len(result)
        truncated_info["kept_keys"] = kept
        out["result"] = new_result
    elif isinstance(result, str):
        # 字符串直接按字符数估算
        approx_chars = max(1, max_bytes // 2)
        out["result"] = (
            result[:approx_chars] + "...(truncated)"
            if len(result) > approx_chars else result
        )
        truncated_info["original_chars"] = len(result)
    else:
        # 其他奇怪类型：转字符串截断
        s = str(result)
        approx_chars = max(1, max_bytes // 2)
        out["result"] = s[:approx_chars] + "...(truncated)"

    out["__truncated__"] = truncated_info
    return out


def _truncate_list(items, max_bytes):
    # type: (list, int) -> "tuple[list, int]"
    """按 max_bytes 二分式选保留多少项。"""
    if not items:
        return [], 0
    n = len(items)
    # 以"先尝试整体留一半，不行再减半"的方式，最多 6 轮逼近
    keep = n
    for _ in range(6):
        candidate = items[:keep]
        try:
            size = len(
                json.dumps(candidate, ensure_ascii=False).encode("utf-8"),
            )
        except Exception:  # pylint: disable=broad-except
            size = max_bytes + 1
        if size <= max_bytes:
            break
        keep = max(1, keep // 2)
    if keep < n:
        return items[:keep] + ["...(truncated, {} more items omitted)".format(
            n - keep,
        )], keep
    return items, n


def _truncate_dict(d, max_bytes):
    # type: (dict, int) -> "tuple[dict, int]"
    """字典按 key 顺序保留前若干项。"""
    if not d:
        return {}, 0
    keys = list(d.keys())
    keep = len(keys)
    for _ in range(6):
        candidate = {k: d[k] for k in keys[:keep]}
        try:
            size = len(
                json.dumps(candidate, ensure_ascii=False).encode("utf-8"),
            )
        except Exception:  # pylint: disable=broad-except
            size = max_bytes + 1
        if size <= max_bytes:
            break
        keep = max(1, keep // 2)
    if keep < len(keys):
        out_dict = {k: d[k] for k in keys[:keep]}
        out_dict["__omitted__"] = "{} more keys omitted".format(
            len(keys) - keep,
        )
        return out_dict, keep
    return d, len(keys)