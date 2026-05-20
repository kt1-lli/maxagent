#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主停靠面板：聊天界面 + 工具调用展示 + 设置入口。

UI 布局 (v0.3: 基于 widget 的消息列表 + 流式增量 + Markdown 渲染):
+-------------------------------------------+
|  [Profile: ▼ DeepSeek-Chat]   [⚙设置]     |
+-------------------------------------------+
| ┌─ QScrollArea (消息列表) ──────────────┐ |
| │              [👤 你]                  │ |  ← 用户气泡靠右
| │     [创建一个红色的茶壶]              │ |
| │                                       │ |
| │ [🤖 助手]                             │ |  ← 助手气泡靠左
| │ [好的，我来创建...]                   │ |
| │ ▶ 工具 create_teapot ✓ 展开/折叠     │ |
| └───────────────────────────────────────┘ |
+============== 拖拽调节 ===================+  ← QSplitter
|  [输入框 (Enter发送, Shift+Enter换行)] [发送][停止] |
+-------------------------------------------+

设计要点:
- 每条消息是独立 QFrame，append 到 messages_layout 末尾，O(1)
- 流式时使用 _StreamingAssistantBubble，chunk 直接 append 到内部
  QTextEdit，避免重绘开销；流式结束后用 markdown 渲染最终 HTML
- 工具调用块是 QToolButton + 内容 widget，可折叠
- 自动滚动只在用户处于底部时触发，避免阅读历史时被打断
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import threading
from typing import Any
from typing import Optional

from ..agent import AgentWorker
from ..agent import Conversation
from ..config import ConfigManager
from ..llm_client import build_client_from_profile
from ..qt_compat import QtCore
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets
from ..sessions import SessionManager
from ..sessions import SessionMeta
from ..skills import SkillManager
from ..tools import ToolDispatcher
from ..ui_state import UIStateManager
from .markdown_render import extract_code_blocks
from .markdown_render import html_escape
from .markdown_render import render_markdown

QApplication = QtWidgets.QApplication


# ---------------------------------------------------------------------- #
# 样式表
# ---------------------------------------------------------------------- #
_STYLE = """
QWidget#MaxAgentDockWidget { background-color: #1e1e1e; }
QScrollArea#chatScroll {
    background-color: #1e1e1e;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
}
QWidget#chatContent { background-color: #1e1e1e; }
QPlainTextEdit, QTextEdit {
    background-color: #2b2b2b;
    color: #d4d4d4;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
    font-size: 11pt;
    padding: 4px;
}
QPushButton {
    background-color: #4a4a4a;
    color: #ffffff;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 4px 12px;
    min-height: 24px;
}
QPushButton:hover { background-color: #5a5a5a; }
QPushButton:disabled { background-color: #333; color: #777; }
QPushButton#sendBtn { background-color: #2d7d46; }
QPushButton#sendBtn:hover { background-color: #3a9c5a; }
QPushButton#stopBtn { background-color: #a93232; }
QPushButton#stopBtn:hover { background-color: #c44040; }
QPushButton.miniBtn {
    background-color: transparent;
    color: #888;
    border: 1px solid #444;
    border-radius: 3px;
    padding: 1px 6px;
    min-height: 18px;
    font-size: 9pt;
}
QPushButton.miniBtn:hover { background-color: #333; color: #ddd; }
QToolButton {
    background-color: transparent;
    color: #d0d0d0;
    border: none;
    text-align: left;
    padding: 2px 4px;
    font-family: Consolas, 'Courier New', monospace;
    font-size: 10pt;
}
QToolButton:hover { color: #ffffff; }
QComboBox {
    background-color: #3c3c3c;
    color: #d4d4d4;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 2px 8px;
    min-height: 22px;
}
QLabel { color: #d4d4d4; }

QSplitter::handle:vertical {
    background-color: #3c3c3c;
    height: 6px;
    border-top: 1px solid #2a2a2a;
    border-bottom: 1px solid #2a2a2a;
}
QSplitter::handle:vertical:hover { background-color: #5a8ab8; }
QSplitter::handle:vertical:pressed { background-color: #6ba3d4; }

QScrollBar:vertical {
    background: #1e1e1e;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #4a4a4a;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #5a5a5a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


# ---------------------------------------------------------------------- #
# 消息气泡基类
# ---------------------------------------------------------------------- #
class _BubbleFrame(QtWidgets.QFrame):
    """单条消息的气泡容器（一个 QFrame，内含 layout）。

    通过外部 hbox 控制左右对齐：layout 里加 stretch 推到一边。
    """

    def __init__(self, align='left', bg='#2d3d2d', fg='#d4ead4',
                 parent=None):
        super(_BubbleFrame, self).__init__(parent)
        self._align = align
        self._bg = bg
        self._fg = fg
        self.setStyleSheet(
            'QFrame {{ background:{bg}; color:{fg};'
            'border-radius:10px; padding:0; }}'.format(bg=bg, fg=fg)
        )
        # 让气泡宽度按内容自适应，但不要把整个父容器撑满
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Maximum,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self._inner = QtWidgets.QVBoxLayout(self)
        self._inner.setContentsMargins(10, 6, 10, 8)
        self._inner.setSpacing(2)

    def add_widget(self, w):
        self._inner.addWidget(w)

    def add_layout(self, layout):
        self._inner.addLayout(layout)

    @property
    def align(self):
        return self._align


class _ChatLabel(QtWidgets.QLabel):
    """气泡内的富文本标签，自动换行 + 可选中复制。"""

    def __init__(self, text='', parent=None):
        super(_ChatLabel, self).__init__(parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.setOpenExternalLinks(True)
        self.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.setStyleSheet('background:transparent;')
        if text:
            self.setText(text)


# ---------------------------------------------------------------------- #
# 流式助手气泡：chunk 增量 append，结束后 markdown 重渲染
# ---------------------------------------------------------------------- #
class _StreamingAssistantBubble(QtWidgets.QWidget):
    """正在流式接收的助手气泡。

    流式过程中显示 plain text（避免 markdown 半截解析的闪烁），
    end_streaming() 时一次性切换到 markdown 渲染的 HTML。
    """

    def __init__(self, parent=None):
        super(_StreamingAssistantBubble, self).__init__(parent)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._bubble = _BubbleFrame(
            align='left', bg='#2d3d2d', fg='#d4ead4',
        )
        # 标题
        head = QtWidgets.QLabel(
            '<span style="color:#a8e6a8;font-size:9pt;">🤖 助手</span>'
        )
        head.setStyleSheet('background:transparent;')
        self._bubble.add_widget(head)
        # 流式正文
        self._label = _ChatLabel('')
        self._bubble.add_widget(self._label)
        outer.addWidget(self._bubble, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        outer.addStretch(1)
        self._buffer = ''
        self._closed = False

    def append_chunk(self, chunk):
        # type: (str) -> None
        if not chunk or self._closed:
            return
        self._buffer += chunk
        # 流式过程：转义 + 简单 br 替换，先不解析 markdown，避免闪烁
        # 等 end_streaming 时再做完整 markdown 渲染
        body = html_escape(self._buffer).replace('\n', '<br>')
        self._label.setText(body)

    def end_streaming(self):
        # type: () -> str
        """流式结束，返回最终 buffer。调用方负责把这个 bubble
        替换为最终的 markdown 渲染版本。"""
        self._closed = True
        return self._buffer

    def is_empty(self):
        return not self._buffer.strip()


# ---------------------------------------------------------------------- #
# 最终助手气泡（markdown 渲染 + 复制按钮）
# ---------------------------------------------------------------------- #
class _AssistantBubble(QtWidgets.QWidget):
    """已完成的助手回复气泡，渲染 markdown，并附带复制按钮。"""

    def __init__(self, text, parent=None):
        super(_AssistantBubble, self).__init__(parent)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        bubble = _BubbleFrame(align='left', bg='#2d3d2d', fg='#d4ead4')

        # 标题行：[🤖 助手]  [复制] [复制代码]
        title_row = QtWidgets.QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        head = QtWidgets.QLabel(
            '<span style="color:#a8e6a8;font-size:9pt;">🤖 助手</span>'
        )
        head.setStyleSheet('background:transparent;')
        title_row.addWidget(head)
        title_row.addStretch(1)

        copy_btn = QtWidgets.QPushButton('复制')
        copy_btn.setProperty('class', 'miniBtn')
        copy_btn.setStyleSheet(self._mini_btn_style())
        copy_btn.clicked.connect(lambda: self._copy_to_clipboard(text))
        title_row.addWidget(copy_btn)

        # 如果包含代码块，加"复制代码"按钮
        code_blocks = extract_code_blocks(text)
        if len(code_blocks) == 1:
            code_btn = QtWidgets.QPushButton('复制代码')
            code_btn.setStyleSheet(self._mini_btn_style())
            code_btn.clicked.connect(
                lambda: self._copy_to_clipboard(code_blocks[0][1])
            )
            title_row.addWidget(code_btn)
        elif len(code_blocks) > 1:
            for idx, (_lang, _code) in enumerate(code_blocks):
                btn = QtWidgets.QPushButton('代码{}'.format(idx + 1))
                btn.setStyleSheet(self._mini_btn_style())
                # 闭包变量绑定
                btn.clicked.connect(
                    lambda _checked=False, c=_code:
                    self._copy_to_clipboard(c)
                )
                title_row.addWidget(btn)

        bubble.add_layout(title_row)

        # 正文：markdown 渲染
        body = render_markdown(text)
        label = _ChatLabel(body)
        bubble.add_widget(label)

        outer.addWidget(bubble, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        outer.addStretch(1)

    @staticmethod
    def _mini_btn_style():
        return (
            'QPushButton { background:transparent; color:#888;'
            'border:1px solid #444; border-radius:3px;'
            'padding:1px 6px; min-height:18px; font-size:9pt; }'
            'QPushButton:hover { background:#333; color:#ddd; }'
        )

    @staticmethod
    def _copy_to_clipboard(text):
        cb = QApplication.clipboard()
        cb.setText(text)


# ---------------------------------------------------------------------- #
# 用户气泡（靠右）
# ---------------------------------------------------------------------- #
class _UserBubble(QtWidgets.QWidget):
    def __init__(self, text, parent=None):
        super(_UserBubble, self).__init__(parent)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)

        bubble = _BubbleFrame(align='right', bg='#2c5d8f', fg='#ffffff')
        head = QtWidgets.QLabel(
            '<span style="color:#bbd9f5;font-size:9pt;">👤 你</span>'
        )
        head.setStyleSheet('background:transparent; color:#bbd9f5;')
        bubble.add_widget(head)

        body = html_escape(text).replace('\n', '<br>')
        label = _ChatLabel(
            '<span style="color:#ffffff;line-height:1.5;">'
            + body + '</span>'
        )
        bubble.add_widget(label)
        outer.addWidget(bubble, 0, QtCore.Qt.AlignmentFlag.AlignRight)


# ---------------------------------------------------------------------- #
# 工具调用块（可折叠）
# ---------------------------------------------------------------------- #
class _ToolCallBlock(QtWidgets.QWidget):
    """可折叠的工具调用展示。

    布局：
    ▶ 🔧 create_box  ✓ <耗时灰色>     ← 头部按钮（点击折叠/展开）
    └ args / result（默认折叠）
    """

    def __init__(self, name, args_str, dangerous=False, parent=None):
        super(_ToolCallBlock, self).__init__(parent)
        self._name = name
        self._args_str = args_str
        self._dangerous = dangerous
        self._result_text = ''
        self._result_ok = None  # type: Optional[bool]

        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(28, 2, 0, 2)  # 左缩进
        outer.setSpacing(0)

        container = QtWidgets.QFrame()
        container.setStyleSheet(
            'QFrame { background:#252525; border-left:3px solid '
            + ('#ffaa66' if dangerous else '#7fb3d5') + ';'
            'border-radius:3px; }'
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
            'QToolButton { background:transparent; color:#aaa;'
            'border:none; padding:0; font-size:10pt; }'
            'QToolButton:hover { color:#fff; }'
        )
        self._head_btn.clicked.connect(self._toggle)
        head_row.addWidget(self._head_btn)

        # 工具名 QLabel（支持富文本），可点击同步折叠
        self._head_label = QtWidgets.QLabel()
        self._head_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._head_label.setStyleSheet(
            'background:transparent; color:#d0d0d0;'
            'font-family:Consolas,\'Courier New\',monospace;'
            'font-size:10pt;'
        )
        self._head_label.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        # 让 label 也接受点击折叠
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
        self._args_label = _ChatLabel(self._format_args_html(args_str))
        self._args_label.setStyleSheet(
            'background:#1a1a1a; color:#bbb;'
            'font-family:Consolas,monospace; font-size:10pt;'
            'padding:4px 8px; border-radius:3px;'
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
        self._result_label = _ChatLabel('')
        self._result_label.setStyleSheet(
            'background:#1a1a1a; color:#bbb;'
            'font-family:Consolas,monospace; font-size:10pt;'
            'padding:4px 8px; border-radius:3px;'
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
        # 箭头由 QToolButton 单独承载，避免 setText 吃 HTML 的问题
        self._head_btn.setText('▼' if expanded else '▶')
        # 工具名 + 状态符放进 QLabel（支持 RichText）
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
        self._result_ok = bool(ok)
        self._result_text = result_str or ''
        # 刷新头部
        self._refresh_head_label(running=False)
        # 刷新结果区
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


# ---------------------------------------------------------------------- #
# 状态/错误气泡
# ---------------------------------------------------------------------- #
class _StatusLine(QtWidgets.QWidget):
    def __init__(self, text, parent=None):
        super(_StatusLine, self).__init__(parent)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 2, 0, 2)
        lbl = QtWidgets.QLabel(
            '<span style="color:#888;font-style:italic;font-size:10pt;">'
            '⋯ {}</span>'.format(html_escape(text))
        )
        lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet('background:transparent;')
        h.addWidget(lbl, 1)


class _ErrorBubble(QtWidgets.QWidget):
    def __init__(self, text, parent=None):
        super(_ErrorBubble, self).__init__(parent)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        bubble = _BubbleFrame(align='left', bg='#4a2a2a', fg='#ffaaaa')
        head = QtWidgets.QLabel(
            '<b style="color:#ffaaaa;">⚠ 错误</b>'
        )
        head.setStyleSheet('background:transparent;')
        bubble.add_widget(head)
        body = html_escape(text).replace('\n', '<br>')
        label = _ChatLabel(
            '<span style="color:#ffaaaa;font-size:10pt;">'
            + body + '</span>'
        )
        bubble.add_widget(label)
        outer.addWidget(bubble, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        outer.addStretch(1)


class _WelcomeBlock(QtWidgets.QWidget):
    """欢迎块。可点击的示例按钮会触发 example_picked 信号。"""

    example_picked = QtCore.Signal(str)

    _EXAMPLES = (
        '创建一个红色的茶壶并加上 TurboSmooth 修改器',
        '列出场景里所有的灯光，按强度排序',
        '把所有 Box001 重命名为 wall_xx 序列',
    )

    def __init__(self, html_body, parent=None):
        super(_WelcomeBlock, self).__init__(parent)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 8, 0, 8)
        v.setSpacing(6)
        head = QtWidgets.QLabel(
            '<div align="center" style="color:#888;font-size:10pt;">'
            + html_body + '</div>'
        )
        head.setWordWrap(True)
        head.setStyleSheet('background:transparent;')
        v.addWidget(head)

        for ex in self._EXAMPLES:
            btn = QtWidgets.QPushButton('💡 ' + ex)
            btn.setStyleSheet(
                'QPushButton { background:#252525; color:#a0a0a0;'
                'border:1px dashed #444; border-radius:4px;'
                'padding:6px 10px; font-size:10pt; text-align:left; }'
                'QPushButton:hover { background:#2d3d2d; color:#ddd;'
                'border-color:#5a8a5a; }'
            )
            # 闭包绑定
            btn.clicked.connect(
                lambda _checked=False, t=ex: self.example_picked.emit(t)
            )
            v.addWidget(btn)


# ---------------------------------------------------------------------- #
# 聊天列表渲染器：管理 messages_layout 里的 widget
# ---------------------------------------------------------------------- #
class _ChatRenderer(QtCore.QObject):
    """管理消息列表区的所有气泡 widget。

    职责:
    - 把消息插入到 messages_layout（在 stretch spacer 之前）
    - 管理"当前正在流式的助手气泡"（_StreamingAssistantBubble）
    - 智能滚动：只在用户处于底部时才自动跟随
    """

    example_picked = QtCore.Signal(str)

    def __init__(self, scroll_area, content_widget, content_layout,
                 parent=None):
        super(_ChatRenderer, self).__init__(parent)
        self._scroll = scroll_area
        self._content = content_widget
        self._layout = content_layout  # type: QtWidgets.QVBoxLayout
        # 末尾 stretch，让消息从顶部开始堆
        self._layout.addStretch(1)
        self._streaming = None  # type: Optional[_StreamingAssistantBubble]

    # ------------------------------------------------------------------ #
    # 底部追加（在 stretch 之前）
    # ------------------------------------------------------------------ #
    def _append(self, widget):
        # 去掉末尾 stretch -> 加 widget -> 重新加 stretch
        # 直接 insertWidget 到倒数第二（stretch 是最后一个 item）
        idx = self._layout.count() - 1
        if idx < 0:
            idx = 0
        # 滚动跟随策略：插入前判断用户当前是否在底部
        was_at_bottom = self._is_at_bottom()
        self._layout.insertWidget(idx, widget)
        if was_at_bottom:
            QtCore.QTimer.singleShot(0, self._scroll_to_bottom)

    def _is_at_bottom(self):
        bar = self._scroll.verticalScrollBar()
        # 容差 30px：用户略微往上一点也认为是"在底部"
        return bar.value() >= bar.maximum() - 30

    def _scroll_to_bottom(self):
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------------ #
    # 消息接口
    # ------------------------------------------------------------------ #
    def add_user(self, text):
        self._close_streaming_if_any()
        self._append(_UserBubble(text))

    def add_assistant_start(self):
        """开始一段助手回复气泡，后续 chunk 会增量追加。"""
        self._close_streaming_if_any()
        bubble = _StreamingAssistantBubble()
        self._streaming = bubble
        self._append(bubble)

    def add_assistant_chunk(self, chunk):
        if self._streaming is None:
            self.add_assistant_start()
        self._streaming.append_chunk(chunk)
        # 流式过程也要跟随滚动（如果用户在底部）
        if self._is_at_bottom():
            QtCore.QTimer.singleShot(0, self._scroll_to_bottom)

    def end_turn(self):
        """一次流式段落结束：把 streaming bubble 替换成 markdown 渲染版本。"""
        self._close_streaming_if_any()

    def _close_streaming_if_any(self):
        if self._streaming is None:
            return
        bubble = self._streaming
        self._streaming = None
        text = bubble.end_streaming()
        # 找到 streaming bubble 在 layout 里的位置，替换为 _AssistantBubble
        # 如果是空的，直接移除（占位作用已尽）
        idx = self._layout.indexOf(bubble)
        if idx < 0:
            return
        self._layout.takeAt(idx)
        bubble.setParent(None)
        bubble.deleteLater()
        if text.strip():
            final = _AssistantBubble(text)
            self._layout.insertWidget(idx, final)

    def add_tool_call(self, name, args_str, dangerous=False):
        # 工具块出现时收尾流式（LLM 不会在工具调用之间还吐 token）
        self._close_streaming_if_any()
        block = _ToolCallBlock(name, args_str, dangerous=dangerous)
        self._append(block)
        return block

    def add_status(self, text):
        self._close_streaming_if_any()
        self._append(_StatusLine(text))

    def add_error(self, text):
        self._close_streaming_if_any()
        self._append(_ErrorBubble(text))

    def add_welcome(self, html_body):
        block = _WelcomeBlock(html_body)
        block.example_picked.connect(self.example_picked.emit)
        self._append(block)

    def clear(self):
        """清空全部消息，但保留末尾 stretch。"""
        self._streaming = None
        # 倒序删，留最后一个 stretch
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()


# ---------------------------------------------------------------------- #
# 主面板
# ---------------------------------------------------------------------- #
class MaxAgentDockWidget(QtWidgets.QWidget):
    """主聊天面板。

    Max 那边会用 QtMax.GetQMaxMainWindow() 当 parent，把这个 widget 包到一个
    QDockWidget 里贴在 Max 主窗口上。在非 Max 环境也能独立 show() 出来调试。
    """

    _DEFAULT_SPLIT_RATIO = (78, 22)
    _MIN_INPUT_HEIGHT = 60
    _MIN_CHAT_HEIGHT = 120

    def __init__(self, config_manager=None, parent=None):
        # type: (Optional[ConfigManager], Optional[Any]) -> None
        super(MaxAgentDockWidget, self).__init__(parent)
        self.setObjectName('MaxAgentDockWidget')
        self.setWindowTitle('MaxAgent · AI 助手')
        self.setStyleSheet(_STYLE)

        self._config = config_manager or ConfigManager()
        self._ui_state_mgr = UIStateManager()
        self._ui_state = self._ui_state_mgr.load()
        self._llm = self._build_llm_client()
        self._session_mgr = SessionManager()
        self._skill_mgr = SkillManager()
        self._current_session = None  # type: Optional[SessionMeta]
        self._conv = Conversation()
        self._dispatcher = ToolDispatcher()
        # type: Optional[AgentWorker]
        self._worker = None
        self._is_running = False
        # 当前正在执行的工具块映射: call_id -> _ToolCallBlock
        self._pending_tool_blocks = {}

        self._build_ui()
        self._refresh_profiles()
        self._refresh_sessions_combo()
        # UI 构建完成后恢复分割器尺寸（其他几何由外层 QDockWidget 处理）
        self._restore_splitter_state()
        # 自动恢复上次的会话或新建一个
        self._bootstrap_session()
        # 注册"学习新工具"审批回调（把弹窗与本面板挂钩）
        self._install_learn_approval_callback()

    # ------------------------------------------------------------------ #
    # 构建 UI
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        # === 顶部条第 1 行：Profile + 设置 ===
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QtWidgets.QLabel('Profile:'))
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.setMinimumWidth(180)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        top.addWidget(self.profile_combo)
        top.addStretch(1)
        self.settings_btn = QtWidgets.QPushButton('⚙ 设置')
        self.settings_btn.clicked.connect(self._open_settings)
        top.addWidget(self.settings_btn)
        outer.addLayout(top)

        # === 顶部条第 2 行：会话管理 ===
        sess_row = QtWidgets.QHBoxLayout()
        sess_row.setSpacing(4)
        self.new_session_btn = QtWidgets.QPushButton('➕ 新对话')
        self.new_session_btn.setToolTip('开启一个新的空白对话')
        self.new_session_btn.clicked.connect(self._on_new_session)
        sess_row.addWidget(self.new_session_btn)

        sess_row.addWidget(QtWidgets.QLabel('会话:'))
        self.session_combo = QtWidgets.QComboBox()
        self.session_combo.setMinimumWidth(220)
        self.session_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents,
        )
        self.session_combo.currentIndexChanged.connect(
            self._on_session_combo_changed,
        )
        sess_row.addWidget(self.session_combo, 1)

        self.rename_session_btn = QtWidgets.QPushButton('✏')
        self.rename_session_btn.setFixedWidth(28)
        self.rename_session_btn.setToolTip('重命名当前会话')
        self.rename_session_btn.clicked.connect(self._on_rename_session)
        sess_row.addWidget(self.rename_session_btn)

        self.delete_session_btn = QtWidgets.QPushButton('🗑')
        self.delete_session_btn.setFixedWidth(28)
        self.delete_session_btn.setToolTip('删除当前会话')
        self.delete_session_btn.clicked.connect(self._on_delete_session)
        sess_row.addWidget(self.delete_session_btn)

        self.clear_btn = QtWidgets.QPushButton('清空')
        self.clear_btn.setToolTip('清空当前会话的消息（保留会话本身）')
        self.clear_btn.clicked.connect(self._clear_history)
        sess_row.addWidget(self.clear_btn)
        outer.addLayout(sess_row)

        # === 顶部条第 3 行：上下文 token 监控 + 压缩按钮 ===
        ctx_row = QtWidgets.QHBoxLayout()
        ctx_row.setSpacing(4)
        self.context_label = QtWidgets.QLabel('📊 上下文: -')
        self.context_label.setToolTip(
            '当前对话历史占用的估算 token 数 / 上限。\n'
            '超过上限时会自动裁剪最早的消息（保护 tool_call 配对与最近 4 条）。\n'
            '上限可在「设置」中按 Profile 调整。',
        )
        self.context_label.setStyleSheet(
            'color:#aaa;font-size:9pt;padding:0 4px;',
        )
        ctx_row.addWidget(self.context_label)
        ctx_row.addStretch(1)

        self.compress_btn = QtWidgets.QPushButton('🗜 压缩对话')
        self.compress_btn.setToolTip(
            '让 LLM 总结早期对话内容并替换为摘要，保留最近 2 轮。\n'
            '适合长对话节省 token，但会失去早期细节。',
        )
        self.compress_btn.clicked.connect(self._on_compress_history)
        ctx_row.addWidget(self.compress_btn)
        outer.addLayout(ctx_row)

        # === 中部：QSplitter (聊天区 | 输入区) ===
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)

        # 聊天区: QScrollArea + 内部 QWidget + QVBoxLayout
        self.chat_scroll = QtWidgets.QScrollArea()
        self.chat_scroll.setObjectName('chatScroll')
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setMinimumHeight(self._MIN_CHAT_HEIGHT)
        self.chat_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        chat_content = QtWidgets.QWidget()
        chat_content.setObjectName('chatContent')
        self._messages_layout = QtWidgets.QVBoxLayout(chat_content)
        self._messages_layout.setContentsMargins(8, 8, 8, 8)
        self._messages_layout.setSpacing(4)
        self.chat_scroll.setWidget(chat_content)
        self.splitter.addWidget(self.chat_scroll)

        self._renderer = _ChatRenderer(
            self.chat_scroll, chat_content, self._messages_layout,
        )
        self._renderer.example_picked.connect(self._on_example_picked)

        # 输入区
        input_container = QtWidgets.QWidget()
        input_layout = QtWidgets.QHBoxLayout(input_container)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(4)

        self.input_edit = _SmartInput(self)
        self.input_edit.setMinimumHeight(self._MIN_INPUT_HEIGHT)
        self.input_edit.setPlaceholderText(
            '在这里输入指令...\n'
            'Enter 发送 / Shift+Enter 换行 / Ctrl+Enter 发送（拖动上方分割条可调整大小）',
        )
        self.input_edit.send_requested.connect(self._on_send)
        input_layout.addWidget(self.input_edit, 1)

        btn_col = QtWidgets.QVBoxLayout()
        btn_col.setSpacing(2)
        self.send_btn = QtWidgets.QPushButton('发送')
        self.send_btn.setObjectName('sendBtn')
        self.send_btn.clicked.connect(self._on_send)
        btn_col.addWidget(self.send_btn)
        self.stop_btn = QtWidgets.QPushButton('停止')
        self.stop_btn.setObjectName('stopBtn')
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)
        btn_col.addWidget(self.stop_btn)
        btn_col.addStretch(1)
        input_layout.addLayout(btn_col)

        input_container.setMinimumHeight(self._MIN_INPUT_HEIGHT + 8)
        self.splitter.addWidget(input_container)

        self.splitter.setStretchFactor(0, self._DEFAULT_SPLIT_RATIO[0])
        self.splitter.setStretchFactor(1, self._DEFAULT_SPLIT_RATIO[1])
        self.splitter.setSizes([400, 100])

        outer.addWidget(self.splitter, 1)

        # === 底部状态栏 ===
        self.status_label = QtWidgets.QLabel('准备就绪')
        self.status_label.setStyleSheet('color:#888;font-size:10pt;')
        outer.addWidget(self.status_label)

        # 欢迎语 / 历史回放由 _bootstrap_session() 在 __init__ 末尾负责

    # ------------------------------------------------------------------ #
    # Profile / LLM
    # ------------------------------------------------------------------ #
    def _refresh_profiles(self):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        active = self._config.get_active_profile_name()
        for name in self._config.list_profile_names():
            self.profile_combo.addItem(name)
        idx = self.profile_combo.findText(active)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)

    def _build_llm_client(self):
        prof = self._config.get_active_profile()
        return build_client_from_profile(prof)

    # ------------------------------------------------------------------ #
    # UI 状态：恢复 / 保存
    # ------------------------------------------------------------------ #
    def _restore_splitter_state(self):
        """从 ui_state.json 恢复分割器尺寸。

        只在 sizes 看起来合法时应用，避免上次崩溃存了奇怪的值导致
        本次启动就看不到输入框。
        """
        sizes = list(self._ui_state.splitter_sizes or [])
        if len(sizes) == 2 and all(isinstance(s, int) and s >= 0 for s in sizes):
            # 至少留出最小输入区高度
            if sizes[1] < self._MIN_INPUT_HEIGHT:
                sizes[1] = self._MIN_INPUT_HEIGHT
            try:
                self.splitter.setSizes(sizes)
            except Exception:  # pylint: disable=broad-except
                # Qt 在 widget 还未真正渲染时可能拒绝 setSizes，忽略
                pass

    def save_ui_state(self, geometry_b64='', floating=None,
                      dock_area=None, embedded_ok=None):
        """持久化当前 UI 状态到磁盘。

        :param geometry_b64: 调用方（startup.py）从 QDockWidget /
            QMainWindow.saveGeometry 拿到的 base64 字符串。dock_widget
            自己不负责编码这部分，因为真正的 widget 是包在外层的
            QDockWidget。
        :param floating: 是否浮动；None 表示沿用旧值
        :param dock_area: Qt 停靠区域枚举的整数值；None 表示沿用旧值
        :param embedded_ok: 本次启动是否成功嵌入到 Max
        """
        st = self._ui_state
        # 分割器尺寸总是从当前 widget 取
        try:
            st.splitter_sizes = list(self.splitter.sizes())
        except Exception:  # pylint: disable=broad-except
            pass
        if geometry_b64:
            st.geometry_b64 = geometry_b64
        if floating is not None:
            st.floating = bool(floating)
        if dock_area is not None:
            try:
                st.dock_area = int(dock_area)
            except (TypeError, ValueError):
                pass
        if embedded_ok is not None:
            st.last_embedded_ok = bool(embedded_ok)
        # 独立窗口模式下记录窗口尺寸
        try:
            if self.isWindow():
                geo = self.geometry()
                st.window_w = geo.width()
                st.window_h = geo.height()
                st.window_x = geo.x()
                st.window_y = geo.y()
                st.maximized = bool(self.isMaximized())
        except Exception:  # pylint: disable=broad-except
            pass
        self._ui_state_mgr.save(st)

    def get_ui_state(self):
        """返回当前 UIState（供 startup.py 决定如何恢复几何）。"""
        return self._ui_state

    def closeEvent(self, event):  # noqa: N802 (Qt 命名)
        """窗口关闭时持久化分割器/几何状态。"""
        try:
            self.save_ui_state()
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            self._save_current_session()
        except Exception:  # pylint: disable=broad-except
            pass
        super(MaxAgentDockWidget, self).closeEvent(event)

    def _get_active_max_loops(self):
        # type: () -> int
        """从当前 profile 读取工具调用最大循环数；缺省/异常时回退到默认。"""
        try:
            prof = self._config.get_active_profile()
            v = int(getattr(prof, 'max_tool_loops', 0) or 0)
            if v > 0:
                return v
        except Exception:  # pylint: disable=broad-except
            pass
        # 回退到 worker 模块定义的默认值
        from ..agent.worker import MAX_TOOL_LOOPS
        return MAX_TOOL_LOOPS

    def _get_active_max_history_tokens(self):
        # type: () -> int
        """从当前 profile 读取历史 token 预算；缺省/异常时回退到 32000。"""
        try:
            prof = self._config.get_active_profile()
            v = int(getattr(prof, 'max_history_tokens', 0) or 0)
            if v > 0:
                return v
        except Exception:  # pylint: disable=broad-except
            pass
        return 32000

    def _refresh_context_label(self):
        """刷新顶部 token 状态条。

        显示格式：📊 上下文: 2.5K/32K (8 条)
        颜色根据占比变化：<60% 灰、<85% 橙、>=85% 红
        """
        try:
            cur = self._conv.estimate_total_tokens()
        except Exception:  # pylint: disable=broad-except
            cur = 0
        budget = self._get_active_max_history_tokens()
        msgs = len(self._conv) if self._conv else 0

        def _fmt(n):
            if n >= 1000:
                return '{:.1f}K'.format(n / 1000.0)
            return str(n)

        ratio = (cur / budget) if budget > 0 else 0.0
        if ratio < 0.6:
            color = '#888'
        elif ratio < 0.85:
            color = '#d89e3a'
        else:
            color = '#d65c5c'
        text = '📊 上下文: {} / {}  ({} 条)'.format(
            _fmt(cur), _fmt(budget), msgs,
        )
        self.context_label.setText(text)
        self.context_label.setStyleSheet(
            'color:{};font-size:9pt;padding:0 4px;'.format(color),
        )

    def _on_history_trimmed(self, removed, current_tokens, budget_tokens):
        """worker 通知"已自动裁剪 N 条早期消息"。"""
        self._renderer.add_status(
            '🧹 历史已自动裁剪 {} 条早期消息以适配 token 预算 '
            '({}/{})'.format(removed, current_tokens, budget_tokens),
        )
        self._refresh_context_label()

    def _on_compress_history(self):
        """方案 B：手动触发"压缩对话"——让 LLM 总结后替换早期消息。"""
        if self._is_running:
            QtWidgets.QMessageBox.information(
                self, '提示',
                '请等待当前对话轮完成后再压缩。',
            )
            return
        if len(self._conv) < 4:
            QtWidgets.QMessageBox.information(
                self, '提示',
                '当前对话较短，无需压缩。',
            )
            return
        # 二次确认（压缩不可逆）
        reply = QtWidgets.QMessageBox.question(
            self, '压缩对话',
            '将让 LLM 阅读并总结当前对话历史，然后用一段摘要替换早期消息，'
            '只保留最近 2 轮。\n\n'
            '✅ 节省 token、加速后续对话\n'
            '⚠️ 早期细节将不可恢复\n\n'
            '是否继续？',
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self._set_running(True)
        self.status_label.setText('正在生成历史摘要...')
        self._renderer.add_status('🗜 正在压缩对话历史，请稍候...')
        # 同步在后台线程跑摘要请求，避免冻结 UI
        from ..qt_compat import QtCore as _QtCore

        worker_holder = {'result': None, 'err': None}

        class _CompressThread(_QtCore.QThread):

            def __init__(self, parent_dock):
                super(_CompressThread, self).__init__(parent_dock)
                self._dock = parent_dock

            def run(self):
                try:
                    # 用一个临时的 AgentWorker 跑 compress（共享 conv/llm）
                    tmp = AgentWorker(
                        llm_client=self._dock._llm,
                        conversation=self._dock._conv,
                        dispatcher=self._dock._dispatcher,
                    )
                    worker_holder['result'] = tmp.compress_history(
                        keep_recent=2,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    worker_holder['err'] = str(exc)

        thr = _CompressThread(self)

        def _on_done():
            self._set_running(False)
            self.status_label.setText('准备就绪')
            if worker_holder['err']:
                self._renderer.add_error(
                    '压缩失败: {}'.format(worker_holder['err']),
                )
                return
            res = worker_holder['result'] or {}
            if not res.get('ok'):
                self._renderer.add_error(
                    '压缩失败: {}'.format(res.get('error') or '未知错误'),
                )
                return
            removed = res.get('removed', 0)
            self._renderer.add_status(
                '✅ 已压缩 {} 条早期消息为摘要。'.format(removed),
            )
            # 刷新视图：清空重放
            self._renderer.clear()
            self._pending_tool_blocks.clear()
            self._replay_messages(self._conv)
            self._refresh_context_label()
            self._save_current_session(force=True)

        thr.finished.connect(_on_done)
        thr.start()

    # ------------------------------------------------------------------ #
    # 槽：用户操作
    # ------------------------------------------------------------------ #
    def _on_profile_changed(self, _idx):
        name = self.profile_combo.currentText()
        if not name:
            return
        try:
            self._config.set_active_profile(name)
            self._llm = self._build_llm_client()
            self._renderer.add_status('已切换到 Profile: {}'.format(name))
            self._refresh_context_label()
        except Exception as exc:  # pylint: disable=broad-except
            self._renderer.add_error('切换 Profile 失败: {}'.format(exc))

    def _open_settings(self):
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self._config, parent=self)
        if dlg.exec_():
            self._llm = self._build_llm_client()
            self._refresh_profiles()
            self._renderer.add_status('设置已保存')
            self._refresh_context_label()

    def _clear_history(self):
        self._conv.clear()
        self._renderer.clear()
        self._pending_tool_blocks.clear()
        self._renderer.add_welcome(
            '对话已清空。点击下方任一示例快速开始：'
        )
        # 同步把空对话写回当前会话文件
        self._save_current_session(force=True)
        self._refresh_context_label()

    # ------------------------------------------------------------------ #
    # 会话管理（多对话）
    # ------------------------------------------------------------------ #
    def _refresh_sessions_combo(self, select_sid=None):
        """刷新会话下拉，可选择切到指定 sid。"""
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        target_idx = -1
        for i, m in enumerate(self._session_mgr.list_sessions()):
            label = '{}  ({}条)'.format(m.title or '未命名', m.message_count)
            self.session_combo.addItem(label, m.sid)
            if select_sid and m.sid == select_sid:
                target_idx = i
        if target_idx >= 0:
            self.session_combo.setCurrentIndex(target_idx)
        self.session_combo.blockSignals(False)

    def _bootstrap_session(self):
        """启动时恢复上次会话或新建一个。

        策略：UI 状态里记录了 last_session_sid 时优先恢复；否则取最近的；
        都没有就 create 一个新的。
        """
        last_sid = getattr(self._ui_state, 'last_session_sid', '') or ''
        sessions = self._session_mgr.list_sessions()
        target = None
        if last_sid:
            for m in sessions:
                if m.sid == last_sid:
                    target = m
                    break
        if target is None and sessions:
            target = sessions[0]
        if target is None:
            # 第一次启动：创建一个新的
            target = self._session_mgr.create_session()
        self._load_session(target.sid)
        self._refresh_sessions_combo(select_sid=target.sid)

    def _load_session(self, sid):
        # type: (str) -> bool
        """加载指定会话到当前面板，返回是否成功。"""
        result = self._session_mgr.load(sid)
        if result is None:
            return False
        meta, conv = result
        self._current_session = meta
        self._conv = conv
        self._pending_tool_blocks.clear()
        self._renderer.clear()
        # 方案 C：从磁盘恢复的会话注入"重启对齐"提示，
        # 让 LLM 知道场景可能已变。空会话不注入。
        try:
            if conv.messages and not conv.has_restored_marker():
                injected = conv.inject_restored_notice()
                if injected:
                    # 立刻持久化，避免下次启动重复注入
                    self._save_current_session(force=True)
        except Exception as exc:  # pylint: disable=broad-except
            print('[maxagent] inject_restored_notice 异常: {}'.format(exc))
        # 持久化最近一次会话 ID 到 ui_state
        try:
            self._ui_state.last_session_sid = sid
            self._ui_state_mgr.save(self._ui_state)
        except Exception:  # pylint: disable=broad-except
            pass
        # 回放历史消息
        if not conv.messages:
            self._renderer.add_welcome(
                '👋 你好，我是 <b style="color:#a8e6a8;">MaxAgent</b>。'
                '点击下方任一示例快速开始：'
            )
        else:
            self._replay_messages(conv)
        # 刷新 token 状态显示
        self._refresh_context_label()
        return True

    def _replay_messages(self, conv):
        """把 Conversation 里的消息按气泡形式重新渲染。

        工具调用按 (assistant tool_calls -> tool result) 配对展示，
        不再实际执行。
        """
        # 建索引: tool_call_id -> tool result message
        tool_results = {}
        for m in conv.messages:
            if m.role == 'tool' and m.tool_call_id:
                tool_results[m.tool_call_id] = m

        for m in conv.messages:
            if m.role == 'user':
                if m.content:
                    self._renderer.add_user(m.content)
            elif m.role == 'assistant':
                if m.content:
                    # 直接渲染最终版（不走流式）
                    self._renderer._close_streaming_if_any()  # noqa: SLF001
                    bubble = _AssistantBubble(m.content)
                    self._renderer._append(bubble)  # noqa: SLF001
                if m.tool_calls:
                    for tc in m.tool_calls:
                        try:
                            fn = tc.get('function') or {}
                            name = fn.get('name', '')
                            args_str = fn.get('arguments', '{}')
                            call_id = tc.get('id', '')
                        except AttributeError:
                            continue
                        from ..tools.registry import get_tool
                        spec = get_tool(name)
                        dangerous = bool(spec and spec.dangerous)
                        block = self._renderer.add_tool_call(
                            name, args_str, dangerous=dangerous,
                        )
                        # 回填结果
                        rmsg = tool_results.get(call_id)
                        if rmsg is not None:
                            ok = True
                            try:
                                rj = json.loads(rmsg.content or '{}')
                                ok = bool(rj.get('ok', True))
                            except (TypeError, ValueError):
                                ok = True
                            block.set_result(ok, rmsg.content or '')
            elif m.role == 'system':
                # 中途的 system note，不展示给用户（避免污染观感）
                continue

    def _save_current_session(self, force=False):
        """保存当前会话到磁盘并刷新下拉。"""
        if self._current_session is None:
            return
        # 没消息时也允许保存（force=True），用于清空后立即落盘
        if not force and len(self._conv) == 0:
            return
        try:
            self._session_mgr.save(self._current_session, self._conv)
        except OSError as exc:
            print('[maxagent] 保存会话失败: {}'.format(exc))
            return
        # 刷新下拉但保持当前选中
        self._refresh_sessions_combo(select_sid=self._current_session.sid)

    def _on_new_session(self):
        if self._is_running:
            self._renderer.add_status('请先停止当前对话再新建会话')
            return
        # 先把当前会话存盘
        self._save_current_session()
        meta = self._session_mgr.create_session()
        self._load_session(meta.sid)
        self._refresh_sessions_combo(select_sid=meta.sid)

    def _on_session_combo_changed(self, idx):
        if idx < 0 or self._is_running:
            return
        sid = self.session_combo.itemData(idx)
        if not sid or (self._current_session
                       and sid == self._current_session.sid):
            return
        # 切换前保存
        self._save_current_session()
        if not self._load_session(sid):
            self._renderer.add_error('加载会话失败: {}'.format(sid))

    def _on_rename_session(self):
        if self._current_session is None:
            return
        old = self._current_session.title
        new, ok = QtWidgets.QInputDialog.getText(
            self, '重命名会话', '新标题:',
            QtWidgets.QLineEdit.EchoMode.Normal, old,
        )
        if not ok:
            return
        new = (new or '').strip()
        if not new or new == old:
            return
        if self._session_mgr.rename(self._current_session.sid, new):
            self._current_session.title = new
            self._refresh_sessions_combo(
                select_sid=self._current_session.sid,
            )

    def _on_delete_session(self):
        if self._current_session is None:
            return
        if self._is_running:
            self._renderer.add_status('请先停止当前对话再删除会话')
            return
        ret = QtWidgets.QMessageBox.question(
            self, '删除会话',
            '确定要删除会话「{}」吗？此操作不可恢复。'.format(
                self._current_session.title,
            ),
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if ret != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        sid = self._current_session.sid
        self._session_mgr.delete(sid)
        # 切到下一个会话（或新建一个）
        sessions = self._session_mgr.list_sessions()
        if sessions:
            self._load_session(sessions[0].sid)
            self._refresh_sessions_combo(select_sid=sessions[0].sid)
        else:
            meta = self._session_mgr.create_session()
            self._load_session(meta.sid)
            self._refresh_sessions_combo(select_sid=meta.sid)

    # ------------------------------------------------------------------ #
    # 工具学习（自进化）
    # ------------------------------------------------------------------ #
    def _install_learn_approval_callback(self):
        """把审批回调挂到 learn_tools 模块。

        learn_tools 的 propose_new_tool 工具在主线程执行（已配置
        run_on_main_thread=True），所以这个 callback 会在主线程被调用，
        弹窗 exec_ 不会阻塞 Qt 事件循环失败。
        """
        try:
            from .learn_approval_dialog import make_approval_callback
            from ..tools.learn_tools import set_approval_callback
            set_approval_callback(make_approval_callback(parent_widget=self))
        except Exception as exc:  # pylint: disable=broad-except
            print('[maxagent] 注册学习审批回调失败: {}'.format(exc))

    def _on_example_picked(self, text):
        # 把示例文本填入输入框，让用户可以编辑后再发
        self.input_edit.setPlainText(text)
        self.input_edit.setFocus()

    def _on_send(self):
        if self._is_running:
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        self.input_edit.clear()
        self._renderer.add_user(text)
        self._renderer.add_assistant_start()
        self._set_running(True)

        self._worker = AgentWorker(
            llm_client=self._llm,
            conversation=self._conv,
            dispatcher=self._dispatcher,
            max_tool_loops=self._get_active_max_loops(),
            max_history_tokens=self._get_active_max_history_tokens(),
        )
        self._worker.set_sync_tool_runner(self._run_tool_sync)
        self._worker.set_system_prompt_addon_provider(
            self._skill_mgr.build_system_prompt_addon,
        )
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.tool_started.connect(self._on_tool_started)
        self._worker.tool_finished.connect(self._on_tool_finished)
        self._worker.text_message_complete.connect(self._on_text_complete)
        self._worker.status_changed.connect(self._on_status)
        self._worker.history_trimmed.connect(self._on_history_trimmed)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.run_in_thread(text)

    def _on_stop(self):
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText('正在停止...')

    def _set_running(self, running):
        self._is_running = bool(running)
        self.send_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.profile_combo.setEnabled(not running)
        self.settings_btn.setEnabled(not running)

    # ------------------------------------------------------------------ #
    # 槽：worker 信号
    # ------------------------------------------------------------------ #
    def _on_chunk(self, chunk):
        self._renderer.add_assistant_chunk(chunk)

    def _on_text_complete(self, _text):
        self._renderer.end_turn()

    def _on_tool_started(self, name, args_str, call_id):
        from ..tools.registry import get_tool
        spec = get_tool(name)
        dangerous = bool(spec and spec.dangerous)
        block = self._renderer.add_tool_call(
            name, args_str, dangerous=dangerous,
        )
        if call_id:
            self._pending_tool_blocks[call_id] = block

    def _on_tool_finished(self, name, ok, result_str, call_id):
        # 从映射里取出对应 block 并填入结果（不再新增 widget）
        block = self._pending_tool_blocks.pop(call_id, None)
        if block is not None:
            block.set_result(ok, result_str)
        else:
            # 兜底：找不到 block 时，作为独立条目展示
            self._renderer.add_status(
                '工具 {} 完成: {}'.format(name, 'ok' if ok else 'fail')
            )
        # 工具结束后开个新气泡待 LLM 继续说话
        self._renderer.add_assistant_start()

    def _on_status(self, text):
        self.status_label.setText(text)

    def _on_finished(self):
        self._renderer.end_turn()
        self.status_label.setText('完成')
        self._set_running(False)
        self._save_current_session()
        self._refresh_context_label()

    def _on_failed(self, err):
        self._renderer.add_error(err)
        self.status_label.setText('失败')
        self._set_running(False)
        # 失败也保存：用户能在历史里看到失败原因
        self._save_current_session()
        self._refresh_context_label()

    # ------------------------------------------------------------------ #
    # 主线程同步工具执行
    # ------------------------------------------------------------------ #
    def _run_tool_sync(self, tool_name, arguments):
        """Worker 子线程通过此函数同步派回主线程执行 pymxs。"""
        result_box = {}
        done = threading.Event()

        def _run_in_main():
            try:
                result_box['value'] = self._dispatcher.dispatch(
                    tool_name, arguments,
                )
            except Exception as exc:  # pylint: disable=broad-except
                result_box['error'] = exc
            finally:
                done.set()

        cur_thread = QtCore.QThread.currentThread()
        app = QApplication.instance()
        main_thread = app.thread() if app is not None else None
        if main_thread is None or cur_thread is main_thread:
            _run_in_main()
        else:
            QtCore.QTimer.singleShot(0, _run_in_main)
            done.wait(timeout=300.0)
            if not done.is_set():
                raise RuntimeError(
                    '工具 {} 在主线程执行超时(300s)'.format(tool_name),
                )

        if 'error' in result_box:
            raise result_box['error']
        return result_box.get('value')


# ---------------------------------------------------------------------- #
# 输入框：Enter 发送 / Shift+Enter 换行
# ---------------------------------------------------------------------- #
class _SmartInput(QtWidgets.QPlainTextEdit):
    """支持 Enter 发送、Shift+Enter 换行、Ctrl+Enter 发送的输入框。"""

    send_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super(_SmartInput, self).__init__(parent)

    def keyPressEvent(self, event):
        key = event.key()
        is_enter = key in (
            QtCore.Qt.Key.Key_Return,
            QtCore.Qt.Key.Key_Enter,
        )
        if is_enter:
            shift = self._has_shift(event)
            if shift:
                # 换行
                super(_SmartInput, self).keyPressEvent(event)
                return
            # Enter / Ctrl+Enter / Cmd+Enter -> 发送
            self.send_requested.emit()
            return
        super(_SmartInput, self).keyPressEvent(event)

    @staticmethod
    def _has_shift(event):
        """跨 PySide2/6 兼容地判断 Shift 是否按下。

        - PySide6: ``mods & ShiftModifier`` 返回枚举可直接 bool
        - PySide2: 必须先 int() 再做位运算，否则会抛 SystemError
        """
        mods = event.modifiers()
        shift_flag = QtCore.Qt.KeyboardModifier.ShiftModifier
        # 优先走 PySide2 风格（先 int 再位运算），最稳
        try:
            return bool(int(mods) & int(shift_flag))
        except (TypeError, ValueError):
            pass
        # PySide6: 直接位运算
        try:
            return bool(mods & shift_flag)
        except (TypeError, SystemError):
            return False
