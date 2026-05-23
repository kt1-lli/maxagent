#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge server 端到端单元测试。

用真实 socket 启动 BridgeServer，发请求，验证响应。
不依赖 Qt / Max；execute_python 走 ``main_thread_runner=None`` 直接
在后台线程执行（仅测试用）。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import socket
import time
import unittest

from maxagent.bridge import BridgeServer
from maxagent.bridge import start_global_server
from maxagent.bridge import stop_global_server
from maxagent.bridge import get_global_server
from maxagent.bridge.protocol import BRIDGE_PROTOCOL_VERSION
from maxagent.bridge.protocol import BridgeErrorCode
from maxagent.bridge.protocol import BridgeMethod
from maxagent.bridge.protocol import encode_frame


def _free_port():
    """找一个当前未占用的回环端口。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _send_request(host, port, request, timeout=5.0):
    """发一次请求，读单行 JSON 响应；返回 dict。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((host, port))
    try:
        s.sendall(encode_frame(request))
        buf = b''
        while b'\n' not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b'\n', 1)[0]
        return json.loads(line.decode('utf-8'))
    finally:
        s.close()


class _ServerCase(unittest.TestCase):
    """提供 server 启停的基类。"""

    def setUp(self):
        self.port = _free_port()
        self.srv = BridgeServer(host='127.0.0.1', port=self.port)
        self.srv.start()
        # 给 accept 线程一点时间起来
        time.sleep(0.05)

    def tearDown(self):
        try:
            self.srv.stop()
        except Exception:  # pylint: disable=broad-except
            pass


class TestServerLifecycle(_ServerCase):

    def test_start_stop_idempotent(self):
        self.assertTrue(self.srv.is_running())
        # 二次 start 不抛错
        self.srv.start()
        self.assertTrue(self.srv.is_running())
        self.srv.stop()
        self.assertFalse(self.srv.is_running())
        # 二次 stop 不抛错
        self.srv.stop()

    def test_capabilities_method(self):
        resp = _send_request('127.0.0.1', self.port, {
            'request_id': 'cap-1',
            'method': BridgeMethod.CAPABILITIES,
            'payload': {},
        })
        self.assertTrue(resp['ok'])
        self.assertEqual(
            resp['data']['protocol_version'], BRIDGE_PROTOCOL_VERSION,
        )
        self.assertEqual(resp['data']['dcc'], '3dsMax')
        self.assertIn('execute_python', resp['data']['methods'])
        self.assertIn('dispatch_task', resp['data']['methods'])
        self.assertEqual(resp['request_id'], 'cap-1')

    def test_unsupported_method(self):
        resp = _send_request('127.0.0.1', self.port, {
            'request_id': 'um-1',
            'method': 'no_such_method',
            'payload': {},
        })
        self.assertFalse(resp['ok'])
        self.assertEqual(
            resp['error']['code'], BridgeErrorCode.UNSUPPORTED_METHOD,
        )

    def test_invalid_json(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect(('127.0.0.1', self.port))
        try:
            s.sendall(b'not json {{{\n')
            buf = b''
            while b'\n' not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        finally:
            s.close()
        line = buf.split(b'\n', 1)[0]
        resp = json.loads(line.decode('utf-8'))
        self.assertFalse(resp['ok'])
        self.assertEqual(
            resp['error']['code'], BridgeErrorCode.INVALID_RESPONSE,
        )


class TestExecutePython(_ServerCase):

    def test_execute_simple_code(self):
        resp = _send_request('127.0.0.1', self.port, {
            'request_id': 'ep-1',
            'method': 'execute_python',
            'payload': {
                'code': 'result = 1 + 2\nprint("hello")',
            },
        })
        self.assertTrue(resp['ok'], msg=resp)
        self.assertEqual(resp['data']['result'], 3)
        self.assertIn('hello', resp['data']['stdout'])
        self.assertIsNone(resp['data']['traceback'])

    def test_execute_runtime_error(self):
        resp = _send_request('127.0.0.1', self.port, {
            'request_id': 'ep-2',
            'method': 'execute_python',
            'payload': {
                'code': 'raise RuntimeError("boom")',
            },
        })
        self.assertFalse(resp['ok'])
        self.assertEqual(
            resp['error']['code'], BridgeErrorCode.EXECUTION_ERROR,
        )
        self.assertIn('boom', resp['data']['traceback'])

    def test_execute_empty_code(self):
        resp = _send_request('127.0.0.1', self.port, {
            'request_id': 'ep-3',
            'method': 'execute_python',
            'payload': {'code': ''},
        })
        self.assertFalse(resp['ok'])
        self.assertEqual(
            resp['error']['code'], BridgeErrorCode.INVALID_RESPONSE,
        )

    def test_execute_non_serializable_result_falls_back_to_repr(self):
        resp = _send_request('127.0.0.1', self.port, {
            'request_id': 'ep-4',
            'method': 'execute_python',
            'payload': {
                'code': 'class Foo: pass\nresult = Foo()',
            },
        })
        self.assertTrue(resp['ok'], msg=resp)
        # 不可 JSON 序列化的对象应被 repr 化
        self.assertIsInstance(resp['data']['result'], str)
        self.assertIn('Foo', resp['data']['result'])


class TestAuthToken(unittest.TestCase):

    def setUp(self):
        self.port = _free_port()
        self.srv = BridgeServer(
            host='127.0.0.1', port=self.port, token='secret',
        )
        self.srv.start()
        time.sleep(0.05)

    def tearDown(self):
        self.srv.stop()

    def test_missing_token_rejected(self):
        resp = _send_request('127.0.0.1', self.port, {
            'request_id': 'auth-1',
            'method': 'capabilities',
            'payload': {},
        })
        self.assertFalse(resp['ok'])
        self.assertEqual(
            resp['error']['code'], BridgeErrorCode.UNAUTHORIZED,
        )

    def test_wrong_token_rejected(self):
        resp = _send_request('127.0.0.1', self.port, {
            'request_id': 'auth-2',
            'method': 'capabilities',
            'payload': {},
            'token': 'wrong',
        })
        self.assertFalse(resp['ok'])
        self.assertEqual(
            resp['error']['code'], BridgeErrorCode.UNAUTHORIZED,
        )

    def test_correct_token_passes(self):
        resp = _send_request('127.0.0.1', self.port, {
            'request_id': 'auth-3',
            'method': 'capabilities',
            'payload': {},
            'token': 'secret',
        })
        self.assertTrue(resp['ok'])


class TestDispatchDisabled(unittest.TestCase):

    def setUp(self):
        self.port = _free_port()
        self.srv = BridgeServer(
            host='127.0.0.1', port=self.port,
            dispatch_enabled=False,
        )
        self.srv.start()
        time.sleep(0.05)

    def tearDown(self):
        self.srv.stop()

    def test_dispatch_blocked_when_disabled(self):
        resp = _send_request('127.0.0.1', self.port, {
            'request_id': 'd-1',
            'method': 'dispatch_task',
            'payload': {'prompt': 'hi'},
        })
        self.assertFalse(resp['ok'])
        self.assertEqual(
            resp['error']['code'], BridgeErrorCode.UNSUPPORTED_METHOD,
        )

    def test_capabilities_excludes_dispatch_when_disabled(self):
        resp = _send_request('127.0.0.1', self.port, {
            'request_id': 'c-1',
            'method': 'capabilities',
            'payload': {},
        })
        self.assertTrue(resp['ok'])
        self.assertNotIn('dispatch_task', resp['data']['methods'])


class TestGlobalServer(unittest.TestCase):

    def tearDown(self):
        # 保证测试间不残留全局实例
        try:
            stop_global_server()
        except Exception:  # pylint: disable=broad-except
            pass

    def test_start_stop_global(self):
        port = _free_port()
        srv1 = start_global_server(host='127.0.0.1', port=port)
        self.assertIs(get_global_server(), srv1)
        # 同参数 start 不重启
        srv2 = start_global_server(host='127.0.0.1', port=port)
        self.assertIs(srv1, srv2)
        stop_global_server()
        self.assertIsNone(get_global_server())

    def test_restart_on_param_change(self):
        port1 = _free_port()
        port2 = _free_port()
        srv1 = start_global_server(host='127.0.0.1', port=port1)
        srv2 = start_global_server(host='127.0.0.1', port=port2)
        # 端口不同应该是新实例
        self.assertIsNot(srv1, srv2)
        self.assertEqual(srv2.port, port2)


if __name__ == '__main__':
    unittest.main()
