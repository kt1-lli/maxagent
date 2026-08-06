#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全屏截图框选蒙层（多屏 + HiDPI 感知）。

零依赖、跨平台：遍历 ``QGuiApplication.screens()`` 抓齐所有屏幕的
像素合成一张虚拟桌面 pixmap，蒙层覆盖整个虚拟桌面 → 鼠标拖拽框选
→ 返回选区 ``QPixmap``。

调用方式::

    pix = ScreenshotOverlay.capture_interactive()
    if pix is not None and not pix.isNull():
        # 用户完成了框选
        ...
    else:
        # 用户取消了（按 ESC 或右键）
        ...

设计要点：
- 多屏支持：主屏可能不是 (0,0) 起点、副屏可能负坐标，用虚拟桌面
  统一坐标系，鼠标可在任意屏幕上跨屏拖框。
- HiDPI：按每屏 devicePixelRatio 抓像素，合成后设置整张 pixmap 的
  devicePixelRatio，让逻辑坐标（Qt widget 事件坐标）能与像素图正确
  抠图。
- 无边框 + 置顶 + 覆盖全屏；ESC / 右键 / 双击空白 = 取消。
- 不主动抬起任何应用主窗——用户看到什么就截什么，不越权。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Optional

from ..logger import get_logger
from ..qt_compat import QtCore
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets


logger = get_logger(__name__)


def _grab_virtual_desktop():
    """抓取虚拟桌面（含所有屏幕）的 pixmap。

    返回值:
        (pixmap, virtual_origin_qpoint, dpr)
        - pixmap: 合成后的 QPixmap，已设置 devicePixelRatio
        - virtual_origin_qpoint: 虚拟桌面左上角在 Qt 逻辑坐标系中的位置
          （副屏在主屏左边时可能是负坐标）
        - dpr: 采用的 devicePixelRatio（取所有屏幕的最大值以保精度）

    失败返回 (None, None, 1.0)。
    """
    screens = list(QtGui.QGuiApplication.screens() or [])
    if not screens:
        logger.warning('截图失败：QGuiApplication.screens() 返回空')
        return None, None, 1.0

    # 计算虚拟桌面在 Qt 逻辑坐标系中的并集矩形
    virtual_rect = QtCore.QRect()
    for scr in screens:
        try:
            virtual_rect = virtual_rect.united(scr.geometry())
        except Exception:  # pylint: disable=broad-except
            continue
    if virtual_rect.isEmpty():
        logger.warning('截图失败：虚拟桌面 rect 为空')
        return None, None, 1.0

    # 取所有屏幕 devicePixelRatio 的最大值——避免 100% 主屏 + 200% 副屏时
    # 副屏截图被降采样成模糊。低 dpr 的屏在合成时会被自然缩放。
    dpr = 1.0
    for scr in screens:
        try:
            dpr = max(dpr, float(scr.devicePixelRatio()))
        except Exception:  # pylint: disable=broad-except
            continue

    # 目标合成 pixmap：物理像素尺寸 = 逻辑尺寸 × dpr
    px_w = int(round(virtual_rect.width() * dpr))
    px_h = int(round(virtual_rect.height() * dpr))
    if px_w <= 0 or px_h <= 0:
        logger.warning('截图失败：合成像素尺寸非法 %dx%d', px_w, px_h)
        return None, None, 1.0

    canvas = QtGui.QPixmap(px_w, px_h)
    canvas.fill(QtCore.Qt.GlobalColor.black)

    painter = QtGui.QPainter(canvas)
    try:
        for scr in screens:
            try:
                geo = scr.geometry()
                # 抓单屏原生像素图（grabWindow(0) 抓的是该屏 root window）
                shot = scr.grabWindow(0)
                if shot is None or shot.isNull():
                    logger.debug('屏幕 %s 抓屏返回空，跳过', scr.name())
                    continue
                # 该屏在虚拟桌面里的偏移（逻辑坐标）
                offset_x_logic = geo.x() - virtual_rect.x()
                offset_y_logic = geo.y() - virtual_rect.y()
                # 换算到合成 pixmap 的物理像素坐标
                dst_x = int(round(offset_x_logic * dpr))
                dst_y = int(round(offset_y_logic * dpr))
                dst_w = int(round(geo.width() * dpr))
                dst_h = int(round(geo.height() * dpr))
                painter.drawPixmap(
                    QtCore.QRect(dst_x, dst_y, dst_w, dst_h),
                    shot,
                    shot.rect(),
                )
            except Exception:  # pylint: disable=broad-except
                logger.debug(
                    '屏幕 %s 抓屏或合成失败（已忽略）',
                    getattr(scr, 'name', lambda: '?')(),
                    exc_info=True,
                )
                continue
    finally:
        painter.end()

    # 关键：把 dpr 打进 pixmap，Qt 后续按逻辑坐标绘制时会自动缩放
    try:
        canvas.setDevicePixelRatio(dpr)
    except Exception:  # pylint: disable=broad-except
        pass

    logger.debug(
        '虚拟桌面抓屏完成: 逻辑 %dx%d @ (%d,%d), dpr=%.2f, 像素 %dx%d',
        virtual_rect.width(), virtual_rect.height(),
        virtual_rect.x(), virtual_rect.y(),
        dpr, px_w, px_h,
    )
    return canvas, virtual_rect.topLeft(), dpr


class ScreenshotOverlay(QtWidgets.QWidget):
    """全屏截图蒙层窗口（覆盖整个虚拟桌面，支持多屏跨屏框选）。"""

    # 选区最小有效边长（像素），低于此值视为误触不返回结果
    _MIN_EDGE = 4

    def __init__(self, screen_pixmap, virtual_origin, parent=None):
        # type: (QtGui.QPixmap, QtCore.QPoint, QtCore.QObject) -> None
        super(ScreenshotOverlay, self).__init__(parent)
        self._snapshot = screen_pixmap
        # 虚拟桌面左上角在 Qt 全局坐标里的位置——副屏可能负坐标
        self._virtual_origin = QtCore.QPoint(virtual_origin)
        # 用户拖拽的起点和终点（widget 局部坐标，从 0,0 开始）
        self._origin = None  # type: Optional[QtCore.QPoint]
        self._cursor = None  # type: Optional[QtCore.QPoint]
        self._dragging = False
        # 结果：用户松开鼠标后保存的最终选区像素图，None 表示已取消
        self._result_pix = None  # type: Optional[QtGui.QPixmap]

        # 无边框 + 置顶。不用 Tool flag —— 部分窗口管理器下 Tool 窗口
        # 拿不到键盘焦点（ESC 失效）；统一用普通 Window 即可。
        flags = (
            QtCore.Qt.WindowType.Window
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowFlags(flags)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        # 让蒙层覆盖整个虚拟桌面（含多屏，覆盖负坐标区域）。
        # 逻辑坐标：虚拟桌面尺寸 = pixmap 尺寸 / dpr（Qt 已按 dpr 自动换算）
        try:
            dpr = self._snapshot.devicePixelRatio() or 1.0
        except Exception:  # pylint: disable=broad-except
            dpr = 1.0
        logic_w = int(round(self._snapshot.width() / dpr))
        logic_h = int(round(self._snapshot.height() / dpr))
        self.setGeometry(
            self._virtual_origin.x(),
            self._virtual_origin.y(),
            logic_w,
            logic_h,
        )

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def paintEvent(self, event):  # noqa: D401  Qt 重载
        painter = QtGui.QPainter(self)
        try:
            # 1. 底图：虚拟桌面抓屏（Qt 会按 pixmap 的 dpr 自动做缩放绘制）
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
                # 提示文本（画在鼠标所在屏幕中央，不是虚拟桌面中央——
                # 虚拟桌面中央在双屏时可能落在屏幕拼接缝上看不清）
                painter.setPen(QtGui.QColor('#ffffff'))
                tip = '拖动鼠标框选截图区域 · ESC 取消 · 右键取消'
                # 简单起见：还是用 widget 中央（多屏时用户能看到即可）
                painter.drawText(
                    self.rect(),
                    QtCore.Qt.AlignmentFlag.AlignCenter,
                    tip,
                )
        finally:
            painter.end()

    # ------------------------------------------------------------------ #
    # 鼠标事件（widget 局部坐标，虚拟桌面 topLeft 为原点）
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
        # 抠图：widget 逻辑坐标 → pixmap 物理像素坐标
        try:
            dpr = self._snapshot.devicePixelRatio() or 1.0
        except Exception:  # pylint: disable=broad-except
            dpr = 1.0
        px_rect = QtCore.QRect(
            int(round(rect.x() * dpr)),
            int(round(rect.y() * dpr)),
            int(round(rect.width() * dpr)),
            int(round(rect.height() * dpr)),
        )
        # 边界裁剪，防越界
        px_rect = px_rect.intersected(self._snapshot.rect())
        if px_rect.isEmpty():
            self._cancel()
            return
        cropped = self._snapshot.copy(px_rect)
        # 保留 dpr，便于后续显示时按逻辑尺寸展现
        try:
            cropped.setDevicePixelRatio(dpr)
        except Exception:  # pylint: disable=broad-except
            pass
        self._result_pix = cropped
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

        设计原则：
        - 不主动抬起 Max 主窗或其他任何应用——用户想截什么就截什么，
          比如浏览器里的参考图、其他 DCC 软件的贴图预览等。
        - 由调用方（dock_widget._on_snip）负责在抓屏前隐藏 Knot 面板
          自身（避免面板入镜），抓完 show 恢复。
        - 全屏合成 = 遍历所有 QScreen 抓像素后拼接，蒙层覆盖整个虚拟
          桌面，鼠标可在任意屏幕上跨屏拖框。
        """
        # 让上一次的 hide 请求完成 + 让 DWM 合成刷新，再抓屏
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.processEvents()
        except Exception:  # pylint: disable=broad-except
            pass
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

        # ---- 抓全屏（多屏合成） ---------------------------------- #
        snap, virtual_origin, _dpr = _grab_virtual_desktop()
        if snap is None or snap.isNull() or virtual_origin is None:
            logger.warning('截图失败：虚拟桌面抓屏返回空')
            return None

        overlay = cls(snap, virtual_origin, parent=parent)
        loop = QtCore.QEventLoop()

        def _on_close(_event):
            if loop.isRunning():
                loop.quit()

        overlay._on_close_hook = _on_close  # 注入到子类的 hook 槽

        # 用 show()（而不是 showFullScreen()）——多屏下 showFullScreen
        # 只会把 widget 放到主屏，副屏就没蒙层了。setGeometry 已经把
        # 覆盖区域正确设置为整个虚拟桌面，show 即可全域展开。
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()
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
                '截图完成: 选区 %dx%d (dpr=%.2f)',
                result.width(), result.height(),
                float(getattr(result, 'devicePixelRatio', lambda: 1.0)()),
            )
        try:
            overlay.deleteLater()
        except Exception:  # pylint: disable=broad-except
            pass
        return result


__all__ = ['ScreenshotOverlay']
