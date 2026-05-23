#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置面板安全 / 交互回归测试：API Key 显示按钮 + 导航顺序。

历史 bug 防回退：
- 早期版本 show_key_btn 默认是 autoDefault=True，用户在表单内
  按 Enter 会把 API Key 切成明文显示——属于敏感信息泄露级 bug。
- 早期版本切 profile 不会复位显示按钮，跨 profile 也会泄露明文。
- 早期 _NAV_ITEMS 与 stack.addWidget 顺序耦合，对调一处忘记另一处
  会导致点击日志跳到 IDE 接口（一类静默错位）。
"""
from __future__ import absolute_import
from __future__ import print_function

import os
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def _make_qapp():
    from maxagent.qt_compat import QtWidgets
    return (
        QtWidgets.QApplication.instance()
        or QtWidgets.QApplication(sys.argv)
    )


class ApiKeyVisibilityTests(unittest.TestCase):
    """API Key 显示/隐藏按钮的安全与交互回归。"""

    def setUp(self):
        from maxagent.config import ConfigManager
        from maxagent.ui.settings_dialog import SettingsDialog
        self._app = _make_qapp()
        self._tmpdir = tempfile.mkdtemp()
        self._cfg = ConfigManager(
            os.path.join(self._tmpdir, 'config.json'),
        )
        self._dlg = SettingsDialog(self._cfg)

    def tearDown(self):
        self._dlg.deleteLater()

    def test_show_key_btn_blocks_enter(self):
        """回车键不能让 API Key 切到明文（历史严重 bug 防回退）。"""
        from maxagent.qt_compat import QtCore, QtGui, QtWidgets

        # 三道防线必须同时成立：
        # 1. 不是默认按钮 -> Dialog 接受 Enter 时不会被触发
        self.assertFalse(self._dlg.show_key_btn.autoDefault())
        self.assertFalse(self._dlg.show_key_btn.isDefault())
        # 2. 不接收键盘焦点 -> Tab 串过去后空格也不会切换
        self.assertEqual(
            self._dlg.show_key_btn.focusPolicy(), QtCore.Qt.NoFocus,
        )

        # 3. 端到端：模拟键盘 Enter 后 EchoMode 仍是 Password
        self._dlg.api_key_edit.setText('top-secret-key')
        self.assertEqual(
            self._dlg.api_key_edit.echoMode(),
            QtWidgets.QLineEdit.Password,
        )
        evt = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress,
            QtCore.Qt.Key_Return,
            QtCore.Qt.NoModifier,
        )
        QtWidgets.QApplication.sendEvent(self._dlg, evt)
        self.assertEqual(
            self._dlg.api_key_edit.echoMode(),
            QtWidgets.QLineEdit.Password,
            '严重 bug：回车导致 API Key 明文显示',
        )

    def test_click_toggles_visibility_and_label(self):
        """点击按钮：切 EchoMode + 切按钮文案 + 一致性。"""
        from maxagent.qt_compat import QtWidgets

        # 初始：Password + 显示
        self.assertEqual(
            self._dlg.api_key_edit.echoMode(),
            QtWidgets.QLineEdit.Password,
        )
        self.assertIn('显示', self._dlg.show_key_btn.text())

        # 第一次点击：切到 Normal + 文案变"隐藏"
        self._dlg.show_key_btn.toggle()
        self.assertEqual(
            self._dlg.api_key_edit.echoMode(),
            QtWidgets.QLineEdit.Normal,
        )
        self.assertIn('隐藏', self._dlg.show_key_btn.text())

        # 第二次点击：切回 Password + 文案变"显示"
        self._dlg.show_key_btn.toggle()
        self.assertEqual(
            self._dlg.api_key_edit.echoMode(),
            QtWidgets.QLineEdit.Password,
        )
        self.assertIn('显示', self._dlg.show_key_btn.text())

    def test_load_to_form_resets_visibility(self):
        """切换 profile 时强制隐藏 API Key（防跨 profile 泄露）。"""
        from maxagent.qt_compat import QtWidgets

        # 先把按钮切到"显示"状态
        self._dlg.show_key_btn.setChecked(True)
        self.assertEqual(
            self._dlg.api_key_edit.echoMode(),
            QtWidgets.QLineEdit.Normal,
        )

        # 重新加载当前 profile（等价于切 profile）
        active = self._cfg.get_active_profile_name()
        self._dlg._load_to_form(active)

        # 三件事必须同时复位
        self.assertFalse(self._dlg.show_key_btn.isChecked())
        self.assertEqual(
            self._dlg.api_key_edit.echoMode(),
            QtWidgets.QLineEdit.Password,
        )
        self.assertIn('显示', self._dlg.show_key_btn.text())

    def test_button_is_checkable_with_visual_style(self):
        """按钮 checkable + 有 :checked QSS 才能让用户一眼分辨当前态。"""
        self.assertTrue(self._dlg.show_key_btn.isCheckable())
        # 样式表至少要包含 :checked 选择器
        qss = self._dlg.show_key_btn.styleSheet()
        self.assertIn(':checked', qss)


class NavOrderTests(unittest.TestCase):
    """左侧导航顺序与 stack 索引一致性回归。"""

    def setUp(self):
        from maxagent.config import ConfigManager
        from maxagent.ui.settings_dialog import SettingsDialog
        self._app = _make_qapp()
        self._tmpdir = tempfile.mkdtemp()
        self._cfg = ConfigManager(
            os.path.join(self._tmpdir, 'config.json'),
        )
        self._dlg = SettingsDialog(self._cfg)

    def tearDown(self):
        self._dlg.deleteLater()

    def test_bridge_before_log(self):
        """IDE 接口排在日志之前——功能性配置优先于辅助排错。"""
        keys = [k for _label, k in self._dlg._NAV_ITEMS]
        self.assertIn('bridge', keys)
        self.assertIn('log', keys)
        self.assertLess(
            keys.index('bridge'), keys.index('log'),
            'IDE 接口必须排在日志之前',
        )

    def test_stack_index_matches_nav(self):
        """stack 索引必须与 _NAV_ITEMS 一一对应（防对调时漏改其一）。"""
        # bridge 索引指向的页面必须包含 bridge_token_edit
        b_idx = next(
            i for i, (_l, k) in enumerate(self._dlg._NAV_ITEMS)
            if k == 'bridge'
        )
        b_widget = self._dlg.stack.widget(b_idx)
        self.assertTrue(
            b_widget.isAncestorOf(self._dlg.bridge_token_edit),
            'bridge 在 _NAV_ITEMS 与 stack 中的索引不一致',
        )

        # log 索引指向的页面必须包含日志相关 widget
        # （日志页有三态单选 log_radio_off/on/debug）
        l_idx = next(
            i for i, (_l, k) in enumerate(self._dlg._NAV_ITEMS)
            if k == 'log'
        )
        l_widget = self._dlg.stack.widget(l_idx)
        self.assertTrue(
            l_widget.isAncestorOf(self._dlg.log_radio_off),
            'log 在 _NAV_ITEMS 与 stack 中的索引不一致',
        )


if __name__ == '__main__':
    unittest.main()
