#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具调用展示块。

把 LLM 的一次工具调用渲染成可折叠的卡片：
    ▶ 🔧 create_box  ✓ <耗时灰色>     ← 头部按钮（点击折叠/展开）
    └ args / result（默认折叠）

跟普通气泡的区别在于这是嵌入式的"操作日志"，不左不右居中显示，
而且执行过程中需要从 ``running`` -> ``done(ok)`` -> ``done(err)``
状态切换。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
from typing import Optional

from ..qt_compat import QtCore
from ..qt_compat import QtWidgets
from .bubbles import ChatLabel
from .markdown_render import html_escape


class ToolCallBlock(QtWidgets.QWidget):
    """可折叠的工具调用展示。"""

    def __init__(self, name, args_str, dangerous=False, parent=None):
        super(ToolCallBlock, self).__init__(parent)
        self._name = name
        self._args_str = args_str
        self._dangerous = dangerous
        self._result_text = ''
        self._result_ok = None  # type: Optional[bool]

        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(28, 2, 0, 2)
        outer.setSpacing(0)

        container = QtWidgets.QFrame()
        container.setStyleSheet(
            'QFrame { background:#252525; }'
        )
        cv = QtWidgets.QVBoxLayout(container)
        cv.setContentsMargins(8, 4, 8, 4)
        cv.setSpacing(2)

        # 头部行：[▶箭头按钮] [🔧 name 状态符]  ← 整行可点击折叠
        head_row = QtWidgets.QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(4)

        self._head_btn = QtWidgets.QToolButton()
        self._head_btn.setCheckable(True)
        self._head_btn.setChecked(False)
        self._head_btn.setText('▶')
        self._head_btn.setFixedWidth(18)
        self._head_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._head_btn.setStyleSheet(
            'QToolButton { background:transparent; color:#aaa; }'
            'QToolButton:hover { color:#fff; }'
        )
        self._head_btn.clicked.connect(self._toggle)
        head_row.addWidget(self._head_btn)

        self._head_label = QtWidgets.QLabel()
        self._head_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._head_label.setStyleSheet(
            'background:transparent; color:#d0d0d0;'
        )
        self._head_label.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._head_label.mousePressEvent = self._on_label_clicked
        head_row.addWidget(self._head_label, 1)
        cv.addLayout(head_row)

        self._refresh_head_label(running=True)

        # 详情区（默认折叠）
        self._detail = QtWidgets.QWidget()
        dv = QtWidgets.QVBoxLayout(self._detail)
        dv.setContentsMargins(16, 4, 0, 4)
        dv.setSpacing(3)

        # args
        self._args_label = ChatLabel(self._format_args_html(args_str))
        self._args_label.setStyleSheet(
            'background:#1a1a1a; color:#bbb;'
        )
        dv.addWidget(QtWidgets.QLabel(
            '<span style="color:#7fb3d5;font-size:9pt;">参数:</span>'
        ))
        dv.addWidget(self._args_label)

        # result（待填）
        self._result_title = QtWidgets.QLabel(
            '<span style="color:#7fb3d5;font-size:9pt;">'
            '结果: <i>等待执行...</i></span>'
        )
        self._result_label = ChatLabel('')
        self._result_label.setStyleSheet(
            'background:#1a1a1a; color:#bbb;'
        )
        dv.addWidget(self._result_title)
        dv.addWidget(self._result_label)
        self._result_label.hide()

        self._detail.hide()
        cv.addWidget(self._detail)

        outer.addWidget(container, 1)

    def _refresh_head_label(self, running=False):
        """刷新工具名行的富文本 + 箭头按钮文本。

        头部由两个 widget 拼成：
        - self._head_btn：纯文本的 ▶ / ▼，QToolButton 直接显示符号
        - self._head_label：富文本的图标 + 工具名 + 状态对勾
        """
        icon = '⚠️' if self._dangerous else '🔧'
        if running:
            sym = '⋯'
            color = '#888'
        elif self._result_ok is True:
            sym = '✓'
            color = '#8fce8f'
        else:
            sym = '✗'
            color = '#e57373'
        expanded = bool(self._head_btn and self._head_btn.isChecked())
        self._head_btn.setText('▼' if expanded else '▶')
        label_html = (
            '{icon} <b>{name}</b>  '
            '<span style="color:{color};">{sym}</span>'
        ).format(
            icon=icon, name=html_escape(self._name),
            color=color, sym=sym,
        )
        self._head_label.setText(label_html)

    def _on_label_clicked(self, _event):
        """点击工具名 label 时也触发折叠/展开。"""
        self._head_btn.setChecked(not self._head_btn.isChecked())
        self._toggle()

    def _toggle(self):
        expanded = self._head_btn.isChecked()
        self._detail.setVisible(expanded)
        self._refresh_head_label(running=(self._result_ok is None))

    def set_result(self, ok, result_str):
        # type: (bool, str) -> None
        """工具执行完成后回填结果。"""
        self._result_ok = bool(ok)
        self._result_text = result_str or ''
        self._refresh_head_label(running=False)
        self._result_title.setText(
            '<span style="color:#7fb3d5;font-size:9pt;">结果:</span>'
        )
        body = self._format_result_html(result_str, ok)
        self._result_label.setText(body)
        self._result_label.show()

    @staticmethod
    def _format_args_html(args_str):
        try:
            obj = json.loads(args_str)
            pretty = json.dumps(obj, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            pretty = args_str or '{}'
        return '<pre style="margin:0;white-space:pre-wrap;">{}</pre>'.format(
            html_escape(pretty)
        )

    @staticmethod
    def _format_result_html(result_str, ok):
        try:
            obj = json.loads(result_str)
            pretty = json.dumps(obj, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            pretty = result_str or ''
        if len(pretty) > 1200:
            pretty = pretty[:1200] + '\n... (截断)'
        color = '#a8e6a8' if ok else '#e57373'
        return (
            '<pre style="margin:0;white-space:pre-wrap;color:{c};">'
            '{body}</pre>'
        ).format(c=color, body=html_escape(pretty))
