#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置面板模型 Tab 布局回归测试。

针对截图反馈的 bug：API Key 那一行被 base_url_hint（默认隐藏的 QLabel）
撑成超高块，输入框被压在底部。

修复要点：
1. base_url_hint 不再独占 form 行，而是和 base_url_edit 包在同一个 widget 里
2. API Key 容器使用 SizePolicy.Fixed 限定垂直高度
3. hint 隐藏时 base_url 这一行高度不应被撑大
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from maxagent.config import ConfigManager
from maxagent.qt_compat import QtCore
from maxagent.qt_compat import QtWidgets
from maxagent.ui.settings_dialog import SettingsDialog


class TestModelTabLayout(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or \
            QtWidgets.QApplication([])

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='maxagent-test-layout-')
        cfg_path = os.path.join(self.tmpdir, 'config.json')
        self.cfg_mgr = ConfigManager(config_path=cfg_path)
        self.dialog = SettingsDialog(self.cfg_mgr)
        self.dialog.resize(900, 700)
        self.dialog.show()
        QtWidgets.QApplication.processEvents()

    def tearDown(self):
        try:
            self.dialog.deleteLater()
        except Exception:  # pylint: disable=broad-except
            pass

    # ------------------------------------------------------------------ #
    # 1) hint 隐藏时不应单独占 form 行
    # ------------------------------------------------------------------ #
    def test_base_url_hint_not_in_form_row(self):
        """base_url_hint 必须嵌在 base_url 容器内，不独占 form 行。"""
        d = self.dialog
        hint = d.base_url_hint
        edit = d.base_url_edit
        # hint 默认应隐藏
        self.assertFalse(hint.isVisible())
        # hint 与 base_url_edit 必须在同一个直接父 widget 内
        self.assertIs(hint.parentWidget(), edit.parentWidget())

    # ------------------------------------------------------------------ #
    # 2) API Key 行高 ≈ Base URL 行高（hint 隐藏时）
    # ------------------------------------------------------------------ #
    def test_api_key_row_height_close_to_base_url(self):
        """hint 隐藏时，API Key 行高不应明显大于 Base URL 行高。"""
        d = self.dialog
        QtWidgets.QApplication.processEvents()
        # 容器 widget = QLineEdit 的 parent；它在 form 中被作为字段单元
        base_url_field = d.base_url_edit.parentWidget()
        api_key_field = d.api_key_edit.parentWidget()
        # 都需要有合法尺寸
        self.assertGreater(base_url_field.height(), 0)
        self.assertGreater(api_key_field.height(), 0)
        # API Key 行不应明显超过 Base URL 行（容差 30px，
        # 因为按钮可能略高于纯输入框）
        diff = abs(api_key_field.height() - base_url_field.height())
        self.assertLess(
            diff, 30,
            'api_key field height ({}) deviates too much from '
            'base_url field height ({})'.format(
                api_key_field.height(), base_url_field.height(),
            ),
        )

    # ------------------------------------------------------------------ #
    # 3) API Key 容器的 SizePolicy 必须为 Fixed 垂直
    # ------------------------------------------------------------------ #
    def test_api_key_widget_vertical_policy_fixed(self):
        """key_widget 必须显式锁定垂直高度，避免被 form 拉伸。"""
        api_key_field = self.dialog.api_key_edit.parentWidget()
        policy = api_key_field.sizePolicy()
        self.assertEqual(
            policy.verticalPolicy(), QtWidgets.QSizePolicy.Fixed,
            'api_key container should not expand vertically',
        )

    # ------------------------------------------------------------------ #
    # 4) hint 显示时仍嵌在容器内（不会触发 form 整体重排）
    # ------------------------------------------------------------------ #
    def test_base_url_hint_visible_does_not_break_neighbor(self):
        """触发 base_url 提示出现时，API Key 行不应跟着变高很多。"""
        d = self.dialog
        QtWidgets.QApplication.processEvents()
        api_key_h_before = d.api_key_edit.parentWidget().height()
        # 故意填一个有问题的 url，触发 hint 显示
        d.base_url_edit.setText('https://example.com/wrong/path')
        QtWidgets.QApplication.processEvents()
        api_key_h_after = d.api_key_edit.parentWidget().height()
        # API Key 行高变化不应超过 5 px
        self.assertLessEqual(
            abs(api_key_h_after - api_key_h_before), 5,
            'api_key row height should not be affected by base_url hint',
        )

    # ------------------------------------------------------------------ #
    # 5) hint 与 base_url_edit 之间垂直贴近，无大间距
    # ------------------------------------------------------------------ #
    def test_base_url_box_layout_compact(self):
        d = self.dialog
        edit = d.base_url_edit
        layout = edit.parentWidget().layout()
        # 容器应该是 VBox，spacing 小（≤ 4）
        self.assertIsInstance(layout, QtWidgets.QVBoxLayout)
        self.assertLessEqual(layout.spacing(), 4)
        margins = layout.contentsMargins()
        self.assertEqual(margins.left(), 0)
        self.assertEqual(margins.top(), 0)
        self.assertEqual(margins.right(), 0)
        self.assertEqual(margins.bottom(), 0)

    # ------------------------------------------------------------------ #
    # 6) hint 触发显示后仍可见，且容器整体变高（功能性回归）
    # ------------------------------------------------------------------ #
    def test_hint_still_works_when_triggered(self):
        d = self.dialog
        QtWidgets.QApplication.processEvents()
        box = d.base_url_edit.parentWidget()
        h_hidden = box.sizeHint().height()
        # 触发 hint 显示
        d.base_url_edit.setText('https://example.com/wrong/path')
        QtWidgets.QApplication.processEvents()
        # hint 必须真的显示了
        self.assertTrue(d.base_url_hint.isVisible())
        # 容器整体期望高度变大，证明 hint 不会被裁切
        h_shown = box.sizeHint().height()
        self.assertGreater(h_shown, h_hidden)

    # ------------------------------------------------------------------ #
    # 7) resize 稳定性：拉大/缩小窗口时 API Key 行高保持不变
    # ------------------------------------------------------------------ #
    def test_api_key_row_stable_on_resize(self):
        """resize 时 API Key 行高与位置不应漂移。

        用户反馈的核心问题：调整界面 size 时 API Key 栏脱节。
        """
        d = self.dialog
        QtWidgets.QApplication.processEvents()
        api_key_field = d.api_key_edit.parentWidget()
        recorded_heights = []
        recorded_y_in_field = []
        for w, h in [(800, 600), (1200, 700), (1500, 900),
                     (900, 1000), (1280, 720)]:
            d.resize(w, h)
            QtWidgets.QApplication.processEvents()
            recorded_heights.append(api_key_field.height())
            # 输入框相对其容器的 y 偏移（0 表示顶部贴齐）
            recorded_y_in_field.append(d.api_key_edit.geometry().y())

        # 行高在所有尺寸下应保持一致（容差 2px 兼容字体微调）
        max_h = max(recorded_heights)
        min_h = min(recorded_heights)
        self.assertLessEqual(
            max_h - min_h, 2,
            'api_key field height varies on resize: {}'.format(
                recorded_heights,
            ),
        )

        # 输入框相对容器的纵向偏移也应稳定
        max_y = max(recorded_y_in_field)
        min_y = min(recorded_y_in_field)
        self.assertLessEqual(
            max_y - min_y, 2,
            'api_key edit y-offset drifts on resize: {}'.format(
                recorded_y_in_field,
            ),
        )

    # ------------------------------------------------------------------ #
    # 8) resize 稳定性：API Key 与 Base URL / 模型 行高对齐
    # ------------------------------------------------------------------ #
    def test_api_key_and_neighbor_rows_aligned_on_resize(self):
        """在多种窗口尺寸下，API Key 行高都与上下相邻行保持接近。"""
        d = self.dialog
        for w, h in [(900, 700), (1300, 800), (1600, 1000)]:
            d.resize(w, h)
            QtWidgets.QApplication.processEvents()
            base_url_h = d.base_url_edit.parentWidget().height()
            api_key_h = d.api_key_edit.parentWidget().height()
            model_h = d.model_edit.height()
            # 三者高度差不应超过 6px（容器内部 padding 可能略有差异）
            self.assertLessEqual(
                abs(api_key_h - base_url_h), 6,
                'api_key vs base_url height mismatch '
                '({} vs {}) at {}x{}'.format(
                    api_key_h, base_url_h, w, h,
                ),
            )
            self.assertLessEqual(
                abs(api_key_h - model_h), 6,
                'api_key vs model height mismatch '
                '({} vs {}) at {}x{}'.format(
                    api_key_h, model_h, w, h,
                ),
            )

    # ------------------------------------------------------------------ #
    # 9) key_widget 显式锁定 maximumHeight，防止被外部布局拉伸
    # ------------------------------------------------------------------ #
    def test_api_key_widget_has_max_height(self):
        api_key_field = self.dialog.api_key_edit.parentWidget()
        max_h = api_key_field.maximumHeight()
        # Qt 默认 maximumHeight 是 16777215（QWIDGETSIZE_MAX）
        # 我们必须显式设置一个合理值，否则视为未修复
        self.assertLess(
            max_h, 200,
            'api_key container should have explicit maximumHeight '
            'to lock its size, got {}'.format(max_h),
        )

    # ------------------------------------------------------------------ #
    # 10) 自定义 Header 输入框允许垂直扩张：随窗口 resize 变高
    # ------------------------------------------------------------------ #
    def test_headers_edit_grows_with_window(self):
        """窗口高度增大时，自定义 Header 输入框应跟着变高。"""
        d = self.dialog
        d.resize(900, 600)
        QtWidgets.QApplication.processEvents()
        h_small = d.headers_edit.height()
        d.resize(900, 1100)
        QtWidgets.QApplication.processEvents()
        h_large = d.headers_edit.height()
        # 期望：窗口加高 500px，Header 输入框至少能多吃 200px
        self.assertGreater(
            h_large - h_small, 200,
            'headers_edit should grow with window height '
            '(got {} -> {})'.format(h_small, h_large),
        )

    # ------------------------------------------------------------------ #
    # 11) Headers 输入框 SizePolicy 必须是垂直 Expanding
    # ------------------------------------------------------------------ #
    def test_headers_edit_expanding_policy(self):
        policy = self.dialog.headers_edit.sizePolicy()
        self.assertEqual(
            policy.verticalPolicy(), QtWidgets.QSizePolicy.Expanding,
            'headers_edit must allow vertical expansion '
            'so it grows with window resize',
        )

    # ------------------------------------------------------------------ #
    # 12) Headers 不再被 maximumHeight=80 锁死
    # ------------------------------------------------------------------ #
    def test_headers_edit_no_artificial_height_cap(self):
        """maximumHeight 不能小于 200，否则视为又被锁死了。"""
        max_h = self.dialog.headers_edit.maximumHeight()
        self.assertGreater(
            max_h, 200,
            'headers_edit maximumHeight should not be capped, '
            'got {}'.format(max_h),
        )

    # ------------------------------------------------------------------ #
    # 13) Headers 拉伸不会反过来影响 API Key 行高
    # ------------------------------------------------------------------ #
    def test_headers_grow_does_not_drift_api_key(self):
        """Headers 跟着窗口拉伸时，API Key 行应保持稳定。"""
        d = self.dialog
        api_key_field = d.api_key_edit.parentWidget()
        for w, h in [(900, 600), (900, 900), (900, 1200), (900, 1500)]:
            d.resize(w, h)
            QtWidgets.QApplication.processEvents()
        # 最后一个尺寸下，API Key 行高仍接近 LineEdit 自身 sizeHint
        expected = d.api_key_edit.sizeHint().height()
        self.assertLessEqual(
            abs(api_key_field.height() - expected), 6,
            'api_key row height ({}) deviates from sizeHint ({}) '
            'after headers grew'.format(api_key_field.height(), expected),
        )


if __name__ == '__main__':
    unittest.main()
