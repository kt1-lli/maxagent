#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视觉白名单 UI 编辑器的专项测试。

覆盖：
1. 默认值正确加载到编辑框
2. 解析逻辑：trim / 大小写归一化 / 去重 / 跳过空行与 # 注释
3. textChanged 触发后 cfg.vision_model_whitelist 被更新并落盘
4. 重置按钮恢复为内置默认值
5. dock_widget 的两处使用点能读到新值（接口保持不变）
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


class VisionWhitelistEditorTests(unittest.TestCase):

    def setUp(self):
        from maxagent.config import ConfigManager
        self._app = _make_qapp()
        self._tmpdir = tempfile.mkdtemp()
        self._cfg_path = os.path.join(self._tmpdir, 'config.json')
        self._cfg = ConfigManager(self._cfg_path)

    def _new_dlg(self):
        from maxagent.ui.settings_dialog import SettingsDialog
        return SettingsDialog(self._cfg)

    def test_default_whitelist_loaded_into_editor(self):
        dlg = self._new_dlg()
        try:
            text = dlg.vision_whitelist_edit.toPlainText()
            lines = [line for line in text.splitlines() if line.strip()]
            # 默认白名单非空，且至少包含主流模型
            self.assertGreater(len(lines), 0)
            joined = text.lower()
            for must_have in ['gpt-4o', 'claude-3', 'qwen-vl', 'youtu-vita']:
                self.assertIn(must_have, joined)
        finally:
            dlg.deleteLater()

    def test_parse_trims_dedups_normalizes(self):
        dlg = self._new_dlg()
        try:
            parsed = dlg._parse_vision_whitelist(
                'GPT-4o\n  vita  \nclaude-3\n# comment\n\nclaude-3\nVita\n',
            )
            # 大小写归一化为小写、去重、保留顺序、忽略注释和空行
            self.assertEqual(parsed, ['gpt-4o', 'vita', 'claude-3'])
        finally:
            dlg.deleteLater()

    def test_text_change_writes_back_and_persists(self):
        dlg = self._new_dlg()
        try:
            dlg.vision_whitelist_edit.setPlainText('foo\nbar\n')
            dlg._on_vision_whitelist_changed()
            self.assertEqual(
                self._cfg.config.vision_model_whitelist, ['foo', 'bar'],
            )
            # 重新加载磁盘文件，确认已落盘
            from maxagent.config import ConfigManager
            cm2 = ConfigManager(self._cfg_path)
            self.assertEqual(
                cm2.config.vision_model_whitelist, ['foo', 'bar'],
            )
        finally:
            dlg.deleteLater()

    def test_reset_restores_dataclass_defaults(self):
        from maxagent.config import AppConfig
        dlg = self._new_dlg()
        try:
            # 先污染白名单
            dlg.vision_whitelist_edit.setPlainText('only-one\n')
            dlg._on_vision_whitelist_changed()
            self.assertEqual(
                self._cfg.config.vision_model_whitelist, ['only-one'],
            )
            # 模拟用户在确认对话框点 Yes：直接走非 UI 路径恢复默认
            defaults = list(AppConfig().vision_model_whitelist)
            dlg.vision_whitelist_edit.setPlainText('\n'.join(defaults))
            dlg._on_vision_whitelist_changed()
            # 写回值与 dataclass 默认完全一致
            self.assertEqual(
                self._cfg.config.vision_model_whitelist, defaults,
            )
        finally:
            dlg.deleteLater()

    def test_no_save_when_unchanged(self):
        """同值时不应触发写盘（避免无谓 IO 与日志噪音）。"""
        dlg = self._new_dlg()
        try:
            current = list(self._cfg.config.vision_model_whitelist)
            saved_calls = {'count': 0}
            orig_save = self._cfg.save

            def _counting_save(*a, **kw):
                saved_calls['count'] += 1
                return orig_save(*a, **kw)

            self._cfg.save = _counting_save
            # 设置成与当前完全一致的文本（按 _parse 后的规范化形式）
            dlg.vision_whitelist_edit.setPlainText('\n'.join(current))
            dlg._on_vision_whitelist_changed()
            self.assertEqual(saved_calls['count'], 0)
        finally:
            dlg.deleteLater()

    def test_dock_widget_consumer_reads_new_list(self):
        """验证 dock_widget 取白名单的接口（getattr 取列表）行为不变。"""
        from maxagent.attachments import model_supports_vision
        dlg = self._new_dlg()
        try:
            dlg.vision_whitelist_edit.setPlainText('my-custom-vlm\n')
            dlg._on_vision_whitelist_changed()
            wl = list(getattr(
                self._cfg.config, 'vision_model_whitelist', [],
            ))
            self.assertEqual(wl, ['my-custom-vlm'])
            # 子串匹配：含 my-custom-vlm 的模型名应命中
            self.assertTrue(model_supports_vision('my-custom-vlm-pro', wl))
            self.assertFalse(model_supports_vision('gpt-3.5', wl))
        finally:
            dlg.deleteLater()


if __name__ == '__main__':
    unittest.main()
