#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge 协议层单元测试。"""

from __future__ import absolute_import
from __future__ import print_function

import json
import socket
import threading
import time
import unittest

from maxagent.bridge.protocol import BridgeErrorCode
from maxagent.bridge.protocol import BridgeMethod
from maxagent.bridge.protocol import BRIDGE_PROTOCOL_VERSION
from maxagent.bridge.protocol import decode_frame
from maxagent.bridge.protocol import encode_frame
from maxagent.bridge.protocol import make_response
from maxagent.bridge.protocol import read_frame


class TestProtocol(unittest.TestCase):
    """协议常量与序列化。"""

    def test_protocol_version_v2(self):
        # 协议 bump 到 2.0（新增 dispatch_task / capabilities）
        self.assertEqual(BRIDGE_PROTOCOL_VERSION, '2.0')

    def test_method_constants(self):
        self.assertEqual(BridgeMethod.EXECUTE_PYTHON, 'execute_python')
        self.assertEqual(BridgeMethod.DISPATCH_TASK, 'dispatch_task')
        self.assertEqual(BridgeMethod.CAPABILITIES, 'capabilities')
        self.assertIn(BridgeMethod.EXECUTE_PYTHON, BridgeMethod.ALL)
        self.assertIn(BridgeMethod.DISPATCH_TASK, BridgeMethod.ALL)

    def test_error_code_constants(self):
        # 与 dcc-mcp 严格对齐 + maxagent 扩展
        self.assertEqual(BridgeErrorCode.CONNECTION_ERROR, 'connection_error')
        self.assertEqual(BridgeErrorCode.TIMEOUT, 'timeout')
        self.assertEqual(BridgeErrorCode.INVALID_RESPONSE, 'invalid_response')
        self.assertEqual(BridgeErrorCode.EXECUTION_ERROR, 'execution_error')
        self.assertEqual(BridgeErrorCode.INTERNAL_ERROR, 'internal_error')
        self.assertEqual(BridgeErrorCode.UNSUPPORTED_METHOD, 'unsupported_method')
        self.assertEqual(BridgeErrorCode.UNAUTHORIZED, 'unauthorized')
        self.assertEqual(BridgeErrorCode.BUSY, 'busy')

    def test_make_response_ok(self):
        resp = make_response('rid-1', True, data={'foo': 'bar'})
        self.assertEqual(resp['request_id'], 'rid-1')
        self.assertTrue(resp['ok'])
        self.assertEqual(resp['data'], {'foo': 'bar'})
        self.assertIsNone(resp['error'])

    def test_make_response_error(self):
        resp = make_response(
            'rid-2', False,
            error_code=BridgeErrorCode.UNAUTHORIZED,
            error_message='bad token',
            error_details={'hint': 'check settings'},
        )
        self.assertFalse(resp['ok'])
        self.assertIsNone(resp['data'])
        self.assertEqual(resp['error']['code'], 'unauthorized')
        self.assertEqual(resp['error']['message'], 'bad token')
        self.assertEqual(resp['error']['details'], {'hint': 'check settings'})

    def test_make_response_error_with_data(self):
        # 失败时也允许携带 data（与 dcc-mcp 对齐：execution_error 仍要
        # 把 stdout/stderr/traceback 回传给客户端）
        resp = make_response(
            'rid-3', False,
            data={'stdout': 'x', 'traceback': 'tb'},
            error_code=BridgeErrorCode.EXECUTION_ERROR,
            error_message='Python execution failed',
        )
        self.assertFalse(resp['ok'])
        self.assertEqual(resp['data']['stdout'], 'x')
        self.assertIn('tb', resp['data']['traceback'])

    def test_make_response_unknown_request_id_default(self):
        resp = make_response(None, True, data={})
        self.assertEqual(resp['request_id'], 'unknown')

    def test_encode_decode_roundtrip(self):
        obj = {'a': 1, 'b': '中文', 'c': [1, 2, {'d': True}]}
        wire = encode_frame(obj)
        # 必有尾部换行
        self.assertTrue(wire.endswith(b'\n'))
        # decode 不带换行符的部分能还原
        line = wire.rstrip(b'\n')
        self.assertEqual(decode_frame(line), obj)

    def test_decode_empty_frame_raises(self):
        with self.assertRaises(ValueError):
            decode_frame(b'')

    def test_decode_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            decode_frame(b'not a json {{{')

    def test_decode_invalid_utf8_raises(self):
        with self.assertRaises(ValueError):
            decode_frame(b'\xff\xfe')

    def test_read_frame_via_socketpair(self):
        # 用一对 socket 模拟真实 read
        s1, s2 = socket.socketpair()
        try:
            payload = encode_frame({'request_id': 'r', 'method': 'x'})
            s1.sendall(payload)
            buf = read_frame(s2)
            self.assertEqual(
                json.loads(buf.decode('utf-8')),
                {'request_id': 'r', 'method': 'x'},
            )
        finally:
            s1.close()
            s2.close()

    def test_read_frame_empty_raises(self):
        s1, s2 = socket.socketpair()
        try:
            s1.close()
            with self.assertRaises(ValueError):
                read_frame(s2)
        finally:
            s2.close()

    def test_read_frame_max_bytes_guard(self):
        # 超出 max_bytes 必须抛错（防止恶意大包打爆内存）
        s1, s2 = socket.socketpair()
        try:
            big = b'x' * 5000 + b'\n'
            s1.sendall(big)
            with self.assertRaises(ValueError):
                read_frame(s2, max_bytes=1024)
        finally:
            s1.close()
            s2.close()


if __name__ == '__main__':
    unittest.main()
