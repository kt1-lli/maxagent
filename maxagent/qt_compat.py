#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PySide2 / PySide6 统一适配层。

设计目标：
    Max 2022~2024 自带 PySide2 (Qt5)
    Max 2025+ 自带 PySide6 (Qt6)
    本层屏蔽两者 import 路径与 API 差异，让上层 UI 代码无感切换。

使用方式：
    from maxagent.qt_compat import QtCore, QtGui, QtWidgets
    from maxagent.qt_compat import Signal, Slot, QAction
    from maxagent.qt_compat import IS_PYSIDE6, exec_compat
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


# ---------------------------------------------------------------------- #
# PySide6 风格枚举命名空间补丁（PySide2 兼容）
#
# PySide6 将枚举提升到了独立 scope，例如：
#     QtCore.Qt.AlignmentFlag.AlignLeft   (PySide6)
#     QtCore.Qt.AlignLeft                  (PySide2)
# 为了让上层代码统一用 PySide6 风格写，在 PySide2 下打补丁，
# 把扁平枚举包装成带命名空间的 SimpleNamespace 形态。
# ---------------------------------------------------------------------- #
def _patch_pyside2_enums():
    """在 PySide2 上补齐 PySide6 风格的枚举命名空间。"""
    if not IS_PYSIDE2:
        return

    Qt = QtCore.Qt

    # (枚举命名空间名, [枚举值名 ...])
    qt_enum_groups = [
        ("AlignmentFlag", [
            "AlignLeft", "AlignRight", "AlignCenter",
            "AlignTop", "AlignBottom", "AlignVCenter", "AlignHCenter",
        ]),
        ("Orientation", ["Horizontal", "Vertical"]),
        ("CursorShape", [
            "ArrowCursor", "PointingHandCursor", "IBeamCursor",
            "SizeVerCursor", "SizeHorCursor",
        ]),
        ("TextInteractionFlag", [
            "TextSelectableByMouse", "TextSelectableByKeyboard",
            "TextBrowserInteraction", "TextEditorInteraction",
            "NoTextInteraction", "LinksAccessibleByMouse",
        ]),
        ("TextFormat", [
            "AutoText", "PlainText", "RichText", "MarkdownText",
        ]),
        ("Key", [
            "Key_Return", "Key_Enter", "Key_Escape",
            "Key_Up", "Key_Down", "Key_Left", "Key_Right",
            "Key_Tab", "Key_Backtab", "Key_Space",
        ]),
        ("KeyboardModifier", [
            "NoModifier", "ShiftModifier", "ControlModifier",
            "AltModifier", "MetaModifier",
        ]),
        ("ScrollBarPolicy", [
            "ScrollBarAsNeeded", "ScrollBarAlwaysOff", "ScrollBarAlwaysOn",
        ]),
        ("DockWidgetArea", [
            "LeftDockWidgetArea", "RightDockWidgetArea",
            "TopDockWidgetArea", "BottomDockWidgetArea",
            "AllDockWidgetAreas", "NoDockWidgetArea",
        ]),
        ("MouseButton", [
            "LeftButton", "RightButton", "MiddleButton", "NoButton",
        ]),
        ("FocusPolicy", [
            "NoFocus", "TabFocus", "ClickFocus", "StrongFocus", "WheelFocus",
        ]),
        ("WindowType", [
            "Window", "Dialog", "Popup", "Tool", "ToolTip",
            "Widget", "FramelessWindowHint",
        ]),
    ]

    for group_name, names in qt_enum_groups:
        if hasattr(Qt, group_name):
            continue
        ns = type("_QtEnumNS_" + group_name, (), {})
        for nm in names:
            if hasattr(Qt, nm):
                setattr(ns, nm, getattr(Qt, nm))
        setattr(Qt, group_name, ns)

    # QEvent.Type
    QEvent = QtCore.QEvent
    if not hasattr(QEvent, "Type") or not hasattr(QEvent.Type, "KeyPress"):
        ns = type("_QEventTypeNS", (), {})
        for nm in ("KeyPress", "KeyRelease", "MouseButtonPress",
                   "MouseButtonRelease", "Resize", "Show", "Hide",
                   "Close", "Wheel", "FocusIn", "FocusOut"):
            if hasattr(QEvent, nm):
                setattr(ns, nm, getattr(QEvent, nm))
        # 不覆盖原 Type（如有）
        if not hasattr(QEvent, "Type"):
            QEvent.Type = ns

    # QSizePolicy.Policy
    QSizePolicy = QtWidgets.QSizePolicy
    if not hasattr(QSizePolicy, "Policy"):
        ns = type("_QSizePolicyNS", (), {})
        for nm in ("Fixed", "Minimum", "Maximum", "Preferred",
                   "Expanding", "MinimumExpanding", "Ignored"):
            if hasattr(QSizePolicy, nm):
                setattr(ns, nm, getattr(QSizePolicy, nm))
        QSizePolicy.Policy = ns

    # QTextCursor.MoveOperation / MoveMode（已有的代码用到了）
    QTextCursor = QtGui.QTextCursor
    if not hasattr(QTextCursor, "MoveOperation"):
        ns = type("_QTCMoveOpNS", (), {})
        for nm in ("Start", "End", "PreviousBlock", "NextBlock",
                   "PreviousCharacter", "NextCharacter"):
            if hasattr(QTextCursor, nm):
                setattr(ns, nm, getattr(QTextCursor, nm))
        QTextCursor.MoveOperation = ns
    if not hasattr(QTextCursor, "MoveMode"):
        ns = type("_QTCMoveModeNS", (), {})
        for nm in ("MoveAnchor", "KeepAnchor"):
            if hasattr(QTextCursor, nm):
                setattr(ns, nm, getattr(QTextCursor, nm))
        QTextCursor.MoveMode = ns

    # QMessageBox.StandardButton
    QMessageBox = QtWidgets.QMessageBox
    if not hasattr(QMessageBox, "StandardButton"):
        ns = type("_QMBNS", (), {})
        for nm in ("Yes", "No", "Ok", "Cancel", "Save", "Close", "Apply"):
            if hasattr(QMessageBox, nm):
                setattr(ns, nm, getattr(QMessageBox, nm))
        QMessageBox.StandardButton = ns

    # QLineEdit.EchoMode
    QLineEdit = QtWidgets.QLineEdit
    if not hasattr(QLineEdit, "EchoMode") \
            or not hasattr(QLineEdit.EchoMode, "Password"):
        ns = type("_QLEEchoNS", (), {})
        for nm in ("Normal", "NoEcho", "Password", "PasswordEchoOnEdit"):
            if hasattr(QLineEdit, nm):
                setattr(ns, nm, getattr(QLineEdit, nm))
        QLineEdit.EchoMode = ns


_patch_pyside2_enums()


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


# ---------------------------------------------------------------------- #
# emoji 字体回退（修复"中文+emoji 混合时按钮文字被裁切"的问题）
#
# 现象：Max 内嵌 PySide 默认主字体（如 Microsoft YaHei UI）不含 emoji 字形，
# 当 QPushButton 文本同时含 emoji + 中文/英文时，Qt 渲染引擎会触发字体
# fallback：emoji 走 Segoe UI Emoji，中文走主字体。两套字体的 metric
# （字符高度 / 行距）不一致，渲染对齐时整段文字会被压缩，最后 1~2 个字
# 被挤出按钮，导致"🚀  发送" → "发"、"⚙ 设置" → "⚙"。
#
# 解决方案：QFont.setFamilies(['MainFont', 'Segoe UI Emoji', 'Segoe UI Symbol'])
# 让 Qt 在 fallback 时走"声明过的同一 QFont 内的次选 family"，metric 来自
# 主 family 不会被覆盖，emoji 会以主字体的 metric 缩放渲染，整段对齐。
#
# 该函数不在 import 时自动调用；由 startup 在确保 QApplication 实例存在
# 之后显式 install，避免在测试 / 离屏 / 非 Max 场景误触。
# ---------------------------------------------------------------------- #

# Windows / macOS / Linux 各自常见的 emoji 字体候选（按优先级），
# 任意一个被系统识别就生效，不存在的 family 名 Qt 会自动忽略。
_EMOJI_FAMILY_CANDIDATES = (
    'Segoe UI Emoji',     # Windows 10/11 默认 emoji 字体（覆盖最全）
    'Segoe UI Symbol',    # 早期 Windows 兜底
    'Apple Color Emoji',  # macOS
    'Noto Color Emoji',   # Linux 常见
    'EmojiOne Color',     # 部分 Linux 发行版
    'Twemoji Mozilla',    # Firefox 自带
)


def install_emoji_font_fallback():
    """为 ``QApplication`` 默认字体安装 emoji fallback 列表。

    :returns: ``True`` 表示已安装；``False`` 表示无 QApplication / 不支持。

    幂等：重复调用不会叠加多次 fallback。
    """
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False

    base_font = app.font()
    main_family = base_font.family() or 'Microsoft YaHei UI'

    families = [main_family]
    for fam in _EMOJI_FAMILY_CANDIDATES:
        if fam not in families:
            families.append(fam)

    # PySide6 / PySide2 (Qt 5.13+) 都支持 setFamilies；
    # 对极老的 PySide2 fallback 到 CSS-like family 字符串。
    try:
        if hasattr(base_font, 'setFamilies'):
            base_font.setFamilies(families)
        else:  # pragma: no cover - 仅在极老的 Qt5 走这里
            base_font.setFamily(', '.join(families))
        app.setFont(base_font)
        return True
    except Exception:  # pylint: disable=broad-except
        # 字体安装失败不能让 UI 起不来，安静失败
        return False


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
    "install_emoji_font_fallback",
]
