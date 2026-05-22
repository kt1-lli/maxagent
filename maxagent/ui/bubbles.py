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
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets
from .emoji_compat import ee as _ee
from .markdown_render import extract_code_blocks
from .markdown_render import html_escape
from .markdown_render import render_markdown
from .markdown_render import split_into_segments


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

    宽度策略：
    - sizePolicy 用 ``Maximum`` 让气泡按内容收缩；
    - 通过 ``apply_max_width`` 在外层（``_ChatRenderer``）拿到滚动区
      可用宽度后下发，限制气泡不会撑满到整个滚动区——这样一条很长的
      单行消息也能在 75~85% 视宽处自动换行，而不是顶到右边缘。
    - 重绘时 ``ChatLabel.setWordWrap(True)`` 配合 ``maximumWidth``，
      让 QLabel 自己处理软换行；代码块用 ``QPlainTextEdit`` 的横向
      滚动条，不被强制换行破坏格式。
    """

    # 占用滚动区可视宽度的比例上限：85% 为偏宽松、阅读舒适
    _WIDTH_RATIO = 0.85
    # 绝对下限：避免极窄面板下气泡缩成竖条
    _MIN_BUBBLE_WIDTH = 200

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

    def apply_max_width(self, viewport_width):
        # type: (int) -> None
        """根据外层滚动区可视宽度，按比例设置气泡最大宽度。

        - viewport_width <= 0 时忽略（视图还未布局完成）
        - 计算结果会与 ``_MIN_BUBBLE_WIDTH`` 取大，防止极窄面板
          下气泡退化为竖条
        """
        try:
            vw = int(viewport_width)
        except (TypeError, ValueError):
            return
        if vw <= 0:
            return
        target = int(vw * self._WIDTH_RATIO)
        if target < self._MIN_BUBBLE_WIDTH:
            target = self._MIN_BUBBLE_WIDTH
        if self.maximumWidth() != target:
            self.setMaximumWidth(target)

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
# 代码块独立 widget（问题 3）
# 让代码与正文分离，方便用户精确选中、整块复制、避免被 RichText 噪音干扰
# ---------------------------------------------------------------------- #
class _CodeBlockWidget(QtWidgets.QWidget):
    """单个代码块的独立组件。

    设计：
    - 顶部条：语言标签 + 复制按钮
    - 主体：QPlainTextEdit 只读模式，等宽字体，纯文本（无 HTML 噪音），
      用户三击全选、Ctrl+C 复制都按代码编辑器习惯响应
    - 自动撑高：根据行数计算高度，不出滚动条（除非超过上限）

    与原 <pre> 内嵌方案对比：
    - 原方案：整段被当作 RichText 渲染，选中代码会带上前后正文文本
    - 现方案：QPlainTextEdit 是独立焦点单元，只能选中代码本身
    """

    # 单个代码块在气泡内最大显示高度（超出走滚动条），单位像素
    _MAX_HEIGHT = 360

    def __init__(self, lang, code, parent=None):
        # type: (str, str, QtWidgets.QWidget) -> None
        super(_CodeBlockWidget, self).__init__(parent)
        self._code = code or ''

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(0)

        # 顶部条：语言名称 + 复制按钮
        head = QtWidgets.QWidget(self)
        head.setStyleSheet(
            'QWidget { background:#252525; }'
        )
        head_h = QtWidgets.QHBoxLayout(head)
        head_h.setContentsMargins(8, 2, 4, 2)
        head_h.setSpacing(6)

        lang_label = (lang or '').strip() or 'code'
        lbl = QtWidgets.QLabel('⌨ {}'.format(lang_label))
        lbl.setStyleSheet(
            'QLabel { color:#888; background:transparent; }'
        )
        head_h.addWidget(lbl)
        head_h.addStretch(1)

        copy_btn = QtWidgets.QPushButton('⎘ 复制代码')
        copy_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet(_mini_btn_style())
        # 用 default arg 锁定当前 code，避免 lambda 闭包陷阱
        copy_btn.clicked.connect(
            lambda _checked=False, c=self._code: _copy_to_clipboard(c)
        )
        head_h.addWidget(copy_btn)

        outer.addWidget(head)

        # 主体：QPlainTextEdit 只读纯文本
        editor = QtWidgets.QPlainTextEdit(self)
        editor.setReadOnly(True)
        editor.setPlainText(self._code)
        editor.setLineWrapMode(
            QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap
        )
        # 等宽字体；不同平台 fallback
        font = QtGui.QFont('Consolas')
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        font.setPointSize(10)
        editor.setFont(font)
        editor.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        editor.setStyleSheet(
            'QPlainTextEdit { background:#1a1a1a; color:#e0e0e0; }'
        )
        editor.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        editor.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        # 自适应高度：行数 * 行高 + padding，超过 _MAX_HEIGHT 出滚动条
        self._auto_resize_editor(editor)
        outer.addWidget(editor)
        self._editor = editor

    def _auto_resize_editor(self, editor):
        """根据代码行数估算一个合理高度，避免每个代码块占满屏。"""
        line_count = max(1, self._code.count('\n') + 1)
        fm = QtGui.QFontMetrics(editor.font())
        line_h = fm.lineSpacing()
        # 代码 padding 上下 6 + 6，再加 4 像素余量
        total = line_count * line_h + 16
        if total > self._MAX_HEIGHT:
            total = self._MAX_HEIGHT
        editor.setFixedHeight(total)


# ---------------------------------------------------------------------- #
# 流式助手气泡：chunk 增量 append，结束后 markdown 重渲染
# ---------------------------------------------------------------------- #
class StreamingAssistantBubble(QtWidgets.QWidget):
    """正在流式接收的助手气泡。

    性能要点（问题 1 根因修复）：
    - 旧版用 QLabel + 每次 setText(整个 buffer)，是 O(N²) 的：
      回复越长，每个 chunk 重排版越久，主线程被持续打断 → 卡顿。
    - 新版用 QPlainTextEdit.appendPlainText，每个 chunk 只追加新增
      字符（O(chunk_len)），不再重渲染历史文本。
    - 流式期间不做 markdown 解析，end_streaming() 时上层会用
      _AssistantBubble 重渲染，那时才做一次性的 markdown / 代码块拆分。
    """

    # 自适应高度上限：超过则出滚动条；保证单次 LLM 长回复不撑爆面板
    _MAX_HEIGHT = 480

    def __init__(self, parent=None, employee=None):
        super(StreamingAssistantBubble, self).__init__(parent)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._bubble = BubbleFrame(
            align='left', bg='#2d3d2d', fg='#d4ead4',
        )
        # 助手头部：员工档案（名字 + 头像）。employee=None 时用默认值，
        # 实现"换皮"——LLM 行为完全不动，只改 UI 视觉。
        from .employee import Employee as _Employee
        emp = employee if employee is not None else _Employee()
        head = QtWidgets.QLabel(emp.display_html())
        # PySide6 的 QLabel 在 AutoText 模式下对包含 ``<img src="file:///...">``
        # 的字符串识别不稳定（PySide2 默认会判为 RichText，PySide6 会判
        # 为 PlainText，导致自定义图片头像不显示）。这里强制 RichText。
        head.setTextFormat(QtCore.Qt.TextFormat.RichText)
        head.setStyleSheet('background:transparent;')
        self._bubble.add_widget(head)

        # 用 QPlainTextEdit 替代 QLabel：appendPlainText 是 O(chunk)
        self._editor = QtWidgets.QPlainTextEdit()
        self._editor.setReadOnly(True)
        self._editor.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._editor.setStyleSheet(
            'QPlainTextEdit { background:transparent; color:#d4ead4; }'
        )
        self._editor.setLineWrapMode(
            QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth
        )
        self._editor.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._editor.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        # 初始高度先小一点，等内容多了再 _grow_height
        self._editor.setFixedHeight(28)
        self._bubble.add_widget(self._editor)

        outer.addWidget(self._bubble, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        outer.addStretch(1)
        self._buffer = ''
        self._closed = False
        # 已应用的行数；只在行数发生变化时才 setFixedHeight，
        # 避免每个 chunk 都触发外层 QScrollArea 重新 layout（卡顿主因）
        self._last_lines = 1
        # 60Hz 节流定时器：合并多个 chunk 内的高度调整请求
        self._height_timer = QtCore.QTimer()
        self._height_timer.setSingleShot(True)
        self._height_timer.setInterval(33)  # 约 30Hz，足够流畅
        self._height_timer.timeout.connect(self._grow_height)

    def append_chunk(self, chunk):
        # type: (str) -> None
        """O(chunk_len) 增量追加，不在主线程做 layout 计算。

        关键优化：
        - 高度调整通过 33ms 定时器节流，不每 chunk 一次
        - 用行数估算高度，不调用 documentLayout().documentSize()
          （后者会强制同步 layout，是卡顿主因）
        - ensureCursorVisible 让 editor 内部处理光标跟随，
          外层不需要每 chunk 滚一次
        """
        if not chunk or self._closed:
            return
        self._buffer += chunk
        # insertText 是 O(chunk_len)：只在末尾追加，不重排版历史文本
        cursor = self._editor.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.insertText(chunk)
        self._editor.setTextCursor(cursor)
        # 让 editor 自己保证光标可见（不影响外层滚动条）
        self._editor.ensureCursorVisible()
        # 节流触发高度调整
        if not self._height_timer.isActive():
            self._height_timer.start()

    def _grow_height(self):
        """根据行数估算 editor 高度。

        用行数 * 行高估算，避免触发 documentLayout 同步 layout。
        只在行数变化时才 setFixedHeight，最大限度减少外层 relayout。
        """
        # 用 newline 数量估算（不强求精确，估高一点也没关系）
        line_count = max(1, self._buffer.count('\n') + 1)
        if line_count == self._last_lines:
            return
        self._last_lines = line_count
        fm = QtGui.QFontMetrics(self._editor.font())
        h = line_count * fm.lineSpacing() + 16
        if h > self._MAX_HEIGHT:
            h = self._MAX_HEIGHT
        if h < 28:
            h = 28
        if self._editor.height() != h:
            self._editor.setFixedHeight(h)

    def end_streaming(self):
        # type: () -> str
        """流式结束，返回最终 buffer。调用方负责把这个 bubble 替换为
        最终的 markdown 渲染版本。"""
        self._closed = True
        # 收尾时强制刷一次高度
        if self._height_timer.isActive():
            self._height_timer.stop()
        self._grow_height()
        return self._buffer

    def is_empty(self):
        return not self._buffer.strip()

    def apply_max_width(self, viewport_width):
        # type: (int) -> None
        """转发到内部 ``BubbleFrame``，由其按比例限制最大宽度。"""
        if self._bubble is not None:
            self._bubble.apply_max_width(viewport_width)


# ---------------------------------------------------------------------- #
# 最终助手气泡（markdown 渲染 + 复制按钮）
# ---------------------------------------------------------------------- #
class AssistantBubble(QtWidgets.QWidget):
    """已完成的助手回复气泡，渲染 markdown，并附带复制按钮。"""

    def __init__(self, text, parent=None, employee=None):
        super(AssistantBubble, self).__init__(parent)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        bubble = BubbleFrame(align='left', bg='#2d3d2d', fg='#d4ead4')
        self._bubble = bubble

        # 标题行：[头像 员工名]  [复制全部]
        # 注：代码块的复制按钮挂在每个 _CodeBlockWidget 自己头上，
        # 这里只保留"复制全部回复"一个全局按钮，避免标题栏被一堆
        # "代码1/代码2/..." 按钮挤爆。
        title_row = QtWidgets.QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        # 助手头部：员工档案（名字 + 头像）。employee=None 时用默认值，
        # 实现"换皮"——LLM 行为完全不动，只改 UI 视觉。
        from .employee import Employee as _Employee
        emp = employee if employee is not None else _Employee()
        head = QtWidgets.QLabel(emp.display_html())
        # 同 StreamingAssistantBubble：强制 RichText 让 PySide6 也能渲染
        # ``<img>`` 标签的自定义图片头像。
        head.setTextFormat(QtCore.Qt.TextFormat.RichText)
        head.setStyleSheet('background:transparent;')
        title_row.addWidget(head)
        title_row.addStretch(1)

        copy_btn = QtWidgets.QPushButton('⎘ 复制全部')
        copy_btn.setStyleSheet(_mini_btn_style())
        copy_btn.clicked.connect(lambda: _copy_to_clipboard(text))
        title_row.addWidget(copy_btn)

        bubble.add_layout(title_row)

        # 正文：按 markdown 切分段落，代码块独立成 widget（问题 3）
        # 这样用户能精确选中代码而不会带上前后正文。
        segments = split_into_segments(text)
        if not segments:
            # 兜底：空回复也至少显示一个空 label，保持气泡布局稳定
            bubble.add_widget(ChatLabel(''))
        else:
            for seg in segments:
                if seg[0] == 'code':
                    _, lang, code = seg
                    bubble.add_widget(_CodeBlockWidget(lang, code))
                else:
                    _, md_text = seg
                    md_text = md_text.strip('\n')
                    if not md_text:
                        continue
                    body = render_markdown(md_text)
                    bubble.add_widget(ChatLabel(body))

        outer.addWidget(bubble, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        outer.addStretch(1)

    def apply_max_width(self, viewport_width):
        # type: (int) -> None
        """转发到内部 ``BubbleFrame``。"""
        if self._bubble is not None:
            self._bubble.apply_max_width(viewport_width)


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
        self._bubble = bubble
        head = QtWidgets.QLabel(
            '<span style="color:#bbd9f5;font-size:9pt;">{} 你</span>'.format(
                _ee('👤'),
            )
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

    def apply_max_width(self, viewport_width):
        # type: (int) -> None
        """转发到内部 ``BubbleFrame``。"""
        if self._bubble is not None:
            self._bubble.apply_max_width(viewport_width)


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
        self._bubble = bubble
        head = QtWidgets.QLabel(
            '<b style="color:#ffaaaa;">{} 错误</b>'.format(_ee('⚠'))
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

    def apply_max_width(self, viewport_width):
        # type: (int) -> None
        """转发到内部 ``BubbleFrame``。"""
        if self._bubble is not None:
            self._bubble.apply_max_width(viewport_width)


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
        # 同气泡头部：PySide6 对 AutoText 的 HTML 识别更挑剔，强制
        # RichText 让欢迎屏的 HTML（含未来可能的 ``<img>``）稳定渲染
        head.setTextFormat(QtCore.Qt.TextFormat.RichText)
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
