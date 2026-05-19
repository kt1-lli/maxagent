#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PySide2 / PySide6 统一适配层。

设计目标：
    Max 2022~2024 自带 PySide2 (Qt5)
    Max 2025+ 自带 PySide6 (Qt6)
    本层屏蔽两者 import 路径与 API 差异，让上层 UI 代码无感切换。

使用方式：
    from maxagent.ui.qt_compat import QtCore, QtGui, QtWidgets
    from maxagent.ui.qt_compat import Signal, Slot, QAction
    from maxagent.ui.qt_compat import IS_PYSIDE6, exec_compat
"""

from __future__ import absolute_import
from __future__ import print_function

import sys


IS_PYSIDE6 = False
IS_PYSIDE2 = False

# pylint: disable=import-error,no-name-in-module,unused-import
try:
    from PySide6 import QtCore  # type: ignore
    from PySide6 import QtGui  # type: ignore
    from PySide6 import QtWidgets  # type: ignore
    from PySide6.QtCore import Signal  # type: ignore
    from PySide6.QtCore import Slot  # type: ignore
    from PySide6.QtGui import QAction  # type: ignore
    from PySide6.QtGui import QShortcut  # type: ignore
    from PySide6.QtCore import QRegularExpression as _QRegularExpression  # type: ignore
    IS_PYSIDE6 = True
except ImportError:
    try:
        from PySide2 import QtCore  # type: ignore
        from PySide2 import QtGui  # type: ignore
        from PySide2 import QtWidgets  # type: ignore
        from PySide2.QtCore import Signal  # type: ignore
        from PySide2.QtCore import Slot  # type: ignore
        from PySide2.QtWidgets import QAction  # type: ignore
        from PySide2.QtWidgets import QShortcut  # type: ignore
        from PySide2.QtCore import QRegExp as _QRegularExpression  # type: ignore
        IS_PYSIDE2 = True
    except ImportError as exc:
        raise ImportError(
            "maxagent 需要 PySide2 (Max 2022-2024) 或 PySide6 (Max 2025+)，"
            "当前环境均未找到。原始错误: {}".format(exc)
        )
# pylint: enable=import-error,no-name-in-module,unused-import


# 暴露统一的正则类（PySide2: QRegExp / PySide6: QRegularExpression）
QRegex = _QRegularExpression


def exec_compat(widget):
    """统一调用 dialog.exec_() (PySide2) 或 dialog.exec() (PySide6)。

    :param widget: 实现了 exec_/exec 的对象（QDialog、QMenu、QApplication 等）
    :returns: exec 的返回值
    """
    fn = getattr(widget, "exec", None)
    if IS_PYSIDE6 and callable(fn):
        return fn()
    fn = getattr(widget, "exec_", None)
    if callable(fn):
        return fn()
    # 极端情况下两个都有，PySide6 也保留了 exec_，按上面顺序兜底
    raise AttributeError("对象 {} 既无 exec 也无 exec_".format(type(widget)))


def get_qapp():
    """获取或创建 QApplication 实例。"""
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    return app


# ---------------------------------------------------------------------- #
# Max 主窗口获取（不同版本 API 略有差异，统一封装）
# ---------------------------------------------------------------------- #

def get_max_main_window():
    """获取 3ds Max 主窗口的 QWidget 句柄。

    Max 2017+ 推荐使用 qtmax.GetQMaxMainWindow()。
    Max 早期需要通过 sip + win32 句柄包装，已不再支持。

    :returns: QWidget 或 None（在 Max 之外运行时返回 None）
    """
    try:
        import qtmax  # type: ignore  # pylint: disable=import-error
        return qtmax.GetQMaxMainWindow()
    except ImportError:
        pass

    # 兜底：找顶层窗口里 objectName 是 'QmaxMainWindow' 的
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    for w in app.topLevelWidgets():
        if w.objectName() in ("QmaxMainWindow", "QMaxMainWindow"):
            return w
    return None


# ---------------------------------------------------------------------- #
# 信号常量兼容（PySide6 改用 enum，PySide2 是裸 int）
# ---------------------------------------------------------------------- #

def alignment_right():
    """返回右对齐枚举，兼容 PySide2/6。"""
    if IS_PYSIDE6:
        return QtCore.Qt.AlignmentFlag.AlignRight
    return QtCore.Qt.AlignRight


def alignment_left():
    if IS_PYSIDE6:
        return QtCore.Qt.AlignmentFlag.AlignLeft
    return QtCore.Qt.AlignLeft


def dock_area_right():
    if IS_PYSIDE6:
        return QtCore.Qt.DockWidgetArea.RightDockWidgetArea
    return QtCore.Qt.RightDockWidgetArea


def msgbox_yes():
    if IS_PYSIDE6:
        return QtWidgets.QMessageBox.StandardButton.Yes
    return QtWidgets.QMessageBox.Yes


def msgbox_no():
    if IS_PYSIDE6:
        return QtWidgets.QMessageBox.StandardButton.No
    return QtWidgets.QMessageBox.No


__all__ = [
    "QtCore",
    "QtGui",
    "QtWidgets",
    "Signal",
    "Slot",
    "QAction",
    "QShortcut",
    "QRegex",
    "IS_PYSIDE2",
    "IS_PYSIDE6",
    "exec_compat",
    "get_qapp",
    "get_max_main_window",
    "alignment_right",
    "alignment_left",
    "dock_area_right",
    "msgbox_yes",
    "msgbox_no",
]
