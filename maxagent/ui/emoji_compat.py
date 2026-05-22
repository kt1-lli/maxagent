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
   - ``e(emoji_char, fallback_text)`` 在 PySide6 下原样返回 emoji；在
     PySide2 下返回调用方指定的 BMP / ASCII 兜底。
   - ``ee(emoji_char)`` 内置一张主题字符表（见
     :data:`EMOJI_FALLBACK_TABLE`），直接按 emoji 查表得到视觉相近的
     **BMP 单字符**兜底，省去手填 fallback 字符串。

   BMP 范围（U+0000~U+FFFF）内的字符在 Qt5 上几乎全部能稳定渲染，
   特别是 Win10 自带的 Segoe UI / Symbol / DejaVu Sans 字体覆盖很全。

什么时候用哪个？
================
- 永远调用一次 ``apply_font_fallback(window)``：成本极低，影响全局。
- 关键控件（按钮 / 状态栏 / Tab 标签 / 标题）的 emoji 字面量改用
  ``ee(...)`` 或 ``e(..., ...)``，防止 PySide2 上 emoji 渲染异常导致
  按钮文字看不见。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import List
from typing import Optional

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
        例如 ``'◆'`` 或 ``'※'``。BMP 范围 (U+0000~U+FFFF) 内的字符
        在 Qt5 上几乎都能正确渲染。

    用法示例：

        from maxagent.ui.emoji_compat import e
        btn.setText(e('🌐', '◈') + ' 联网')

    .. note::
       常用 emoji 推荐直接使用 :func:`ee`，省去手填 fallback。
    """
    return emoji_char if _USE_REAL_EMOJI else fallback_text


# ---------------------------------------------------------------------- #
# 主题字符表：emoji -> BMP 单字符兜底
# ---------------------------------------------------------------------- #
# 设计原则：
# 1. 全部使用 BMP 范围（U+0000~U+FFFF）内的字符，Qt5 渲染稳定。
# 2. 视觉上尽量贴近原 emoji 的"语义/形状"，不堆砌花体让按钮变奇怪。
# 3. 同一类语义统一符号（成功一律 ✓ / 失败一律 ✗ / 警告一律 ⚠ / 信息一律 ℹ），
#    避免 UI 视觉碎片化。
# 4. 数据条目里给出每个 emoji 的语义注释，未来扩充更易维护。
EMOJI_FALLBACK_TABLE = {
    # —— 全局功能图标 —— #
    '🌐': '◈',       # 联网 / 全球（菱形带点，"网"的暗示）
    '🤖': '◆',       # 机器人 / 助手（实心菱形，比较稳重）
    '🎨': '✦',       # 应用 / 主题 / 美化（四角星，类调色板的视觉密度）
    '📜': '≡',       # 日志 / 文本（三横线，文档/列表暗示）
    '❓': '?',       # 帮助
    '⚙': '✱',       # 设置（齿轮替身：花型符）
    '⚙️': '✱',
    '✏': '✎',       # 编辑（铅笔的 BMP 等价字符）
    '✏️': '✎',
    '🗑': '✕',       # 删除
    '🚀': '►',       # 发送（实心右三角）
    '■': '■',       # 停止（已是 BMP）
    '🧹': '⌫',       # 清理 / 裁剪
    '📝': '✎',       # 摘要 / 记录
    '👤': '☻',       # 用户（笑脸符号当人形替身；BMP 内）
    '👋': '★',       # 欢迎 / 招手（用星形作"亮点"提示）
    '🔧': '✱',       # 工具（与齿轮统一为花型）
    # —— 状态符号 —— #
    '🟢': '●',       # 准备就绪 / 在线（实心圆）
    '🔴': '●',       # 离线（同上，靠颜色区分）
    '🟡': '◐',       # 警告中（半圆）
    '🟠': '◐',
    '⚠': '⚠',       # 警告（U+26A0 在 Qt5 渲染稳定，不需要降级）
    '⚠️': '⚠',
    '✅': '✓',       # 成功（已是 BMP）
    '✓': '✓',
    '❌': '✗',       # 失败
    '✗': '✗',
    '⏳': '◷',       # 进行中（钟表的 BMP 替身）
    '⏰': '◷',
    'ℹ': 'ℹ',       # 信息（U+2139 BMP 内）
    'ℹ️': 'ℹ',
    '🚫': '⊘',       # 禁止（带斜线的圆）
    '⛔': '⊘',
    # —— 其他常用 —— #
    '🔍': '⌕',       # 搜索（U+2315 看起来像放大镜）
    '🔎': '⌕',
    '📋': '☷',       # 剪贴板 / 列表
    '💡': '✦',       # 提示（与"美化"复用）
    '🌟': '★',
    '⭐': '★',
    '🔥': '✦',
    '🎯': '◎',       # 目标（同心圆）
    '🛠': '✱',
    '🛠️': '✱',
}


def ee(emoji_char, fallback_text=None):
    # type: (str, Optional[str]) -> str
    """按表查询 emoji 兜底字符（推荐入口）。

    :param emoji_char: 真 emoji 字符
    :param fallback_text: 表里没有时使用的兜底；若也没传，最终回退到
        emoji 原字符（保底不影响显示，最多就是 PySide2 下渲染异常）

    用法：

        from maxagent.ui.emoji_compat import ee
        btn.setText(ee('🌐') + ' 联网')      # PySide2 → '◈ 联网'
        title.setText(ee('🤖') + ' 助手')    # PySide2 → '◆ 助手'

    与 :func:`e` 的区别：
    - ``e()`` 由调用方决定 fallback，灵活但每处都要写。
    - ``ee()`` 走全局主题表，保证整套 UI 视觉一致；只有特殊场景
      （比如同一界面里要区分多个 ``⚠️``）才用 ``e()`` 自定义。
    """
    if _USE_REAL_EMOJI:
        return emoji_char
    if emoji_char in EMOJI_FALLBACK_TABLE:
        return EMOJI_FALLBACK_TABLE[emoji_char]
    if fallback_text is not None:
        return fallback_text
    # 表里没有 + 调用方没传 fallback：直接返回原字符兜底
    # （ASCII / BMP 内的字符如 '✓' '✗' 本来就能渲染，emoji 才会糊）
    return emoji_char


def _build_fallback_font(base_font, families):
    # type: (object, List[str]) -> object
    """在 ``base_font`` 基础上设置回退族，返回新的 QFont。

    若 ``base_font`` 是真正的 QFont，会拷贝其 pointSize / weight / italic
    等属性后再覆盖 family；若不是（如单元测试里传的 FakeFont），直接
    在它身上设 family 后原样返回。
    """
    # 只有真正的 QFont 才能用 QFont(other) 拷贝构造；其余情况（包括
    # 单元测试里 mock 的 FakeFont 与 None）按 in-place 修改处理
    if base_font is not None and isinstance(base_font, QtGui.QFont):
        font = QtGui.QFont(base_font)
    elif base_font is not None:
        font = base_font
    else:
        font = QtGui.QFont()
    set_families = getattr(font, 'setFamilies', None)
    if callable(set_families):
        try:
            set_families(list(families))
            return font
        except Exception:  # pylint: disable=broad-except
            # 极端情况下 setFamilies 也可能拒绝（PySide2 5.12 之前）；
            # 退回到单族字符串作为最后兜底
            font.setFamily(families[0])
            return font
    # PySide2 5.12 没有 setFamilies；用 CSS 风格 family 串
    # （Qt 内部会做粗略的回退）
    font.setFamily(', '.join(families))
    return font


def apply_font_fallback(widget, families=None, recursive=False):
    # type: (object, List[str], bool) -> None
    """给指定控件设置带回退族的 QFont。

    依赖 ``QFont.setFamilies()``（Qt 5.13+）。在更老的 Qt 上自动降级
    为单族字符串，效果略差但不会崩。

    :param widget: 任意 ``QWidget``；通常是主面板根 widget。
    :param families: 自定义字体回退候选；不传时使用 ``_DEFAULT_FAMILIES``。
    :param recursive: True 时递归遍历所有子控件并各自 setFont。
        Qt 字体不会自动级联到子控件——单纯给顶层 widget setFont
        无法保证 QPushButton / QLabel 等子控件继承到回退族。
        在 PySide2 + 嵌入 Max 主窗口的环境里，主题字体可能压过 setFont，
        递归覆盖才能确保所有子控件都使用我们的回退族。
    """
    if widget is None:
        return
    fams = list(families or _DEFAULT_FAMILIES)
    base_font = widget.font() if hasattr(widget, 'font') else None
    font = _build_fallback_font(base_font, fams)
    try:
        widget.setFont(font)
    except Exception:  # pylint: disable=broad-except
        return
    if not recursive:
        return
    # 递归对所有子 QWidget setFont。Qt 的 findChildren 默认深度遍历。
    find_children = getattr(widget, 'findChildren', None)
    if not callable(find_children):
        return
    try:
        from ..qt_compat import QtWidgets
        children = find_children(QtWidgets.QWidget)
    except Exception:  # pylint: disable=broad-except
        return
    for child in children:
        try:
            child_base = child.font() if hasattr(child, 'font') else None
            child.setFont(_build_fallback_font(child_base, fams))
        except Exception:  # pylint: disable=broad-except
            # 单个子控件失败不影响其他兄弟节点
            continue


def install_app_font_fallback(app=None):
    # type: (object) -> None
    """把字体回退族应用到 QApplication 级别，覆盖所有未来创建的控件。

    应在创建任何业务 QWidget **之前**调用一次（如 ``startup.show_panel``
    顶部）。这样后续所有 ``QWidget()`` 默认就会继承到回退族。
    对于已经存在的控件（如 Max 主窗口）此调用不会回溯生效，需要
    再走 ``apply_font_fallback(widget, recursive=True)``。
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
    'EMOJI_FALLBACK_TABLE',
    'apply_font_fallback',
    'e',
    'ee',
    'install_app_font_fallback',
    'set_use_real_emoji',
    'use_real_emoji',
]
