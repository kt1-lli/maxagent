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
import time
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
from .registry import validate_tool_args

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
            logger.warning("调用未知工具: %s", tool_name)
            return _err("未知工具: {}".format(tool_name), "unknown_tool")

        # 禁用名单兜底：即便 LLM 通过历史 tool_call_id 试图调用已禁用工具，
        # 这里也直接拒绝。schema 过滤是"让 LLM 看不到"，dispatch 拦截是
        # "看到也调不动"——双层防御。
        try:
            from ..disabled_registry import is_tool_disabled
            if is_tool_disabled(tool_name):
                logger.info("拦截已禁用工具调用: %s", tool_name)
                return _err(
                    "工具 {} 已被用户在「我的资源」中禁用".format(tool_name),
                    "tool_disabled",
                )
        except Exception:  # pylint: disable=broad-except
            # 禁用模块异常不应阻塞正常调用
            pass

        if not isinstance(arguments, dict):
            return _err(
                "参数必须是对象，收到: {}".format(type(arguments).__name__),
                "bad_arguments",
            )

        # DEBUG 埋点：入参摘要（截断长字符串/避免污染日志）
        if logger.isEnabledFor(10):  # logging.DEBUG
            logger.debug(
                "▶ tool=%s on_main=%s wrap_undo=%s args=%s",
                tool_name, spec.run_on_main_thread, spec.wrap_undo,
                _summarize_args(arguments),
            )

        # 阶段计时收集器：confirm / marshal / undo_enter / func / undo_exit /
        # serialize / truncate 各占一格；最后汇总打印，方便定位"耗时大头"。
        stages = {}  # type: Dict[str, float]

        # 1. 危险工具确认
        t_confirm = time.time()
        if spec.dangerous and self._confirm_cb is not None:
            try:
                if not self._confirm_cb(tool_name, arguments):
                    return _err("用户已取消执行", "user_cancelled")
            except Exception as exc:  # pylint: disable=broad-except
                return _err(
                    "确认回调异常: {}".format(exc), "confirm_error",
                )
        stages["confirm"] = (time.time() - t_confirm) * 1000

        # 2. 参数校验前置化：在主线程执行前拦截非法参数
        is_valid, error_msg = validate_tool_args(tool_name, arguments)
        if not is_valid:
            logger.warning("参数校验失败 tool=%s: %s", tool_name, error_msg)
            return _err(error_msg, "bad_arguments")

        # 3. 实际执行（带阶段计时）
        t0 = time.time()
        try:
            result = self._invoke(spec, arguments, stages)
        except TimeoutError as exc:
            elapsed = (time.time() - t0) * 1000
            logger.warning(
                "✗ tool=%s timeout after %.0fms stages=%s: %s",
                tool_name, elapsed, _fmt_stages(stages), exc,
            )
            try:
                from ..user_tools_loader import bump_tool_usage
                bump_tool_usage(tool_name, ok=False)
            except Exception:  # pylint: disable=broad-except
                pass
            return _err(str(exc), "timeout")
        except Exception as exc:  # pylint: disable=broad-except
            elapsed = (time.time() - t0) * 1000
            tb = traceback.format_exc()
            logger.error(
                "✗ tool=%s 执行异常 after %.0fms stages=%s:\n%s",
                tool_name, elapsed, _fmt_stages(stages), tb,
            )
            try:
                from ..user_tools_loader import bump_tool_usage
                bump_tool_usage(tool_name, ok=False)
            except Exception:  # pylint: disable=broad-except
                pass
            return _err(
                "{}: {}".format(type(exc).__name__, exc),
                "exec_error",
            )
        elapsed_ms = (time.time() - t0) * 1000

        # 4. 序列化兜底：保证返回值可被 json.dumps
        t_ser = time.time()

        # D3：工具返回结果增强——对 create_ 类工具，自动附加新对象的
        # 简要摘要（位置、包围盒中心），让 LLM 无需额外查询就能确认
        # 空间状态，减少 "创建后位置未知" 的幻觉。
        safe = _safe_serialize(result)
        if tool_name.startswith('create_') and isinstance(safe, dict):
            safe = _enrich_create_result(safe)

        out = {
            "ok": True,
            "data": safe,
            "error": None,
            "suggestion": None,
        }
        stages["serialize"] = (time.time() - t_ser) * 1000

        # 5. 结果体积裁剪：避免 list_scene_objects 这类返回数千项的
        #    工具一次性把上下文打爆。
        t_trunc = time.time()
        if self._result_max_bytes > 0:
            out = _maybe_truncate_result(
                out, self._result_max_bytes, tool_name=tool_name,
            )
        stages["truncate"] = (time.time() - t_trunc) * 1000

        # 5. 学习工具的使用统计累加（user tools 才有 .meta.json）。
        #    捕获所有异常：进化指标累加绝不能影响主路径返回。
        try:
            from ..user_tools_loader import bump_tool_usage
            bump_tool_usage(tool_name, ok=True)
        except Exception:  # pylint: disable=broad-except
            pass

        # DEBUG 埋点：出参摘要 + 总耗时 + 阶段分布
        # 超过 500ms 自动升到 INFO，便于线上抓到慢调用现场。
        total_ms = (time.time() - t_confirm) * 1000
        if total_ms >= 500:
            logger.info(
                "✔ tool=%s total=%.0fms exec=%.0fms stages=%s result=%s",
                tool_name, total_ms, elapsed_ms,
                _fmt_stages(stages), _summarize_result(out),
            )
        elif logger.isEnabledFor(10):
            logger.debug(
                "✔ tool=%s total=%.0fms exec=%.0fms stages=%s result=%s",
                tool_name, total_ms, elapsed_ms,
                _fmt_stages(stages), _summarize_result(out),
            )
        return out

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _invoke(self, spec, arguments, stages):
        """根据 spec 决定如何调用 func。

        :param stages: 由 dispatch() 传入的阶段计时字典；本函数会按需
                       写入 ``marshal_wait`` / ``undo_enter`` /
                       ``func`` / ``undo_exit`` 等键，便于上层汇总输出。
        """
        do_undo = self._wrap_undo and spec.wrap_undo
        on_main = spec.run_on_main_thread and IN_MAX

        def _call_with_undo():
            # undo_enter / func / undo_exit 三段独立计时；不包 undo
            # 的工具只记 func 一段。
            if do_undo:
                t_enter = time.time()
                with undo_block("agent: " + spec.name):
                    stages["undo_enter"] = (time.time() - t_enter) * 1000
                    t_func = time.time()
                    try:
                        return spec.func(**arguments)
                    finally:
                        stages["func"] = (time.time() - t_func) * 1000
                        # undo_exit 在 with 退出后才知道，先记结束时间戳
                        stages["_undo_exit_t0"] = time.time()
            t_func = time.time()
            try:
                return spec.func(**arguments)
            finally:
                stages["func"] = (time.time() - t_func) * 1000

        if on_main:
            t_marshal = time.time()
            try:
                return run_on_main(_call_with_undo, _timeout=self._timeout)
            finally:
                # marshal_wait 包含：投递 emit + 主线程排队 + 整段主线程
                # 执行（含 undo_enter/func/undo_exit）+ done.set 通知。
                # 减去 func/undo_enter 后剩下的就是"纯排队 + 主线程切换"开销。
                stages["marshal_wait"] = (time.time() - t_marshal) * 1000
                # 补 undo_exit（with 已退出）
                t0 = stages.pop("_undo_exit_t0", None)
                if t0 is not None:
                    stages["undo_exit"] = (time.time() - t0) * 1000
        else:
            try:
                return _call_with_undo()
            finally:
                t0 = stages.pop("_undo_exit_t0", None)
                if t0 is not None:
                    stages["undo_exit"] = (time.time() - t0) * 1000


# ---------------------------------------------------------------------- #
# 辅助
# ---------------------------------------------------------------------- #

def _err(msg, kind):
    # type: (str, str) -> Dict[str, Any]
    """构造统一错误协议，并给出 LLM 可执行的下一步建议。"""
    suggestions = {
        "unknown_tool": (
            "请检查工具名拼写是否准确，并从可用工具列表中选择合适的工具；"
            "如不确定，先调用 list_tools 或 list_all_tools 获取当前可用工具。"
        ),
        "tool_disabled": (
            "该工具已被用户在「我的资源」中禁用；"
            "请改用其他可用工具完成目标，或引导用户在设置中启用该工具。"
        ),
        "bad_arguments": (
            "请重新核对工具 schema 中 required 字段与参数类型，"
            "按规范构造参数对象后重试；必要时可先调用 get_tool_schema 查看详情。"
        ),
        "user_cancelled": (
            "用户已取消本次危险操作；请向用户确认是否继续，"
            "或改用非危险方式完成目标。"
        ),
        "confirm_error": (
            "确认回调发生异常；请稍后重试，或检查 UI 确认流程是否正常。"
        ),
        "timeout": (
            "工具执行超时；建议缩小操作范围（如减少选中对象、降低精度）后重试，"
            "或在设置中调高工具超时时间。"
        ),
        "exec_error": (
            "工具执行期间发生异常；请检查参数合法性、对象是否存在、"
            "以及当前场景状态是否符合工具预期，修正后重试。"
        ),
    }
    return {
        "ok": False,
        "data": None,
        "error": msg,
        "suggestion": suggestions.get(kind, "请检查输入参数与当前场景状态后重试。"),
        "type": kind,
    }


def _fmt_stages(stages):
    # type: (Dict[str, float]) -> str
    """把阶段耗时字典格式化成 ``[k1=12ms k2=345ms]`` 形式，方便日志。

    - 仅打印耗时 >= 1ms 的阶段，避免日志被 0ms 噪声塞满；
    - 按耗时降序排序，最贵的阶段排最前面，第一眼就能定位瓶颈；
    - 跳过下划线开头的内部临时键（如 ``_undo_exit_t0``）。
    """
    items = [
        (k, v) for k, v in stages.items()
        if not k.startswith("_") and v >= 1.0
    ]
    if not items:
        return "[<1ms]"
    items.sort(key=lambda kv: kv[1], reverse=True)
    return "[{}]".format(
        " ".join("{}={:.0f}ms".format(k, v) for k, v in items),
    )


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

    data = out.get("data")
    truncated_info = {
        "tool": tool_name,
        "original_bytes": raw_size,
        "max_bytes": max_bytes,
        "hint": (
            "结果过大已被自动截断。如需完整数据，请用 limit/offset/filter "
            "等参数缩小范围后重试。"
        ),
    }

    if isinstance(data, list):
        new_data, kept = _truncate_list(data, max_bytes)
        truncated_info["original_count"] = len(data)
        truncated_info["kept_count"] = kept
        out["data"] = new_data
    elif isinstance(data, dict):
        new_data, kept = _truncate_dict(data, max_bytes)
        truncated_info["original_keys"] = len(data)
        truncated_info["kept_keys"] = kept
        out["data"] = new_data
    elif isinstance(data, str):
        # 字符串直接按字符数估算
        approx_chars = max(1, max_bytes // 2)
        out["data"] = (
            data[:approx_chars] + "...(truncated)"
            if len(data) > approx_chars else data
        )
        truncated_info["original_chars"] = len(data)
    else:
        # 其他奇怪类型：转字符串截断
        s = str(data)
        approx_chars = max(1, max_bytes // 2)
        out["data"] = s[:approx_chars] + "...(truncated)"

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


def _summarize_args(arguments, max_chars=200):
    # type: (Dict[str, Any], int) -> str
    """把工具入参摘要成一行，适合日志打印。

    长字符串截断为 ``...<N more chars>``，避免污染日志。
    """
    try:
        text = json.dumps(arguments, ensure_ascii=False, default=str)
    except Exception:  # pylint: disable=broad-except
        text = str(arguments)
    if len(text) > max_chars:
        return "{}...<{} more chars>".format(
            text[:max_chars], len(text) - max_chars,
        )
    return text


def _summarize_result(out, max_chars=300):
    # type: (Dict[str, Any], int) -> str
    """把工具出参摘要成一行，仅保留结构与体量信息。"""
    if not isinstance(out, dict):
        return str(out)[:max_chars]
    if not out.get("ok"):
        return "FAILED: {}".format(out.get("error") or "?")[:max_chars]
    data = out.get("data")
    if isinstance(data, list):
        return "list[{}]".format(len(data))
    if isinstance(data, dict):
        keys = list(data.keys())[:5]
        return "dict(keys={}, total={})".format(keys, len(data))
    if isinstance(data, str):
        if len(data) > max_chars:
            return "str[{}]: {}...".format(len(data), data[:max_chars])
        return "str: {}".format(data)
    return "{}={}".format(type(data).__name__, str(data)[:max_chars])


def _enrich_create_result(result):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """对 create_* 工具的返回结果附加对象摘要。

    利用 pymxs 在主线程中已经执行完毕的优势，直接查询刚创建对象的
    关键属性（位置、包围盒），减少 LLM 下一轮 "get_object_info"
    的额外调用。

    :param result: 原始工具返回 dict（含 "name" 键）
    :returns: 增强后的 dict（附加 "position" / "bounding_box" 等）
    """
    name = result.get('name', '')
    if not name:
        return result

    try:
        from pymxs import runtime as rt  # type: ignore[import]
        obj = rt.getNodeByName(name, exact=True, all=False)
        if obj is None:
            return result

        # 位置（世界坐标）
        pos = obj.pos
        result['position'] = [float(pos.x), float(pos.y), float(pos.z)]

        # 包围盒最小/最大点（世界坐标）
        try:
            bb_min = rt.point3()
            bb_max = rt.point3()
            rt.worldBoundingBox(obj, bb_min, bb_max)
            result['bounding_box'] = {
                'min': [float(bb_min.x), float(bb_min.y), float(bb_min.z)],
                'max': [float(bb_max.x), float(bb_max.y), float(bb_max.z)],
            }
            # 中心点 = (min + max) / 2
            result['center'] = [
                (float(bb_min.x) + float(bb_max.x)) / 2.0,
                (float(bb_min.y) + float(bb_max.y)) / 2.0,
                (float(bb_min.z) + float(bb_max.z)) / 2.0,
            ]
        except Exception:  # pylint: disable=broad-except
            pass

        # 材质名（如有）
        try:
            mat = obj.material
            if mat is not None:
                result['material'] = str(mat.name)
        except Exception:  # pylint: disable=broad-except
            pass

    except Exception:  # pylint: disable=broad-except
        # 任何异常都不应阻塞主路径
        pass

    return result