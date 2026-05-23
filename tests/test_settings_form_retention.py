#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""锁定本次修复的回归测试。

覆盖：
1. _read_form 在 UI 不暴露的字段上保留旧值（kind / 计费 / tool_result_max_bytes）
2. max_tokens=0 写入后保持 0（不再被强制改为 4096）
3. LLMClient.chat 在 max_tokens<=0 时不发该字段（兼容严苛网关 vita）
4. 设置面板 add_btn / del_btn 关闭 autoDefault（防止表单 Enter 误触发新建）
5. apply_btn 设为 default（表单 Enter 直接保存）
6. youtu-vita / vita 进入视觉白名单
"""
from __future__ import absolute_import
from __future__ import print_function

import json
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def _make_qapp():
    from maxagent.qt_compat import QtWidgets
    return (
        QtWidgets.QApplication.instance()
        or QtWidgets.QApplication(sys.argv)
    )


class ReadFormFieldRetentionTests(unittest.TestCase):
    """_read_form 必须保留 UI 不暴露的字段。"""

    def setUp(self):
        import tempfile
        from maxagent.config import ConfigManager
        from maxagent.config import LLMProfile
        self._app = _make_qapp()
        self._tmpdir = tempfile.mkdtemp()
        self._cfg = ConfigManager(os.path.join(self._tmpdir, 'config.json'))
        # 准备一个带"UI 不暴露字段"非默认值的 profile
        self._cfg.config.profiles = [
            LLMProfile(
                name='vita',
                base_url='https://x.example.com/v1',
                api_key='sk-test',
                model='youtu-vita',
                kind='remote',
                tool_result_max_bytes=8192,
                price_input_per_1m=2.5,
                price_output_per_1m=5.0,
                auto_summarize_threshold=24000,
            ),
        ]
        self._cfg.config.active_profile = 'vita'
        self._cfg.save()

    def test_apply_keeps_hidden_fields(self):
        from maxagent.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self._cfg)
        try:
            # 选中 vita profile
            for i in range(dlg.profile_list.count()):
                if dlg.profile_list.item(i).text() == 'vita':
                    dlg.profile_list.setCurrentRow(i)
                    break
            # 模拟用户改了 UI 上暴露的字段
            dlg.temperature_spin.setValue(0.5)
            dlg.max_tokens_spin.setValue(0)  # "由模型决定"
            new_prof = dlg._read_form()
            # UI 暴露字段应被覆盖
            self.assertEqual(new_prof.temperature, 0.5)
            self.assertEqual(new_prof.max_tokens, 0)  # 0 应保留为 0
            # UI 不暴露字段应保留旧值，不被默认值覆盖
            self.assertEqual(new_prof.kind, 'remote')
            self.assertEqual(new_prof.tool_result_max_bytes, 8192)
            self.assertAlmostEqual(new_prof.price_input_per_1m, 2.5)
            self.assertAlmostEqual(new_prof.price_output_per_1m, 5.0)
            self.assertEqual(new_prof.auto_summarize_threshold, 24000)
        finally:
            dlg.deleteLater()

    def test_new_profile_uses_dataclass_defaults(self):
        """新建 profile（基底为 None）应使用 dataclass 默认值兜底。"""
        from maxagent.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self._cfg)
        try:
            # 模拟新建时 _current_profile 还没设
            dlg._current_profile = ''
            dlg.name_edit.setText('brand-new')
            dlg.base_url_edit.setText('http://x/v1')
            dlg.api_key_edit.setText('k')
            dlg.model_edit.setText('m')
            new_prof = dlg._read_form()
            # 应能落到 dataclass 默认值（不会因找不到基底报错）
            self.assertEqual(new_prof.name, 'brand-new')
            self.assertIsInstance(new_prof.tool_result_max_bytes, int)
        finally:
            dlg.deleteLater()


class MaxTokensZeroTests(unittest.TestCase):
    """max_tokens<=0 时 chat() 不发该字段。"""

    def test_payload_omits_max_tokens_when_zero(self):
        from maxagent.llm_client import LLMClient
        captured = {}

        class _FakeURL(object):
            def __init__(self, payload_bytes):
                captured['payload'] = json.loads(
                    payload_bytes.decode('utf-8'),
                )
                self._raw = (
                    b'{"choices":[{"message":{"content":"ok"},'
                    b'"finish_reason":"stop"}]}'
                )

            def read(self):
                return self._raw

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        import urllib.request as _ur
        _orig = _ur.urlopen

        def _fake_urlopen(req, timeout=None):
            return _FakeURL(req.data)

        _ur.urlopen = _fake_urlopen
        try:
            client = LLMClient(
                base_url='http://x',
                api_key='k',
                model='youtu-vita',
                timeout=5,
            )
            client.chat(
                messages=[{'role': 'user', 'content': 'hi'}],
                max_tokens=0,
                stream=False,
            )
            self.assertNotIn('max_tokens', captured['payload'])

            client.chat(
                messages=[{'role': 'user', 'content': 'hi'}],
                max_tokens=2048,
                stream=False,
            )
            self.assertEqual(captured['payload']['max_tokens'], 2048)
        finally:
            _ur.urlopen = _orig


class EnterKeyDefaultsTests(unittest.TestCase):
    """add_btn / del_btn 必须关掉 autoDefault；apply_btn 必须为 default。"""

    def setUp(self):
        import tempfile
        from maxagent.config import ConfigManager
        self._app = _make_qapp()
        self._tmpdir = tempfile.mkdtemp()
        self._cfg = ConfigManager(os.path.join(self._tmpdir, 'config.json'))

    def test_button_default_states(self):
        from maxagent.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self._cfg)
        try:
            # add / del 必须关掉 default 行为，否则在表单输入框按 Enter
            # 会冒出"新建 Profile"对话框（用户实际遇到的 bug）
            self.assertFalse(dlg.add_btn.autoDefault())
            self.assertFalse(dlg.add_btn.isDefault())
            self.assertFalse(dlg.del_btn.autoDefault())
            self.assertFalse(dlg.del_btn.isDefault())
            # 测试按钮也不应抢 default
            self.assertFalse(dlg.test_btn.autoDefault())
            self.assertFalse(dlg.test_full_btn.autoDefault())
            # 应用按钮才是表单 default
            self.assertTrue(dlg.apply_btn.autoDefault())
            self.assertTrue(dlg.apply_btn.isDefault())
        finally:
            dlg.deleteLater()


class VisionWhitelistTests(unittest.TestCase):
    """youtu-vita / vita 应被识别为视觉模型。"""

    def test_vita_in_default_whitelist(self):
        from maxagent.config import AppConfig
        cfg = AppConfig()
        whitelist = list(cfg.vision_model_whitelist)
        self.assertIn('youtu-vita', whitelist)
        self.assertIn('vita', whitelist)

    def test_vita_supports_vision(self):
        from maxagent.attachments import model_supports_vision
        from maxagent.config import AppConfig
        wl = list(AppConfig().vision_model_whitelist)
        self.assertTrue(model_supports_vision('youtu-vita', wl))
        # 子串匹配，未来类似命名也应命中
        self.assertTrue(model_supports_vision('vita-pro', wl))


if __name__ == '__main__':
    unittest.main()
