#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聊天气泡组件集合。

本模块从 ``dock_widget`` 中拆分出来，专职负责一条条独立消息的视觉
表达——气泡容器、富文本标签、用户/助手/状态/错误/欢迎气泡等。
``MaxAgentDockWidget`` 只负责把这些组件按时间顺序追加到滚动区里。

约定：
- 所有气泡均为独立 ``QWidget``，自带左右对齐控制（用户右、助手左、
  状态居中），调用方只需 ``layout.addWidget(bubble)`` 即可。
- 气泡内部文本统一走 ``markdown_render`` 渲染并做 XSS 转义。
"""

from __future__ import absolute_import
from __future__ import print_function

from ..qt_compat import QtCore
from ..qt_compat import QtWidgets
from .markdown_render import extract_code_blocks
from .markdown_render import html_escape
from .markdown_render import render_markdown


QApplication = QtWidgets.QApplication


def _mini_btn_style():
    """所有"复制"等小辅助按钮统一用这个样式。"""
    return (
        'QPushButton { background:transparent; color:#888;'
        'border:1px solid #444; border-radius:3px;'
        'padding:1px 6px; min-height:18px; font-size:9pt; }'
        'QPushButton:hover { background:#333; color:#ddd; }'
    )


def _copy_to_clipboard(text):
    """把指定文本写入系统剪贴板。"""
    cb = QApplication.clipboard()
    cb.setText(text)


# ---------------------------------------------------------------------- #
# 基础组件
# ---------------------------------------------------------------------- #
class BubbleFrame(QtWidgets.QFrame):
    """单条消息的气泡容器（一个 QFrame，内含 layout）。

    通过外部 hbox 控制左右对齐：layout 里加 stretch 推到一边。
    """

    def __init__(self, align='left', bg='#2d3d2d', fg='#d4ead4',
                 parent=None):
        super(BubbleFrame, self).__init__(parent)
        self._align = align
        self._bg = bg
        self._fg = fg
        self.setStyleSheet(
            'QFrame {{ background:{bg}; color:{fg};'
            'border-radius:10px; padding:0; }}'.format(bg=bg, fg=fg)
        )
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


class ChatLabel(QtWidgets.QLabel):
    """气泡内的富文本标签，自动换行 + 可选中复制。"""

    def __init__(self, text='', parent=None):
        super(ChatLabel, self).__init__(parent)
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
class StreamingAssistantBubble(QtWidgets.QWidget):
    """正在流式接收的助手气泡。

    流式过程中显示 plain text（避免 markdown 半截解析的闪烁），
    end_streaming() 时一次性切换到 markdown 渲染的 HTML。
    """

    def __init__(self, parent=None):
        super(StreamingAssistantBubble, self).__init__(parent)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._bubble = BubbleFrame(
            align='left', bg='#2d3d2d', fg='#d4ead4',
        )
        head = QtWidgets.QLabel(
            '<span style="color:#a8e6a8;font-size:9pt;">🤖 助手</span>'
        )
        head.setStyleSheet('background:transparent;')
        self._bubble.add_widget(head)
        self._label = ChatLabel('')
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
        body = html_escape(self._buffer).replace('\n', '<br>')
        self._label.setText(body)

    def end_streaming(self):
        # type: () -> str
        """流式结束，返回最终 buffer。调用方负责把这个 bubble 替换为
        最终的 markdown 渲染版本。"""
        self._closed = True
        return self._buffer

    def is_empty(self):
        return not self._buffer.strip()


# ---------------------------------------------------------------------- #
# 最终助手气泡（markdown 渲染 + 复制按钮）
# ---------------------------------------------------------------------- #
class AssistantBubble(QtWidgets.QWidget):
    """已完成的助手回复气泡，渲染 markdown，并附带复制按钮。"""

    def __init__(self, text, parent=None):
        super(AssistantBubble, self).__init__(parent)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        bubble = BubbleFrame(align='left', bg='#2d3d2d', fg='#d4ead4')

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
        copy_btn.setStyleSheet(_mini_btn_style())
        copy_btn.clicked.connect(lambda: _copy_to_clipboard(text))
        title_row.addWidget(copy_btn)

        # 如果包含代码块，加"复制代码"按钮
        code_blocks = extract_code_blocks(text)
        if len(code_blocks) == 1:
            code_btn = QtWidgets.QPushButton('复制代码')
            code_btn.setStyleSheet(_mini_btn_style())
            code_btn.clicked.connect(
                lambda: _copy_to_clipboard(code_blocks[0][1])
            )
            title_row.addWidget(code_btn)
        elif len(code_blocks) > 1:
            for idx, (_lang, _code) in enumerate(code_blocks):
                btn = QtWidgets.QPushButton('代码{}'.format(idx + 1))
                btn.setStyleSheet(_mini_btn_style())
                btn.clicked.connect(
                    lambda _checked=False, c=_code: _copy_to_clipboard(c)
                )
                title_row.addWidget(btn)

        bubble.add_layout(title_row)

        # 正文：markdown 渲染
        body = render_markdown(text)
        label = ChatLabel(body)
        bubble.add_widget(label)

        outer.addWidget(bubble, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        outer.addStretch(1)


# ---------------------------------------------------------------------- #
# 用户气泡（靠右）
# ---------------------------------------------------------------------- #
class UserBubble(QtWidgets.QWidget):
    """用户消息气泡，蓝色，右对齐。"""

    def __init__(self, text, parent=None):
        super(UserBubble, self).__init__(parent)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)

        bubble = BubbleFrame(align='right', bg='#2c5d8f', fg='#ffffff')
        head = QtWidgets.QLabel(
            '<span style="color:#bbd9f5;font-size:9pt;">👤 你</span>'
        )
        head.setStyleSheet('background:transparent; color:#bbd9f5;')
        bubble.add_widget(head)

        body = html_escape(text).replace('\n', '<br>')
        label = ChatLabel(
            '<span style="color:#ffffff;line-height:1.5;">'
            + body + '</span>'
        )
        bubble.add_widget(label)
        outer.addWidget(bubble, 0, QtCore.Qt.AlignmentFlag.AlignRight)


# ---------------------------------------------------------------------- #
# 状态/错误/欢迎 等辅助气泡
# ---------------------------------------------------------------------- #
class StatusLine(QtWidgets.QWidget):
    """居中的灰色状态行，用于显示"思考中…""已切换 Profile"等。"""

    def __init__(self, text, parent=None):
        super(StatusLine, self).__init__(parent)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 2, 0, 2)
        lbl = QtWidgets.QLabel(
            '<span style="color:#888;font-style:italic;font-size:10pt;">'
            '⋯ {}</span>'.format(html_escape(text))
        )
        lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet('background:transparent;')
        h.addWidget(lbl, 1)


class ErrorBubble(QtWidgets.QWidget):
    """红色错误气泡。"""

    def __init__(self, text, parent=None):
        super(ErrorBubble, self).__init__(parent)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        bubble = BubbleFrame(align='left', bg='#4a2a2a', fg='#ffaaaa')
        head = QtWidgets.QLabel(
            '<b style="color:#ffaaaa;">⚠ 错误</b>'
        )
        head.setStyleSheet('background:transparent;')
        bubble.add_widget(head)
        body = html_escape(text).replace('\n', '<br>')
        label = ChatLabel(
            '<span style="color:#ffaaaa;font-size:10pt;">'
            + body + '</span>'
        )
        bubble.add_widget(label)
        outer.addWidget(bubble, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        outer.addStretch(1)


class WelcomeBlock(QtWidgets.QWidget):
    """欢迎块。可点击的示例按钮会触发 ``example_picked`` 信号。"""

    example_picked = QtCore.Signal(str)

    _EXAMPLES = (
        '创建一个红色的茶壶并加上 TurboSmooth 修改器',
        '列出场景里所有的灯光，按强度排序',
        '把所有 Box001 重命名为 wall_xx 序列',
    )

    def __init__(self, html_body, parent=None):
        super(WelcomeBlock, self).__init__(parent)
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
            btn.clicked.connect(
                lambda _checked=False, t=ex: self.example_picked.emit(t)
            )
            v.addWidget(btn)
