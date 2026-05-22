#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头像裁剪对话框：上传图片 → 平移 / 缩放 → 输出方形 64×64 PNG。

实现思路（零依赖，纯 Qt 原生）：
- ``QGraphicsView`` 显示原图 ``QGraphicsPixmapItem``
- 中央叠加固定尺寸的方形选区框（视觉提示）+ 半透明遮罩
- 鼠标拖动平移图片（``QGraphicsView.ScrollHandDrag``）
- 滚轮缩放
- 点确定时把选区在图片坐标系下的矩形 ``copy(rect)`` 出来，
  再 ``scaled(64, 64)`` 得到最终头像

UX 要点：
- 选区固定 240×240 视觉框，方便用户对齐
- 图片首次居中显示，自动 fit 到比选区大一点点
- 缩放范围限制 0.1× ~ 5×，避免极端值
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Optional

from ..qt_compat import QtCore
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets


# 视觉选区边长（像素）：用户在这个框内对齐图片
_CROP_FRAME_SIZE = 240
# 视图整体尺寸
_VIEW_SIZE = 360


class AvatarCropDialog(QtWidgets.QDialog):
    """让用户裁剪一张图片得到方形头像。

    使用方式::

        dlg = AvatarCropDialog(image_path, parent=self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            pixmap = dlg.cropped_pixmap()  # QPixmap, 方形
            # 交给 employee.save_avatar_image() 持久化
    """

    def __init__(self, image_path, parent=None):
        # type: (str, Optional[QtWidgets.QWidget]) -> None
        super(AvatarCropDialog, self).__init__(parent)
        self.setWindowTitle('裁剪头像')
        self.setModal(True)
        self.resize(440, 520)

        # 加载原图
        self._original = QtGui.QPixmap(image_path)
        if self._original.isNull():
            QtWidgets.QMessageBox.warning(
                self, '加载失败',
                '无法读取图片：{}'.format(image_path),
            )
            QtCore.QTimer.singleShot(0, self.reject)
            return

        self._build_ui()
        self._fit_initial()

    # ------------------------------------------------------------------ #
    # UI 构建
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        tip = QtWidgets.QLabel(
            '拖动图片对齐方框，滚轮缩放；方框内的内容将作为头像。'
        )
        tip.setStyleSheet('color:#aaa;')
        tip.setWordWrap(True)
        layout.addWidget(tip)

        # 自定义视图：显示原图 + 中心遮罩 + 选区框
        self._view = _CropGraphicsView(self._original, self)
        self._view.setFixedSize(_VIEW_SIZE, _VIEW_SIZE)
        layout.addWidget(self._view, 0, QtCore.Qt.AlignCenter)

        # 缩放滑动条
        zoom_row = QtWidgets.QHBoxLayout()
        zoom_row.addWidget(QtWidgets.QLabel('缩放:'))
        self._zoom_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._zoom_slider.setMinimum(10)
        self._zoom_slider.setMaximum(500)
        self._zoom_slider.setValue(100)
        self._zoom_slider.valueChanged.connect(self._on_zoom_slider)
        zoom_row.addWidget(self._zoom_slider, 1)
        layout.addLayout(zoom_row)

        # 视图缩放变化时反向同步 slider
        self._view.zoomChanged.connect(self._on_view_zoom_changed)

        # 底部按钮
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        self._cancel_btn = QtWidgets.QPushButton('取消')
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)
        self._ok_btn = QtWidgets.QPushButton('确定')
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._ok_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------ #
    # 初始化：让原图居中并 fit 到比选区稍大
    # ------------------------------------------------------------------ #
    def _fit_initial(self):
        if self._original.isNull():
            return
        # 让最短边稍大于裁剪框，确保有余量调整
        w = self._original.width()
        h = self._original.height()
        short = min(w, h)
        if short <= 0:
            return
        # 目标：短边 = _CROP_FRAME_SIZE * 1.05，给点冗余
        scale = (_CROP_FRAME_SIZE * 1.05) / short
        self._view.set_zoom(scale)
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(int(scale * 100))
        self._zoom_slider.blockSignals(False)

    # ------------------------------------------------------------------ #
    # 缩放联动
    # ------------------------------------------------------------------ #
    def _on_zoom_slider(self, val):
        scale = val / 100.0
        self._view.set_zoom(scale)

    def _on_view_zoom_changed(self, scale):
        val = int(round(scale * 100))
        val = max(self._zoom_slider.minimum(),
                  min(self._zoom_slider.maximum(), val))
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(val)
        self._zoom_slider.blockSignals(False)

    # ------------------------------------------------------------------ #
    # 输出
    # ------------------------------------------------------------------ #
    def cropped_pixmap(self):
        # type: () -> QtGui.QPixmap
        """返回裁剪结果（方形 QPixmap，原始分辨率，调用方再缩到 64）。"""
        return self._view.crop_to_pixmap()


class _CropGraphicsView(QtWidgets.QGraphicsView):
    """承载原图 + 选区遮罩 + 鼠标平移 + 滚轮缩放。

    所有坐标计算最终都落到"图片坐标系下的裁剪矩形"，输出时
    用 ``QPixmap.copy(rect)`` 取出方形子图。
    """

    zoomChanged = QtCore.Signal(float)  # 当前缩放（相对原图 1×）

    def __init__(self, pixmap, parent=None):
        super(_CropGraphicsView, self).__init__(parent)
        self._pixmap = pixmap
        self._scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = self._scene.addPixmap(pixmap)
        self._item.setTransformationMode(QtCore.Qt.SmoothTransformation)
        # 让 item 中心初始落到视图中心
        self._scene.setSceneRect(QtCore.QRectF(
            -10000, -10000, 20000, 20000,
        ))
        self._item.setOffset(-pixmap.width() / 2.0, -pixmap.height() / 2.0)
        self.centerOn(0, 0)

        self.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.setBackgroundBrush(QtGui.QBrush(QtGui.QColor('#1e1e1e')))

        # 当前缩放（item 相对原图）
        self._zoom = 1.0

    # ---- 缩放 ---- #
    def set_zoom(self, scale):
        # type: (float) -> None
        scale = max(0.1, min(5.0, float(scale)))
        if abs(scale - self._zoom) < 1e-4:
            return
        self._item.setScale(scale)
        self._zoom = scale
        self.zoomChanged.emit(scale)
        self.viewport().update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y() if hasattr(event, 'angleDelta') \
            else event.delta()
        factor = 1.1 if delta > 0 else 1 / 1.1
        self.set_zoom(self._zoom * factor)
        event.accept()

    # ---- 选区遮罩绘制（直接画在 viewport 上，与场景坐标完全解耦） ---- #
    def paintEvent(self, event):
        """先让基类把场景画完，再在 viewport 上叠加蒙版与选区描边。

        为什么不用 ``drawForeground``：
        - ``drawForeground`` 拿到的 painter 默认处在场景坐标系；
          调用 ``resetTransform`` 仅重置 painter 的变换矩阵，但其逻辑
          原点仍受 viewport 在场景中的可见区影响。当用户拖动/滚动图
          片时，``viewport().rect()`` 看似不变（始终是 0~width），但
          实际绘制位置会随场景视口偏移而漂移——这就是用户截图里看到
          的"绿色描边框跑到右上角"的原因。
        - 改成 ``paintEvent`` + ``QPainter(self.viewport())`` 后，画笔
          直接在 viewport 这块物理 widget 上工作，不沾染任何场景变换，
          蒙版与选区始终钉死在 viewport 几何中心，无论图片如何平移
          缩放都不会偏移。

        填充策略：
        - ``QPainterPath`` + ``Qt.OddEvenFill`` 奇偶规则：外层 viewport
          矩形与内层选区矩形重叠区域（即选区）不被填充，天然形成
          "带洞蒙版"——框外半透明黑、框内透出原图。
        """
        super(_CropGraphicsView, self).paintEvent(event)

        painter = QtGui.QPainter(self.viewport())
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
            vp = self.viewport().rect()
            cx = vp.width() / 2.0
            cy = vp.height() / 2.0
            half = _CROP_FRAME_SIZE / 2.0
            sel = QtCore.QRectF(
                cx - half, cy - half,
                _CROP_FRAME_SIZE, _CROP_FRAME_SIZE,
            )

            # 带洞蒙版：外层 vp 矩形 + 内层选区矩形 → 奇偶填充夹层
            path = QtGui.QPainterPath()
            path.setFillRule(QtCore.Qt.OddEvenFill)
            path.addRect(QtCore.QRectF(vp))
            path.addRect(sel)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(0, 0, 0, 150))
            painter.drawPath(path)

            # 选区描边
            pen = QtGui.QPen(QtGui.QColor('#a8e6a8'))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRect(sel)
        finally:
            painter.end()

    def resizeEvent(self, event):
        """视口尺寸变化时强制重绘，确保选区始终居中。"""
        super(_CropGraphicsView, self).resizeEvent(event)
        self.viewport().update()

    def scrollContentsBy(self, dx, dy):
        """拖动/滚动场景时也要立即重绘 viewport 上的蒙版。

        ``QGraphicsView`` 拖动时只刷新场景层，viewport 上叠加的蒙版
        不会自动跟着 update——必须显式触发，否则用户拖动图片瞬间会
        看到"图片移动 + 蒙版残留"的撕裂感。
        """
        super(_CropGraphicsView, self).scrollContentsBy(dx, dy)
        self.viewport().update()

    # ---- 输出裁剪结果 ---- #
    def crop_to_pixmap(self):
        # type: () -> QtGui.QPixmap
        """计算选区在原图坐标系下的矩形，copy 出方形子图。

        坐标转换链：
        - 视图中心 (cx, cy) → 场景坐标
        - 场景坐标 - item.pos / scale → item 局部坐标
        - item 局部坐标 + offset → 原图像素坐标
        """
        vp = self.viewport().rect()
        cx = vp.width() / 2
        cy = vp.height() / 2
        # 选区 4 个角在视图坐标系
        half = _CROP_FRAME_SIZE / 2
        tl_view = QtCore.QPoint(int(cx - half), int(cy - half))
        br_view = QtCore.QPoint(int(cx + half), int(cy + half))
        # → 场景坐标
        tl_scene = self.mapToScene(tl_view)
        br_scene = self.mapToScene(br_view)
        # → item 局部坐标（item 自己有 scale 和 offset）
        tl_item = self._item.mapFromScene(tl_scene)
        br_item = self._item.mapFromScene(br_scene)
        # 原图像素 = item 局部 + offset(已是负的半宽)
        # mapFromScene 已考虑 setScale，所以这里 tl_item 直接是
        # 相对 item.offset 之前的局部坐标，再加 offset 得像素
        offset = self._item.offset()
        x1 = int(tl_item.x() - offset.x())
        y1 = int(tl_item.y() - offset.y())
        x2 = int(br_item.x() - offset.x())
        y2 = int(br_item.y() - offset.y())
        # 限定到原图范围内
        w = self._pixmap.width()
        h = self._pixmap.height()
        x1 = max(0, min(w, x1))
        y1 = max(0, min(h, y1))
        x2 = max(0, min(w, x2))
        y2 = max(0, min(h, y2))
        rect = QtCore.QRect(
            x1, y1,
            max(1, x2 - x1),
            max(1, y2 - y1),
        )
        # copy 出来；如果选区是矩形（视区比例 ≠ 1），强制取最短边为方形
        side = min(rect.width(), rect.height())
        # 中心化裁剪（避免横竖不一致时偏移）
        cx_px = rect.x() + rect.width() // 2
        cy_px = rect.y() + rect.height() // 2
        sq = QtCore.QRect(
            cx_px - side // 2,
            cy_px - side // 2,
            side,
            side,
        )
        return self._pixmap.copy(sq)
