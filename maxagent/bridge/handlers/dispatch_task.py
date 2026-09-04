#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``dispatch_task`` method 处理器。

把"自然语言任务 + 附件"派给 maxagent 自己跑：内部走完整的
LLM ↔ 工具循环（headless 模式，不依赖 Qt worker / UI），
返回最终回复 + 全部工具调用 trace。

形成 IDE Agent ↔ maxagent Agent 的协作模式：
- IDE LLM 派任务（"帮我测我新写的工具，规约见附件"）
- maxagent 自己思考、自己调内部 tool、自己出报告

语义与 ``agent.worker.AgentWorker`` 的 ``_run_loop`` 对齐，但去掉
Qt 信号、流式回调、UI 状态切换；只保留 LLM ↔ 工具的核心循环。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import threading
import time
import traceback
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from ...agent.conversation import Conversation
from ...llm_client import build_client_from_profile
from ...llm_client import LLMError
from ...logger import get_logger
from ...tools import build_openai_tools_schema
from ...tools import ToolDispatcher
from ..protocol import BridgeErrorCode
from ..protocol import make_response


logger = get_logger(__name__)


def _resolve_profile(config_manager, profile_name):
    """根据 profile 名拿到 LLMProfile；为空时取激活 profile。"""
    if config_manager is None:
        return None
    if profile_name:
        for p in config_manager.config.profiles:
            if p.name == profile_name:
                return p
        # 找不到指定 profile：fallback 到激活
        logger.warning(
            'dispatch_task: profile "%s" not found, fallback to active',
            profile_name,
        )
    resolver = getattr(config_manager, 'resolve_active_llm', None)
    if callable(resolver):
        resolved = resolver()
        if resolved is not None:
            return resolved
    return config_manager.get_active_profile()


def _build_tool_call_trace(name, args_obj, result_obj, ok, elapsed_ms):
    """把一次工具调用压缩成可序列化的 trace 条目。"""
    # 防止超大字段把响应膨胀到 IDE 端崩溃
    def _shrink(v, limit=4096):
        try:
            text = json.dumps(v, ensure_ascii=False)
        except (TypeError, ValueError):
            text = repr(v)
        if len(text) > limit:
            return text[:limit] + '...[truncated]'
        return v if len(text) <= limit else text[:limit] + '...[truncated]'

    return {
        'name': name,
        'arguments': _shrink(args_obj or {}),
        'ok': bool(ok),
        'result': _shrink(result_obj),
        'elapsed_ms': int(elapsed_ms),
    }


def _run_dispatch_loop(prompt, profile, max_rounds, timeout_sec,
                       cancel_event):
    # type: (str, Any, int, float, threading.Event) -> dict
    """在当前线程跑完整的 LLM ↔ 工具循环。

    本函数被 ``main_thread_runner`` 调度回 Qt 主线程，整个循环都在
    主线程上跑。LLM HTTP 请求会短暂阻塞 Max UI（与现有 worker 在
    无 worker 路径下的行为一致），代价换来 pymxs 工具调用 100% 安全
    且实现极简——dispatch_task 不是"对话主路径"，只是 IDE 偶发派任务。
    """
    if profile is None:
        return {
            'final_message': '',
            'tool_calls': [],
            'rounds': 0,
            'elapsed_ms': 0,
            'error': 'no LLM profile available',
        }

    # 准备 LLM client + 对话状态
    llm = build_client_from_profile(profile)
    conv = Conversation()
    conv.add_user(prompt)
    dispatcher = ToolDispatcher(wrap_undo=True)
    # Function Calling 总开关：对应 profile.supports_tools。视觉专用模型
    # （tokenhub vita 等）必须把 tools 字段彻底剥离才能避免 5xx，故此处
    # 同时尊重该开关——tools_enabled=False 时整轮 dispatch 不携带 tools。
    if bool(getattr(profile, 'supports_tools', True)):
        tools_schema = build_openai_tools_schema()
    else:
        logger.info(
            'dispatch_task: Function Calling 已禁用 (profile=%s)，跳过 tools schema',
            getattr(profile, 'name', '?'),
        )
        tools_schema = []

    started = time.time()
    deadline = started + float(timeout_sec or 0)
    tool_trace = []  # type: List[dict]
    final_text = ''
    rounds = 0

    for loop_idx in range(int(max_rounds or 1)):
        rounds = loop_idx + 1
        if cancel_event.is_set():
            logger.info(
                'dispatch_task cancelled at round=%d', rounds,
            )
            return {
                'final_message': final_text,
                'tool_calls': tool_trace,
                'rounds': rounds,
                'elapsed_ms': int((time.time() - started) * 1000),
                'cancelled': True,
            }
        if timeout_sec and time.time() > deadline:
            logger.warning(
                'dispatch_task timeout at round=%d after %.1fs',
                rounds, time.time() - started,
            )
            return {
                'final_message': final_text,
                'tool_calls': tool_trace,
                'rounds': rounds,
                'elapsed_ms': int((time.time() - started) * 1000),
                'timeout': True,
            }

        messages = conv.to_openai_messages()
        logger.debug(
            'dispatch_task round=%d sending %d messages to LLM',
            rounds, len(messages),
        )
        try:
            resp = llm.chat(
                messages=messages,
                tools=tools_schema,
                stream=False,
                cancel_check=cancel_event.is_set,
            )
        except LLMError as exc:
            logger.warning(
                'dispatch_task round=%d LLM error: %s', rounds, exc,
            )
            return {
                'final_message': final_text,
                'tool_calls': tool_trace,
                'rounds': rounds,
                'elapsed_ms': int((time.time() - started) * 1000),
                'error': 'LLM error: {}'.format(exc),
            }

        content = resp.get('content') or ''
        flat_calls = resp.get('tool_calls') or []
        finish_reason = resp.get('finish_reason') or ''

        # 复原 OpenAI 原生 tool_calls 结构（与 worker._run_loop 一致）
        tool_calls = []
        for tc in flat_calls:
            args_obj = tc.get('arguments')
            if isinstance(args_obj, str):
                args_str = args_obj
            else:
                try:
                    args_str = json.dumps(
                        args_obj or {}, ensure_ascii=False,
                    )
                except (TypeError, ValueError):
                    args_str = '{}'
            tool_calls.append({
                'id': tc.get('id') or '',
                'type': 'function',
                'function': {
                    'name': tc.get('name') or '',
                    'arguments': args_str,
                },
            })

        conv.add_assistant(
            content=content if content else None,
            tool_calls=tool_calls if tool_calls else None,
            reasoning_content=resp.get('reasoning_content') or None,
        )

        if content:
            final_text = content

        # 没有工具调用 → 本轮就是最终回复
        if not tool_calls:
            elapsed = int((time.time() - started) * 1000)
            logger.info(
                'dispatch_task done: rounds=%d elapsed=%dms tools=%d',
                rounds, elapsed, len(tool_trace),
            )
            return {
                'final_message': final_text,
                'tool_calls': tool_trace,
                'rounds': rounds,
                'elapsed_ms': elapsed,
                'finish_reason': finish_reason,
            }

        # 有工具调用 → 逐个执行
        for tc in tool_calls:
            if cancel_event.is_set():
                return {
                    'final_message': final_text,
                    'tool_calls': tool_trace,
                    'rounds': rounds,
                    'elapsed_ms': int((time.time() - started) * 1000),
                    'cancelled': True,
                }
            tc_id = tc.get('id') or ''
            fn = tc.get('function') or {}
            tool_name = fn.get('name') or ''
            args_str = fn.get('arguments') or '{}'
            try:
                args_obj = json.loads(args_str) if args_str else {}
            except (TypeError, ValueError):
                args_obj = {}

            t_tool = time.time()
            logger.debug(
                'dispatch_task round=%d invoking tool=%s args_keys=%s',
                rounds, tool_name, list(args_obj.keys()) if args_obj else [],
            )
            try:
                result = dispatcher.dispatch(tool_name, args_obj)
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception('dispatch_task tool crashed: %s', exc)
                result = {
                    'ok': False,
                    'error': '{}: {}'.format(type(exc).__name__, exc),
                    'type': 'exec_error',
                }
            elapsed_ms = (time.time() - t_tool) * 1000
            ok = bool(result.get('ok'))
            logger.debug(
                'dispatch_task round=%d tool=%s ok=%s elapsed=%dms',
                rounds, tool_name, ok, int(elapsed_ms),
            )
            content_payload = result.get('data') if ok else result

            try:
                content_str = json.dumps(
                    result, ensure_ascii=False, default=repr,
                )
            except (TypeError, ValueError):
                content_str = json.dumps({'ok': ok, 'data': repr(result)})

            conv.add_tool_result(
                tool_call_id=tc_id,
                name=tool_name,
                content=content_str,
            )
            tool_trace.append(_build_tool_call_trace(
                tool_name, args_obj, content_payload, ok, elapsed_ms,
            ))

    # 超过 max_rounds 仍没收尾
    elapsed_total = int((time.time() - started) * 1000)
    logger.warning(
        'dispatch_task reached max_rounds=%d without final reply '
        '(elapsed=%dms, tools=%d)',
        max_rounds, elapsed_total, len(tool_trace),
    )
    return {
        'final_message': final_text or '⚠️ 任务在达到最大轮数前未给出最终回复',
        'tool_calls': tool_trace,
        'rounds': rounds,
        'elapsed_ms': elapsed_total,
        'reached_max_rounds': True,
    }


def handle_dispatch_task(payload, request_id, main_thread_runner=None,
                         config_manager=None,
                         default_max_rounds=20,
                         default_timeout_sec=300):
    # type: (dict, str, Optional[Callable], Any, int, float) -> dict
    """``dispatch_task`` method 入口。

    payload 字段::

        {
          "prompt": "自然语言任务",
          "profile": "可选: 指定 LLM profile 名",
          "max_rounds": 20,
          "timeout_seconds": 300,
          "session_mode": "new"     // 当前固定 new；预留扩展位
        }

    返回 ``data`` 结构::

        {
          "final_message": "...",
          "tool_calls":   [{name, arguments, ok, result, elapsed_ms}, ...],
          "rounds":       7,
          "elapsed_ms":   12340,
          "profile":      "deepseek-v4-flash",
          "model":        "deepseek-v4-flash"
        }
    """
    prompt = payload.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip():
        return make_response(
            request_id, False,
            error_code=BridgeErrorCode.INVALID_RESPONSE,
            error_message='payload.prompt must be a non-empty string',
        )

    profile_name = str(payload.get('profile') or '')
    try:
        max_rounds = int(payload.get('max_rounds') or default_max_rounds)
    except (TypeError, ValueError):
        max_rounds = int(default_max_rounds)
    if max_rounds <= 0:
        max_rounds = int(default_max_rounds)

    try:
        timeout_sec = float(
            payload.get('timeout_seconds') or default_timeout_sec,
        )
    except (TypeError, ValueError):
        timeout_sec = float(default_timeout_sec)
    if timeout_sec <= 0:
        timeout_sec = float(default_timeout_sec)

    profile = _resolve_profile(config_manager, profile_name)
    if profile is None:
        return make_response(
            request_id, False,
            error_code=BridgeErrorCode.INTERNAL_ERROR,
            error_message=(
                'no LLM profile available; please configure one in maxagent'
            ),
        )

    cancel_event = threading.Event()

    logger.info(
        'dispatch_task rid=%s prompt_len=%d profile=%s max_rounds=%d '
        'timeout=%.0fs',
        request_id, len(prompt), profile.name, max_rounds, timeout_sec,
    )

    try:
        if main_thread_runner is not None:
            data = main_thread_runner(
                _run_dispatch_loop,
                (prompt, profile, max_rounds, timeout_sec, cancel_event),
                # 主线程超时给 timeout_sec 留 30s 余量，避免 marshal 边界抢跑
                timeout_sec + 30.0,
            )
        else:
            data = _run_dispatch_loop(
                prompt, profile, max_rounds, timeout_sec, cancel_event,
            )
    except TimeoutError as exc:
        return make_response(
            request_id, False,
            error_code=BridgeErrorCode.TIMEOUT,
            error_message='dispatch_task timed out: {}'.format(exc),
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception('dispatch_task dispatch failed: %s', exc)
        return make_response(
            request_id, False,
            error_code=BridgeErrorCode.INTERNAL_ERROR,
            error_message='dispatch failed: {}'.format(exc),
            error_details={'traceback': traceback.format_exc()},
        )

    # 把 profile/model 信息回传，便于 IDE 端报告
    data = dict(data or {})
    data['profile'] = profile.name
    data['model'] = profile.model

    if data.get('error'):
        return make_response(
            request_id, False,
            data=data,
            error_code=BridgeErrorCode.EXECUTION_ERROR,
            error_message=data['error'],
        )
    return make_response(request_id, True, data=data)
