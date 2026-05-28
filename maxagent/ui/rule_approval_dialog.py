#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规则学习审批对话框。

LLM 调用 ``suggest_rule_addition`` 时，主线程弹出本对话框，
显示规则 ID / 标题 / 正文 / 正反例，用户可以阅读、编辑、批准或拒绝。

设计与 ``learn_approval_dialog.py``（工具学习审批）保持一致风格，
但表单字段不同（规则文本而非源码）。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import Optional

from ..qt_compat import QtCore
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets
from .emoji_compat import btn_label as _btn_label
from .emoji_compat import ee as _ee


_DIALOG_STYLE = """
QDialog#RuleApprovalDialog { background-color:#1e1e1e; }
QDialog#RuleApprovalDialog QLabel { color:#d4d4d4; background:transparent; }
QDialog#RuleApprovalDialog QLineEdit,
QDialog#RuleApprovalDialog QPlainTextEdit,
QDialog#RuleApprovalDialog QTextEdit {
    background-color:#252525;
    color:#d4d4d4;
    border:1px solid #3a3a3a;
    selection-background-color:#264f78;
}
/* 通用按钮：必须把 background-image 一同清掉，否则 Max 全局样式表里的
   QPushButton 渐变 / 图片 会把我们的 background-color 盖住，造成"白底白字"。 */
QDialog#RuleApprovalDialog QPushButton {
    background-color:#4a4a4a;
    background-image:none;
    color:#ffffff;
    border:1px solid #5a5a5a;
    padding:6px 14px;
    min-width:90px;
    border-radius:3px;
}
QDialog#RuleApprovalDialog QPushButton:hover {
    background-color:#5a5a5a;
    background-image:none;
    border:1px solid #6a6a6a;
}
QDialog#RuleApprovalDialog QPushButton:pressed {
    background-color:#3a3a3a;
    background-image:none;
}
QDialog#RuleApprovalDialog QPushButton:disabled {
    background-color:#333;
    color:#888;
    border:1px solid #444;
}
QDialog#RuleApprovalDialog QPushButton#approveBtn {
    background-color:#2d7d46;
    background-image:none;
    color:#ffffff;
    border:1px solid #3a9c5a;
}
QDialog#RuleApprovalDialog QPushButton#approveBtn:hover {
    background-color:#3a9c5a;
    background-image:none;
    border:1px solid #4ab06a;
}
QDialog#RuleApprovalDialog QPushButton#approveBtn:pressed {
    background-color:#246138;
    background-image:none;
}
QDialog#RuleApprovalDialog QPushButton#rejectBtn {
    background-color:#a93232;
    background-image:none;
    color:#ffffff;
    border:1px solid #c44040;
}
QDialog#RuleApprovalDialog QPushButton#rejectBtn:hover {
    background-color:#c44040;
    background-image:none;
    border:1px solid #d65454;
}
QDialog#RuleApprovalDialog QPushButton#rejectBtn:pressed {
    background-color:#8a2828;
    background-image:none;
}
"""


class RuleApprovalDialog(QtWidgets.QDialog):
    """规则学习审批对话框。"""

    def __init__(self, proposal, parent=None):
        # type: (Dict[str, Any], Optional[Any]) -> None
        super(RuleApprovalDialog, self).__init__(parent)
        self.setObjectName('RuleApprovalDialog')
        self.setWindowTitle('MaxAgent · 规则沉淀审批')
        self.setStyleSheet(_DIALOG_STYLE)
        self._proposal = proposal or {}
        self._original = {
            'title': self._proposal.get('title', '') or '',
            'content': self._proposal.get('content', '') or '',
            'good_example': self._proposal.get('good_example', '') or '',
            'bad_example': self._proposal.get('bad_example', '') or '',
        }
        self._verdict = {
            'approved': False,
            'edited_title': self._original['title'],
            'edited_content': self._original['content'],
            'edited_good_example': self._original['good_example'],
            'edited_bad_example': self._original['bad_example'],
            'reason': '',
        }
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        # 头部说明
        existing = self._proposal.get('existing')
        if existing:
            head_text = (
                '<b style="color:#ffaa66;">{} AI 想要更新一条已有规则</b><br>'
                '<span style="color:#aaa;">'
                '同 ID 规则将被覆盖，原内容会丢失。'
                '</span>'
            ).format(_ee('⚠'))
        else:
            head_text = (
                '<b style="color:#aaffaa;">{} AI 想要沉淀一条新规则</b><br>'
                '<span style="color:#aaa;">'
                '批准后会保存到本地，并在后续每轮对话注入到 system prompt。'
                '可在设置面板 → 我的规则中查看 / 删除。'
                '</span>'
            ).format(_ee('💡'))
        head = QtWidgets.QLabel(head_text)
        head.setWordWrap(True)
        head.setStyleSheet('background:transparent;')
        v.addWidget(head)

        # 规则 ID（只读）
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel('规则 ID：'))
        self.id_edit = QtWidgets.QLineEdit(self._proposal.get('id', ''))
        self.id_edit.setReadOnly(True)
        self.id_edit.setStyleSheet('color:#a8e6a8;')
        row.addWidget(self.id_edit, 1)
        v.addLayout(row)

        # 标签（只读 chips）
        tags = self._proposal.get('tags') or []
        if tags:
            tag_label = QtWidgets.QLabel(
                '标签：<span style="color:#7ec0ff;">{}</span>'.format(
                    '  '.join('#' + str(t) for t in tags),
                ),
            )
            tag_label.setStyleSheet('background:transparent;')
            v.addWidget(tag_label)

        # 标题（可编辑）
        v.addWidget(QtWidgets.QLabel('标题（可编辑）：'))
        self.title_edit = QtWidgets.QLineEdit(self._original['title'])
        v.addWidget(self.title_edit)

        # 内容（可编辑）
        v.addWidget(QtWidgets.QLabel('规则正文（可编辑）：'))
        self.content_edit = QtWidgets.QPlainTextEdit(self._original['content'])
        self.content_edit.setMinimumHeight(80)
        v.addWidget(self.content_edit, 1)

        # 反例
        v.addWidget(QtWidgets.QLabel('反例（可编辑，选填）：'))
        self.bad_edit = QtWidgets.QPlainTextEdit(self._original['bad_example'])
        self.bad_edit.setMaximumHeight(60)
        self._set_mono(self.bad_edit)
        v.addWidget(self.bad_edit)

        # 正例
        v.addWidget(QtWidgets.QLabel('正例（可编辑，选填）：'))
        self.good_edit = QtWidgets.QPlainTextEdit(
            self._original['good_example'],
        )
        self.good_edit.setMaximumHeight(60)
        self._set_mono(self.good_edit)
        v.addWidget(self.good_edit)

        # 理由（如果有）
        rationale = self._proposal.get('rationale') or ''
        if rationale:
            v.addWidget(QtWidgets.QLabel('AI 给出的理由：'))
            ra_label = QtWidgets.QLabel(rationale)
            ra_label.setWordWrap(True)
            ra_label.setStyleSheet('background:#252525;color:#bbb;padding:4px;')
            v.addWidget(ra_label)

        # 底部按钮
        btn_row = QtWidgets.QHBoxLayout()
        reset_btn = QtWidgets.QPushButton('↺ 重置')
        reset_btn.setToolTip('恢复到 AI 最初提交的内容')
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch(1)
        self.reject_btn = QtWidgets.QPushButton(_btn_label('❌', '拒绝'))
        self.reject_btn.setObjectName('rejectBtn')
        self.reject_btn.clicked.connect(self._on_reject)
        btn_row.addWidget(self.reject_btn)
        self.approve_btn = QtWidgets.QPushButton(
            _btn_label('✅', '批准并保存'),
        )
        self.approve_btn.setObjectName('approveBtn')
        self.approve_btn.clicked.connect(self._on_approve)
        btn_row.addWidget(self.approve_btn)
        v.addLayout(btn_row)

    @staticmethod
    def _set_mono(widget):
        """给文本框设置等宽字体。"""
        font = QtGui.QFont('Consolas')
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        font.setPointSize(10)
        widget.setFont(font)

    # ------------------------------------------------------------------ #
    # 事件处理
    # ------------------------------------------------------------------ #
    def _on_reset(self):
        self.title_edit.setText(self._original['title'])
        self.content_edit.setPlainText(self._original['content'])
        self.good_edit.setPlainText(self._original['good_example'])
        self.bad_edit.setPlainText(self._original['bad_example'])

    def _on_approve(self):
        self._verdict = {
            'approved': True,
            'edited_title': self.title_edit.text().strip(),
            'edited_content': self.content_edit.toPlainText().strip(),
            'edited_good_example': self.good_edit.toPlainText().strip(),
            'edited_bad_example': self.bad_edit.toPlainText().strip(),
            'reason': '',
        }
        self.accept()

    def _on_reject(self):
        self._verdict = {
            'approved': False,
            'edited_title': self.title_edit.text().strip(),
            'edited_content': self.content_edit.toPlainText().strip(),
            'edited_good_example': self.good_edit.toPlainText().strip(),
            'edited_bad_example': self.bad_edit.toPlainText().strip(),
            'reason': '用户拒绝沉淀此规则',
        }
        self.reject()

    # ------------------------------------------------------------------ #
    # 公共
    # ------------------------------------------------------------------ #
    def get_verdict(self):
        # type: () -> Dict[str, Any]
        return dict(self._verdict)


def make_rule_approval_callback(parent_widget=None):
    """生成一个 set_rule_approval_callback 用的回调。

    返回的回调签名 ``cb(proposal) -> verdict_dict``，
    内部弹出 RuleApprovalDialog 并阻塞等待。

    parent 选取策略：
        优先使用 Max 主窗口（进程级永生），dock widget 在 Max 中
        可能被销毁/重建，作为 parent 会导致弹窗 C++ 实例被一并回收，
        触发 ``RuntimeError: Internal C++ object already deleted``。
        ``parent_widget`` 仅作为兜底（在非 Max 环境运行测试时使用）。
    """
    def _cb(proposal):
        from ..qt_compat import exec_compat
        from ..qt_compat import get_max_main_window
        parent = get_max_main_window() or parent_widget
        try:
            dlg = RuleApprovalDialog(proposal, parent=parent)
            # 提前取出 verdict 引用，避免 exec_ 阻塞期间被回收后再访问
            dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
            exec_compat(dlg)
            verdict = dlg.get_verdict()
            try:
                dlg.deleteLater()
            except RuntimeError:
                pass
            return verdict
        except RuntimeError as exc:
            # PySide 'Internal C++ object already deleted' 防御兜底：
            # 弹窗在 exec_ 期间因 parent 被销毁连带回收，返回拒绝结果，
            # 让上游工具收到清晰的"未通过"，不抛异常打断对话流。
            return {
                'approved': False,
                'edited_title': proposal.get('title', '') or '',
                'edited_content': proposal.get('content', '') or '',
                'edited_good_example': proposal.get('good_example', '') or '',
                'edited_bad_example': proposal.get('bad_example', '') or '',
                'reason': '审批弹窗被中途销毁: {}'.format(exc),
            }
    return _cb


__all__ = [
    'RuleApprovalDialog',
    'make_rule_approval_callback',
]
