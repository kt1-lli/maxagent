#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桥接协议常量与序列化。

协议版本: ``2.0``
- 1.0 由 dcc-mcp 旧 ``agents/max_agent.py`` 定义，仅支持 ``execute_python``
- 2.0 在 maxagent 内部新增 ``dispatch_task``、``capabilities`` method，
  与 dcc-mcp 双向兼容（dcc-mcp 端通过 capabilities 探测能力按需注册工具）

请求 / 响应均为单行 JSON，``\\n`` 结束::

    request:  {"request_id":"...","method":"...","payload":{...},
               "protocol_version":"2.0"}\\n
    response: {"request_id":"...","ok":true,"data":{...},"error":null}\\n

错误响应 ``error`` 字段::

    {"code":"connection_error|timeout|invalid_response|"
            "execution_error|internal_error|unsupported_method|"
            "unauthorized|busy",
     "message":"...","details":{...}}
"""

from __future__ import absolute_import
from __future__ import print_function

import json


# 协议版本
BRIDGE_PROTOCOL_VERSION = '2.0'

# 单帧最大字节数（防 OOM）
MAX_FRAME_BYTES = 32 * 1024 * 1024  # 32MB（dispatch_task 含附件可能较大）


class BridgeMethod(object):
    """支持的方法名常量（不用 enum 避免依赖）。"""

    EXECUTE_PYTHON = 'execute_python'
    DISPATCH_TASK = 'dispatch_task'
    CAPABILITIES = 'capabilities'

    ALL = (EXECUTE_PYTHON, DISPATCH_TASK, CAPABILITIES)


class BridgeErrorCode(object):
    """错误码常量，与 dcc-mcp 严格对齐。"""

    CONNECTION_ERROR = 'connection_error'
    TIMEOUT = 'timeout'
    INVALID_RESPONSE = 'invalid_response'
    EXECUTION_ERROR = 'execution_error'
    INTERNAL_ERROR = 'internal_error'
    UNSUPPORTED_METHOD = 'unsupported_method'
    UNAUTHORIZED = 'unauthorized'
    BUSY = 'busy'


def make_response(request_id, ok, data=None, error_code=None,
                  error_message=None, error_details=None):
    """构造一个标准响应 dict（不含尾部 ``\\n``）。

    :param request_id: 对应请求的 ``request_id``，未知用 ``"unknown"``
    :param ok: 是否成功
    :param data: 成功时的 payload dict；失败时也允许携带 data（如执行错误
        要把 stdout/stderr/traceback 一并返回，与 dcc-mcp 行为对齐）
    :param error_code: ``BridgeErrorCode`` 之一
    :param error_message: 人类可读描述
    :param error_details: 可选额外信息（如 traceback）
    :returns: dict
    """
    resp = {
        'request_id': request_id or 'unknown',
        'ok': bool(ok),
        # 失败时若调用方主动传了 data 也保留，便于 IDE 端拿到 stdout/stderr
        'data': data,
        'error': None,
    }
    if not ok:
        err = {
            'code': error_code or BridgeErrorCode.INTERNAL_ERROR,
            'message': error_message or '',
        }
        if error_details is not None:
            err['details'] = error_details
        resp['error'] = err
    return resp


def encode_frame(obj):
    """把 dict 编码成单帧 ``bytes`` 输出（含尾部 ``\\n``）。"""
    text = json.dumps(obj, ensure_ascii=False)
    return (text + '\n').encode('utf-8')


def decode_frame(raw):
    """把 ``bytes`` 反序列化成 dict；空帧或非法 JSON 抛 ``ValueError``。"""
    if not raw:
        raise ValueError('empty frame')
    try:
        return json.loads(raw.decode('utf-8'))
    except UnicodeDecodeError as exc:
        raise ValueError('utf-8 decode error: {}'.format(exc))
    # json.JSONDecodeError 继承自 ValueError，会自动抛出


def read_frame(sock, max_bytes=MAX_FRAME_BYTES):
    """从 socket 读取一行 JSON 帧（以 ``\\n`` 结束）。

    :raises ValueError: 帧为空或超出 ``max_bytes``
    :returns: 不含尾部换行符的 ``bytes``
    """
    buffer = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise ValueError(
                'frame exceeds max size {} bytes'.format(max_bytes),
            )
        if b'\n' in chunk:
            break
    if not buffer:
        raise ValueError('empty frame')
    return bytes(buffer).split(b'\n', 1)[0]
