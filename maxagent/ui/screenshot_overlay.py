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

from ..logger import get_logger
from ..qt_compat import QtCore
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets


logger = get_logger(__name__)


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

        # 无边框 + 置顶。注意：不用 Tool flag —— 部分窗口管理器下 Tool
        # 窗口拿不到键盘焦点（ESC 失效）；统一用普通 Window 即可。
        flags = (
            QtCore.Qt.WindowType.Window
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowFlags(flags)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        # 让蒙层覆盖整个虚拟桌面（含多屏），避免在多屏环境下漏角
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

    def closeEvent(self, event):  # noqa: D401
        # capture_interactive 注入的 hook：用于退出嵌套事件循环
        hook = getattr(self, '_on_close_hook', None)
        if callable(hook):
            try:
                hook(event)
            except Exception:  # pylint: disable=broad-except
                pass
        super(ScreenshotOverlay, self).closeEvent(event)

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

        抓屏前会强制把 3ds Max 主窗口和调用方父窗口抬到 Z 序最前，
        避免出现"截图抓到 Max 后面的其他软件"的问题（用户曾反馈：
        点截图后蒙层里显示的是其他软件而不是 Max 界面，根因是
        Max 主窗被最小化 / 被其他窗口遮挡 / 不是当前 Z 序最前时，
        grabWindow(0) 会抓到桌面区域实际暴露出的下层窗口内容）。
        """
        # ---- 抓屏前的窗口置顶（关键修复） ------------------------- #
        # 1. 优先把 Max 主窗口抬到最前——Max 内运行时这一步能把
        #    可能被最小化或被其他窗口盖住的主窗口恢复并置顶。
        max_win = None
        try:
            from ..qt_compat import get_max_main_window
            max_win = get_max_main_window()
        except Exception:  # pylint: disable=broad-except
            max_win = None
        if max_win is not None:
            try:
                # 如果被最小化了，先恢复；已正常显示的不受影响。
                if max_win.isMinimized():
                    max_win.showNormal()
                max_win.raise_()
                max_win.activateWindow()
            except Exception:  # pylint: disable=broad-except
                logger.debug('抬起 Max 主窗口失败（已忽略）', exc_info=True)
        # 2. 兜底：把调用方 parent 也抬一次（比如 Max 之外测试时）
        if parent is not None:
            try:
                parent.raise_()
                parent.activateWindow()
            except Exception:  # pylint: disable=broad-except
                pass
        # 3. 让窗口管理器完成 raise + 重绘，再抓屏。processEvents
        #    冲刷 Qt 事件队列；100ms 是经验值，覆盖 Windows DWM 合成延迟。
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.processEvents()
        except Exception:  # pylint: disable=broad-except
            pass
        # 阻塞式短暂等待，让 OS 完成窗口切换动画（Aero/DWM 合成）。
        # QThread.msleep 是跨平台且不阻塞信号的等待。
        try:
            QtCore.QThread.msleep(120)
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.processEvents()
        except Exception:  # pylint: disable=broad-except
            pass

        # ---- 正式抓屏 --------------------------------------------- #
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            logger.warning('截图失败：找不到主屏幕（primaryScreen=None）')
            return None
        # grabWindow(0) = 抓整个屏幕（root window）
        snap = screen.grabWindow(0)
        if snap is None or snap.isNull():
            logger.warning('截图失败：grabWindow 返回空 pixmap')
            return None
        logger.debug(
            '截图蒙层启动: snap=%dx%d',
            snap.width(), snap.height(),
        )

        overlay = cls(snap, parent=parent)
        # 不依赖 destroyed 信号——destroyed 触发时 C++ 对象已析构，
        # 取结果会 RuntimeError。改为在 closeEvent 里主动 quit 嵌套 loop。
        loop = QtCore.QEventLoop()

        def _on_close(_event):
            if loop.isRunning():
                loop.quit()

        overlay._on_close_hook = _on_close  # 注入到子类的 hook 槽

        overlay.showFullScreen()
        overlay.raise_()
        overlay.activateWindow()
        # 抢一次键盘焦点，确保 ESC 取消生效
        try:
            overlay.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        except Exception:  # pylint: disable=broad-except
            pass
        if hasattr(loop, 'exec_'):
            loop.exec_()
        else:
            loop.exec()
        result = overlay.get_result()
        if result is None or result.isNull():
            logger.debug('截图取消或选区为空')
        else:
            logger.info(
                '截图完成: 选区 %dx%d',
                result.width(), result.height(),
            )
        # 显式销毁，释放主屏快照
        try:
            overlay.deleteLater()
        except Exception:  # pylint: disable=broad-except
            pass
        return result


__all__ = ['ScreenshotOverlay']
