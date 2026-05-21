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
|  [输入框 (Enter 发送, Shift+Enter 换行)]  |
|                            [🚀 发送 / ■ 停止] |
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
from .markdown_render import html_escape
from .bubbles import AssistantBubble as _AssistantBubble
from .bubbles import BubbleFrame as _BubbleFrame  # noqa: F401
from .bubbles import ChatLabel as _ChatLabel  # noqa: F401
from .bubbles import ErrorBubble as _ErrorBubble
from .bubbles import StatusLine as _StatusLine
from .bubbles import StreamingAssistantBubble as _StreamingAssistantBubble
from .bubbles import UserBubble as _UserBubble
from .bubbles import WelcomeBlock as _WelcomeBlock
from .tool_block import ToolCallBlock as _ToolCallBlock

QApplication = QtWidgets.QApplication


# ---------------------------------------------------------------------- #
# 样式表
# ---------------------------------------------------------------------- #
_STYLE = """
QWidget#MaxAgentDockWidget { background-color: #1e1e1e; }
QScrollArea#chatScroll { background-color: #1e1e1e; }
QWidget#chatContent { background-color: #1e1e1e; }
QPlainTextEdit, QTextEdit {
    background-color: #2b2b2b;
    color: #d4d4d4;
    border: 1px solid #3c3c3c;
    border-radius: 3px;
    padding: 2px;
}
QPushButton {
    background-color: #4a4a4a;
    color: #ffffff;
    border: 1px solid #5a5a5a;
    border-radius: 3px;
    padding: 4px 10px;
    min-height: 18px;
}
QPushButton:hover {
    background-color: #5a5a5a;
    border-color: #6a6a6a;
}
QPushButton:pressed { background-color: #3a3a3a; }
QPushButton:disabled { background-color: #333; color: #777; border-color: #444; }
QToolButton.iconBtn {
    background-color: #4a4a4a;
    color: #ffffff;
    border: 1px solid #5a5a5a;
    border-radius: 3px;
    padding: 4px 10px;
}
QToolButton.iconBtn:hover {
    background-color: #5a5a5a;
    border-color: #6a6a6a;
}
QToolButton.iconBtn:pressed { background-color: #3a3a3a; }
QPushButton#sendBtn {
    background-color: #2d7d46;
    border-color: #3a9c5a;
}
QPushButton#sendBtn:hover {
    background-color: #3a9c5a;
    border-color: #4ab36a;
}
QPushButton#sendBtn:pressed { background-color: #266838; }
QPushButton#stopBtn {
    background-color: #a93232;
    border-color: #c44040;
}
QPushButton#stopBtn:hover {
    background-color: #c44040;
    border-color: #d75050;
}
QPushButton#stopBtn:pressed { background-color: #8a2828; }
QPushButton.miniBtn {
    background-color: transparent;
    color: #888;
    border: none;
    padding: 2px 6px;
}
QPushButton.miniBtn:hover { background-color: #333; color: #ddd; }
QToolButton {
    background-color: transparent;
    color: #d0d0d0;
    border: none;
    padding: 2px 4px;
}
QToolButton:hover { color: #ffffff; }
QComboBox {
    background-color: #3c3c3c;
    color: #d4d4d4;
    border: 1px solid #5a5a5a;
    border-radius: 3px;
    padding: 2px 6px;
    min-height: 18px;
}
QComboBox:hover { border-color: #6a6a6a; }
QLabel { color: #d4d4d4; }

QSplitter::handle:vertical { background-color: #3c3c3c; }
QSplitter::handle:vertical:hover { background-color: #5a8ab8; }
QSplitter::handle:vertical:pressed { background-color: #6ba3d4; }

QScrollBar:vertical { background: #1e1e1e; }
QScrollBar::handle:vertical { background: #4a4a4a; border-radius: 3px; }
QScrollBar::handle:vertical:hover { background: #5a5a5a; }
"""


# ---------------------------------------------------------------------- #
# UI 适配辅助
# ---------------------------------------------------------------------- #
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
        # 流式期间的滚动节流标志：避免每个 chunk 都派 timer
        self._scroll_pending = False

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

    def _scroll_to_bottom_pending(self):
        """节流版：被多次 schedule 时只执行一次。"""
        self._scroll_pending = False
        self._scroll_to_bottom()

    def scroll_to_bottom_force(self):
        """对外强制滚动到底（供切换会话时调用）。

        与内部 _scroll_to_bottom 区别：
        - 不依赖 _is_at_bottom 判定，无条件跳到最新一条；
        - 用 0ms QTimer 延迟一帧，等所有 widget 完成 layout
          后再设置 scrollbar，否则 maximum 还未刷新会跳错位置。
        """
        QtCore.QTimer.singleShot(0, self._scroll_to_bottom)

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
        # 流式期间的滚动节流：避免每个 chunk 都派一个 0ms timer，
        # 否则一秒 ~10 次 chunk = 10 个 timer + 10 次 layout 重算，
        # 是新的卡顿源。改为合并到下一帧只触发一次。
        if self._is_at_bottom() and not self._scroll_pending:
            self._scroll_pending = True
            QtCore.QTimer.singleShot(0, self._scroll_to_bottom_pending)

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
    # 整体最小宽度：低于此值时按钮文字会被挤掉。
    # 命令面板下方塞 MaxAgent 时，宽度大约 380px 才能完整显示中文按钮 +
    # emoji 字体，强制设最小值让 Max 主窗口在拖窄时给出横向滚动条。
    _MIN_WIDGET_WIDTH = 360

    def __init__(self, config_manager=None, parent=None):
        # type: (Optional[ConfigManager], Optional[Any]) -> None
        super(MaxAgentDockWidget, self).__init__(parent)
        self.setObjectName('MaxAgentDockWidget')
        self.setWindowTitle('MaxAgent · AI 助手')
        self.setStyleSheet(_STYLE)
        self.setMinimumWidth(self._MIN_WIDGET_WIDTH)

        self._config = config_manager or ConfigManager()
        self._ui_state_mgr = UIStateManager()
        self._ui_state = self._ui_state_mgr.load()
        self._llm = self._build_llm_client()
        self._session_mgr = SessionManager()
        self._skill_mgr = SkillManager()
        self._current_session = None  # type: Optional[SessionMeta]
        self._conv = Conversation()
        self._dispatcher = self._build_dispatcher()
        # type: Optional[AgentWorker]
        self._worker = None
        self._is_running = False
        # 当前正在执行的工具块映射: call_id -> _ToolCallBlock
        self._pending_tool_blocks = {}
        # 累计用量统计：跨多轮、跨多个会话累加（重启后归零）
        self._usage_session = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'cost_usd': 0.0,
            'count': 0,  # 总调用次数
        }

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
        self.profile_combo.setMinimumWidth(160)
        self.profile_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        top.addWidget(self.profile_combo, 1)
        # 重加载按钮：纯文字，避免 emoji 在 Max 内嵌 PySide 下渲染异常
        self.reload_btn = QtWidgets.QPushButton('重载')
        self.reload_btn.setProperty('class', 'iconBtn')
        self.reload_btn.setToolTip(
            '热重载整个 MaxAgent 包（开发态便利）。\n'
            '会保存当前会话与 UI 状态、关闭面板、清空模块缓存后重新加载。\n'
            '修改 .py 文件后无需重启 3ds Max。',
        )
        self.reload_btn.clicked.connect(self._on_reload_clicked)
        top.addWidget(self.reload_btn)
        self.settings_btn = QtWidgets.QPushButton('设置')
        self.settings_btn.setToolTip('打开设置面板（Profile / API Key / 应用开关）')
        self.settings_btn.clicked.connect(self._open_settings)
        top.addWidget(self.settings_btn)
        outer.addLayout(top)

        # === 顶部条第 2 行：会话管理 ===
        sess_row = QtWidgets.QHBoxLayout()
        sess_row.setSpacing(4)
        # 新对话按钮：纯文字，节省横向空间
        self.new_session_btn = QtWidgets.QPushButton('新对话')
        self.new_session_btn.setProperty('class', 'iconBtn')
        self.new_session_btn.setToolTip('开启一个新的空白对话')
        self.new_session_btn.clicked.connect(self._on_new_session)
        sess_row.addWidget(self.new_session_btn)

        sess_row.addWidget(QtWidgets.QLabel('会话:'))
        self.session_combo = QtWidgets.QComboBox()
        self.session_combo.setMinimumWidth(160)
        # 不再 AdjustToContents——避免长会话名把整行撑爆挤掉 ⋯ 按钮
        self.session_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon,
        )
        self.session_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.session_combo.currentIndexChanged.connect(
            self._on_session_combo_changed,
        )
        sess_row.addWidget(self.session_combo, 1)

        # 会话操作菜单（重命名 / 删除 / 清空）合并到一个 ⋯ 按钮
        # 之前 ✏/🗑/清空 三个独立按钮在窄面板下会把会话下拉框挤掉
        self.session_menu_btn = QtWidgets.QToolButton()
        self.session_menu_btn.setText('菜单')
        self.session_menu_btn.setProperty('class', 'iconBtn')
        self.session_menu_btn.setToolTip('会话操作（重命名 / 删除 / 清空消息）')
        self.session_menu_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup,
        )
        sess_menu = QtWidgets.QMenu(self.session_menu_btn)
        act_rename = sess_menu.addAction('重命名当前会话')
        act_rename.triggered.connect(self._on_rename_session)
        act_delete = sess_menu.addAction('删除当前会话')
        act_delete.triggered.connect(self._on_delete_session)
        sess_menu.addSeparator()
        act_clear = sess_menu.addAction('清空当前会话消息')
        act_clear.triggered.connect(self._clear_history)
        self.session_menu_btn.setMenu(sess_menu)
        sess_row.addWidget(self.session_menu_btn)
        outer.addLayout(sess_row)

        # === 顶部条第 3 行：上下文 token 监控 + 用量统计 + 压缩按钮 ===
        ctx_row = QtWidgets.QHBoxLayout()
        ctx_row.setSpacing(4)
        self.context_label = QtWidgets.QLabel('上下文: -')
        self.context_label.setToolTip(
            '当前对话历史占用的估算 token 数 / 上限。\n'
            '超过上限时会自动裁剪最早的消息（保护 tool_call 配对与最近 4 条）。\n'
            '上限可在「设置」中按 Profile 调整。',
        )
        self.context_label.setStyleSheet('color:#aaa;')
        ctx_row.addWidget(self.context_label)

        # 累计用量（实际 LLM usage 反馈）
        self.usage_label = QtWidgets.QLabel('用量: -')
        self.usage_label.setToolTip(
            '本次启动以来的累计 LLM token 用量与成本估算。\n'
            '数据来自 LLM 后端返回的 usage 字段（OpenAI / DeepSeek 等支持）。\n'
            '价格可在「设置」→ Profile 中按 USD/1M tokens 配置。\n'
            '点击复位累计值。',
        )
        self.usage_label.setStyleSheet('color:#aaa;')
        self.usage_label.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.usage_label.mousePressEvent = self._on_usage_label_clicked
        ctx_row.addWidget(self.usage_label)

        ctx_row.addStretch(1)

        # 压缩按钮：纯文字
        self.compress_btn = QtWidgets.QPushButton('压缩')
        self.compress_btn.setProperty('class', 'iconBtn')
        self.compress_btn.setToolTip(
            '压缩对话：让 LLM 总结早期对话内容并替换为摘要，保留最近 2 轮。\n'
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

        # 输入区：垂直布局（输入框 / 底部操作行）
        # 按钮放底部而不是右侧，节省横向宽度，窄面板也能完整显示
        input_container = QtWidgets.QWidget()
        input_layout = QtWidgets.QVBoxLayout(input_container)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(4)

        self.input_edit = _SmartInput(self)
        self.input_edit.setMinimumHeight(self._MIN_INPUT_HEIGHT)
        self.input_edit.setPlaceholderText(
            '✏️ 在这里输入指令...\n'
            'Enter 发送 / Shift+Enter 换行 / Ctrl+Enter 发送（拖动上方分割条可调整大小）',
        )
        self.input_edit.send_requested.connect(self._on_send)
        input_layout.addWidget(self.input_edit, 1)

        # 底部操作行：发送/停止 合一按钮占满整行，文字在窄面板下也不会被截断
        action_row = QtWidgets.QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(6)

        # 发送/停止 合一：未运行时为发送（绿色），运行时切换为停止（红色）
        # 通过 _is_running 状态分发到 _on_send 或 _on_stop
        self.send_btn = QtWidgets.QPushButton('发送')
        self.send_btn.setObjectName('sendBtn')
        # 占满整行，避免窄面板下被父布局压缩成"发"
        self.send_btn.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.send_btn.setToolTip('发送消息（Enter 或 Ctrl+Enter）')
        self.send_btn.clicked.connect(self._on_send_or_stop)
        # 兼容代码：保留 stop_btn 字段指向同一按钮，避免外部引用炸掉
        self.stop_btn = self.send_btn
        action_row.addWidget(self.send_btn, 1)
        input_layout.addLayout(action_row)

        input_container.setMinimumHeight(self._MIN_INPUT_HEIGHT + 8)
        self.splitter.addWidget(input_container)

        self.splitter.setStretchFactor(0, self._DEFAULT_SPLIT_RATIO[0])
        self.splitter.setStretchFactor(1, self._DEFAULT_SPLIT_RATIO[1])
        self.splitter.setSizes([400, 100])

        outer.addWidget(self.splitter, 1)

        # === 底部状态栏 ===
        self.status_label = QtWidgets.QLabel('🟢 准备就绪')
        self.status_label.setStyleSheet('color:#888;')
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

    def _build_dispatcher(self):
        """根据当前 profile 构造 dispatcher（含工具结果裁剪上限）。"""
        prof = self._config.get_active_profile()
        try:
            cap = int(getattr(prof, 'tool_result_max_bytes', 0) or 0)
        except (TypeError, ValueError):
            cap = 0
        if cap <= 0:
            from ..tools.dispatcher import DEFAULT_RESULT_MAX_BYTES
            cap = DEFAULT_RESULT_MAX_BYTES
        return ToolDispatcher(result_max_bytes=cap)

    def _get_active_prices(self):
        """读当前 profile 的 (input, output) 计费单价（USD per 1M tokens）。"""
        prof = self._config.get_active_profile()
        try:
            pin = float(getattr(prof, 'price_input_per_1m', 0.0) or 0.0)
        except (TypeError, ValueError):
            pin = 0.0
        try:
            pout = float(getattr(prof, 'price_output_per_1m', 0.0) or 0.0)
        except (TypeError, ValueError):
            pout = 0.0
        return pin, pout

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
                      dock_area=None, embedded_ok=None,
                      main_state_b64=None):
        """持久化当前 UI 状态到磁盘。

        :param geometry_b64: 调用方（startup.py）从 QDockWidget /
            QMainWindow.saveGeometry 拿到的 base64 字符串。dock_widget
            自己不负责编码这部分，因为真正的 widget 是包在外层的
            QDockWidget。
        :param floating: 是否浮动；None 表示沿用旧值
        :param dock_area: Qt 停靠区域枚举的整数值；None 表示沿用旧值
        :param embedded_ok: 本次启动是否成功嵌入到 Max
        :param main_state_b64: Max 主窗口 ``saveState()`` 的 base64，
            None 表示沿用旧值
        """
        st = self._ui_state
        # 分割器尺寸总是从当前 widget 取
        try:
            st.splitter_sizes = list(self.splitter.sizes())
        except Exception:  # pylint: disable=broad-except
            pass
        if geometry_b64:
            st.geometry_b64 = geometry_b64
        if main_state_b64 is not None and main_state_b64 != '':
            st.main_state_b64 = main_state_b64
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

        显示格式：上下文: 2.5K/32K (8 条)
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
        text = '上下文: {} / {}  ({} 条)'.format(
            _fmt(cur), _fmt(budget), msgs,
        )
        self.context_label.setText(text)
        self.context_label.setStyleSheet('color:{};'.format(color))

    def _on_history_trimmed(self, removed, current_tokens, budget_tokens):
        """worker 通知"已自动裁剪 N 条早期消息"。"""
        self._renderer.add_status(
            '🧹 历史已自动裁剪 {} 条早期消息以适配 token 预算 '
            '({}/{})'.format(removed, current_tokens, budget_tokens),
        )
        self._refresh_context_label()

    def _on_usage_received(self, prompt_tokens, completion_tokens,
                           total_tokens, cost_usd):
        """worker 收到 LLM usage 数据后回调。"""
        u = self._usage_session
        u['prompt_tokens'] += int(prompt_tokens)
        u['completion_tokens'] += int(completion_tokens)
        u['total_tokens'] += int(total_tokens)
        if cost_usd >= 0:
            u['cost_usd'] += float(cost_usd)
        u['count'] += 1
        self._refresh_usage_label()

    def _on_usage_label_clicked(self, _event):
        """点击用量 label：复位累计值。"""
        self._usage_session = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'cost_usd': 0.0,
            'count': 0,
        }
        self._refresh_usage_label()
        try:
            self._renderer.add_status('用量统计已复位')
        except Exception:  # pylint: disable=broad-except
            pass

    @staticmethod
    def _fmt_token(n):
        if n >= 1_000_000:
            return '{:.2f}M'.format(n / 1_000_000.0)
        if n >= 1000:
            return '{:.1f}K'.format(n / 1000.0)
        return str(n)

    def _refresh_usage_label(self):
        u = self._usage_session
        if u['count'] == 0:
            self.usage_label.setText('用量: -')
            return
        in_s = self._fmt_token(u['prompt_tokens'])
        out_s = self._fmt_token(u['completion_tokens'])
        cost = u['cost_usd']
        if cost > 0:
            self.usage_label.setText(
                '用量: in {} / out {}  ${:.4f}  ({}次)'.format(
                    in_s, out_s, cost, u['count'],
                ),
            )
        else:
            self.usage_label.setText(
                '用量: in {} / out {}  ({}次)'.format(
                    in_s, out_s, u['count'],
                ),
            )

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
            '节省 token、加速后续对话\n'
            '⚠️ 早期细节将不可恢复\n\n'
            '是否继续？',
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self._set_running(True)
        self.status_label.setText('📝 正在生成历史摘要...')
        self._renderer.add_status('正在压缩对话历史，请稍候...')
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
            self.status_label.setText('🟢 准备就绪')
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
                '已压缩 {} 条早期消息为摘要。'.format(removed),
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
            self._dispatcher = self._build_dispatcher()
            self._renderer.add_status('已切换到 Profile: {}'.format(name))
            self._refresh_context_label()
        except Exception as exc:  # pylint: disable=broad-except
            self._renderer.add_error('切换 Profile 失败: {}'.format(exc))

    def _open_settings(self):
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self._config, parent=self)
        if dlg.exec_():
            self._llm = self._build_llm_client()
            self._dispatcher = self._build_dispatcher()
            self._refresh_profiles()
            self._renderer.add_status('设置已保存')
            self._refresh_context_label()

    def _on_reload_clicked(self):
        """触发整个 maxagent 包热重载。

        在 reload 真正执行前给用户一次确认机会；用户在长任务中误点不会
        丢工作。reload 自身会先 flush 状态再卸载模块。
        """
        if getattr(self, '_running', False):
            QtWidgets.QMessageBox.information(
                self, '请稍候',
                '当前有任务正在运行，请先停止或等待完成再重加载。',
            )
            return
        ret = QtWidgets.QMessageBox.question(
            self, '确认重加载',
            ('热重载会关闭并重建 MaxAgent 面板，'
             '当前会话和 UI 状态会先保存到磁盘。\n\n是否继续？'),
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes,
        )
        if ret != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        # 延迟执行：避免在按钮 clicked 槽里同步销毁自己
        QtCore.QTimer.singleShot(0, self._do_reload)

    def _do_reload(self):
        """实际执行热重载（在事件循环下一拍调用，安全销毁自己）。"""
        try:
            from ..reload import reload_maxagent
            reload_maxagent(reshow=True)
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.critical(
                None, 'MaxAgent 重加载失败',
                '重加载过程中出错：{}\n\n'
                '建议重启 3ds Max 后重试。'.format(exc),
            )

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
        # 切会话后无条件跳到最新一条（问题 4）
        self._renderer.scroll_to_bottom_force()
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
            price_input_per_1m=self._get_active_prices()[0],
            price_output_per_1m=self._get_active_prices()[1],
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
        self._worker.usage_received.connect(self._on_usage_received)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.run_in_thread(text)

    def _on_stop(self):
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText('⏸ 正在停止...')

    def _on_send_or_stop(self):
        """合一按钮的入口：根据当前运行状态分发到 _on_send 或 _on_stop。"""
        if self._is_running:
            self._on_stop()
        else:
            self._on_send()

    def _set_running(self, running):
        self._is_running = bool(running)
        # 发送/停止 合一按钮：切换文字 + 样式 + 启用状态
        if running:
            self.send_btn.setText('停止')
            self.send_btn.setObjectName('stopBtn')
            self.send_btn.setToolTip('停止当前对话')
            self.send_btn.setEnabled(True)
        else:
            self.send_btn.setText('发送')
            self.send_btn.setObjectName('sendBtn')
            self.send_btn.setToolTip('发送消息（Enter 或 Ctrl+Enter）')
            self.send_btn.setEnabled(True)
        # objectName 改了之后必须重刷样式，否则 QSS 不会重新匹配
        self.send_btn.style().unpolish(self.send_btn)
        self.send_btn.style().polish(self.send_btn)
        self.profile_combo.setEnabled(not running)
        self.settings_btn.setEnabled(not running)
        if hasattr(self, 'reload_btn'):
            self.reload_btn.setEnabled(not running)

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
        self.status_label.setText('✅ 完成')
        self._set_running(False)
        self._save_current_session()
        self._refresh_context_label()

    def _on_failed(self, err):
        self._renderer.add_error(err)
        self.status_label.setText('❌ 失败')
        self._set_running(False)
        # 失败也保存：用户能在历史里看到失败原因
        self._save_current_session()
        self._refresh_context_label()

    # ------------------------------------------------------------------ #
    # 主线程同步工具执行
    # ------------------------------------------------------------------ #
    def _run_tool_sync(self, tool_name, arguments):
        """Worker 子线程通过此函数同步派回主线程执行 pymxs。

        关键设计（优化C - 可重入安全）：

        1. 主线程嵌套调用直接同步执行——避免 QTimer.singleShot 入队
           导致的"主线程 → 等主线程"自死锁。
        2. 子线程等待时**分片轮询**（每 100ms 醒一次），同时检查
           ``self._worker._cancel_event``——这样用户点击"停止"按钮
           能在 100ms 内中断等待，而不是傻等 300s 超时。
        3. 超时从 300s 降到 120s。pymxs 单次工具执行如果真的超过
           120s，多半已经卡死或陷入死循环，与其继续等不如尽早
           ``raise``、把控制权还给用户。
        4. 错误信息带上工具名 + 参数预览，方便定位是哪个调用阻塞。
        """
        # 分片等待参数
        # - poll_interval: 100ms，平衡 CPU 占用和取消响应延迟
        # - max_wait: 120s，工具单次执行硬上限
        poll_interval = 0.1
        max_wait = 120.0

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
            # 当前已经在主线程：直接同步执行，避免事件队列嵌套死锁
            _run_in_main()
        else:
            QtCore.QTimer.singleShot(0, _run_in_main)
            # 分片等待：每个 poll_interval 醒一次，检查取消标志
            cancel_event = getattr(self._worker, '_cancel_event', None)
            elapsed = 0.0
            while elapsed < max_wait:
                if done.wait(timeout=poll_interval):
                    break
                # 用户点了"停止"——立刻 raise 让 worker 跳出工具循环
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError(
                        '用户取消了工具 {} 的执行'.format(tool_name),
                    )
                elapsed += poll_interval
            if not done.is_set():
                # 真正超时：给出可定位的错误
                arg_preview = repr(arguments)
                if len(arg_preview) > 120:
                    arg_preview = arg_preview[:120] + '...'
                raise RuntimeError(
                    '工具 {} 在主线程执行超时（>{:.0f}s），参数: {}'.format(
                        tool_name, max_wait, arg_preview,
                    ),
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
