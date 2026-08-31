#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置面板的"助手形象"Tab。

让用户为 MaxAgent 这个岗位选择"上任的员工"——名字 + 头像。
本 Tab 完全是 UI 层皮肤，LLM 行为与身份铁律不受影响。

布局::

    ┌─ 👤 助手形象 ────────────────────────────┐
    │                                           │
    │  📋 岗位说明                              │
    │  ──────────────                           │
    │  这位助手担任 MaxAgent 岗位...            │
    │                                           │
    │  名字: [助手        ]                     │
    │                                           │
    │  头像: ● Emoji   [🤖]  快选: [🐱][🦊]...│
    │       ○ 图片                              │
    │       ┌──────┐                            │
    │       │ 64x64│  [选择图片] [清除]         │
    │       └──────┘                            │
    │                                           │
    │  💬 实时预览                              │
    │  ──────────────                           │
    │  ┌─────────────────────┐                  │
    │  │ 🤖 助手             │                  │
    │  │ 你好，我可以...      │                  │
    │  └─────────────────────┘                  │
    │                                           │
    │  [恢复默认]                       [保存]  │
    └───────────────────────────────────────────┘
"""

from __future__ import absolute_import
from __future__ import print_function

import os
from typing import Any
from typing import Optional

from ..logger import get_logger
from ..qt_compat import QtCore
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets
from .emoji_compat import apply_font_fallback as _apply_font_fallback
from .emoji_compat import btn_label as _btn_label
from .emoji_compat import ee as _ee
from ..dcc.runtime import current_dcc as _current_dcc
from .employee import AVATAR_DISPLAY_SIZE
from .employee import DEFAULT_EMOJI
from .employee import DEFAULT_NAME
from .employee import Employee
from .employee import SUGGESTED_EMOJIS
from .employee import get_avatar_image_full_path
from .employee import remove_avatar_image
from .employee import save_avatar_image


def _current_dcc_name():
    """返回当前 DCC 的显示名（Maya 或 3ds Max）。"""
    try:
        dcc = _current_dcc()
        if dcc == 'maya':
            return 'Maya'
        if dcc == '3dsmax':
            return '3ds Max'
        return dcc
    except Exception:  # pylint: disable=broad-except
        return '3ds Max'


logger = get_logger(__name__)


class EmployeeTab(QtWidgets.QWidget):
    """"助手形象"Tab 的根 Widget。

    :param config_manager: ConfigManager 实例，用于读取/保存员工档案
    """

    def __init__(self, config_manager, parent=None):
        # type: (Any, Optional[QtWidgets.QWidget]) -> None
        super(EmployeeTab, self).__init__(parent)
        self._config = config_manager
        # 当前编辑中的员工档案副本（保存前不写回 ConfigManager）
        self._draft = Employee.from_config(config_manager)
        # 临时上传后未保存的图片绝对路径（保存时才落到正式位置）
        self._pending_image_path = ''
        # 图片是否被标记为"清除"（保存时删除磁盘上的 avatar.png）
        self._pending_image_remove = False

        self._build_ui()
        self._load_draft_into_ui()
        # PySide2 + Win 上 emoji + 中文混排需要字体回退
        _apply_font_fallback(self, recursive=True)

    # ------------------------------------------------------------------ #
    # UI 构建
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setSpacing(12)

        # ---------- 标题 + 岗位说明 ---------- #
        title = QtWidgets.QLabel(_ee('👤') + '  助手形象')
        title.setStyleSheet('font-size:16px; font-weight:bold;')
        outer.addWidget(title)

        dcc_name = _current_dcc_name()
        dcc_display = _current_dcc_name()
        desc = QtWidgets.QLabel(
            '这位助手担任 <b>MaxAgent</b> 岗位，职责是协助你操作 {dcc_name}。'
            '<br>你可以为 ta 起一个名字、配一张头像 —— '
            '岗位职责不变，只是换个对外形象。'.format(dcc_name=dcc_display)
        )
        desc.setWordWrap(True)
        desc.setStyleSheet('color:#aaa;')
        desc.setTextFormat(QtCore.Qt.RichText)
        outer.addWidget(desc)

        # ---------- 名字 ---------- #
        name_row = QtWidgets.QHBoxLayout()
        name_row.addWidget(QtWidgets.QLabel('名字:'))
        self._name_edit = QtWidgets.QLineEdit()
        self._name_edit.setMaxLength(20)
        self._name_edit.setPlaceholderText('助手 / 小猫 / 张三...')
        self._name_edit.textChanged.connect(self._on_name_changed)
        name_row.addWidget(self._name_edit, 1)
        outer.addLayout(name_row)

        # ---------- 头像类型选择 ---------- #
        kind_box = QtWidgets.QGroupBox('头像')
        kind_layout = QtWidgets.QVBoxLayout(kind_box)

        # Emoji 行
        emoji_row = QtWidgets.QHBoxLayout()
        self._kind_emoji_radio = QtWidgets.QRadioButton('Emoji')
        self._kind_emoji_radio.toggled.connect(self._on_kind_changed)
        emoji_row.addWidget(self._kind_emoji_radio)
        self._emoji_edit = QtWidgets.QLineEdit()
        self._emoji_edit.setMaxLength(4)
        self._emoji_edit.setFixedWidth(60)
        self._emoji_edit.textChanged.connect(self._on_emoji_changed)
        emoji_row.addWidget(self._emoji_edit)

        # 快选 emoji 按钮组
        emoji_row.addWidget(QtWidgets.QLabel('快选:'))
        for ch in SUGGESTED_EMOJIS:
            btn = QtWidgets.QPushButton(_ee(ch))
            btn.setFixedSize(28, 28)
            btn.setToolTip(ch)
            btn.clicked.connect(
                lambda _checked=False, c=ch: self._pick_suggested_emoji(c),
            )
            emoji_row.addWidget(btn)
        emoji_row.addStretch(1)
        kind_layout.addLayout(emoji_row)

        # 图片行
        img_row = QtWidgets.QHBoxLayout()
        self._kind_image_radio = QtWidgets.QRadioButton('图片')
        self._kind_image_radio.toggled.connect(self._on_kind_changed)
        img_row.addWidget(self._kind_image_radio)

        self._image_preview = QtWidgets.QLabel()
        self._image_preview.setFixedSize(64, 64)
        self._image_preview.setStyleSheet(
            'border:1px solid #555; background:#2a2a2a;'
        )
        self._image_preview.setAlignment(QtCore.Qt.AlignCenter)
        self._image_preview.setText('（无）')
        img_row.addWidget(self._image_preview)

        self._upload_btn = QtWidgets.QPushButton(
            _btn_label('📷', '选择图片'),
        )
        self._upload_btn.setToolTip(
            '从本地选一张图片作为头像，会弹出裁剪对话框让你框出方形区域',
        )
        self._upload_btn.clicked.connect(self._on_pick_image)
        img_row.addWidget(self._upload_btn)

        self._clear_img_btn = QtWidgets.QPushButton(
            _btn_label('🗑', '清除'),
        )
        self._clear_img_btn.setToolTip(
            '清除当前已上传的头像图片，回到 emoji 头像',
        )
        self._clear_img_btn.clicked.connect(self._on_clear_image)
        img_row.addWidget(self._clear_img_btn)

        img_row.addStretch(1)
        kind_layout.addLayout(img_row)

        outer.addWidget(kind_box)

        # ---------- 实时预览 ---------- #
        preview_label = QtWidgets.QLabel(_ee('💬') + '  实时预览')
        preview_label.setStyleSheet('color:#888; margin-top:4px;')
        outer.addWidget(preview_label)

        self._preview_box = QtWidgets.QFrame()
        self._preview_box.setStyleSheet(
            'QFrame { background:#2d3d2d; border-radius:4px; padding:8px; }'
        )
        preview_layout = QtWidgets.QVBoxLayout(self._preview_box)
        preview_layout.setContentsMargins(10, 6, 10, 8)
        preview_layout.setSpacing(2)
        self._preview_head = QtWidgets.QLabel()
        # 显式 RichText，避免 PySide6 把含 ``<img>`` 的 HTML 误判成纯文本
        self._preview_head.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._preview_head.setStyleSheet('background:transparent;')
        preview_layout.addWidget(self._preview_head)
        preview_body = QtWidgets.QLabel(
            '你好，我可以帮你操作 {dcc_name} 场景。'.format(
                dcc_name=_current_dcc_name(),
            )
        )
        preview_body.setStyleSheet('color:#d4ead4; background:transparent;')
        preview_layout.addWidget(preview_body)
        outer.addWidget(self._preview_box)

        outer.addStretch(1)

        # ---------- 底部按钮 ---------- #
        btn_row = QtWidgets.QHBoxLayout()
        self._reset_btn = QtWidgets.QPushButton('恢复默认')
        self._reset_btn.setToolTip(
            '名字回到 "{}"，头像回到 {}'.format(DEFAULT_NAME, DEFAULT_EMOJI),
        )
        self._reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(self._reset_btn)
        btn_row.addStretch(1)
        self._save_btn = QtWidgets.QPushButton('保存')
        self._save_btn.setMinimumWidth(96)
        self._save_btn.setMinimumHeight(30)
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)
        outer.addLayout(btn_row)

    # ------------------------------------------------------------------ #
    # 状态加载与同步
    # ------------------------------------------------------------------ #
    def _load_draft_into_ui(self):
        """把 ``self._draft`` 同步到 UI 控件，避免循环触发信号。"""
        self._name_edit.blockSignals(True)
        self._emoji_edit.blockSignals(True)
        self._kind_emoji_radio.blockSignals(True)
        self._kind_image_radio.blockSignals(True)

        self._name_edit.setText(self._draft.name)
        self._emoji_edit.setText(self._draft.avatar_emoji)
        if self._draft.avatar_kind == 'image':
            self._kind_image_radio.setChecked(True)
        else:
            self._kind_emoji_radio.setChecked(True)

        self._name_edit.blockSignals(False)
        self._emoji_edit.blockSignals(False)
        self._kind_emoji_radio.blockSignals(False)
        self._kind_image_radio.blockSignals(False)

        self._refresh_image_preview()
        self._refresh_preview()

    def _refresh_image_preview(self):
        """根据 draft / pending 状态更新左下角的小图预览。"""
        # 优先级：pending 上传 > pending 清除 > 已存在的 avatar.png
        path = ''
        if self._pending_image_path:
            path = self._pending_image_path
        elif not self._pending_image_remove:
            full = get_avatar_image_full_path()
            if os.path.exists(full):
                path = full
        if path:
            pix = QtGui.QPixmap(path)
            if not pix.isNull():
                self._image_preview.setPixmap(
                    pix.scaled(
                        64, 64,
                        QtCore.Qt.IgnoreAspectRatio,
                        QtCore.Qt.SmoothTransformation,
                    ),
                )
                return
        self._image_preview.clear()
        self._image_preview.setText('（无）')

    def _refresh_preview(self):
        """刷新对话气泡的实时预览。"""
        emp = self._build_preview_employee()
        self._preview_head.setText(emp.display_html())

    def _build_preview_employee(self):
        # type: () -> Employee
        """构造仅供预览使用的 Employee；image 模式下用 pending 路径。"""
        emp = Employee(
            name=self._draft.name,
            avatar_kind=self._draft.avatar_kind,
            avatar_emoji=self._draft.avatar_emoji,
            avatar_image=self._draft.avatar_image,
        )
        # 预览 image 时如果有 pending 上传，临时把图片显示出来
        if (
            emp.avatar_kind == 'image'
            and self._pending_image_path
            and os.path.exists(self._pending_image_path)
        ):
            # 直接 inline 一段 HTML，绕过 Employee.display_html 内部
            # 必须文件存在于 config_dir 的限制
            url = 'file:///' + self._pending_image_path.replace('\\', '/')
            html = (
                '<img src="{url}" width="{w}" height="{w}" '
                'style="vertical-align:middle;"> '
                '<span style="color:#a8e6a8;font-size:9pt;">{name}</span>'
            ).format(
                url=url,
                w=AVATAR_DISPLAY_SIZE,
                name=_safe_html(emp.name),
            )

            # 用一个 lambda 类型的对象返回 html——简单起见直接构造一个
            # 行为一致的 fake Employee：覆盖 display_html
            class _PreviewEmp(Employee):
                def display_html(_self, *_a, **_kw):  # noqa: D401, N805
                    return html
            return _PreviewEmp(
                name=emp.name,
                avatar_kind=emp.avatar_kind,
                avatar_emoji=emp.avatar_emoji,
                avatar_image=emp.avatar_image,
            )
        return emp

    # ------------------------------------------------------------------ #
    # 信号处理
    # ------------------------------------------------------------------ #
    def _on_name_changed(self, text):
        self._draft.name = text.strip() or DEFAULT_NAME
        self._refresh_preview()

    def _on_emoji_changed(self, text):
        ch = (text or '').strip()
        self._draft.avatar_emoji = ch or DEFAULT_EMOJI
        self._refresh_preview()

    def _on_kind_changed(self, _checked):
        if self._kind_image_radio.isChecked():
            self._draft.avatar_kind = 'image'
        else:
            self._draft.avatar_kind = 'emoji'
        self._refresh_preview()

    def _pick_suggested_emoji(self, ch):
        self._emoji_edit.setText(ch)
        self._kind_emoji_radio.setChecked(True)

    def _on_pick_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, '选择头像图片',
            os.path.expanduser('~'),
            '图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*.*)',
        )
        if not path:
            logger.debug('选择头像图片：用户取消文件对话框')
            return
        logger.info('选择头像图片：path=%s', path)
        # 弹裁剪对话框
        from .avatar_crop_dialog import AvatarCropDialog
        dlg = AvatarCropDialog(path, parent=self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            logger.debug('裁剪对话框：用户取消')
            return
        cropped = dlg.cropped_pixmap()
        if cropped is None or cropped.isNull():
            logger.warning('裁剪失败：cropped pixmap is null, src=%s', path)
            QtWidgets.QMessageBox.warning(
                self, '裁剪失败',
                '未能从图片中提取头像。',
            )
            return
        # 暂存到临时文件，等用户点保存才落正式位置
        tmp_path = os.path.join(
            QtCore.QDir.tempPath(),
            'maxagent_avatar_pending.png',
        )
        from .employee import AVATAR_STORE_SIZE
        scaled = cropped.scaled(
            AVATAR_STORE_SIZE, AVATAR_STORE_SIZE,
            QtCore.Qt.IgnoreAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        if not scaled.save(tmp_path, 'PNG'):
            logger.error('裁剪后保存临时文件失败：%s', tmp_path)
            QtWidgets.QMessageBox.warning(
                self, '保存失败',
                '无法写入临时文件：{}'.format(tmp_path),
            )
            return
        self._pending_image_path = tmp_path
        self._pending_image_remove = False
        self._kind_image_radio.setChecked(True)
        self._refresh_image_preview()
        self._refresh_preview()
        logger.info(
            '头像图片暂存完成：tmp=%s scaled=%dx%d',
            tmp_path, AVATAR_STORE_SIZE, AVATAR_STORE_SIZE,
        )

    def _on_clear_image(self):
        self._pending_image_path = ''
        self._pending_image_remove = True
        # 清除后切回 emoji 模式（视觉上头像消失也合理）
        self._kind_emoji_radio.setChecked(True)
        self._refresh_image_preview()
        self._refresh_preview()
        logger.info('清除头像图片，回退到 emoji 模式')

    def _on_reset(self):
        ret = QtWidgets.QMessageBox.question(
            self, '恢复默认',
            '确认把名字和头像恢复为默认值（{} + {}）吗？'.format(
                DEFAULT_NAME, DEFAULT_EMOJI,
            ),
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return
        self._draft = Employee()
        self._pending_image_path = ''
        self._pending_image_remove = True  # 把磁盘上的也清掉
        self._load_draft_into_ui()

    def _on_save(self):
        # 先处理图片：根据 pending 状态落盘 / 删盘
        if self._pending_image_path:
            pix = QtGui.QPixmap(self._pending_image_path)
            if pix.isNull():
                QtWidgets.QMessageBox.warning(
                    self, '保存失败',
                    '临时图片读取异常。',
                )
                return
            filename = save_avatar_image(pix)
            if not filename:
                QtWidgets.QMessageBox.warning(
                    self, '保存失败',
                    '头像图片写盘失败。',
                )
                return
            self._draft.avatar_image = filename
            self._pending_image_path = ''
            self._pending_image_remove = False
        elif self._pending_image_remove:
            remove_avatar_image()
            self._draft.avatar_image = ''
            self._pending_image_remove = False

        # image 模式但实际无图：自动降级到 emoji，避免气泡空头像
        if self._draft.avatar_kind == 'image' and not self._draft.avatar_image:
            self._draft.avatar_kind = 'emoji'

        # 持久化到配置
        self._draft.save(self._config)

        QtWidgets.QMessageBox.information(
            self, '已保存',
            '助手形象已更新，下一条新消息开始生效。',
        )
        self._refresh_preview()


def _safe_html(text):
    """供本模块预览路径使用的 HTML 转义。"""
    if not text:
        return ''
    return (
        text
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )
