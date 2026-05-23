#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge 新接入日志埋点 + 帮助文档更新的回归测试。

目标:
1. 验证 bridge 启停 / 用户操作 / dispatch_task 关键路径都有日志记录
2. 验证设置面板帮助页面新增"IDE 接口"章节
"""

from __future__ import absolute_import
from __future__ import print_function

import logging
import os
import socket
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from maxagent.bridge import BridgeServer
from maxagent.bridge.handlers import dispatch_task as dt_mod
from maxagent.config import ConfigManager
from maxagent.qt_compat import QtWidgets
from maxagent.ui.settings_dialog import SettingsDialog


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ====================================================================== #
# 1) Server 启停日志
# ====================================================================== #
class TestBridgeServerLogging(unittest.TestCase):

    def test_start_stop_emit_info(self):
        port = _free_port()
        srv = BridgeServer(host='127.0.0.1', port=port)
        with self.assertLogs('maxagent.bridge.server', level='INFO') as ctx:
            srv.start()
            time.sleep(0.05)
            srv.stop()
        joined = '\n'.join(ctx.output)
        self.assertIn('bridge started', joined)
        self.assertIn('bridge stopped', joined)
        # 端口必须被写入日志，方便排障
        self.assertIn(str(port), joined)

    def test_accept_loop_debug_logged(self):
        port = _free_port()
        srv = BridgeServer(host='127.0.0.1', port=port)
        with self.assertLogs('maxagent.bridge.server', level='DEBUG') as ctx:
            srv.start()
            time.sleep(0.05)
            # 真实建一个连接，让 accept 计数 +1
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            try:
                s.connect(('127.0.0.1', port))
                s.sendall(b'{"request_id":"x","method":"capabilities",'
                          b'"payload":{}}\n')
                # 读完响应再断开（避免被服务端记成 connection error）
                buf = b''
                while b'\n' not in buf:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
            finally:
                s.close()
            time.sleep(0.05)
            srv.stop()
        joined = '\n'.join(ctx.output)
        self.assertIn('accept loop started', joined)
        self.assertIn('accept loop exited', joined)
        self.assertIn('accepted conn', joined)

    def test_unsupported_method_does_not_crash_handler(self):
        # 即便不存在的方法也不应进入 logger.exception 路径
        port = _free_port()
        srv = BridgeServer(host='127.0.0.1', port=port)
        srv.start()
        time.sleep(0.05)
        try:
            with self.assertLogs(
                'maxagent.bridge.server', level='DEBUG',
            ) as ctx:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect(('127.0.0.1', port))
                s.sendall(b'{"request_id":"u","method":"nosuch",'
                          b'"payload":{}}\n')
                buf = b''
                while b'\n' not in buf:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                s.close()
            joined = '\n'.join(ctx.output)
            # 不应出现 handler crashed
            self.assertNotIn('handler crashed', joined)
        finally:
            srv.stop()


# ====================================================================== #
# 2) dispatch_task 日志覆盖
# ====================================================================== #
class _FakeProfile(object):
    name = 'fake'
    base_url = 'http://fake'
    api_key = ''
    model = 'fake-model'
    timeout = 30.0
    extra_headers = ''


class _FakeConfig(object):
    def __init__(self):
        self.profiles = [_FakeProfile()]


class _FakeConfigManager(object):
    def __init__(self):
        self.config = _FakeConfig()

    def get_active_profile(self):
        return self.config.profiles[0]


class _FakeLLMOneRound(object):

    def chat(self, **kwargs):
        return {
            'content': 'done',
            'tool_calls': [],
            'finish_reason': 'stop',
            'usage': {},
        }


class _FakeLLMWithTool(object):
    """两轮：第 1 轮调一次工具，第 2 轮收尾。"""

    def __init__(self):
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                'content': '',
                'tool_calls': [{
                    'id': 't1', 'name': 'my_tool', 'arguments': {'x': 1},
                }],
                'finish_reason': 'tool_calls',
                'usage': {},
            }
        return {
            'content': 'all good',
            'tool_calls': [],
            'finish_reason': 'stop',
            'usage': {},
        }


class TestDispatchTaskLogging(unittest.TestCase):

    def test_happy_path_emits_start_and_done(self):
        cfg = _FakeConfigManager()
        with mock.patch.object(
            dt_mod, 'build_client_from_profile',
            return_value=_FakeLLMOneRound(),
        ), mock.patch.object(
            dt_mod, 'build_openai_tools_schema', return_value=[],
        ), mock.patch.object(
            dt_mod, 'ToolDispatcher', return_value=mock.MagicMock(),
        ), self.assertLogs(
            'maxagent.bridge.handlers.dispatch_task', level='DEBUG',
        ) as ctx:
            resp = dt_mod.handle_dispatch_task(
                payload={'prompt': 'hi'},
                request_id='r1',
                config_manager=cfg,
            )
        self.assertTrue(resp['ok'])
        joined = '\n'.join(ctx.output)
        # 入口 INFO
        self.assertIn('dispatch_task rid=r1', joined)
        # 每轮 DEBUG
        self.assertIn('round=1 sending', joined)
        # 完成 INFO
        self.assertIn('dispatch_task done', joined)

    def test_tool_invocation_debug_traced(self):
        cfg = _FakeConfigManager()
        fake_dispatch = mock.MagicMock(return_value={
            'ok': True, 'result': {'value': 1},
        })
        with mock.patch.object(
            dt_mod, 'build_client_from_profile',
            return_value=_FakeLLMWithTool(),
        ), mock.patch.object(
            dt_mod, 'build_openai_tools_schema', return_value=[],
        ), mock.patch.object(
            dt_mod, 'ToolDispatcher',
            return_value=mock.MagicMock(dispatch=fake_dispatch),
        ), self.assertLogs(
            'maxagent.bridge.handlers.dispatch_task', level='DEBUG',
        ) as ctx:
            dt_mod.handle_dispatch_task(
                payload={'prompt': 'use tool'},
                request_id='r-tool',
                config_manager=cfg,
            )
        joined = '\n'.join(ctx.output)
        # 工具调用前后必须有 DEBUG
        self.assertIn('invoking tool=my_tool', joined)
        self.assertIn('tool=my_tool ok=True', joined)
        self.assertIn('elapsed=', joined)


# ====================================================================== #
# 3) 帮助文档新章节
# ====================================================================== #
class TestHelpDocBridgeSection(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or \
            QtWidgets.QApplication([])

    def test_help_contains_bridge_section(self):
        html = SettingsDialog._help_html()
        # 必备关键词
        self.assertIn('IDE 接口', html)
        self.assertIn('Bridge', html)
        self.assertIn('execute_python', html)
        self.assertIn('dispatch_task', html)
        self.assertIn('dcc-mcp', html)
        # 端口 / 安全提示必须出现
        self.assertIn('7003', html)
        self.assertIn('127.0.0.1', html)
        # 文档链接指引
        self.assertIn('IDE_MCP_USAGE', html)

    def test_help_logging_section_mentions_bridge(self):
        # 日志章节也应说明 bridge 事件会记录到 DEBUG
        html = SettingsDialog._help_html()
        self.assertIn('Bridge', html)
        # 日志项中提到了"连接与方法分发"
        self.assertIn('Bridge 连接', html)


# ====================================================================== #
# 4) UI 操作日志（开关 / 应用 / 复制）
# ====================================================================== #
class TestSettingsDialogBridgeLogging(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or \
            QtWidgets.QApplication([])

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='maxagent-test-bridge-log-')
        cfg_path = os.path.join(self.tmpdir, 'config.json')
        self.cfg_mgr = ConfigManager(config_path=cfg_path)
        self.dialog = SettingsDialog(self.cfg_mgr)

    def tearDown(self):
        try:
            self.dialog.deleteLater()
        except Exception:  # pylint: disable=broad-except
            pass

    def test_apply_logs_summary(self):
        d = self.dialog
        d.bridge_port_spin.setValue(17777)
        d.bridge_token_edit.setText('secret')
        d.bridge_dispatch_chk.setChecked(True)
        d.bridge_max_rounds_spin.setValue(8)
        d.bridge_timeout_spin.setValue(120)
        with mock.patch.object(
            QtWidgets.QMessageBox, 'information', return_value=None,
        ), self.assertLogs(
            'maxagent.ui.settings_dialog', level='INFO',
        ) as ctx:
            d._on_bridge_apply()
        joined = '\n'.join(ctx.output)
        self.assertIn('bridge apply', joined)
        self.assertIn('17777', joined)
        # token 不能被原文记录（只标注 set/empty）
        self.assertNotIn('secret', joined)

    def test_copy_config_logged(self):
        with mock.patch.object(
            QtWidgets.QMessageBox, 'information', return_value=None,
        ), self.assertLogs(
            'maxagent.ui.settings_dialog', level='INFO',
        ) as ctx:
            self.dialog._on_bridge_copy_config()
        joined = '\n'.join(ctx.output)
        self.assertIn('mcp.json snippet copied', joined)


if __name__ == '__main__':
    unittest.main()
