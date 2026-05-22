#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输入区图片附件相关 UI 组件。

包含：
- ``AttachmentStrip``：输入框上方的"待发送图片"预览条，
  缩略图 + 尺寸提示 + 删除按钮，支持横向滚动；
- ``ImageViewerDialog``：双击缩略图弹出的大图查看器（带"另存为"）；
- ``pixmap_to_attachment``：把 QPixmap 编码为 PNG 字节并落盘，
  返回 ``Attachment`` 实例的辅助函数。

设计要点：
- 缩略图统一 80×80，等比缩放保持图片宽高比；
- 预览条不限张数，超过容器宽度自动出横向滚动条；
- 删除单张时通过信号告知 dock_widget 同步状态；
- 全部组件零依赖，不引入 PIL，QPixmap.save 即可输出 PNG。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import List
from typing import Optional

from ..attachments import Attachment
from ..attachments import save_image_bytes
from ..qt_compat import QtCore
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets


def pixmap_to_attachment(pixmap, name=''):
    # type: (QtGui.QPixmap, str) -> Optional[Attachment]
    """把 QPixmap 编码为 PNG 字节并落盘到 attachments 目录。

    返回 ``Attachment`` 实例。失败返回 None。
    """
    if pixmap is None or pixmap.isNull():
        return None
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    ok = pixmap.save(buf, 'PNG')
    if not ok:
        return None
    raw = bytes(buf.data())
    return save_image_bytes(raw, mime='image/png', name=name)


class _Thumbnail(QtWidgets.QFrame):
    """单张缩略图组件：图 + 删除小按钮 + 尺寸提示。"""

    SIZE = 80
    # 信号：用户请求删除自己（参数=自身在父容器中的 attachment 引用）
    deleted = QtCore.Signal(object)
    # 信号：用户双击预览大图
    preview_requested = QtCore.Signal(object)

    def __init__(self, attachment, parent=None):
        # type: (Attachment, Optional[QtWidgets.QWidget]) -> None
        super(_Thumbnail, self).__init__(parent)
        self._att = attachment
        self.setFixedSize(self.SIZE + 8, self.SIZE + 16)
        self.setStyleSheet(
            'QFrame { background:#222; border:1px solid #444;'
            ' border-radius:4px; }'
        )
        # 缩略图：从磁盘加载，失败则灰色占位
        self._pix = QtGui.QPixmap(attachment.path)
        if self._pix.isNull():
            self._pix = QtGui.QPixmap(self.SIZE, self.SIZE)
            self._pix.fill(QtGui.QColor('#444'))

        # 删除按钮（右上角悬浮）
        self._del_btn = QtWidgets.QPushButton('×', self)
        self._del_btn.setFixedSize(16, 16)
        self._del_btn.setStyleSheet(
            'QPushButton { background:#c33; color:#fff; border:none;'
            ' border-radius:8px; font-weight:bold; }'
            'QPushButton:hover { background:#e44; }'
        )
        self._del_btn.move(self.SIZE - 8, 0)
        self._del_btn.clicked.connect(self._on_delete)
        self._del_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._del_btn.raise_()

        self.setToolTip('双击查看大图 · × 删除')

    def paintEvent(self, event):  # noqa: D401
        super(_Thumbnail, self).paintEvent(event)
        painter = QtGui.QPainter(self)
        try:
            scaled = self._pix.scaled(
                self.SIZE, self.SIZE,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            x = 4 + (self.SIZE - scaled.width()) // 2
            y = 2 + (self.SIZE - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            # 底部尺寸文本
            painter.setPen(QtGui.QColor('#aaa'))
            font = painter.font()
            font.setPointSize(7)
            painter.setFont(font)
            kb = max(1, int(self._att.size / 1024))
            tip = '{}KB'.format(kb)
            painter.drawText(
                4, self.SIZE + 14, tip,
            )
        finally:
            painter.end()

    def mouseDoubleClickEvent(self, event):  # noqa: D401
        self.preview_requested.emit(self._att)
        super(_Thumbnail, self).mouseDoubleClickEvent(event)

    def _on_delete(self):
        self.deleted.emit(self._att)


class AttachmentStrip(QtWidgets.QScrollArea):
    """输入区上方的图片预览条。

    自动按需展开/收起：内部有 attachments 时显示，否则隐藏。
    """

    # 信号：列表内容变更（增删）
    changed = QtCore.Signal()

    HEIGHT = 110

    def __init__(self, parent=None):
        super(AttachmentStrip, self).__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded,
        )
        self.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setStyleSheet(
            'QScrollArea { background:#1a1a1a; border:none; }'
        )

        container = QtWidgets.QWidget()
        container.setStyleSheet('background:#1a1a1a;')
        self._row = QtWidgets.QHBoxLayout(container)
        self._row.setContentsMargins(6, 6, 6, 6)
        self._row.setSpacing(6)
        self._row.addStretch(1)
        self.setWidget(container)

        self._items = []  # type: List[Attachment]
        self.hide()

    def attachments(self):
        # type: () -> List[Attachment]
        return list(self._items)

    def add(self, attachment):
        # type: (Attachment) -> None
        if attachment is None:
            return
        self._items.append(attachment)
        thumb = _Thumbnail(attachment, parent=self.widget())
        thumb.deleted.connect(self._on_thumb_delete)
        thumb.preview_requested.connect(self._on_preview)
        # 插到 stretch 之前
        self._row.insertWidget(self._row.count() - 1, thumb)
        self.show()
        self.changed.emit()

    def clear(self):
        for i in reversed(range(self._row.count() - 1)):
            item = self._row.takeAt(i)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._items = []
        self.hide()
        self.changed.emit()

    def _on_thumb_delete(self, attachment):
        # 找到对应的 thumbnail widget 并移除
        for i in range(self._row.count() - 1):
            item = self._row.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, _Thumbnail) and w._att is attachment:  # noqa: SLF001
                self._row.takeAt(i)
                w.setParent(None)
                w.deleteLater()
                break
        self._items = [a for a in self._items if a is not attachment]
        if not self._items:
            self.hide()
        self.changed.emit()

    def _on_preview(self, attachment):
        ImageViewerDialog.show_for(attachment, parent=self)


class ImageViewerDialog(QtWidgets.QDialog):
    """大图查看器：等比缩放到当前对话框，留两侧"另存为/关闭"。"""

    def __init__(self, attachment, parent=None):
        # type: (Attachment, Optional[QtWidgets.QWidget]) -> None
        super(ImageViewerDialog, self).__init__(parent)
        self._att = attachment
        self.setWindowTitle('查看图片 · {}'.format(attachment.name or ''))
        self.resize(720, 540)

        layout = QtWidgets.QVBoxLayout(self)

        self._label = QtWidgets.QLabel()
        self._label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet('background:#000;')
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(self._label)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, 1)

        # 底部按钮
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        save_btn = QtWidgets.QPushButton('另存为...')
        save_btn.clicked.connect(self._on_save_as)
        btn_row.addWidget(save_btn)
        close_btn = QtWidgets.QPushButton('关闭')
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._pix = QtGui.QPixmap(attachment.path)
        self._refresh_label()

    def _refresh_label(self):
        if self._pix.isNull():
            self._label.setText('图片读取失败')
            return
        # 自适应当前 label 大小
        target_w = max(200, self._label.width() or 700)
        target_h = max(200, self._label.height() or 500)
        scaled = self._pix.scaled(
            target_w, target_h,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)

    def resizeEvent(self, event):  # noqa: D401
        super(ImageViewerDialog, self).resizeEvent(event)
        self._refresh_label()

    def _on_save_as(self):
        ext = '.png'
        if 'jpeg' in self._att.mime or 'jpg' in self._att.mime:
            ext = '.jpg'
        fname = (self._att.name or 'image') + ext if not (
            self._att.name or '').lower().endswith(ext) else (
            self._att.name or 'image')
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, '另存为', fname,
            'Image (*.png *.jpg *.jpeg *.gif *.webp)',
        )
        if not path:
            return
        try:
            with open(self._att.path, 'rb') as src:
                data = src.read()
            with open(path, 'wb') as dst:
                dst.write(data)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(
                self, '保存失败', '写入文件失败: {}'.format(exc),
            )

    @classmethod
    def show_for(cls, attachment, parent=None):
        dlg = cls(attachment, parent=parent)
        dlg.exec_() if hasattr(dlg, 'exec_') else dlg.exec()


__all__ = [
    'AttachmentStrip',
    'ImageViewerDialog',
    'pixmap_to_attachment',
]
