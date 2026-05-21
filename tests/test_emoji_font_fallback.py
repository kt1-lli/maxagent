#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 emoji 字体回退 install_emoji_font_fallback 的行为。

这些测试不验证 emoji 是否真的渲染（依赖系统字体不靠谱），而是验证：
- 函数返回 True 表示安装到了 QApplication
- 安装后 QApplication.font().families() 包含主字体 + 至少一个 emoji 候选
- 没有 QApplication 实例时返回 False，不抛异常
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import sys

import pytest


# 离屏 Qt：测试不打开真窗口
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture(scope='module')
def qapp():
    """模块级 QApplication，避免反复 new/delete 在某些 PySide 版本上崩溃。"""
    from maxagent.qt_compat import QtWidgets
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    yield app


def test_install_returns_true_with_qapp(qapp):
    """有 QApplication 时，安装函数应返回 True。"""
    from maxagent.qt_compat import install_emoji_font_fallback
    ok = install_emoji_font_fallback()
    assert ok is True


def test_emoji_family_appears_in_app_font(qapp):
    """安装后默认字体的 families 列表应当包含至少一个 emoji 候选。"""
    from maxagent.qt_compat import install_emoji_font_fallback
    install_emoji_font_fallback()
    font = qapp.font()
    # PySide6 / PySide2 5.13+ 都支持 families()
    families = []
    if hasattr(font, 'families'):
        families = list(font.families())
    else:
        # 极老 PySide2: family() 是单字符串
        families = [font.family()]
    # 主字体保留
    assert families, '安装后 families 不应为空'
    # 至少包含一个 emoji 候选
    emoji_candidates = {
        'Segoe UI Emoji', 'Segoe UI Symbol',
        'Apple Color Emoji', 'Noto Color Emoji',
        'EmojiOne Color', 'Twemoji Mozilla',
    }
    assert any(f in emoji_candidates for f in families), (
        '安装后 families 应包含至少一个 emoji 候选，实际: {!r}'.format(families)
    )


def test_install_is_idempotent(qapp):
    """重复调用不应叠加多次相同 fallback。"""
    from maxagent.qt_compat import install_emoji_font_fallback
    install_emoji_font_fallback()
    install_emoji_font_fallback()
    install_emoji_font_fallback()
    font = qapp.font()
    if hasattr(font, 'families'):
        families = list(font.families())
    else:
        families = [font.family()]
    # 'Segoe UI Emoji' 不应被加超过一次
    if 'Segoe UI Emoji' in families:
        count = sum(1 for f in families if f == 'Segoe UI Emoji')
        assert count == 1, '幂等性失败：emoji family 出现 {} 次'.format(count)


def test_install_without_qapp_returns_false():
    """没有 QApplication 实例时返回 False，不抛异常。

    在已经创建过 QApplication 的解释器里无法把它真正销毁，
    因此用 monkeypatch 的方式让 instance() 返回 None。
    """
    from maxagent import qt_compat
    real_instance = qt_compat.QtWidgets.QApplication.instance
    try:
        qt_compat.QtWidgets.QApplication.instance = staticmethod(lambda: None)
        ok = qt_compat.install_emoji_font_fallback()
        assert ok is False
    finally:
        qt_compat.QtWidgets.QApplication.instance = real_instance
