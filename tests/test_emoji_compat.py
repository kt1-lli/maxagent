#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emoji_compat 单元测试。

只覆盖纯逻辑（不依赖 QApplication 渲染）：
- e() 在 PySide2 / PySide6 下的返回值切换
- set_use_real_emoji 覆盖
- apply_font_fallback 不会因为 setFamilies 缺失而报错
"""

from __future__ import absolute_import
from __future__ import print_function

import pytest


from maxagent.ui import emoji_compat as ec


def test_e_returns_emoji_when_real_emoji_enabled():
    ec.set_use_real_emoji(True)
    try:
        assert ec.e('🌐', '[net]') == '🌐'
        assert ec.e('🤖', '[bot]') == '🤖'
    finally:
        # 恢复默认
        ec.set_use_real_emoji(ec.IS_PYSIDE6 if False else ec._USE_REAL_EMOJI)


def test_e_returns_fallback_when_real_emoji_disabled():
    ec.set_use_real_emoji(False)
    try:
        assert ec.e('🌐', '[网]') == '[网]'
        assert ec.e('🎨', '★') == '★'
    finally:
        ec.set_use_real_emoji(True)


def test_use_real_emoji_getter_setter_roundtrip():
    original = ec.use_real_emoji()
    ec.set_use_real_emoji(not original)
    assert ec.use_real_emoji() == (not original)
    ec.set_use_real_emoji(original)
    assert ec.use_real_emoji() == original


def test_apply_font_fallback_handles_none_widget():
    # 不应抛异常
    ec.apply_font_fallback(None)


def test_apply_font_fallback_with_fake_widget():
    """模拟 QWidget：只要有 font() / setFont() 就能调用。"""

    class FakeFont(object):
        def __init__(self):
            self.families_set = None
            self.family_str = None

        def setFamilies(self, fams):
            self.families_set = list(fams)

        def setFamily(self, name):
            self.family_str = name

    class FakeWidget(object):
        def __init__(self):
            self._font = FakeFont()
            self._applied = None

        def font(self):
            return self._font

        def setFont(self, f):
            self._applied = f

    w = FakeWidget()
    ec.apply_font_fallback(w, families=['MyFont', 'FallbackFont'])
    assert w._applied is w._font
    assert w._font.families_set == ['MyFont', 'FallbackFont']


def test_apply_font_fallback_old_qt_no_setfamilies():
    """Qt 5.12 之前没有 setFamilies；走 setFamily 兜底。"""

    class OldFont(object):
        def __init__(self):
            self.family_str = None

        def setFamily(self, name):
            self.family_str = name

    class OldWidget(object):
        def __init__(self):
            self._font = OldFont()

        def font(self):
            return self._font

        def setFont(self, _f):
            pass

    w = OldWidget()
    ec.apply_font_fallback(w, families=['F1', 'F2'])
    assert 'F1' in w._font.family_str
    assert 'F2' in w._font.family_str
