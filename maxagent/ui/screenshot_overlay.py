#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全屏截图框选蒙层。

零依赖、跨平台：基于 Qt 自带的 ``QGuiApplication.primaryScreen()``
抓主屏 → 全屏 ``QWidget`` 蒙层 → 鼠标拖拽框选 → 返回选区 ``QPixmap``。

调用方式::

    pix = ScreenshotOverlay.capture_interactive()
    if pix is not None and not pix.isNull():
        # 用户完成了框选
        ...
    else:
        # 用户取消了（按 ESC 或右键）
        ...

设计要点：
- 全屏蒙层 ``WindowFlag = FramelessWindowHint | WindowStaysOnTopHint``
- 选区外半透明黑色蒙版，选区内透出原图
- ESC / 右键 / 双击空白 = 取消
- 单击拖拽确定矩形，松开鼠标完成截图
- 不依赖系统截图工具，保证 Linux/Windows/macOS 一致
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Optional

from ..qt_compat import QtCore
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets


class ScreenshotOverlay(QtWidgets.QWidget):
    """全屏截图蒙层窗口。"""

    # 选区最小有效边长（像素），低于此值视为误触不返回结果
    _MIN_EDGE = 4

    def __init__(self, screen_pixmap, parent=None):
        # type: (QtGui.QPixmap, QtCore.QObject) -> None
        super(ScreenshotOverlay, self).__init__(parent)
        self._snapshot = screen_pixmap
        # 用户拖拽的起点和终点（屏幕坐标，与 self 几何一致）
        self._origin = None  # type: Optional[QtCore.QPoint]
        self._cursor = None  # type: Optional[QtCore.QPoint]
        self._dragging = False
        # 结果：用户松开鼠标后保存的最终选区像素图，None 表示已取消
        self._result_pix = None  # type: Optional[QtGui.QPixmap]

        # 无边框 + 置顶 + 不在任务栏显示
        flags = (
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setWindowFlags(flags)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        # 让蒙层覆盖整个主屏
        self.setGeometry(self._snapshot.rect())

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def paintEvent(self, event):  # noqa: D401  Qt 重载
        painter = QtGui.QPainter(self)
        try:
            # 1. 底图：原始截图
            painter.drawPixmap(self.rect(), self._snapshot)
            # 2. 半透明黑色蒙版
            mask = QtGui.QColor(0, 0, 0, 140)
            painter.fillRect(self.rect(), mask)
            # 3. 当前选区透出原图
            if self._origin and self._cursor:
                rect = QtCore.QRect(self._origin, self._cursor).normalized()
                if not rect.isEmpty():
                    painter.drawPixmap(rect, self._snapshot, rect)
                    # 选区边框
                    pen = QtGui.QPen(QtGui.QColor('#22cc88'))
                    pen.setWidth(2)
                    painter.setPen(pen)
                    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                    painter.drawRect(rect.adjusted(0, 0, -1, -1))
                    # 选区尺寸提示
                    info = '{}×{}'.format(rect.width(), rect.height())
                    painter.setPen(QtGui.QColor('#ffffff'))
                    text_pos = rect.bottomRight() + QtCore.QPoint(-60, 18)
                    painter.drawText(text_pos, info)
            else:
                # 提示文本
                painter.setPen(QtGui.QColor('#ffffff'))
                tip = '拖动鼠标框选截图区域 · ESC 取消 · 右键取消'
                rect = self.rect()
                painter.drawText(
                    rect, QtCore.Qt.AlignmentFlag.AlignCenter, tip,
                )
        finally:
            painter.end()

    # ------------------------------------------------------------------ #
    # 鼠标事件
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event):  # noqa: D401
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self._cancel()
            return
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._origin = event.pos()
            self._cursor = event.pos()
            self._dragging = True
            self.update()

    def mouseMoveEvent(self, event):  # noqa: D401
        if self._dragging:
            self._cursor = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):  # noqa: D401
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        self._dragging = False
        if self._origin is None or self._cursor is None:
            self._cancel()
            return
        rect = QtCore.QRect(self._origin, self._cursor).normalized()
        # 边长过小视为误触
        if (rect.width() < self._MIN_EDGE
                or rect.height() < self._MIN_EDGE):
            self._cancel()
            return
        # 抠出原图选区
        self._result_pix = self._snapshot.copy(rect)
        self.close()

    def keyPressEvent(self, event):  # noqa: D401
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self._cancel()
            return
        super(ScreenshotOverlay, self).keyPressEvent(event)

    def _cancel(self):
        self._result_pix = None
        self.close()

    def get_result(self):
        # type: () -> Optional[QtGui.QPixmap]
        return self._result_pix

    # ------------------------------------------------------------------ #
    # 工厂入口
    # ------------------------------------------------------------------ #
    @classmethod
    def capture_interactive(cls, parent=None):
        # type: (Optional[QtWidgets.QWidget]) -> Optional[QtGui.QPixmap]
        """阻塞式弹起截图蒙层，返回选区 QPixmap 或 None（取消）。

        在主线程调用，事件循环嵌套（exec_）等待用户操作。
        """
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return None
        # grabWindow(0) = 抓整个屏幕（root window）
        snap = screen.grabWindow(0)
        if snap is None or snap.isNull():
            return None

        overlay = cls(snap, parent=parent)
        # 进入嵌套事件循环阻塞，直到 close()
        loop = QtCore.QEventLoop()
        # close 后窗口被销毁前先取结果，再退出循环
        result_holder = {'pix': None}

        def _on_destroyed():
            result_holder['pix'] = overlay.get_result()
            loop.quit()

        overlay.destroyed.connect(_on_destroyed)
        overlay.showFullScreen()
        overlay.raise_()
        overlay.activateWindow()
        loop.exec_() if hasattr(loop, 'exec_') else loop.exec()
        return result_holder['pix']


__all__ = ['ScreenshotOverlay']
