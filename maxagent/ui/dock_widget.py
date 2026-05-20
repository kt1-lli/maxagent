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
from ..tools import ToolDispatcher
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

        # 头部按钮
        self._head_btn = QtWidgets.QToolButton()
        self._head_btn.setCheckable(True)
        self._head_btn.setChecked(False)
        self._head_btn.setText(self._head_text(running=True))
        self._head_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._head_btn.clicked.connect(self._toggle)
        cv.addWidget(self._head_btn)

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

    def _head_text(self, running=False):
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
        arrow = '▼' if self._head_btn and self._head_btn.isChecked() else '▶'
        return (
            '{arrow} {icon} <b>{name}</b>  '
            '<span style="color:{color};">{sym}</span>'
        ).format(
            arrow=arrow, icon=icon, name=self._name,
            color=color, sym=sym,
        )

    def _toggle(self):
        expanded = self._head_btn.isChecked()
        self._detail.setVisible(expanded)
        # 刷新箭头
        self._head_btn.setText(self._head_text(
            running=(self._result_ok is None)
        ))

    def set_result(self, ok, result_str):
        # type: (bool, str) -> None
        self._result_ok = bool(ok)
        self._result_text = result_str or ''
        # 刷新头部
        self._head_btn.setText(self._head_text(running=False))
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
        self._llm = self._build_llm_client()
        self._conv = Conversation()
        self._dispatcher = ToolDispatcher()
        # type: Optional[AgentWorker]
        self._worker = None
        self._is_running = False
        # 当前正在执行的工具块映射: call_id -> _ToolCallBlock
        self._pending_tool_blocks = {}

        self._build_ui()
        self._refresh_profiles()

    # ------------------------------------------------------------------ #
    # 构建 UI
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        # === 顶部条 ===
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
        self.clear_btn = QtWidgets.QPushButton('🗑 清空')
        self.clear_btn.clicked.connect(self._clear_history)
        top.addWidget(self.clear_btn)
        outer.addLayout(top)

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

        # === 欢迎语 ===
        self._renderer.add_welcome(
            '👋 你好，我是 <b style="color:#a8e6a8;">MaxAgent</b>。'
            '点击下方任一示例快速开始：'
        )

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
        except Exception as exc:  # pylint: disable=broad-except
            self._renderer.add_error('切换 Profile 失败: {}'.format(exc))

    def _open_settings(self):
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self._config, parent=self)
        if dlg.exec_():
            self._llm = self._build_llm_client()
            self._refresh_profiles()
            self._renderer.add_status('设置已保存')

    def _clear_history(self):
        self._conv.clear()
        self._renderer.clear()
        self._pending_tool_blocks.clear()
        self._renderer.add_welcome(
            '对话已清空。点击下方任一示例快速开始：'
        )

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
        )
        self._worker.set_sync_tool_runner(self._run_tool_sync)
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.tool_started.connect(self._on_tool_started)
        self._worker.tool_finished.connect(self._on_tool_finished)
        self._worker.text_message_complete.connect(self._on_text_complete)
        self._worker.status_changed.connect(self._on_status)
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

    def _on_failed(self, err):
        self._renderer.add_error(err)
        self.status_label.setText('失败')
        self._set_running(False)

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
