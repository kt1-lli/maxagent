#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主面板 splitter 拖动锚点回归测试。

针对用户反馈：「输入窗口向上拉的时候会遮挡对话，向下拉能正确将对话
窗口拉动这个是期望的」。

修复策略（方案 A）：
1. 向上拖（输入区扩张）+ 拖动前在底部 → 强制滚到底，最新消息保持可见
2. 向下拖（输入区收缩）→ 不打扰，清零拖动状态快照
3. 向上拖 + 拖动前在翻历史 → 不强制滚动，保留原视点
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from maxagent.config import ConfigManager
from maxagent.qt_compat import QtWidgets
from maxagent.ui.dock_widget import MaxAgentDockWidget


class TestSplitterAnchor(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or \
            QtWidgets.QApplication([])

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='maxagent-test-splitter-')
        cfg_path = os.path.join(self.tmpdir, 'config.json')
        self.cfg_mgr = ConfigManager(config_path=cfg_path)
        self.dock = MaxAgentDockWidget(self.cfg_mgr)
        self.dock.resize(800, 600)
        self.dock.show()
        QtWidgets.QApplication.processEvents()

    def tearDown(self):
        try:
            self.dock.deleteLater()
        except Exception:  # pylint: disable=broad-except
            pass
        QtWidgets.QApplication.processEvents()

    # ------------------------------------------------------------------ #
    # 1) splitter 必须存在且 splitterMoved 信号已连接
    # ------------------------------------------------------------------ #
    def test_splitter_exists_and_signal_connected(self):
        self.assertTrue(hasattr(self.dock, 'splitter'))
        self.assertTrue(hasattr(self.dock, '_on_splitter_moved'))
        # 状态变量必须被初始化
        self.assertTrue(hasattr(self.dock, '_splitter_drag_was_at_bottom'))
        self.assertTrue(hasattr(self.dock, '_splitter_last_input_h'))
        self.assertFalse(self.dock._splitter_drag_was_at_bottom)
        # last_input_h 应在 _build_ui 时被初始化为非零值
        self.assertGreater(self.dock._splitter_last_input_h, 0)

    # ------------------------------------------------------------------ #
    # 2) 向下拖：清零拖动状态快照
    # ------------------------------------------------------------------ #
    def test_drag_down_resets_anchor_flag(self):
        # 模拟之前已进入"向上拖且在底部"状态
        self.dock._splitter_drag_was_at_bottom = True
        # 模拟向下拖：输入区高度比上次记录值小
        self.dock._splitter_last_input_h = 200
        # 设置 splitter sizes 让输入区变小
        self.dock.splitter.setSizes([700, 100])
        QtWidgets.QApplication.processEvents()
        self.dock._on_splitter_moved(0, 1)
        # 向下拖完毕后必须清零，下次重新采样
        self.assertFalse(self.dock._splitter_drag_was_at_bottom)

    # ------------------------------------------------------------------ #
    # 3) 向上拖 + 当时在底部：必须触发强制滚动
    # ------------------------------------------------------------------ #
    def test_drag_up_at_bottom_triggers_scroll(self):
        # mock _renderer.is_at_bottom 返回 True
        called = {'force': 0}

        def _fake_force():
            called['force'] += 1

        self.dock._renderer.is_at_bottom = lambda: True
        self.dock._renderer.scroll_to_bottom_force = _fake_force

        # 模拟向上拖：输入区高度比上次大
        self.dock._splitter_last_input_h = 100
        self.dock.splitter.setSizes([400, 200])
        QtWidgets.QApplication.processEvents()
        self.dock._on_splitter_moved(0, 1)

        self.assertTrue(self.dock._splitter_drag_was_at_bottom)
        self.assertGreaterEqual(called['force'], 1)

    # ------------------------------------------------------------------ #
    # 4) 向上拖 + 当时不在底部（翻历史）：不打扰
    # ------------------------------------------------------------------ #
    def test_drag_up_when_browsing_history_no_scroll(self):
        called = {'force': 0}

        self.dock._renderer.is_at_bottom = lambda: False
        self.dock._renderer.scroll_to_bottom_force = (
            lambda: called.__setitem__('force', called['force'] + 1)
        )

        self.dock._splitter_last_input_h = 100
        self.dock.splitter.setSizes([400, 200])
        QtWidgets.QApplication.processEvents()
        self.dock._on_splitter_moved(0, 1)

        # 拖动前在翻历史，不应强制滚动
        self.assertEqual(called['force'], 0)
        # 状态采样为 False
        self.assertFalse(self.dock._splitter_drag_was_at_bottom)

    # ------------------------------------------------------------------ #
    # 5) 连续多次向上拖：is_at_bottom 只采样一次（避免聊天区被压缩
    #    后误判为"不在底部"）
    # ------------------------------------------------------------------ #
    def test_drag_up_samples_at_bottom_only_once(self):
        sample_count = {'n': 0}

        def _is_at_bottom():
            sample_count['n'] += 1
            return True

        force_count = {'n': 0}
        self.dock._renderer.is_at_bottom = _is_at_bottom
        self.dock._renderer.scroll_to_bottom_force = (
            lambda: force_count.__setitem__('n', force_count['n'] + 1)
        )

        # 连续 3 次向上拖回调
        self.dock._splitter_last_input_h = 100
        for h in (150, 200, 250):
            self.dock.splitter.setSizes([600 - h, h])
            self.dock._on_splitter_moved(0, 1)

        # is_at_bottom 只在第一次"刚开始向上"时被采样
        self.assertEqual(sample_count['n'], 1)
        # 但每次向上拖都强制滚到底
        self.assertEqual(force_count['n'], 3)

    # ------------------------------------------------------------------ #
    # 6) renderer 必须暴露 is_at_bottom 公共方法供外部调用
    # ------------------------------------------------------------------ #
    def test_renderer_exposes_is_at_bottom(self):
        self.assertTrue(hasattr(self.dock._renderer, 'is_at_bottom'))
        # 应该返回 bool
        result = self.dock._renderer.is_at_bottom()
        self.assertIsInstance(result, bool)


if __name__ == '__main__':
    unittest.main()
