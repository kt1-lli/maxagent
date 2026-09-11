#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SVG 图标加载器（Bootstrap Icons 素材，MIT 许可）。

为什么需要这个文件？
====================
PySide2 (Max 2022~2024) 上 emoji 存在字体回退缺陷（见
:mod:`maxagent.ui.emoji_compat`），此前用 BMP 字符兜底，但视觉
质量有限。Max 内嵌 Qt 环境实测（2026-09-11 探针）确认：

- PySide2 (Qt 5.15.1) 与 PySide6 (Qt 6.5.3) 的 ``QtSvg`` 模块均可用；
- ``qsvg`` 图像格式插件齐全（甚至支持 ``QIcon('x.svg')`` 直连）。

因此以 SVG 单色线性图标作为首选视觉方案，emoji/BMP 文本降级为
加载失败时的兜底，三层方案共存互不冲突。

设计要点
========
- 只依赖 ``QtSvg.QSvgRenderer`` 手动渲染成 QPixmap，不依赖 QIcon
  直连，规避插件路径的不确定性；
- 绑定选择严格跟随 :mod:`maxagent.qt_compat` 已解析的结果
  （IS_PYSIDE6 / IS_PYSIDE2），绝不引入第二套 Qt 绑定；
- Bootstrap Icons 使用 ``fill="currentColor"``，渲染前把
  currentColor 替换为目标颜色，即可适配明暗主题；
- 以 2x 尺寸渲染并设置 DevicePixelRatio，保证高分屏下清晰；
- 任何失败（文件缺失 / 模块缺失 / 渲染失败）一律返回 None，
  由调用方走 ``btn_label`` 文本兜底，图标属于纯装饰不影响功能；
- 进程级缓存渲染结果，同一图标多处使用零重复开销；
  ``clear_cache()`` 供热重载场景使用。

用法示例::

    from maxagent.ui.icon_loader import set_btn_icon

    btn = QtWidgets.QPushButton(_btn_label('📌', '立即重新停靠'))
    set_btn_icon(btn, 'pin', '立即重新停靠')
    # 成功：按钮变为 SVG 图标 + 纯文本
    # 失败：按钮保持 btn_label 生成的 emoji/BMP 文本兜底
"""

from __future__ import absolute_import
from __future__ import print_function

import os
from typing import Optional
from typing import Tuple

from ..qt_compat import QtGui
from ..qt_compat import IS_PYSIDE6
from ..qt_compat import IS_PYSIDE2


# 图标资源目录：maxagent/ui/icons/
# 用 __file__ 推导绝对路径，Max 启动 CWD 不定时依然可靠
_ICONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icons')

# 默认图标颜色：中性灰，Max 明暗主题下均有足够对比度
DEFAULT_ICON_COLOR = '#808080'

# 渲染成功的缓存：键为 (图标名, 颜色)，值为 QIcon
_ICON_CACHE = {}  # type: dict

# 渲染失败的图标名集合：记录后不再重复尝试 IO 与解析
_MISSING_ICONS = set()  # type: set


def icons_dir():
    # type: () -> str
    """返回图标资源目录的绝对路径。"""
    return _ICONS_DIR


def clear_cache():
    # type: () -> None
    """清空渲染缓存（热重载 / 换肤场景使用）。"""
    _ICON_CACHE.clear()
    _MISSING_ICONS.clear()


def load_icon(name, color=None, size=16):
    # type: (str, Optional[str], int) -> Optional[object]
    """按名称加载 SVG 图标，渲染为 QIcon。

    :param name: 图标名，对应 ``icons/<name>.svg``，如 ``'refresh'``
    :param color: 图标颜色（CSS 颜色串）；不传用 :data:`DEFAULT_ICON_COLOR`
    :param size: 逻辑尺寸（px），实际按 2x 渲染保证 HiDPI 清晰
    :returns: QIcon；任何一步失败返回 None（调用方走文本兜底）
    """
    fill = color or DEFAULT_ICON_COLOR
    cache_key = (name, fill)  # type: Tuple[str, str]
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]
    if name in _MISSING_ICONS:
        return None
    path = os.path.join(_ICONS_DIR, name + '.svg')
    if not os.path.isfile(path):
        _MISSING_ICONS.add(name)
        return None
    icon = _render_svg_file(path, fill, size)
    if icon is None:
        _MISSING_ICONS.add(name)
        return None
    _ICON_CACHE[cache_key] = icon
    return icon


def set_btn_icon(widget, name, text, color=None):
    # type: (object, str, str, Optional[str]) -> bool
    """给按钮设置 SVG 图标并把标签改为纯文本。

    成功：``setIcon`` + ``setText(text)``（去掉 emoji 前缀），返回 True。
    失败：不做任何修改——按钮保持 ``btn_label`` 生成的 emoji/BMP
    文本兜底，返回 False。调用方无需 if/else 分支。

    :param widget: QPushButton 等 AbstractButton 控件
    :param name: 图标名（对应 icons/<name>.svg）
    :param text: 成功后按钮显示的纯文本
    :param color: 可选图标颜色
    :returns: 是否成功应用了 SVG 图标
    """
    if widget is None:
        return False
    icon = load_icon(name, color=color)
    if icon is None:
        return False
    try:
        widget.setIcon(icon)
        widget.setText(text)
        return True
    except Exception:  # pylint: disable=broad-except
        return False


def _render_svg_file(path, fill, size):
    # type: (str, str, int) -> Optional[object]
    """读取并渲染单个 SVG 文件，失败返回 None。

    绑定跟随 qt_compat 的解析结果：PySide6 环境只 import
    PySide6.QtSvg，PySide2 环境只 import PySide2.QtSvg，
    避免双绑定同时加载导致 Max 崩溃。
    """
    try:
        from ..qt_compat import QtCore
        if IS_PYSIDE6:
            from PySide6.QtSvg import QSvgRenderer  # type: ignore  # pylint: disable=import-error,no-name-in-module
        elif IS_PYSIDE2:
            from PySide2.QtSvg import QSvgRenderer  # type: ignore  # pylint: disable=import-error,no-name-in-module
        else:
            return None

        with open(path, 'r', encoding='utf-8') as f:
            svg_text = f.read()

        # Bootstrap Icons 用 fill="currentColor"，替换为目标颜色；
        # 同时兜底替换写死的黑色，保证颜色参数对任意素材生效
        svg_text = svg_text.replace('currentColor', fill)
        svg_text = svg_text.replace('fill="#000000"', 'fill="{}"'.format(fill))

        renderer = QSvgRenderer(QtCore.QByteArray(svg_text.encode('utf-8')))
        if not renderer.isValid():
            return None

        # 2x 尺寸渲染 + DevicePixelRatio，HiDPI 下依然清晰
        scale = 2.0
        pixmap = QtGui.QPixmap(int(size * scale), int(size * scale))
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        try:
            renderer.render(painter)
        finally:
            painter.end()
        if pixmap.isNull():
            return None
        pixmap.setDevicePixelRatio(scale)
        return QtGui.QIcon(pixmap)
    except Exception:  # pylint: disable=broad-except
        # 图标是纯装饰，任何异常都不能影响 UI 构建
        return None


__all__ = [
    'DEFAULT_ICON_COLOR',
    'clear_cache',
    'icons_dir',
    'load_icon',
    'set_btn_icon',
]
