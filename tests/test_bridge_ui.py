#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IDE Bridge 设置 Tab 的 UI 烟雾测试。

不实际启动 socket（依赖 Qt offscreen 环境），仅校验：
- Tab 可被构建
- 控件存在且初值与 config 字段对齐
- 应用 / 复制配置按钮的处理函数能被调用不抛错
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from maxagent.config import ConfigManager
from maxagent.qt_compat import QtWidgets
from maxagent.ui.settings_dialog import SettingsDialog


class _DialogCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or \
            QtWidgets.QApplication([])

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='maxagent-test-bridge-ui-')
        cfg_path = os.path.join(self.tmpdir, 'config.json')
        self.cfg_mgr = ConfigManager(config_path=cfg_path)
        self.dialog = SettingsDialog(self.cfg_mgr)

    def tearDown(self):
        try:
            self.dialog.deleteLater()
        except Exception:  # pylint: disable=broad-except
            pass


class TestBridgeTabPresent(_DialogCase):

    def test_bridge_tab_exists(self):
        labels = [
            self.dialog.nav.item(i).text()
            for i in range(self.dialog.nav.count())
        ]
        # 应包含 IDE 接口
        self.assertTrue(any('IDE 接口' in t for t in labels))

    def test_bridge_widgets_present(self):
        # 关键控件应已建出来
        self.assertTrue(hasattr(self.dialog, 'bridge_enabled_chk'))
        self.assertTrue(hasattr(self.dialog, 'bridge_port_spin'))
        self.assertTrue(hasattr(self.dialog, 'bridge_token_edit'))
        self.assertTrue(hasattr(self.dialog, 'bridge_dispatch_chk'))
        self.assertTrue(hasattr(self.dialog, 'bridge_max_rounds_spin'))
        self.assertTrue(hasattr(self.dialog, 'bridge_timeout_spin'))
        self.assertTrue(hasattr(self.dialog, 'bridge_status_lbl'))


class TestBridgeInitialValues(_DialogCase):

    def test_initial_values_match_config(self):
        d = self.dialog
        cfg = self.cfg_mgr.config
        self.assertEqual(d.bridge_enabled_chk.isChecked(), cfg.bridge_enabled)
        self.assertEqual(d.bridge_port_spin.value(), cfg.bridge_port)
        self.assertEqual(d.bridge_token_edit.text(), cfg.bridge_token)
        self.assertEqual(
            d.bridge_dispatch_chk.isChecked(),
            cfg.bridge_dispatch_enabled,
        )
        self.assertEqual(
            d.bridge_max_rounds_spin.value(),
            cfg.bridge_dispatch_max_rounds,
        )
        self.assertEqual(
            d.bridge_timeout_spin.value(),
            cfg.bridge_dispatch_timeout_sec,
        )

    def test_default_disabled(self):
        # 默认关闭，避免任何首次启动监听端口
        self.assertFalse(self.dialog.bridge_enabled_chk.isChecked())


class TestBridgeApply(_DialogCase):

    def test_apply_persists_to_config(self):
        d = self.dialog
        d.bridge_port_spin.setValue(17003)
        d.bridge_token_edit.setText('xyz')
        d.bridge_dispatch_chk.setChecked(False)
        d.bridge_max_rounds_spin.setValue(7)
        d.bridge_timeout_spin.setValue(60)
        # 屏蔽 QMessageBox
        with mock.patch.object(
            QtWidgets.QMessageBox, 'information', return_value=None,
        ):
            d._on_bridge_apply()
        cfg = self.cfg_mgr.config
        self.assertEqual(cfg.bridge_port, 17003)
        self.assertEqual(cfg.bridge_token, 'xyz')
        self.assertFalse(cfg.bridge_dispatch_enabled)
        self.assertEqual(cfg.bridge_dispatch_max_rounds, 7)
        self.assertEqual(cfg.bridge_dispatch_timeout_sec, 60)


class TestBridgeCopyConfig(_DialogCase):

    def test_copy_config_writes_clipboard(self):
        with mock.patch.object(
            QtWidgets.QMessageBox, 'information', return_value=None,
        ):
            self.dialog._on_bridge_copy_config()
        clip = QtWidgets.QApplication.clipboard().text()
        self.assertIn('mcpServers', clip)
        self.assertIn('maxagent', clip)
        self.assertIn('DCC_MCP_BRIDGE_PORT', clip)


if __name__ == '__main__':
    unittest.main()
