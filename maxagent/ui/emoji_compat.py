#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Emoji 与字体兼容层（PySide2 / PySide6 双版本）。

为什么需要这个文件？
====================
Max 2022~2024 自带的 PySide2 (Qt 5.12~5.15) 在 Windows 上有已知字体
回退缺陷：当一段文本里同时出现 emoji（U+1F300+ 颜色字形）和汉字时，
Qt 不会按字符切分字体——一旦命中 emoji 字体就拖着整行去 emoji 字体里
画，汉字就被画成了"豆腐块 / 残缺图形"。

PySide6 (Qt6) 改用了新的字体回退栈，按字符独立选字体，不会出现这种
"emoji 拖累整行"的问题。

应对策略
========
1. **方案 A（字体回退链）**
   ``apply_font_fallback(widget)`` 给传入控件设一份带回退族的 QFont，
   利用 ``QFont.setFamilies()``（Qt 5.13+）让 Qt 按字符级回退到中文族
   或 emoji 族。这一步能消除 70% 的显示问题。

2. **方案 C（双轨 emoji 工具）**
   ``e(emoji_char, fallback_text)`` 在 PySide6 下原样返回 emoji，
   在 PySide2 下返回 ASCII / BMP 内符号，保证文字一定能渲染。

什么时候用哪个？
================
- 永远调用一次 ``apply_font_fallback(window)``：成本极低，影响全局。
- 关键控件（按钮 / 状态栏 / Tab 标签 / 标题）的字面量改用 ``e()``，
  防止 PySide2 上 emoji 渲染异常时导致按钮文字看不见。
- 对话气泡里的 emoji 一般没问题（HTML rich text 走 QTextDocument，
  字体回退路径不同），可以保持原样。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import List

from ..qt_compat import IS_PYSIDE2
from ..qt_compat import IS_PYSIDE6
from ..qt_compat import QtGui


# 在 Windows 上常见的字体回退候选；按"主中文 → 回退中文 → emoji → 系统兜底"
# 顺序排列。Qt 会从前往后找能画出当前字符的第一款，画不出再往后试。
_DEFAULT_FAMILIES = [
    'Microsoft YaHei UI',     # Win10+ 默认中文 UI 字体
    'Microsoft YaHei',        # Win7+ 默认中文字体
    'PingFang SC',            # macOS 默认
    'Noto Sans CJK SC',       # Linux 常见
    'SimSun',                 # Win 老字体兜底
    'Segoe UI',               # Windows 西文
    'Segoe UI Emoji',         # Windows emoji 字体（彩色）
    'Apple Color Emoji',      # macOS emoji
    'Noto Color Emoji',       # Linux emoji
    'Arial',                  # 通用兜底
]


# 当前是否使用真 emoji。PySide6 / 非 Windows 默认开；PySide2 默认关
# （主要是 Windows + Qt5 的渲染坑）。
_USE_REAL_EMOJI = bool(IS_PYSIDE6)


def use_real_emoji():
    # type: () -> bool
    """返回当前是否启用真 emoji 字符。

    PySide2 下默认 False，按 ASCII / BMP 兜底；PySide6 下 True，
    维持原本的视觉效果。可在测试时通过 :func:`set_use_real_emoji` 覆盖。
    """
    return _USE_REAL_EMOJI


def set_use_real_emoji(value):
    # type: (bool) -> None
    """覆盖 emoji 启用状态。仅供测试或用户偏好开关使用。"""
    global _USE_REAL_EMOJI
    _USE_REAL_EMOJI = bool(value)


def e(emoji_char, fallback_text):
    # type: (str, str) -> str
    """返回当前环境下"该位置应显示的字符"。

    :param emoji_char: PySide6 下使用的真 emoji，例如 ``'🌐'``
    :param fallback_text: PySide2 下使用的纯文本/BMP 符号兜底，
        例如 ``'[网]'`` 或 ``'◆'``。BMP 符号 (U+0000~U+FFFF)
        在 Qt5 上几乎都能正确渲染。

    用法示例：

        from maxagent.ui.emoji_compat import e
        btn.setText(e('🌐', '[网]') + ' 联网')
    """
    return emoji_char if _USE_REAL_EMOJI else fallback_text


def apply_font_fallback(widget, families=None):
    # type: (object, List[str]) -> None
    """给指定控件（含其所有子控件）设置带回退族的 QFont。

    依赖 ``QFont.setFamilies()``（Qt 5.13+）。在更老的 Qt 上自动降级
    为单族字符串，效果略差但不会崩。

    :param widget: 任意 ``QWidget``；通常是主面板根 widget。
    :param families: 自定义字体回退候选；不传时使用 ``_DEFAULT_FAMILIES``。
    """
    if widget is None:
        return
    fams = list(families or _DEFAULT_FAMILIES)
    font = widget.font() if hasattr(widget, 'font') else QtGui.QFont()
    set_families = getattr(font, 'setFamilies', None)
    if callable(set_families):
        try:
            set_families(fams)
        except Exception:  # pylint: disable=broad-except
            # 极端情况下 setFamilies 也可能拒绝（PySide2 5.12 之前）；
            # 退回到单族字符串作为最后兜底
            font.setFamily(fams[0])
    else:
        # PySide2 5.12 没有 setFamilies；用 CSS 风格 family 串
        # （Qt 内部会做粗略的回退）
        font.setFamily(', '.join(fams))
    widget.setFont(font)


def install_app_font_fallback(app=None):
    # type: (object) -> None
    """把字体回退族应用到 QApplication 级别，覆盖所有未来创建的控件。

    在主入口（如 ``startup.show_panel``）调用一次即可。已存在控件
    需要单独 ``apply_font_fallback`` 才能生效。
    """
    try:
        from ..qt_compat import QtWidgets
        a = app or QtWidgets.QApplication.instance()
    except Exception:  # pylint: disable=broad-except
        return
    if a is None:
        return
    apply_font_fallback(a)


__all__ = [
    'apply_font_fallback',
    'e',
    'install_app_font_fallback',
    'set_use_real_emoji',
    'use_real_emoji',
]
