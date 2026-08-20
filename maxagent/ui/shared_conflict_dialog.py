#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享资源冲突解决对话框。

当检测到本地与共享目录存在同名资产且尚未经过用户确认时，弹出本对话框
让用户选择处理方式。用户的选择会写入 ``SharedConflictResolver`` 并持久化。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import List
from typing import Optional
from typing import Tuple

from ..logger import get_logger
from ..qt_compat import QtCore
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets
from ..shared_resources import CONFLICT_RESOLUTIONS
from ..shared_resources import ConflictResolution
from ..shared_resources import SharedConflictResolver


logger = get_logger(__name__)


# 策略展示文案
_RESOLUTION_LABELS = {
    'use_shared': '使用共享版本',
    'use_local': '使用本地版本',
    'keep_both': '保留两者（共享版本加 shared_ 前缀）',
    'overwrite_local': '用共享版本覆盖本地版本',
}

_RESOLUTION_DETAILS = {
    'use_shared': '共享版本优先，本地版本对 LLM 不可见。',
    'use_local': '完全忽略共享版本，保留本地现有文件。',
    'keep_both': '本地版本保留，共享版本以 shared_<name> 的形式同时出现。',
    'overwrite_local': '把共享版本的内容拷贝到本地目录（共享原始文件仍只读）。',
}


class SharedConflictItem(QtWidgets.QWidget):
    """单个冲突资产的选择控件。"""

    def __init__(self, resolution, parent=None):
        # type: (ConflictResolution, Optional[Any]) -> None
        super(SharedConflictItem, self).__init__(parent)
        self.resolution = resolution
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 8, 8, 8)

        self.setStyleSheet(
            'SharedConflictItem {'
            '  background:#2a2a2a;'
            '  border:1px solid #3a3a3a;'
            '  border-radius:4px;'
            '}'
        )

        title = QtWidgets.QLabel(
            '<b>{}</b>（{}）'.format(
                self.resolution.name,
                self.resolution.asset_type,
            ),
        )
        title.setTextFormat(QtCore.Qt.TextFormat.RichText)
        layout.addWidget(title)

        paths = QtWidgets.QLabel(
            '共享：{}<br>本地：{}'.format(
                self.resolution.shared_path or '—',
                self.resolution.local_path or '—',
            ),
        )
        paths.setTextFormat(QtCore.Qt.TextFormat.RichText)
        paths.setStyleSheet('color:#888; font-size:11px;')
        paths.setWordWrap(True)
        layout.addWidget(paths)

        self.combo = QtWidgets.QComboBox()
        for value in CONFLICT_RESOLUTIONS:
            self.combo.addItem(_RESOLUTION_LABELS[value], value)
        # 默认选中当前记录的策略
        idx = self.combo.findData(self.resolution.resolution)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
        layout.addWidget(self.combo)

        detail = QtWidgets.QLabel(_RESOLUTION_DETAILS[self.resolution.resolution])
        detail.setStyleSheet('color:#aaa; font-size:11px;')
        detail.setWordWrap(True)
        layout.addWidget(detail)

        # 切换策略时同步详情
        def _on_changed(i):
            value = self.combo.itemData(i)
            detail.setText(_RESOLUTION_DETAILS.get(value, ''))

        self.combo.currentIndexChanged.connect(_on_changed)

    def selected_resolution(self):
        # type: () -> str
        return str(self.combo.currentData())


class SharedConflictDialog(QtWidgets.QDialog):
    """批量冲突解决对话框。

    用法：
        conflicts = resolver.list_all()  # 或过滤出未确认的冲突
        dialog = SharedConflictDialog(conflicts, parent=parent)
        if dialog.exec_() == QDialog.Accepted:
            for name, asset_type, resolution in dialog.resolutions():
                resolver.set(name, asset_type, resolution, confirmed=True)
    """

    def __init__(self, conflicts, parent=None):
        # type: (List[ConflictResolution], Optional[Any]) -> None
        super(SharedConflictDialog, self).__init__(parent)
        self._conflicts = list(conflicts)
        self._items = []  # type: List[SharedConflictItem]
        self.setWindowTitle('共享资源冲突处理')
        self.resize(560, 420)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)

        hint = QtWidgets.QLabel(
            '检测到以下本地与共享目录的同名资产。请选择处理方式：'
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        container = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(container)
        vbox.setSpacing(8)
        vbox.setAlignment(QtCore.Qt.AlignTop)
        for rec in self._conflicts:
            item = SharedConflictItem(rec)
            vbox.addWidget(item)
            self._items.append(item)
        vbox.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def resolutions(self):
        # type: () -> List[Tuple[str, str, str]]
        """返回 (name, asset_type, resolution) 列表。"""
        out = []
        for item in self._items:
            out.append((
                item.resolution.name,
                item.resolution.asset_type,
                item.selected_resolution(),
            ))
        return out


def show_shared_conflict_dialog_if_needed(
    conflicts,
    parent=None,
    resolver=None,
):
    # type: (List[ConflictResolution], Optional[Any], Optional[SharedConflictResolver]) -> int
    """若存在未确认冲突则弹出对话框并持久化结果。

    :param conflicts: 需要让用户决策的冲突记录列表
    :param parent: 父窗口
    :param resolver: 用于保存结果的解析器，默认新建
    :returns: QDialog.Accepted / QDialog.Rejected / -1（无冲突）
    """
    if not conflicts:
        return -1
    if resolver is None:
        resolver = SharedConflictResolver()
    dialog = SharedConflictDialog(conflicts, parent=parent)
    result = dialog.exec_()
    if result == QtWidgets.QDialog.Accepted:
        for name, asset_type, resolution in dialog.resolutions():
            existing = resolver.get(name, asset_type)
            resolver.set(
                name, asset_type, resolution,
                shared_path=existing.shared_path if existing else '',
                local_path=existing.local_path if existing else '',
                confirmed=True,
            )
            logger.info(
                '冲突已解决: %s/%s -> %s', asset_type, name, resolution,
            )
    return result
