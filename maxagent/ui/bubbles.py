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
        'QPushButton { background:transparent; color:#888; }'
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
            'QFrame {{ background:{bg}; color:{fg}; }}'.format(bg=bg, fg=fg)
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
    """气泡内的富文本标签，自动换行 + 可选中复制。

    重写 ``contextMenuEvent`` 提供中文右键菜单，菜单项包括：

    - 复制（仅当有选中文本时启用）
    - 复制全部
    - 复制为纯文本（去除 HTML 标签）
    - 全选
    - 打开链接 / 复制链接地址（仅当鼠标位置上有链接时）

    设计注意：QLabel 自身没有 ``copy()`` API，"复制选中"通过把选中区
    rich text 写到剪贴板的方式实现；剪贴板拿到 plain text 也能用。
    """

    def __init__(self, text='', parent=None):
        super(ChatLabel, self).__init__(parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.setOpenExternalLinks(True)
        self.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.setStyleSheet('background:transparent;')
        # 中文右键菜单：用 CustomContextMenu 自己处理，避免 QLabel
        # 默认菜单的英文项（Copy / Select All / ...）混入。
        self.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu
        )
        # noinspection PyUnresolvedReferences
        self.customContextMenuRequested.connect(self._on_context_menu)
        if text:
            self.setText(text)

    def _plain_text(self):
        """把当前富文本转为纯文本（去 HTML 标签）。"""
        # QLabel.text() 在 RichText 模式下返回原始 HTML，需要剥标签。
        # QTextDocumentFragment 是 Qt 自带的最稳健方案。
        try:
            from ..qt_compat import QtGui
            doc = QtGui.QTextDocument()
            doc.setHtml(self.text())
            return doc.toPlainText()
        except Exception:  # pylint: disable=broad-except
            # 兜底：用极简正则去标签
            import re
            return re.sub(r'<[^>]+>', '', self.text())

    def _on_context_menu(self, pos):
        """在 pos（QPoint，相对本控件坐标）位置弹出中文菜单。"""
        menu = QtWidgets.QMenu(self)

        has_selection = bool(self.selectedText())

        # 复制选中
        act_copy = menu.addAction('复制')
        act_copy.setEnabled(has_selection)
        act_copy.triggered.connect(self._copy_selection)

        # 复制全部（HTML 渲染内容的纯文本版本）
        act_copy_all = menu.addAction('复制全部')
        act_copy_all.triggered.connect(
            lambda: _copy_to_clipboard(self._plain_text())
        )

        menu.addSeparator()

        # 全选
        act_select_all = menu.addAction('全选')
        act_select_all.triggered.connect(self._select_all)

        # 链接相关：只有鼠标位置上确实有链接时才显示
        link_url = ''
        try:
            # QLabel 提供了 ChildAtPointF；不同 PySide 版本差异较大，
            # 用最通用的 self.linkAt 兜底（PySide2 5.12+ / PySide6 都有）
            if hasattr(self, 'linkAt'):
                link_url = self.linkAt(pos) or ''
        except Exception:  # pylint: disable=broad-except
            link_url = ''
        if link_url:
            menu.addSeparator()
            act_open = menu.addAction('在浏览器中打开链接')
            act_open.triggered.connect(
                lambda u=link_url: self._open_link(u)
            )
            act_copy_link = menu.addAction('复制链接地址')
            act_copy_link.triggered.connect(
                lambda u=link_url: _copy_to_clipboard(u)
            )

        menu.exec_(self.mapToGlobal(pos))

    def _copy_selection(self):
        """把当前选中文本写入剪贴板（QLabel 没有 copy() 方法）。"""
        sel = self.selectedText()
        if sel:
            # QLabel.selectedText() 在 RichText 模式下返回的是带 0x2028
            # 行分隔符的纯文本，统一替换成普通 \n 再丢剪贴板。
            sel = sel.replace('\u2028', '\n').replace('\u2029', '\n')
            _copy_to_clipboard(sel)

    def _select_all(self):
        """全选标签内全部可见文本。"""
        # QLabel 没有 selectAll API，需自己用 cursor 模拟。
        # 但 PySide2/6 都允许通过 setSelection 实现：
        #   setSelection(start, length)
        # 然而 RichText 模式的字符索引对应"渲染后"位置，无法精确取到
        # 末位；用 plain text 长度作为最大值即可（多出来的会被自动钳制）。
        plain = self._plain_text()
        if plain:
            try:
                self.setSelection(0, len(plain))
            except Exception:  # pylint: disable=broad-except
                pass

    @staticmethod
    def _open_link(url):
        """用系统默认浏览器打开 URL。"""
        try:
            from ..qt_compat import QtGui
            from ..qt_compat import QtCore as _QC
            QtGui.QDesktopServices.openUrl(_QC.QUrl(url))
        except Exception:  # pylint: disable=broad-except
            pass


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
            btn = QtWidgets.QPushButton(ex)
            btn.setStyleSheet(
                'QPushButton { background:#252525; color:#a0a0a0; }'
                'QPushButton:hover { background:#2d3d2d; color:#ddd; }'
            )
            btn.clicked.connect(
                lambda _checked=False, t=ex: self.example_picked.emit(t)
            )
            v.addWidget(btn)
