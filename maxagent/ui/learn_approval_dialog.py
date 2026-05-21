#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学习工具审批对话框。

LLM 调用 ``propose_new_tool`` 时，主线程会弹出这个对话框，
显示工具名 / 描述 / 完整源码，用户可以阅读、编辑、批准或拒绝。

设计要点：
- 必须在主线程显示（弹窗 + exec_）。propose_new_tool 工具被声明
  ``run_on_main_thread=True``，所以 ToolDispatcher 会自动 marshal
  到主线程，弹窗能正常 exec_。
- 代码区用等宽字体；不做语法高亮（需要 QSyntaxHighlighter，引入更多
  代码暂不实现，直接 QPlainTextEdit 已足够阅读）。
- 提供"重置代码"按钮，便于用户编辑后恢复 LLM 原始代码。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import Optional

from ..qt_compat import QtCore
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets


_DIALOG_STYLE = """
QDialog#LearnApprovalDialog { background-color:#1e1e1e; }
QDialog#LearnApprovalDialog QLabel { color:#d4d4d4; }
QDialog#LearnApprovalDialog QLineEdit,
QDialog#LearnApprovalDialog QPlainTextEdit,
QDialog#LearnApprovalDialog QTextEdit {
    background-color:#252525; color:#d4d4d4;
}
QDialog#LearnApprovalDialog QPushButton {
    background-color:#4a4a4a; color:#fff;
}
QDialog#LearnApprovalDialog QPushButton:hover { background-color:#5a5a5a; }
QDialog#LearnApprovalDialog QPushButton#approveBtn {
    background-color:#2d7d46;
}
QDialog#LearnApprovalDialog QPushButton#approveBtn:hover {
    background-color:#3a9c5a;
}
QDialog#LearnApprovalDialog QPushButton#rejectBtn {
    background-color:#a93232;
}
QDialog#LearnApprovalDialog QPushButton#rejectBtn:hover {
    background-color:#c44040;
}
"""


class LearnApprovalDialog(QtWidgets.QDialog):
    """工具学习审批对话框。"""

    def __init__(self, proposal, parent=None):
        # type: (Dict[str, Any], Optional[Any]) -> None
        super(LearnApprovalDialog, self).__init__(parent)
        self.setObjectName('LearnApprovalDialog')
        self.setWindowTitle('MaxAgent · 工具学习审批')
        self.setStyleSheet(_DIALOG_STYLE)
        self._proposal = proposal or {}
        self._original_code = proposal.get('code', '') or ''
        self._verdict = {
            'approved': False,
            'edited_code': self._original_code,
            'edited_description': proposal.get('description', '') or '',
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
        head = QtWidgets.QLabel(
            '<b style="color:#ffaa66;">⚠ AI 想要学习一个新工具</b><br>'
            '<span style="color:#aaa;">'
            '请仔细阅读下面的代码。批准后，这个工具会被永久保存到你的电脑，'
            '下次启动也会自动加载。</span>'
        )
        head.setWordWrap(True)
        head.setStyleSheet('background:transparent;')
        v.addWidget(head)

        # 工具名（只读）
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel('工具名：'))
        self.name_edit = QtWidgets.QLineEdit(self._proposal.get('name', ''))
        self.name_edit.setReadOnly(True)
        self.name_edit.setStyleSheet('color:#a8e6a8;')
        row.addWidget(self.name_edit, 1)
        v.addLayout(row)

        # 描述（可编辑）
        v.addWidget(QtWidgets.QLabel('描述（可编辑）：'))
        self.desc_edit = QtWidgets.QPlainTextEdit(
            self._proposal.get('description', '') or '',
        )
        self.desc_edit.setMaximumHeight(60)
        v.addWidget(self.desc_edit)

        # 理由（如果有）
        rationale = self._proposal.get('rationale') or ''
        if rationale:
            v.addWidget(QtWidgets.QLabel('AI 给出的理由：'))
            ra_label = QtWidgets.QLabel(rationale)
            ra_label.setWordWrap(True)
            ra_label.setStyleSheet(
                'background:#252525;color:#bbb;'
            )
            v.addWidget(ra_label)

        # 代码区（可编辑）
        code_head = QtWidgets.QHBoxLayout()
        code_head.addWidget(QtWidgets.QLabel(
            '<b>源代码（可编辑，请审查）：</b>'
        ))
        code_head.addStretch(1)
        reset_btn = QtWidgets.QPushButton('重置')
        reset_btn.setToolTip('恢复到 AI 最初提交的代码')
        reset_btn.clicked.connect(self._on_reset_code)
        code_head.addWidget(reset_btn)
        v.addLayout(code_head)

        self.code_edit = QtWidgets.QPlainTextEdit(self._original_code)
        # 等宽字体便于阅读代码
        font = QtGui.QFont('Consolas')
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.code_edit.setFont(font)
        self.code_edit.setLineWrapMode(
            QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap,
        )
        v.addWidget(self.code_edit, 1)

        # 底部按钮
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        self.reject_btn = QtWidgets.QPushButton('拒绝')
        self.reject_btn.setObjectName('rejectBtn')
        self.reject_btn.clicked.connect(self._on_reject)
        btn_row.addWidget(self.reject_btn)
        self.approve_btn = QtWidgets.QPushButton('批准并保存')
        self.approve_btn.setObjectName('approveBtn')
        self.approve_btn.clicked.connect(self._on_approve)
        btn_row.addWidget(self.approve_btn)
        v.addLayout(btn_row)

    def _on_reset_code(self):
        self.code_edit.setPlainText(self._original_code)

    def _on_approve(self):
        self._verdict = {
            'approved': True,
            'edited_code': self.code_edit.toPlainText(),
            'edited_description': self.desc_edit.toPlainText().strip(),
            'reason': '',
        }
        self.accept()

    def _on_reject(self):
        self._verdict = {
            'approved': False,
            'edited_code': self.code_edit.toPlainText(),
            'edited_description': self.desc_edit.toPlainText().strip(),
            'reason': '用户拒绝了此次工具学习',
        }
        self.reject()

    # ------------------------------------------------------------------ #
    # 公共
    # ------------------------------------------------------------------ #
    def get_verdict(self):
        # type: () -> Dict[str, Any]
        return dict(self._verdict)


def make_approval_callback(parent_widget=None):
    """生成一个 set_approval_callback 用的回调。

    返回的回调签名 ``cb(proposal) -> verdict_dict``，
    内部弹出 LearnApprovalDialog 并阻塞等待。
    """
    def _cb(proposal):
        dlg = LearnApprovalDialog(proposal, parent=parent_widget)
        dlg.exec_()
        return dlg.get_verdict()
    return _cb


__all__ = [
    'LearnApprovalDialog',
    'make_approval_callback',
]
