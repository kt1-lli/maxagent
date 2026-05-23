#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头像上传链路的 PySide2/6 兼容性回归测试。

防止以下回退：
1. AvatarCropDialog 是独立 QDialog，不会从父 widget 自动继承字体回退族；
   不显式应用一次 ``apply_font_fallback`` 时，PySide2 + Win 嵌入 Max
   环境下中文 / emoji 渲染会出现"豆腐块"。
2. EmployeeTab 的"选择图片 / 清除"按钮没有图标（早期纯中文文本），
   与全局风格不一致。
3. AvatarCropDialog 的取消按钮 autoDefault 历史上未关闭，编辑滑块
   时按 Enter 会误触"取消"。
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


def _make_temp_image():
    """生成一张 200x200 蓝色占位图，返回路径。"""
    from maxagent.qt_compat import QtGui
    img_path = os.path.join(tempfile.mkdtemp(), 'placeholder.png')
    pm = QtGui.QPixmap(200, 200)
    pm.fill(QtGui.QColor('blue'))
    pm.save(img_path, 'PNG')
    return img_path


class AvatarCropDialogCompatTests(unittest.TestCase):
    """AvatarCropDialog 的 PySide2/6 兼容回归。"""

    def setUp(self):
        self._app = _make_qapp()
        self._img = _make_temp_image()

    def test_font_fallback_applied(self):
        """构造完成后必须显式应用字体回退族（独立 dialog 不会继承）。"""
        from maxagent.ui.avatar_crop_dialog import AvatarCropDialog
        dlg = AvatarCropDialog(self._img)
        try:
            # dialog 自身有 font 且非空
            font = dlg.font()
            self.assertTrue(bool(font.family()))
            # 子按钮也应用了同一字体（recursive=True 才能传到子控件）
            ok_font = dlg._ok_btn.font()
            self.assertEqual(ok_font.family(), font.family())
        finally:
            dlg.deleteLater()

    def test_buttons_have_emoji_and_tooltip(self):
        """取消 / 确定按钮加图标 + tooltip，与全局风格一致。"""
        from maxagent.ui.avatar_crop_dialog import AvatarCropDialog
        dlg = AvatarCropDialog(self._img)
        try:
            self.assertIn('取消', dlg._cancel_btn.text())
            self.assertIn('确定', dlg._ok_btn.text())
            # 文本中必须有非中文字符（emoji 或其 BMP 兜底）
            cancel_txt = dlg._cancel_btn.text()
            self.assertTrue(
                any(ord(c) > 127 and c not in '取消' for c in cancel_txt),
                '取消按钮缺少图标兜底字符: %r' % cancel_txt,
            )
            self.assertNotEqual(dlg._cancel_btn.toolTip(), '')
            self.assertNotEqual(dlg._ok_btn.toolTip(), '')
        finally:
            dlg.deleteLater()

    def test_cancel_btn_blocks_enter(self):
        """取消按钮必须关闭 autoDefault，避免编辑滑块时 Enter 误关。"""
        from maxagent.ui.avatar_crop_dialog import AvatarCropDialog
        dlg = AvatarCropDialog(self._img)
        try:
            self.assertFalse(
                dlg._cancel_btn.autoDefault(),
                '取消按钮 autoDefault 未关闭，回车会误触发取消',
            )
            # 确定按钮保留 default=True 是良性默认行为，不强制断言
        finally:
            dlg.deleteLater()


class EmployeeTabUploadCompatTests(unittest.TestCase):
    """EmployeeTab 上传/清除按钮的视觉统一性回归。"""

    def setUp(self):
        from maxagent.config import ConfigManager
        self._app = _make_qapp()
        self._tmpdir = tempfile.mkdtemp()
        self._cfg = ConfigManager(
            os.path.join(self._tmpdir, 'config.json'),
        )

    def test_upload_button_has_icon_and_tooltip(self):
        from maxagent.ui.employee_tab import EmployeeTab
        tab = EmployeeTab(self._cfg)
        try:
            txt = tab._upload_btn.text()
            self.assertIn('选择图片', txt)
            # 必须包含图标兜底字符（非 ASCII 且不属于"选择图片"四个汉字）
            self.assertTrue(
                any(
                    ord(c) > 127 and c not in '选择图片 '
                    for c in txt
                ),
                '上传按钮缺少图标兜底字符: %r' % txt,
            )
            self.assertNotEqual(tab._upload_btn.toolTip(), '')
        finally:
            tab.deleteLater()

    def test_clear_button_has_icon_and_tooltip(self):
        from maxagent.ui.employee_tab import EmployeeTab
        tab = EmployeeTab(self._cfg)
        try:
            txt = tab._clear_img_btn.text()
            self.assertIn('清除', txt)
            self.assertTrue(
                any(
                    ord(c) > 127 and c not in '清除 '
                    for c in txt
                ),
                '清除按钮缺少图标兜底字符: %r' % txt,
            )
            self.assertNotEqual(tab._clear_img_btn.toolTip(), '')
        finally:
            tab.deleteLater()


if __name__ == '__main__':
    unittest.main()
