#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``execute_python`` method 处理器。

把代码扔回 Qt 主线程执行（pymxs 安全），捕获 stdout / stderr / 异常，
返回 dcc-mcp 协议约定的 ``ExecutePythonResult`` 结构。
"""

from __future__ import absolute_import
from __future__ import print_function

import io
import sys
import traceback
from typing import Any
from typing import Callable
from typing import Optional

from ...logger import get_logger
from ..protocol import BridgeErrorCode
from ..protocol import make_response


logger = get_logger(__name__)


# 单次 execute_python 执行超时（秒）
_DEFAULT_EXEC_TIMEOUT = 60.0


def _exec_in_main(code, timeout):
    # type: (str, float) -> dict
    """在 *当前线程* 直接 exec 代码，捕获 stdout/stderr/异常。

    本函数会被 ``main_thread_runner`` 调度回 Qt 主线程执行。
    """
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    saved_out = sys.stdout
    saved_err = sys.stderr
    sys.stdout = out_buf
    sys.stderr = err_buf
    scope = {}  # 每次独立 namespace，避免状态污染
    result = {
        'result': None,
        'stdout': '',
        'stderr': '',
        'traceback': None,
    }
    try:
        exec(code, scope, scope)  # noqa: S102 - 这是设计意图
        # 用户可在代码里把 ``result`` 变量赋值作为返回值
        ret = scope.get('result')
        # 确保 JSON 可序列化；不可序列化的 fallback 到 repr
        try:
            import json as _json
            _json.dumps(ret)
        except (TypeError, ValueError):
            ret = repr(ret)
        result['result'] = ret
    except BaseException:  # pylint: disable=broad-except
        # 捕获 BaseException 是为了兼容 SystemExit 等情形
        result['traceback'] = traceback.format_exc()
    finally:
        sys.stdout = saved_out
        sys.stderr = saved_err
        result['stdout'] = out_buf.getvalue()
        result['stderr'] = err_buf.getvalue()
    return result


def handle_execute_python(payload, request_id, main_thread_runner=None):
    # type: (dict, str, Optional[Callable]) -> dict
    """``execute_python`` method 入口。

    :param payload: 请求 payload，期待 ``{"code": str, "timeout_seconds": float?}``
    :param request_id: 请求 id（用于响应回填）
    :param main_thread_runner: 把 callable 调度回 Qt 主线程执行的钩子。
        签名 ``(fn, args_tuple, timeout) -> Any``。``None`` 时直接在
        当前线程跑（仅用于无 Max 环境的测试）。
    :returns: 标准响应 dict
    """
    code = payload.get('code')
    if not isinstance(code, str) or not code.strip():
        return make_response(
            request_id, False,
            error_code=BridgeErrorCode.INVALID_RESPONSE,
            error_message='payload.code must be a non-empty string',
        )
    try:
        timeout = float(payload.get('timeout_seconds') or _DEFAULT_EXEC_TIMEOUT)
    except (TypeError, ValueError):
        timeout = _DEFAULT_EXEC_TIMEOUT
    if timeout <= 0:
        timeout = _DEFAULT_EXEC_TIMEOUT

    logger.debug(
        'execute_python rid=%s code_len=%d timeout=%.1f',
        request_id, len(code), timeout,
    )

    try:
        if main_thread_runner is not None:
            data = main_thread_runner(_exec_in_main, (code, timeout), timeout)
        else:
            data = _exec_in_main(code, timeout)
    except TimeoutError as exc:
        return make_response(
            request_id, False,
            error_code=BridgeErrorCode.TIMEOUT,
            error_message='execute_python timed out: {}'.format(exc),
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception('execute_python dispatch failed: %s', exc)
        return make_response(
            request_id, False,
            error_code=BridgeErrorCode.INTERNAL_ERROR,
            error_message='dispatch failed: {}'.format(exc),
            error_details={'traceback': traceback.format_exc()},
        )

    # exec 自身报错走 traceback 字段（HTTP 200 但 ok=False，与 dcc-mcp 对齐）
    if data.get('traceback'):
        return make_response(
            request_id, False,
            data=data,
            error_code=BridgeErrorCode.EXECUTION_ERROR,
            error_message='Python execution failed',
            error_details={'traceback': data.get('traceback')},
        )
    return make_response(request_id, True, data=data)
