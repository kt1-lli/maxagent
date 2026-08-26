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
import time
from typing import Any
from typing import Optional

from ..agent import AgentWorker
from ..agent import Conversation
from ..agent import build_default_system_prompt
from ..attachments import model_supports_vision
from ..config import ConfigManager
from ..llm_client import build_client_from_profile
from ..logger import get_logger
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
from .bubbles import SystemNoticeBubble as _SystemNoticeBubble
from .bubbles import TodoListBubble as _TodoListBubble
from .bubbles import UserBubble as _UserBubble
from .bubbles import WelcomeBlock as _WelcomeBlock
from .emoji_compat import apply_font_fallback as _apply_font_fallback
from .emoji_compat import btn_label as _btn_label
from .emoji_compat import e as _e
from .emoji_compat import ee as _ee
from .tool_block import ToolCallBlock as _ToolCallBlock

logger = get_logger(__name__)

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
                 parent=None, employee_provider=None):
        super(_ChatRenderer, self).__init__(parent)
        self._scroll = scroll_area
        self._content = content_widget
        self._layout = content_layout  # type: QtWidgets.QVBoxLayout
        # 末尾 stretch，让消息从顶部开始堆
        self._layout.addStretch(1)
        self._streaming = None  # type: Optional[_StreamingAssistantBubble]
        # 流式期间的滚动节流标志：避免每个 chunk 都派 timer
        self._scroll_pending = False
        # 员工档案 provider：每次创建助手气泡时调用，得到当前最新的
        # Employee 视图。这样改完员工设置后，下一条新气泡立即生效，
        # 已渲染的旧气泡保持原状（避免遍历刷新带来的复杂度）。
        self._employee_provider = employee_provider
        # 监听 scroll viewport 的 resize 事件：面板宽度变化时遍历
        # 所有现存气泡更新 maxWidth，让它们跟随面板宽度按比例缩放
        try:
            self._scroll.viewport().installEventFilter(self)
        except Exception:  # pylint: disable=broad-except
            # 容器构造异常时静默：宽度跟随只是优化项，不应阻止主流程
            pass

    # ------------------------------------------------------------------ #
    # 宽度跟随：监听 viewport resize，把当前可视宽度下发给所有气泡
    # ------------------------------------------------------------------ #
    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Resize:
            self._apply_widths_to_all()
        return False

    def _viewport_width(self):
        """返回当前滚动区可视宽度（已扣除内容布局边距）。"""
        try:
            vw = self._scroll.viewport().width()
        except Exception:  # pylint: disable=broad-except
            return 0
        # 扣除 messages_layout 自身的左右 contentsMargins，避免气泡
        # 撑到边距外被截切
        try:
            m = self._layout.contentsMargins()
            vw -= (m.left() + m.right())
        except Exception:  # pylint: disable=broad-except
            pass
        return max(0, vw)

    def _apply_widths_to_all(self):
        """遍历 messages_layout 中所有支持 apply_max_width 的气泡，
        按当前 viewport 宽度更新各自最大宽度。"""
        vw = self._viewport_width()
        if vw <= 0:
            return
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            if item is None:
                continue
            w = item.widget()
            if w is None:
                continue
            apply = getattr(w, 'apply_max_width', None)
            if callable(apply):
                try:
                    apply(vw)
                except Exception:  # pylint: disable=broad-except
                    pass

    # ------------------------------------------------------------------ #
    # 底部追加（在 stretch 之前）
    # ------------------------------------------------------------------ #
    def _append(self, widget, force_scroll=False):
        # 去掉末尾 stretch -> 加 widget -> 重新加 stretch
        # 直接 insertWidget 到倒数第二（stretch 是最后一个 item）
        idx = self._layout.count() - 1
        if idx < 0:
            idx = 0
        # 滚动跟随策略：插入前判断用户当前是否在底部
        was_at_bottom = self._is_at_bottom()
        self._layout.insertWidget(idx, widget)
        # 新气泡立即应用当前可视宽度（如果它支持的话）
        apply = getattr(widget, 'apply_max_width', None)
        if callable(apply):
            vw = self._viewport_width()
            if vw > 0:
                try:
                    apply(vw)
                except Exception:  # pylint: disable=broad-except
                    pass
        if force_scroll or was_at_bottom:
            # 强制场景（如用户主动发送）需要多帧兜底：
            # 立刻一次 + 下一帧一次 + 30ms 后一次，确保
            # markdown 渲染 / 图片附件加载 / 布局伸展完成后
            # 仍能停在底部。
            self._scroll_to_bottom()
            QtCore.QTimer.singleShot(0, self._scroll_to_bottom)
            if force_scroll:
                QtCore.QTimer.singleShot(30, self._scroll_to_bottom)
                QtCore.QTimer.singleShot(120, self._scroll_to_bottom)

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

    def is_at_bottom(self):
        """对外只读：当前滚动位置是否处于聊天区底部。

        用于外部（如拖动 splitter 前）判断用户是否"贴在最新消息"，
        从而决定要不要在 layout 变化后强制滚回底部。
        """
        return self._is_at_bottom()

    def _current_employee(self):
        """返回当前的员工档案视图。

        provider 为 None 或返回异常时回落到默认 Employee（"助手" + 🤖），
        确保气泡渲染永不因配置异常而崩。
        """
        from .employee import Employee as _Employee
        if self._employee_provider is None:
            return _Employee()
        try:
            emp = self._employee_provider()
        except Exception:  # pylint: disable=broad-except
            return _Employee()
        return emp if emp is not None else _Employee()

    # ------------------------------------------------------------------ #
    # 消息接口
    # ------------------------------------------------------------------ #
    def add_user(self, text, attachments=None):
        self._close_streaming_if_any()
        # 用户主动发送 = 显式意图，无条件滚到底部（即使此前在翻历史）
        self._append(_UserBubble(text, attachments=attachments), force_scroll=True)

    def add_assistant_start(self):
        """开始一段助手回复气泡，后续 chunk 会增量追加。"""
        self._close_streaming_if_any()
        bubble = _StreamingAssistantBubble(employee=self._current_employee())
        self._streaming = bubble
        # 助手气泡紧跟在用户消息之后，同样强制贴底
        self._append(bubble, force_scroll=True)

    def add_assistant_chunk(self, chunk):
        if self._streaming is None:
            self.add_assistant_start()
        self._streaming.append_chunk(chunk)
        # 流式期间强化滚动策略（用户反馈：够不到底）：
        # - 放宽底部粘性判断：只要不在明显往上翻的位置就跟随
        # - 每 chunk 无条件调度一次 0ms 滚动（Qt 会合并多次 timer 到一帧）
        # - 用 ensureWidgetVisible 让 QScrollArea 主动把 bubble 拉进视区，
        #   不依赖 verticalScrollBar.maximum() 的滞后更新
        if self._is_sticky_bottom():
            if not self._scroll_pending:
                self._scroll_pending = True
                QtCore.QTimer.singleShot(0, self._scroll_to_bottom_pending)
            # 让滚动区主动跟随 streaming bubble
            try:
                self._scroll.ensureWidgetVisible(self._streaming, 0, 0)
            except Exception:  # pylint: disable=broad-except
                pass

    def _is_sticky_bottom(self):
        """比 _is_at_bottom 更宽松：底部 200px 范围内都算贴底。

        流式期间用户可能因为 chunk 抖动被"挤"离底部一两屏，只要意图
        还是在跟随最新回复，就应该继续滚。真正想翻历史的用户会滚到
        200px 之外，此时不再自动跟随。
        """
        bar = self._scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - 200

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
            final = _AssistantBubble(text, employee=self._current_employee())
            self._layout.insertWidget(idx, final)
            # 立即应用当前 viewport 宽度，避免最终气泡被撑到边缘
            vw = self._viewport_width()
            if vw > 0:
                try:
                    final.apply_max_width(vw)
                except Exception:  # pylint: disable=broad-except
                    pass

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

    def add_system_notice(self, level, message):
        """在对话流插入一条持久系统通知气泡（居中样式）。

        与 status_bar 提示不同，本气泡会永久留在对话历史中，用户
        滚动回来仍能看到，适合承载 fallback 切换 / 配额告警 /
        备用链耗尽等对上下文有影响的重要事件。
        """
        self._close_streaming_if_any()
        self._append(_SystemNoticeBubble(message, level=level))

    def add_or_update_todo_bubble(self, session_id, snapshot):
        """维持"每会话一张任务清单卡"策略：首次创建，之后就地更新。

        session_id 为空或未在缓存中出现时新建气泡；已有时直接调用
        update_snapshot 刷新，不重复追加，避免把整轮对话历史被
        checklist 淹没。
        """
        if not hasattr(self, '_todo_bubbles') or self._todo_bubbles is None:
            self._todo_bubbles = {}  # type: dict
        sid = session_id or '__default__'
        bubble = self._todo_bubbles.get(sid)
        if bubble is None:
            self._close_streaming_if_any()
            bubble = _TodoListBubble()
            self._todo_bubbles[sid] = bubble
            self._append(bubble)
        try:
            bubble.update_snapshot(session_id, snapshot or {})
        except Exception:  # pylint: disable=broad-except
            logger.debug('TodoListBubble 更新异常（已忽略）')
        # 立即应用宽度限制
        vw = self._viewport_width()
        if vw > 0:
            try:
                bubble.apply_max_width(vw)
            except Exception:  # pylint: disable=broad-except
                pass

    def clear(self):
        """清空全部消息，但保留末尾 stretch。"""
        self._streaming = None
        # 清 todo 卡缓存：会话切换/清空对话时不再复用旧气泡引用
        if hasattr(self, '_todo_bubbles'):
            self._todo_bubbles = {}
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

    # 跨线程派发信号：从任意线程把 callable 调度到主线程执行。
    # self 在主线程构造，亲缘=主线程；配合 QueuedConnection，Qt 会保证
    # 槽函数 _invoke_main_slot 在主线程的事件循环里被调用——这是跨线程
    # marshal 的唯一可靠机制。
    # 切记：不要用裸 QtCore.QTimer.singleShot(0, fn) 替代——后者绑定的是
    # **调用方所在线程**的事件循环；从 worker 子线程调用时会被派回 worker
    # 子线程的"幽灵 timer 队列"，而 worker 子线程此时正阻塞在 done.wait()
    # 上不 spin 事件循环 → fn 永远不会被执行 → 工具调用必 360s 超时。
    _invoke_main_signal = QtCore.Signal(object)

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
        self._conv = Conversation(
            system_prompt=self._build_system_prompt_for_new_conv(),
        )
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

        # 跨线程派发桥：QueuedConnection 强制把 callable marshal 到主线程
        # 事件循环。worker 子线程要把 pymxs 工具任务派回主线程时使用。
        self._invoke_main_signal.connect(
            self._invoke_main_slot,
            QtCore.Qt.QueuedConnection,
        )

        # 字体回退链：在 PySide2 (Qt5) Windows 上，emoji + 中文混排会触发
        # 字体回退缺陷（emoji 字体拖累整行汉字渲染）。这里给整个 dock
        # widget 设一份带 CJK + emoji 回退族的 QFont，让 Qt 按字符级回退。
        # PySide6 不受影响，但应用同一份字体也不会出问题。
        # 必须 recursive=True：Qt 不会自动把父控件 setFont 级联到子控件，
        # 需要逐个 QPushButton/QLabel/QLineEdit 单独设字体才能生效。
        _apply_font_fallback(self, recursive=True)

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
        # 重加载按钮：图标 + 文字（PySide6 走真 emoji，PySide2 走 BMP 兜底）
        self.reload_btn = QtWidgets.QPushButton(_btn_label('🔄', '重载'))
        self.reload_btn.setProperty('class', 'iconBtn')
        self.reload_btn.setToolTip(
            '热重载整个 MaxAgent 包（开发态便利）。\n'
            '会保存当前会话与 UI 状态、关闭面板、清空模块缓存后重新加载。\n'
            '修改 .py 文件后无需重启 3ds Max。',
        )
        self.reload_btn.clicked.connect(self._on_reload_clicked)
        top.addWidget(self.reload_btn)
        self.settings_btn = QtWidgets.QPushButton(_btn_label('⚙️', '设置'))
        self.settings_btn.setToolTip('打开设置面板（Profile / API Key / 应用开关）')
        self.settings_btn.clicked.connect(self._open_settings)
        top.addWidget(self.settings_btn)
        outer.addLayout(top)

        # === 顶部条第 2 行：会话管理 ===
        sess_row = QtWidgets.QHBoxLayout()
        sess_row.setSpacing(4)
        # 新对话按钮：图标 + 文字
        self.new_session_btn = QtWidgets.QPushButton(_btn_label('💬', '新对话'))
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
        self.session_menu_btn.setText(_btn_label('☰', '菜单'))
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
            '上限会根据当前 Profile 的模型自动推断（如 GPT-4o 128K、'
            'DeepSeek 128K、Claude Sonnet 4 1M、Gemini 1.5 Pro 2M）。\n'
            '本地 Ollama 端点按 8K 兜底（实际受 num_ctx 参数限制）。\n'
            '识别失败时回退到「设置」中手填的预算值。\n'
            '超过上限时会自动裁剪最早的消息（保护 tool_call 配对与最近 4 条）。',
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

        # 压缩按钮：图标 + 文字
        self.compress_btn = QtWidgets.QPushButton(_btn_label('🗜️', '压缩'))
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
            employee_provider=self._make_employee,
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
            _ee('✏️') + ' 在这里输入指令...\n'
            'Enter 发送 / Shift+Enter 换行 / Ctrl+Enter 发送 / Ctrl+V 粘贴图片',
        )
        self.input_edit.send_requested.connect(self._on_send)
        # 接收输入框内通过粘贴/拖拽产生的图片字节
        self.input_edit.image_dropped.connect(self._on_image_dropped)
        # 附件预览条（输入框上方，无附件时自动隐藏）
        from .input_attachments import AttachmentStrip
        from .input_attachments import VisionHintBar
        # 视觉降级提示条：放在预览条上方，附件数量 + 视觉能力联动显隐
        self.vision_hint = VisionHintBar(self)
        self.vision_hint.switch_profile_requested.connect(
            self._on_vision_hint_switch_profile,
        )
        input_layout.addWidget(self.vision_hint, 0)
        self.attachment_strip = AttachmentStrip(self)
        # 附件增删时同步刷新提示条状态
        self.attachment_strip.changed.connect(self._refresh_vision_hint)
        input_layout.addWidget(self.attachment_strip, 0)
        input_layout.addWidget(self.input_edit, 1)

        # 底部操作行：发送/停止 合一按钮占满整行，文字在窄面板下也不会被截断
        action_row = QtWidgets.QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(6)

        # 📎 添加图片按钮：打开文件对话框选图
        self.attach_btn = QtWidgets.QPushButton(_ee('📎'))
        self.attach_btn.setFixedWidth(40)
        self.attach_btn.setToolTip(
            '添加图片（也可 Ctrl+V 粘贴 / 拖入图片文件）',
        )
        self.attach_btn.clicked.connect(self._on_attach_image)
        action_row.addWidget(self.attach_btn, 0)

        # ✂️ 截图按钮：进程内 Qt 全屏框选
        self.snip_btn = QtWidgets.QPushButton(_ee('✂️'))
        self.snip_btn.setFixedWidth(40)
        self.snip_btn.setToolTip('截图（全屏框选）')
        self.snip_btn.clicked.connect(self._on_snip)
        action_row.addWidget(self.snip_btn, 0)

        # 🌐 联网按钮（toggle）：本轮对话是否允许 LLM 调联网工具
        # 行为根据全局 web_search_mode 三态联动：
        #   off    -> 按钮置灰不可点，hover 提示"全局已禁用"
        #   auto   -> 按钮可点，亮起=本轮联网/熄灭=本轮关闭
        #   force  -> 按钮强制亮起且不可点，hover 提示"全局已强制开启"
        self.web_btn = QtWidgets.QPushButton(_ee('🌐'))
        self.web_btn.setCheckable(True)
        self.web_btn.setFixedWidth(40)
        self.web_btn.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.web_btn.setStyleSheet(
            'QPushButton:checked { background-color: #094771; color: #fff; }'
        )
        self.web_btn.toggled.connect(self._on_web_btn_toggled)
        action_row.addWidget(self.web_btn, 0)

        # 发送/停止 合一：未运行时为发送（绿色），运行时切换为停止（红色）
        # 通过 _is_running 状态分发到 _on_send 或 _on_stop
        self.send_btn = QtWidgets.QPushButton(_btn_label('🚀', '发送'))
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

        # 初始化 🌐 按钮状态（依赖 self._config）
        self.refresh_web_button_state()

        input_container.setMinimumHeight(self._MIN_INPUT_HEIGHT + 8)
        self.splitter.addWidget(input_container)

        self.splitter.setStretchFactor(0, self._DEFAULT_SPLIT_RATIO[0])
        self.splitter.setStretchFactor(1, self._DEFAULT_SPLIT_RATIO[1])
        self.splitter.setSizes([400, 100])

        # 拖动 splitter 锚点保持：
        # 用户向上拖（输入区扩张 / 聊天区缩小）时，聊天区可视范围
        # 减小，原本停在底部的最新消息会被遮挡 -> 体感"对话被吃掉"。
        # 解决：拖动开始时记录"是否在底部"快照；拖动过程中若输入区
        # 高度增加且拖动前在底部，则强制滚回底部，保持最新消息可见。
        # 翻历史的用户（拖动前不在底部）则不打断，保留原视点。
        self._splitter_drag_was_at_bottom = False
        self._splitter_last_input_h = 0
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        # 第一次进入界面时主动初始化 last_input_h，避免误判方向
        try:
            sizes = self.splitter.sizes()
            if len(sizes) >= 2:
                self._splitter_last_input_h = int(sizes[1])
        except Exception:  # pylint: disable=broad-except
            pass

        outer.addWidget(self.splitter, 1)

        # === 底部状态栏 ===
        self.status_label = QtWidgets.QLabel(_ee('🟢') + ' 准备就绪')
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
        return build_client_from_profile(prof, self._config.config)

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
        dispatcher = ToolDispatcher(result_max_bytes=cap)
        # 让 batch_execute 工具能复用带 result_max_bytes 配置的实例
        from ..tools import set_global_dispatcher
        set_global_dispatcher(dispatcher)
        return dispatcher

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
        """获取当前 profile 的历史 token 预算（覆盖策略 A1）。

        优先级：
        1. 模型能力库根据 ``profile.model`` + ``base_url`` 推断（覆盖手填值）
        2. 用户在设置里手填的 ``max_history_tokens``（兜底）
        3. 最终回退 32000

        Ollama 端点会被特殊处理（按 8K 兜底，避免 num_ctx 默认 2048 撑爆）。
        """
        try:
            prof = self._config.get_active_profile()
        except Exception:  # pylint: disable=broad-except
            return 32000
        # 优先：模型库推断
        try:
            from maxagent.model_capabilities import (
                infer_context_window,
                recommend_history_budget,
            )
            model_id = getattr(prof, 'model', '') or ''
            base_url = getattr(prof, 'base_url', '') or ''
            ctx_win = infer_context_window(model_id, base_url)
            if ctx_win > 0:
                budget = recommend_history_budget(ctx_win)
                if budget > 0:
                    return budget
        except Exception:  # pylint: disable=broad-except
            pass
        # 兜底：用户手填
        try:
            v = int(getattr(prof, 'max_history_tokens', 0) or 0)
            if v > 0:
                return v
        except Exception:  # pylint: disable=broad-except
            pass
        return 32000

    def _get_active_model_label(self):
        # type: () -> str
        """获取当前 profile 的模型标签（用于 UI 提示），失败时返回空串。"""
        try:
            prof = self._config.get_active_profile()
            return (getattr(prof, 'model', '') or '').strip()
        except Exception:  # pylint: disable=broad-except
            return ''

    def _refresh_context_label(self):
        """刷新顶部 token 状态条。

        显示格式：上下文: 2.5K / 96K (8 条) · gpt-4o
        颜色根据占比变化：<60% 灰、<85% 橙、>=85% 红
        模型标签来自当前 Profile 的 ``model`` 字段，让用户直观看到
        budget 是基于哪个模型自动推断的。
        """
        try:
            cur = self._conv.estimate_total_tokens()
        except Exception:  # pylint: disable=broad-except
            cur = 0
        budget = self._get_active_max_history_tokens()
        msgs = len(self._conv) if self._conv else 0
        model_label = self._get_active_model_label()

        def _fmt(n):
            if n >= 1000000:
                return '{:.1f}M'.format(n / 1000000.0)
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
        if model_label:
            # 模型名过长时截短显示，避免把状态条挤变形
            short = model_label if len(model_label) <= 24 else (
                model_label[:21] + '...'
            )
            text = text + '  · ' + short
        self.context_label.setText(text)
        self.context_label.setStyleSheet('color:{};'.format(color))

    def _on_history_trimmed(self, removed, current_tokens, budget_tokens):
        """worker 通知"已自动裁剪 N 条早期消息"。"""
        self._renderer.add_status(
                _ee('🧹') + ' 历史已自动裁剪 {} 条早期消息以适配 token 预算 '
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
            '{} 早期细节将不可恢复\n\n'
            '是否继续？'.format(_ee('⚠️')),
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self._set_running(True)
        self.status_label.setText(_ee('📝') + ' 正在生成历史摘要...')
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
            self.status_label.setText(_ee('🟢') + ' 准备就绪')
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
            self._refresh_vision_hint()
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
            self._refresh_vision_hint()

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
            # 把"当前员工身份"对应的 system prompt 注入到新会话——
            # LLM 自我介绍才会跟随员工名（修复 bug：尼娜会话仍说
            # "我是 MaxAgent" 的根因即此处之前没注入）。
            target = self._session_mgr.create_session(
                system_prompt=self._build_system_prompt_for_new_conv(),
            )
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
        # 崩溃防丢：修复上次崩溃留下的孤立 tool_calls（assistant 消息
        # 里有 tool_calls，但对应的 tool 结果消息因崩溃未落盘）。
        # 未修复的话，下次发消息 API 会返回 400（tool_call 缺少配对 tool
        # 消息）。修复即为每个孤立 call 追加一条 ok=false 占位消息。
        try:
            repaired_calls = conv.repair_incomplete_tool_calls()
            if repaired_calls > 0:
                logger.info(
                    '会话 %s 修复了 %d 个孤立 tool_call（上次崩溃残留）',
                    sid, repaired_calls,
                )
                self._save_current_session(force=True)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('repair_incomplete_tool_calls 异常: %s', exc)
        # 方案 C：从磁盘恢复的会话注入"重启对齐"提示，
        # 让 LLM 知道场景可能已变。空会话不注入。
        try:
            if conv.messages and not conv.has_restored_marker():
                injected = conv.inject_restored_notice()
                if injected:
                    # 立刻持久化，避免下次启动重复注入
                    self._save_current_session(force=True)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('inject_restored_notice 异常: %s', exc)
        # 持久化最近一次会话 ID 到 ui_state
        try:
            self._ui_state.last_session_sid = sid
            self._ui_state_mgr.save(self._ui_state)
        except Exception:  # pylint: disable=broad-except
            pass
        # 回放历史消息
        if not conv.messages:
            # 空会话：把当前最新的"员工身份" system prompt 覆写进去。
            # 这样老用户改名后切回这个空会话时，LLM 自我介绍也会立刻
            # 跟随新名字（修复 bug：截图里"尼娜"会话仍说"我是
            # MaxAgent"——根因就是空会话用了老存盘 prompt）。
            # 非空会话不动，保留历史身份氛围、避免对已有对话的破坏性升级。
            conv.system_prompt = self._build_system_prompt_for_new_conv()
            self._save_current_session(force=True)
            # 欢迎屏的助手称呼跟随员工档案——员工名 'MaxAgent'（默认）
            # 时与改造前完全一致；用户改名后立即生效。
            # 用 escape_name 复用员工模块的 HTML 转义，避免名字含
            # ``<script>`` 时被当 HTML 标签注入。
            emp = self._make_employee()
            from .employee import escape_name
            safe_name = escape_name(emp.name)
            self._renderer.add_welcome(
                '{} 你好，我是 <b style="color:#a8e6a8;">{}</b>。'
                '点击下方任一示例快速开始：'.format(_ee('👋'), safe_name)
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
                if m.content or getattr(m, 'attachments', None):
                    self._renderer.add_user(
                        m.content or '',
                        attachments=getattr(m, 'attachments', None),
                    )
            elif m.role == 'assistant':
                if m.content:
                    # 直接渲染最终版（不走流式）
                    self._renderer._close_streaming_if_any()  # noqa: SLF001
                    bubble = _AssistantBubble(
                        m.content,
                        employee=self._renderer._current_employee(),  # noqa: SLF001
                    )
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
            logger.warning('保存会话失败: %s', exc)
            return
        # 刷新下拉但保持当前选中
        self._refresh_sessions_combo(select_sid=self._current_session.sid)

    def _on_new_session(self):
        if self._is_running:
            self._renderer.add_status('请先停止当前对话再新建会话')
            return
        # 先把当前会话存盘
        self._save_current_session()
        meta = self._session_mgr.create_session(
            system_prompt=self._build_system_prompt_for_new_conv(),
        )
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
            meta = self._session_mgr.create_session(
                system_prompt=self._build_system_prompt_for_new_conv(),
            )
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
            from ..tools.shared.learn_tools import set_approval_callback
            set_approval_callback(make_approval_callback(parent_widget=self))
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('注册学习审批回调失败: %s', exc)
        # 同时安装规则学习审批回调（同样要求主线程）
        try:
            from .rule_approval_dialog import make_rule_approval_callback
            from ..tools.shared.learn_rules import set_rule_approval_callback
            set_rule_approval_callback(
                make_rule_approval_callback(parent_widget=self),
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('注册规则审批回调失败: %s', exc)

    def _make_employee(self):
        """构造当前员工档案视图，供气泡渲染使用。

        每次调用都从 ConfigManager 读最新值——这样用户在"助手形象"
        Tab 改完保存后，下一条新气泡立即按新形象渲染，无需重启面板。
        """
        from .employee import Employee
        return Employee.from_config(self._config)

    def _build_system_prompt_for_new_conv(self):
        """为新建 ``Conversation`` 构造带"员工身份"注入的 system prompt。

        - 老用户（默认员工名 'MaxAgent'）：行为完全等同改造前。
        - 自定义员工名（如 '尼娜'）：LLM 对外只自称 '尼娜'，不暴露
          'MaxAgent' 这个内部岗位代号；岗位职责、工具能力、身份铁律
          一字不改。
        - 仅作用于**新建**会话。已存盘的旧会话保留当时序列化的 prompt
          原文（``Conversation.from_json`` 读档时使用），以保证历史
          沉浸感、避免对老 session 的破坏性升级。
        """
        emp = self._make_employee()
        return build_default_system_prompt(emp.name)

    def _build_system_prompt_addon(self, user_input=None):
        """合并 skills 和用户规则两个 system prompt 附加段。

        :param user_input: 当前用户消息，传给 skills 触发关键词匹配
        :returns: 多行字符串，可能为空
        """
        parts = []
        try:
            skill_part = self._skill_mgr.build_system_prompt_addon(user_input)
            if skill_part:
                parts.append(skill_part)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('skills addon 异常: %s', exc)
        try:
            from ..user_rules_loader import build_system_prompt_addon as _bra
            rule_part = _bra(user_input)
            if rule_part:
                parts.append(rule_part)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('user_rules addon 异常: %s', exc)
        try:
            from ..reflections_loader import (
                build_system_prompt_addon as _rba,
            )
            reflection_part = _rba()
            if reflection_part:
                parts.append(reflection_part)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('reflections addon 异常: %s', exc)
        return '\n\n'.join(parts)

    def _on_example_picked(self, text):
        # 把示例文本填入输入框，让用户可以编辑后再发
        self.input_edit.setPlainText(text)
        self.input_edit.setFocus()

    def _on_splitter_moved(self, pos, index):
        # type: (int, int) -> None
        """聊天区/输入区分隔条被拖动后维护滚动锚点。

        :param pos: 分隔条新位置（像素，从 splitter 顶端起算）
        :param index: 被移动的 handle 索引（恒为 1，因为只有一条）

        策略（方案 A，参考主流 IM 体验）：
        - 向上拖（输入区扩张）：聊天区可视高度变小，原本贴在底部的
          最新消息会被推出可见区。如果用户拖动**之前**就处于底部
          （或 30px 容差内），强制滚回底部，让最新消息保持可见，
          消除"输入区吃掉对话"的体感。
        - 向下拖（输入区收缩）：聊天区变大，原可见消息仍在视野内，
          不需要主动干预，并清零拖动状态快照。
        - 用户在翻历史（拖动前不在底部）：保持原视点，不打扰阅读。

        实现细节：通过比较 splitter sizes()[1] 与上次记录值判断方向；
        在"刚开始向上拖"的那一帧拍下"是否在底部"快照，避免拖动过程中
        多次回调时聊天区已被压缩导致 is_at_bottom 误判。
        """
        try:
            sizes = self.splitter.sizes()
        except Exception:  # pylint: disable=broad-except
            return
        if len(sizes) < 2:
            return
        new_input_h = int(sizes[1])
        old_input_h = int(self._splitter_last_input_h or 0)

        # 方向：输入区变高 = 用户向上拖
        going_up = new_input_h > old_input_h
        # 更新记录值以便下次回调判方向
        self._splitter_last_input_h = new_input_h

        if not going_up:
            # 向下拖或没动：清零快照，下一次拖动重新采样
            self._splitter_drag_was_at_bottom = False
            return

        # 向上拖：仅在"刚开始向上"的第一帧采样，后续帧沿用
        if not self._splitter_drag_was_at_bottom:
            try:
                self._splitter_drag_was_at_bottom = bool(
                    self._renderer.is_at_bottom(),
                )
            except Exception:  # pylint: disable=broad-except
                self._splitter_drag_was_at_bottom = False

        if self._splitter_drag_was_at_bottom:
            # 延迟一帧滚到底，等 splitter layout 落定后 maximum 才正确
            try:
                self._renderer.scroll_to_bottom_force()
                logger.debug(
                    'splitter drag-up detected, anchor=bottom -> force scroll',
                )
            except Exception:  # pylint: disable=broad-except
                pass

    def refresh_web_button_state(self):
        """根据全局 ``web_search_mode`` 同步 🌐 按钮显示与可点击性。

        在以下时机调用：
        1. 主 UI 初始化（_build_ui 末尾）
        2. 设置面板 OK 后（SettingsDialog 主动回调本方法）
        3. 重新加载配置后（reload）
        """
        if not hasattr(self, 'web_btn'):
            return
        cfg = self._config.config
        mode = str(getattr(cfg, 'web_search_mode', 'auto') or 'auto').lower()
        backend = str(
            getattr(cfg, 'web_search_backend', 'duckduckgo') or 'duckduckgo',
        ).lower()

        # 解析当前激活 provider，获取展示名 + 是否真正可用
        active_name = ''
        provider_usable = True
        try:
            from ..web_providers import ProviderRegistry
            reg = ProviderRegistry()
            mapped = reg.get(backend) if backend != 'disabled' else None
            if mapped is None:
                mapped = reg.get_active()
            if mapped is not None:
                active_name = mapped.get('name') or mapped.get('id') or ''
                provider_usable = bool(mapped.get('enabled', True))
        except Exception:  # pylint: disable=broad-except
            provider_usable = (backend != 'disabled')

        # 后端为 disabled 或 provider 已禁用都视同 mode=off
        effective_off = (
            mode == 'off' or backend == 'disabled' or not provider_usable
        )
        # 阻塞 toggle 信号避免触发副作用
        self.web_btn.blockSignals(True)
        if effective_off:
            self.web_btn.setEnabled(False)
            self.web_btn.setChecked(False)
            self.web_btn.setToolTip('联网已被全局关闭（设置 → 联网）')
        elif mode == 'force':
            self.web_btn.setEnabled(False)
            self.web_btn.setChecked(True)
            self.web_btn.setToolTip(
                '联网为强制开启（设置 → 联网）；本按钮不可关闭\n'
                '当前后端：{}'.format(active_name or backend),
            )
        else:  # auto
            self.web_btn.setEnabled(True)
            self.web_btn.setToolTip(
                '本轮对话允许 LLM 联网搜索\n'
                '当前后端：{}\n'
                '点击切换：亮起=本轮联网；熄灭=本轮关闭'.format(
                    active_name or backend,
                ),
            )
        self.web_btn.blockSignals(False)

    def _on_web_btn_toggled(self, checked):
        """用户点击 🌐 切换本轮联网开关——仅 auto 模式下生效。

        force / off 模式下按钮被 setEnabled(False) 拦住，不会进入这里。
        """
        try:
            self.status_label.setText(
                (_ee('🌐') + ' 本轮联网：开启') if checked
                else (_ee('🌐') + ' 本轮联网：关闭'),
            )
        except Exception:  # pylint: disable=broad-except
            pass

    def _should_use_web_this_turn(self):
        """决策本轮是否暴露 web_* 工具。

        :returns: True 表示允许 LLM 调用 web_search / web_fetch
        """
        cfg = self._config.config
        mode = str(getattr(cfg, 'web_search_mode', 'auto') or 'auto').lower()
        backend = str(
            getattr(cfg, 'web_search_backend', 'duckduckgo') or 'duckduckgo',
        ).lower()
        if mode == 'off' or backend == 'disabled':
            return False
        if mode == 'force':
            return True
        # auto 模式：看按钮当前 checked 状态
        try:
            return bool(self.web_btn.isChecked())
        except Exception:  # pylint: disable=broad-except
            return False

    def _on_send(self):
        if self._is_running:
            return
        text = self.input_edit.toPlainText().strip()
        atts = list(self.attachment_strip.attachments())
        if not text and not atts:
            return
        # 没文本但有图片时给一个默认描述，避免 OpenAI 端纯图被拒
        if not text and atts:
            text = '请看图。'
        # DEBUG 埋点：用户发送（input 长度 + 附件数）
        logger.debug('ui_send len=%d atts=%d', len(text), len(atts))
        self.input_edit.clear()
        self.attachment_strip.clear()
        self._renderer.add_user(text, attachments=atts)
        # 把 attachments 同步写入 conv，以便保存到 session
        # 注意：到此时 worker 还没启动，conv.add_user 没被调用
        # —— 它在 worker.run_in_thread(text) 内部添加。这里需要
        # 用一个 hook 让 worker 添加时带上 attachments。
        # 简单做法：直接在这里手动 add_user，然后告诉 worker 不再 add。
        self._conv.add_user(text, attachments=atts)
        self._renderer.add_assistant_start()
        self._set_running(True)

        # 当前 profile 决定是否走视觉协议
        whitelist = list(getattr(self._config.config,
                                 'vision_model_whitelist', []))
        cfg_vision_on = bool(getattr(self._config.config,
                                     'vision_enabled', True))
        active_prof = self._config.get_active_profile()
        prof_vision_supported = bool(
            getattr(active_prof, 'vision_supported', False)
        ) if active_prof is not None else False
        # 视觉真正生效 = 全局开关打开 AND 当前模型在视觉白名单内
        # AND profile 自身声明支持视觉输入。三个条件缺一不可。
        model_name = getattr(active_prof, 'model', '') or ''
        vision_on = (
            cfg_vision_on
            and model_supports_vision(model_name, whitelist)
            and prof_vision_supported
        )
        # 当前 profile 的 Function Calling 总开关（profile.supports_tools）。
        # 这个值在 UI"启用 Function Calling"复选框里维护，过去版本里只写
        # 不读 → 用户关掉对话仍然带 tools，对 vita 这类视觉网关是致命的。
        tools_enabled = bool(
            getattr(active_prof, 'supports_tools', True)
        ) if active_prof is not None else True

        self._worker = AgentWorker(
            llm_client=self._llm,
            conversation=self._conv,
            dispatcher=self._dispatcher,
            max_tool_loops=self._get_active_max_loops(),
            max_history_tokens=self._get_active_max_history_tokens(),
            price_input_per_1m=self._get_active_prices()[0],
            price_output_per_1m=self._get_active_prices()[1],
            # 视觉协议是否启用：这里只决定"能不能把图片塞进 user content"，
            # 自动截图触发在 worker 内部再根据 profile.vision_supported 判断。
            vision_enabled=vision_on,
            vision_whitelist=whitelist,
            tools_enabled=tools_enabled,
            config_manager=self._config,
        )
        self._worker.set_sync_tool_runner(self._run_tool_sync)
        self._worker.set_system_prompt_addon_provider(
            self._build_system_prompt_addon,
        )
        # 根据当前 🌐 按钮状态决定本轮是否暴露 web_* 工具给 LLM
        use_web = self._should_use_web_this_turn()
        if not use_web:
            self._worker.set_tools_filter(
                lambda name: not name.startswith('web_'),
            )
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.tool_started.connect(self._on_tool_started)
        self._worker.tool_finished.connect(self._on_tool_finished)
        self._worker.text_message_complete.connect(self._on_text_complete)
        self._worker.status_changed.connect(self._on_status)
        self._worker.history_trimmed.connect(self._on_history_trimmed)
        self._worker.usage_received.connect(self._on_usage_received)
        self._worker.system_notice.connect(self._on_system_notice)
        self._worker.todo_updated.connect(self._on_todo_updated)
        # 崩溃防丢：worker 每追加一条 assistant/tool_result 就通知落盘。
        # 长任务中途 Max 崩溃时，已完成的步骤不再丢失。
        self._worker.turn_progress.connect(self._on_turn_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.skill_proposed.connect(self._on_skill_proposed)
        # 已经手动 add_user 了，告诉 worker 不要再 add
        self._worker.run_in_thread(text, skip_add_user=True)

    # ------------------------------------------------------------------ #
    # 多模态：附件按钮 / 截屏 / 粘贴 / 拖拽
    # ------------------------------------------------------------------ #
    def _on_attach_image(self):
        """📎 添加图片：弹出文件对话框选 1+ 张图。"""
        if self._is_running:
            return
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, '选择图片', '',
            'Image (*.png *.jpg *.jpeg *.gif *.webp *.bmp)',
        )
        if not paths:
            return
        from ..attachments import save_image_file
        for p in paths:
            att = save_image_file(p)
            if att is not None:
                self.attachment_strip.add(att)
            else:
                self._renderer.add_status(
                    '图片添加失败（可能过大或格式不支持）: {}'.format(p),
                )

    def _on_snip(self):
        """✂️ 截图：进程内 Qt 全屏框选，结果加入预览条。

        流程：
        1. hide 掉 Knot 面板顶层窗口——避免面板本身入镜；
           docked 状态的 QDockWidget 用 hide 不会丢失位置，show 后
           自动回到原停靠槽位。
        2. processEvents + msleep 让 Windows DWM 合成完成再抓屏
           （否则可能抓到 hide 前的旧画面）。
        3. ScreenshotOverlay 内部会遍历所有屏幕合成虚拟桌面，蒙层
           覆盖整个虚拟桌面，支持多屏跨屏框选。
        4. finally 里无条件 show 面板，无论抓屏成功还是异常。

        设计原则：
        - 不主动抬起 Max 主窗或其他任何应用——用户想截什么就截什么。
          比如截浏览器里的参考图、其他 DCC 软件的贴图预览。
        - 停靠时只隐藏内容区，QDockWidget 仍占位，show 后自动回来。

        历史坑（已修）：
        - v1：window().hide() 破坏 Max docked 状态 → 主面板消失。
        - v2：setWindowOpacity(0.0) → 透明窗口仍占 Z 序，DWM 抓屏抓到下层。
        - v3：强制 raise Max 主窗 → 越权抢走用户的截图目标。
        - v4（当前）：停靠时只隐藏内容 widget，浮动时隐藏整个 QDockWidget，
          不影响 Max 主窗口显示。
        """
        if self._is_running:
            return
        try:
            from .screenshot_overlay import ScreenshotOverlay
            from .input_attachments import pixmap_to_attachment
        except ImportError as exc:
            self._renderer.add_error(
                '截图模块加载失败: {}'.format(exc),
            )
            return
        top = self.window()
        qdock = self.parent()
        was_visible = False
        try:
            # 1. 隐藏 MaxAgent 面板本身，但不能影响 Max 主窗口。
            #    - 停靠状态：self.parent() 是 QDockWidget，且它的 parent
            #      是 Max 主窗口；此时只隐藏内部 content widget（self），
            #      QDockWidget 仍占位，Max 主窗口保持显示，截图能拍到 Max。
            #    - 浮动状态：self.parent() 就是 QDockWidget 这个独立顶层窗
            #      口，隐藏它即隐藏整个浮动面板。
            try:
                if qdock is not None and qdock.isFloating():
                    was_visible = qdock.isVisible()
                    if was_visible:
                        qdock.hide()
                else:
                    was_visible = self.isVisible()
                    if was_visible:
                        self.hide()
            except Exception:  # pylint: disable=broad-except
                was_visible = False
            # 2. 让 hide 完成合成再抓屏（DWM 合成延迟经验值 100~200ms）
            QtCore.QCoreApplication.processEvents(
                QtCore.QEventLoop.AllEvents, 100,
            )
            try:
                QtCore.QThread.msleep(150)
            except Exception:  # pylint: disable=broad-except
                pass
            QtCore.QCoreApplication.processEvents(
                QtCore.QEventLoop.AllEvents, 50,
            )
            # 3. 抓屏 + 框选（overlay 内部处理多屏 + HiDPI）
            pix = ScreenshotOverlay.capture_interactive()
        finally:
            # 任何分支都要把面板恢复回来（docked 也能正确回位）
            try:
                if was_visible:
                    if qdock is not None and qdock.isFloating():
                        qdock.show()
                        qdock.raise_()
                    else:
                        self.show()
                        if qdock is not None:
                            qdock.show()
                            qdock.raise_()
            except Exception:  # pylint: disable=broad-except
                pass
        if pix is None or pix.isNull():
            return
        att = pixmap_to_attachment(pix, name='screenshot')
        if att is not None:
            self.attachment_strip.add(att)
        else:
            self._renderer.add_status('截图保存失败')

    def _on_image_dropped(self, payload, mime, name):
        """从输入框转抛上来的图片：bytes 或 文件路径。"""
        if self._is_running:
            return
        from ..attachments import save_image_bytes
        from ..attachments import save_image_file
        att = None
        if isinstance(payload, (bytes, bytearray)):
            att = save_image_bytes(bytes(payload), mime=mime, name=name)
        elif isinstance(payload, str):
            att = save_image_file(payload, name=name)
        if att is not None:
            self.attachment_strip.add(att)
        else:
            self._renderer.add_status(
                '图片添加失败（可能过大或读失败）',
            )

    # ------------------------------------------------------------------ #
    # 视觉降级提示条
    # ------------------------------------------------------------------ #
    def _refresh_vision_hint(self):
        """根据当前 profile + 视觉开关 + 是否有附件，刷新提示条显隐。

        触发点：附件增删（``AttachmentStrip.changed``）、profile 切换、
        设置对话框保存后。提示条本身只决定文案/可见性，不阻断发送——
        让 LLM 端拿到"[图片] N 张"占位提示，与现有降级行为保持一致。
        """
        try:
            cfg = self._config.config
            has_atts = bool(self.attachment_strip.attachments())
            vision_on = bool(getattr(cfg, 'vision_enabled', True))
            whitelist = list(getattr(cfg, 'vision_model_whitelist', []))
            prof = self._config.get_active_profile()
            model_name = ''
            prof_vision_supported = False
            if prof is not None:
                model_name = getattr(prof, 'model', '') or ''
                prof_vision_supported = bool(
                    getattr(prof, 'vision_supported', False)
                )
            supported = (
                model_supports_vision(model_name, whitelist)
                and prof_vision_supported
            )
            self.vision_hint.set_state(
                has_attachments=has_atts,
                vision_enabled=vision_on,
                vision_supported=supported,
                model_name=model_name,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug('refresh_vision_hint 异常: %s', exc)

    def _on_vision_hint_switch_profile(self):
        """提示条上"切换模型"按钮：把焦点交给顶部 profile 下拉。"""
        try:
            self.profile_combo.setFocus()
            # 直接展开下拉，让用户一眼看到所有候选
            self.profile_combo.showPopup()
        except Exception:  # pylint: disable=broad-except
            pass

    def _on_stop(self):
        if self._worker is not None:
            # DEBUG 埋点：用户停止
            logger.debug('ui_stop clicked')
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
            self.send_btn.setText(_btn_label('⏹', '停止'))
            self.send_btn.setObjectName('stopBtn')
            self.send_btn.setToolTip('停止当前对话')
            self.send_btn.setEnabled(True)
        else:
            self.send_btn.setText(_btn_label('🚀', '发送'))
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

    def _on_system_notice(self, level, message):
        """将 worker 发出的系统通知插入对话流为持久气泡。

        level: 'info' / 'warn' / 'error'
        """
        try:
            self._renderer.add_system_notice(level, message)
        except Exception:  # pylint: disable=broad-except
            # 通知渲染失败不应中断主流程
            pass

    def _on_todo_updated(self, session_id, snapshot):
        """LLM 通过 todo_write/update_status 修改清单后触发此槽。

        每会话保持一张任务卡：首次出现时插入气泡，后续更新就地刷新，
        避免把整段对话历史被 checklist 占满。
        """
        try:
            self._renderer.add_or_update_todo_bubble(session_id, snapshot)
        except Exception:  # pylint: disable=broad-except
            pass

    def _on_finished(self):
        self._renderer.end_turn()
        self.status_label.setText(_ee('✅') + ' 完成')
        self._set_running(False)
        # 停止节流 timer，避免 finished 后再触发一次冗余保存
        timer = getattr(self, '_turn_save_timer', None)
        if timer is not None and timer.isActive():
            timer.stop()
        self._save_current_session()
        self._refresh_context_label()

    def _on_turn_progress(self):
        """崩溃防丢：worker 每完成一步立即落盘（带节流）。

        触发时机：worker 追加了 assistant 消息或 tool_result 之后。
        session_mgr.save 用 tmp+rename 原子写，重复触发无副作用；
        长任务中途 Max 崩溃时，已保存的步骤会在下次启动时正确加载。

        节流策略：最短 500ms 落一次盘。避免长任务连续追加大量
        tool_result 时把 IO 打满；即使崩溃丢失也最多丢 500ms 的进度。
        """
        # 复用同一个 QTimer 单发，避免重复排队
        timer = getattr(self, '_turn_save_timer', None)
        if timer is None:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(500)
            timer.timeout.connect(self._do_turn_progress_save)
            self._turn_save_timer = timer
        if not timer.isActive():
            timer.start()

    def _do_turn_progress_save(self):
        """turn_progress 节流后的真正落盘动作。"""
        try:
            self._save_current_session()
        except Exception:  # pylint: disable=broad-except
            # 落盘失败不能阻塞下一步；下次触发时会重试
            logger.warning('turn_progress 落盘失败', exc_info=True)

    def _on_failed(self, err):
        self._renderer.add_error(err)
        self.status_label.setText(_ee('❌') + ' 失败')
        self._set_running(False)
        # 停止节流 timer，避免 failed 后再触发一次冗余保存
        timer = getattr(self, '_turn_save_timer', None)
        if timer is not None and timer.isActive():
            timer.stop()
        # 失败也保存：用户能在历史里看到失败原因
        self._save_current_session()
        self._refresh_context_label()

    def _on_skill_proposed(self, manifest, impl_code):
        # type: (dict, str) -> None
        """worker 提议把本轮操作沉淀为 Skill，弹出确认对话框。"""
        name = manifest.get('name', '未命名')
        text = (
            '检测到本次会话执行了一系列成功操作。\n'
            '是否把该流程保存为可复用 Skill？\n\n'
            '名称：{}\n'
            '状态：draft\n'
            '触发词：{}\n\n'
            '保存后可在设置面板的技能管理中查看和编辑。'
        ).format(
            name,
            ' / '.join(manifest.get('trigger_keywords', []) or ['（无）']),
        )
        ret = QtWidgets.QMessageBox.question(
            self, '保存为 Skill？', text,
            QtWidgets.QMessageBox.Save
            | QtWidgets.QMessageBox.Discard,
            QtWidgets.QMessageBox.Discard,
        )
        if ret != QtWidgets.QMessageBox.Save:
            return
        try:
            from ..skills import Skill, SkillManager
            skill = Skill.from_dict(manifest)
            mgr = SkillManager()
            mgr.save(skill, overwrite=True)
            if impl_code:
                impl_path = mgr._impl_path_for(skill)
                with open(impl_path, 'w', encoding='utf-8') as fh:
                    fh.write(impl_code)
            self._renderer.add_status(
                '已保存 Skill 草案：{}'.format(name),
            )
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.critical(
                self, '保存失败',
                '保存 Skill 失败：{}'.format(exc),
            )

    # ------------------------------------------------------------------ #
    # 主线程同步工具执行
    # ------------------------------------------------------------------ #
    @QtCore.Slot(object)
    def _invoke_main_slot(self, fn):
        """主线程槽：在主线程事件循环里执行任意 callable。

        通过 ``self._invoke_main_signal.emit(fn)`` 从任意线程触发，Qt
        会按 self 的亲缘（主线程）+ QueuedConnection 把调用排到主线程
        队列。fn 自身的异常在这里捕获并落日志，避免冒泡破坏 Qt 事件循环。
        """
        try:
            fn()
        except Exception:  # pylint: disable=broad-except
            logger.exception('invoke_main callable raised')

    def _post_to_main(self, fn):
        """从任意线程把 callable 调度到主线程执行（不等待）。

        :param fn: 无参可调用对象。**禁止**传入 ``functools.partial`` 之外
            的需要参数的函数；如需传参，请用闭包捕获。
        """
        self._invoke_main_signal.emit(fn)

    def _run_tool_sync(self, tool_name, arguments):
        """Worker 子线程通过此函数同步派回主线程执行 pymxs。

        关键设计:

        1. **尊重 ``run_on_main_thread`` 标志**：纯 Python 工具
           （如 ``list_skills`` / ``propose_new_tool``）声明
           ``run_on_main_thread=False``，直接在子线程跑，不走主线程
           marshal —— 这能避免主线程繁忙时这类轻量工具假装"超时"。
        2. **主线程嵌套调用直接同步执行**——避免 QTimer.singleShot 入队
           导致的"主线程 → 等主线程"自死锁。
        3. **动态心跳超时**：子线程等待时分片轮询（每 100ms 醒一次），
           除了检查取消标志，还会观察 ``done`` 信号。基础窗口 60s，
           触发后自动延期一次（最多 5 次 = 累计 5 分钟），同时把
           "等待中…X s" 心跳通过 ``status_changed`` 反馈给 UI，
           让用户看到工具还在跑而不是卡死。LLM 推理时长本就不可控，
           这套机制让超时不再是"要么误杀 list_skills、要么干等 5 分钟"
           的二选一。
        4. **错误信息**带上工具名 + 参数预览 + 累计等待时长，方便定位。
        """
        from ..tools.registry import get_tool

        # 0. 先看工具规格：不需要主线程的工具直接子线程跑
        spec = get_tool(tool_name)
        if spec is not None and not spec.run_on_main_thread:
            return self._dispatcher.dispatch(tool_name, arguments)

        # 1. 当前已在主线程：直接同步执行（嵌套调用安全路径）
        cur_thread = QtCore.QThread.currentThread()
        app = QApplication.instance()
        main_thread = app.thread() if app is not None else None
        if main_thread is None or cur_thread is main_thread:
            return self._dispatcher.dispatch(tool_name, arguments)

        # 2. 子线程 → 主线程派发
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

        # 关键：通过主线程亲缘的 QObject 信号 + QueuedConnection 派发，
        # 而不是 QtCore.QTimer.singleShot(0, _run_in_main)——后者会被绑定
        # 到当前 worker 子线程的事件循环（worker 子线程此时正在 done.wait
        # 阻塞，根本不 spin 事件循环），导致 _run_in_main 永远不被调用。
        self._post_to_main(_run_in_main)

        # 3. 动态心跳等待 + 主线程存活探测
        # 单次窗口 60s，累计上限 = 60s × (1 + max_extensions)
        poll_interval = 0.1
        base_window = 60.0
        max_extensions = 5
        cancel_event = getattr(self._worker, '_cancel_event', None)
        worker = self._worker

        def _emit_status(text):
            """通过 worker 把心跳状态发到 UI（worker 在子线程，
            status_changed 是 QueuedConnection 自动 marshal）。"""
            if worker is None:
                return
            try:
                worker.status_changed.emit(text)
            except Exception:  # pylint: disable=broad-except
                pass

        # ping 探测器：定期向主线程投递一个空 lambda，回包则代表主线程
        # Qt 事件循环还在转。用于区分"当前 task 自己卡死" vs "Max 主线程
        # 整体被其他东西卡死（视口刷新 / 模态对话框 / 第三方插件 hang）"。
        ping_state = {
            'in_flight': False,        # 是否有 ping 正在路上
            'sent_at': 0.0,             # 最近一次发出 ping 的时间
            'last_ack_at': time.time(), # 最近一次成功收到 pong 的时间
            'rtt_ms': 0.0,              # 最近一次 ping-pong 往返耗时
            'count_ok': 0,              # 累计成功次数
            'count_lost': 0,            # 累计未回响（在再次 ping 前未到达）
        }

        def _do_ping():
            now = time.time()
            ping_state['in_flight'] = False
            ping_state['last_ack_at'] = now
            ping_state['rtt_ms'] = (now - ping_state['sent_at']) * 1000
            ping_state['count_ok'] += 1

        def _send_ping():
            if ping_state['in_flight']:
                # 上一次还没回，本次不再投递，计为丢失
                ping_state['count_lost'] += 1
                return
            ping_state['in_flight'] = True
            ping_state['sent_at'] = time.time()
            # 同 _run_in_main 派发：必须用 _post_to_main 走主线程亲缘
            # QObject 的 QueuedConnection，否则 _do_ping 会被钉在子线程
            # 永不触发，count_ok 始终为 0。
            self._post_to_main(_do_ping)

        ping_interval = 10.0  # 每 10 秒探测一次

        extensions_used = 0
        total_elapsed = 0.0
        last_status_at = 0.0
        last_ping_at = 0.0
        while True:
            window_elapsed = 0.0
            while window_elapsed < base_window:
                if done.wait(timeout=poll_interval):
                    break
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError(
                        '用户取消了工具 {} 的执行'.format(tool_name),
                    )
                window_elapsed += poll_interval
                total_elapsed += poll_interval

                # 每 ping_interval 秒探测一次主线程是否还能处理事件
                if total_elapsed - last_ping_at >= ping_interval:
                    last_ping_at = total_elapsed
                    _send_ping()

                # 每 5 秒推一次心跳给 UI（避免信号风暴）
                if total_elapsed - last_status_at >= 5.0:
                    last_status_at = total_elapsed
                    since_ack = time.time() - ping_state['last_ack_at']
                    if ping_state['in_flight'] and since_ack > 5.0:
                        # ping 派出去 5 秒还没回 → 主线程明显在忙
                        _emit_status(
                            '工具 {} 执行中…已用 {:.0f}s（主线程繁忙'
                            ' {:.0f}s 未响应 ping）'.format(
                                tool_name, total_elapsed, since_ack,
                            ),
                        )
                    else:
                        _emit_status(
                            '工具 {} 执行中…已用 {:.0f}s'
                            '（主线程心跳 OK, RTT={:.0f}ms）'.format(
                                tool_name, total_elapsed,
                                ping_state['rtt_ms'],
                            ),
                        )

            if done.is_set():
                break

            # 本轮窗口耗尽，决定是否延期
            if extensions_used < max_extensions:
                extensions_used += 1
                _emit_status(
                    '工具 {} 已运行 {:.0f}s，自动延期 ({}/{})…'.format(
                        tool_name, total_elapsed,
                        extensions_used, max_extensions,
                    ),
                )
                continue

            # 真正超时：先做诊断快照，区分"task 自卡" vs "主线程整体卡死"
            since_ack = time.time() - ping_state['last_ack_at']
            if since_ack > ping_interval * 2 and ping_state['count_lost'] > 0:
                diag = (
                    '主线程整体卡死（最近 {:.0f}s 未响应 ping，'
                    '丢失 {} 次，成功 {} 次）—— 可能原因：'
                    'Max 弹出隐性模态对话框 / 视口大重计算 / 第三方插件 hang'
                ).format(
                    since_ack, ping_state['count_lost'],
                    ping_state['count_ok'],
                )
            else:
                diag = (
                    '主线程仍在响应 ping（最近回包 {:.1f}s 前，'
                    '成功 {} 次，丢失 {} 次）—— 是当前工具自身耗时过长，'
                    '不是事件循环卡死'
                ).format(
                    since_ack, ping_state['count_ok'],
                    ping_state['count_lost'],
                )
            arg_preview = repr(arguments)
            if len(arg_preview) > 120:
                arg_preview = arg_preview[:120] + '...'
            err_msg = (
                '工具 {} 在主线程执行超时（累计 {:.0f}s，已延期 {} 次）；'
                '诊断: {}；参数: {}'
            ).format(
                tool_name, total_elapsed, extensions_used,
                diag, arg_preview,
            )
            logger.warning(
                'tool_sync timeout: tool=%s elapsed=%.0fs ext=%d ping=%s',
                tool_name, total_elapsed, extensions_used, ping_state,
            )
            raise RuntimeError(err_msg)

        if 'error' in result_box:
            raise result_box['error']
        return result_box.get('value')


# ---------------------------------------------------------------------- #
# DCC 感知的统一停靠入口
# ---------------------------------------------------------------------- #

# 模块级单例引用：避免 dock 面板 / 停靠包装层被 Python GC 回收
_DOCK_WIDGET = None  # type: Optional[MaxAgentDockWidget]
_DOCK_HOLDER = None  # type: Optional[Any]


def get_or_create_dock(force=False):
    # type: (bool) -> Optional[Any]
    """显示并返回 MaxAgent 主面板，根据当前 DCC 自动选择停靠方式。

    Max 环境：用 ``QDockWidget`` 嵌入到 Max 主窗口（保留原有几何恢复逻辑）。
    Maya 环境：用 ``cmds.workspaceControl()`` 创建可停靠面板，并把
    MaxAgentDockWidget 作为其内容 widget。
    非 DCC 环境：作为独立顶层窗口显示。

    :param force: 是否忽略 ``auto_show_on_startup`` 配置强制显示
    :returns: 包装后的停靠/窗口对象（Max 返回 QDockWidget，Maya 返回
        workspaceControl 名，独立窗口返回 MaxAgentDockWidget 自身）
    """
    # pylint: disable=global-statement,import-outside-toplevel
    global _DOCK_WIDGET, _DOCK_HOLDER

    from ..config import ConfigManager
    from ..dcc.runtime import current_dcc
    from ..logger import get_logger
    from ..tools import load_all_tools
    from .emoji_compat import install_app_font_fallback

    logger = get_logger(__name__)

    # 1. 加载工具（幂等）
    try:
        load_all_tools()
    except Exception:  # pylint: disable=broad-except
        logger.exception('get_or_create_dock 加载工具失败')

    # 2. 配置门控
    if not force:
        try:
            cfg = ConfigManager()
            if not bool(cfg.config.auto_show_on_startup):
                logger.info(
                    'auto_show_on_startup=False，跳过自动显示；'
                    '可调用 get_or_create_dock(force=True) 手动显示'
                )
                return None
        except Exception:  # pylint: disable=broad-except
            pass

    # 3. 复用已存在实例
    if _DOCK_WIDGET is not None:
        try:
            if _DOCK_HOLDER is not None:
                _DOCK_HOLDER.show()
                _DOCK_HOLDER.raise_()
            else:
                _DOCK_WIDGET.show()
                _DOCK_WIDGET.raise_()
            return _DOCK_HOLDER or _DOCK_WIDGET
        except Exception:  # pylint: disable=broad-except
            _DOCK_WIDGET = None
            _DOCK_HOLDER = None

    # 字体回退族必须在创建业务 widget 前安装
    try:
        install_app_font_fallback()
    except Exception:  # pylint: disable=broad-except
        logger.debug('install_app_font_fallback failed', exc_info=True)

    dcc = current_dcc()
    config = ConfigManager()

    if dcc == '3dsmax':
        return _create_max_dock(config)
    if dcc == 'maya':
        return _create_maya_dock(config)
    return _create_standalone_window(config)


def _create_max_dock(config):
    # type: (ConfigManager) -> Any
    """Max 环境：QDockWidget + addDockWidget，复用原 startup.py 逻辑。"""
    # pylint: disable=import-outside-toplevel
    global _DOCK_WIDGET, _DOCK_HOLDER  # noqa: F824
    from ..qt_compat import QtCore
    from ..qt_compat import QtWidgets
    from ..qt_compat import get_max_main_window
    from ..startup import (
        _connect_qdock_save_hooks,
        _restore_main_window_state,
        _restore_qdock_geometry,
    )

    main_win = get_max_main_window()
    dock_widget = MaxAgentDockWidget(config_manager=config)
    ui_state = dock_widget.get_ui_state()

    if main_win is not None:
        qdock = QtWidgets.QDockWidget('MaxAgent', parent=main_win)
        qdock.setObjectName('MaxAgentQDockWidget')
        qdock.setWidget(dock_widget)
        qdock.setAllowedAreas(
            QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea,
        )

        want_floating = bool(getattr(ui_state, 'floating', True))
        if not (ui_state.geometry_b64 or '').strip():
            want_floating = True

        if want_floating:
            try:
                qdock.setWindowFlags(
                    QtCore.Qt.Tool
                    | QtCore.Qt.WindowTitleHint
                    | QtCore.Qt.WindowCloseButtonHint,
                )
            except Exception:  # pylint: disable=broad-except
                pass
            qdock.setFloating(True)
            restored = _restore_qdock_geometry(qdock, ui_state)
            if not restored:
                qdock.resize(440, 760)
                try:
                    mg = main_win.geometry()
                    cx = mg.x() + mg.width() // 2 - 220
                    cy = mg.y() + mg.height() // 2 - 380
                    qdock.move(max(cx, 50), max(cy, 50))
                except Exception:  # pylint: disable=broad-except
                    pass
        else:
            try:
                area = QtCore.Qt.DockWidgetArea(int(ui_state.dock_area or 2))
            except Exception:  # pylint: disable=broad-except
                area = QtCore.Qt.RightDockWidgetArea
            try:
                main_win.addDockWidget(area, qdock)
            except Exception:  # pylint: disable=broad-except
                qdock.setFloating(True)
            _restore_qdock_geometry(qdock, ui_state)
            _restore_main_window_state(main_win, ui_state)

        _connect_qdock_save_hooks(qdock, dock_widget, main_win=main_win)
        qdock.show()
        qdock.raise_()
    else:
        # 即使探测为 Max 也拿不到主窗口时，退化为独立窗口
        dock_widget = MaxAgentDockWidget(config_manager=config)
        _restore_standalone_geometry(dock_widget, dock_widget.get_ui_state())
        dock_widget.show()

    _DOCK_WIDGET = dock_widget
    _DOCK_HOLDER = qdock if main_win is not None else None
    return _DOCK_HOLDER or _DOCK_WIDGET


def _create_maya_dock(config):
    # type: (ConfigManager) -> str
    """Maya 环境：使用 workspaceControl 创建可停靠面板。"""
    # pylint: disable=import-outside-toplevel
    global _DOCK_WIDGET, _DOCK_HOLDER  # noqa: F824
    from ..qt_compat import QtWidgets
    from ..qt_compat import get_shiboken_wrap_instance

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    dock_widget = MaxAgentDockWidget(config_manager=config)
    ui_state = dock_widget.get_ui_state()

    control_name = 'MaxAgentWorkspaceControl'
    label = 'MaxAgent · AI 助手'

    # 已存在则先关闭再重建，避免重复创建导致句柄冲突
    if cmds.workspaceControl(control_name, exists=True):
        try:
            cmds.deleteUI(control_name, control=True)
        except Exception:  # pylint: disable=broad-except
            pass

    has_geometry = bool((ui_state.geometry_b64 or '').strip())
    create_kwargs = {
        'label': label,
        'dockToControl': ['MayaWindow', 'right'],
        'retain': False,
        'loadImmediately': True,
        'visible': True,
    }  # type: dict
    if not has_geometry:
        create_kwargs['initialWidth'] = 440
        create_kwargs['initialHeight'] = 760

    try:
        cmds.workspaceControl(control_name, **create_kwargs)
    except Exception:  # pylint: disable=broad-except
        # 某些 Maya 版本对初始尺寸参数支持不同，回退最小参数创建
        cmds.workspaceControl(
            control_name,
            label=label,
            dockToControl=['MayaWindow', 'right'],
            retain=False,
            loadImmediately=True,
            visible=True,
        )

    # 把 QWidget 附加到 workspaceControl
    wrap_instance = get_shiboken_wrap_instance()
    if wrap_instance is not None:
        try:
            from maya import OpenMayaUI as omui  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
            ptr = omui.MQtUtil.findControl(control_name)
        except Exception:  # pylint: disable=broad-except
            ptr = None

        if ptr is not None:
            try:
                control_widget = wrap_instance(int(ptr), QtWidgets.QWidget)
                # 清空旧布局（Maya 默认 workspaceControl 可能已有占位 layout）
                old_layout = control_widget.layout()
                if old_layout is not None:
                    while old_layout.count():
                        item = old_layout.takeAt(0)
                        widget = item.widget()
                        if widget is not None:
                            widget.setParent(None)
                layout = QtWidgets.QVBoxLayout(control_widget)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(0)
                layout.addWidget(dock_widget)
                control_widget.setWindowTitle(label)
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    '把 MaxAgentDockWidget 附加到 Maya workspaceControl 失败'
                )
        else:
            logger.warning(
                '未找到 workspaceControl %s 的 QWidget 句柄', control_name
            )
    else:
        logger.warning('当前环境未找到 shiboken，无法把 Widget 嵌入 Maya')

    try:
        cmds.workspaceControl(control_name, edit=True, visible=True)
    except Exception:  # pylint: disable=broad-except
        pass

    _DOCK_WIDGET = dock_widget
    _DOCK_HOLDER = control_name
    return control_name


def _create_standalone_window(config):
    # type: (ConfigManager) -> MaxAgentDockWidget
    """非 DCC 环境：作为普通顶层窗口显示。"""
    # pylint: disable=import-outside-toplevel
    global _DOCK_WIDGET, _DOCK_HOLDER  # noqa: F824
    from ..startup import _restore_standalone_geometry

    dock_widget = MaxAgentDockWidget(config_manager=config)
    ui_state = dock_widget.get_ui_state()
    _restore_standalone_geometry(dock_widget, ui_state)
    dock_widget.show()
    dock_widget.raise_()
    _DOCK_WIDGET = dock_widget
    _DOCK_HOLDER = None
    return dock_widget


# ---------------------------------------------------------------------- #
# 输入框：Enter 发送 / Shift+Enter 换行
# ---------------------------------------------------------------------- #
class _SmartInput(QtWidgets.QPlainTextEdit):
    """支持 Enter 发送、Shift+Enter 换行、Ctrl+Enter 发送的输入框。

    扩展能力（多模态）：
    - Ctrl+V 粘贴剪贴板里的图片，转字节后通过 ``image_dropped(bytes, mime, name)``
      信号外抛给 dock_widget 处理；
    - 拖拽图片文件进来同样通过 ``image_dropped`` 抛出（这里只抛文件路径）。
    """

    send_requested = QtCore.Signal()
    # 抛出图片：(payload, mime, name)，payload 是 bytes 或文件路径(str)
    # bytes 表示来自剪贴板，str 表示来自拖拽文件
    image_dropped = QtCore.Signal(object, str, str)

    # 拖拽时识别的图片扩展名
    _IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')

    def __init__(self, parent=None):
        super(_SmartInput, self).__init__(parent)
        self.setAcceptDrops(True)

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

    # ------------------------------------------------------------------ #
    # 剪贴板：拦截 Ctrl+V 时如果含图片，截走转 bytes
    # ------------------------------------------------------------------ #
    def insertFromMimeData(self, source):  # noqa: D401  Qt 重载
        if source is None:
            return
        # 1. 图片字节（来自截屏 / 复制图片）
        if source.hasImage():
            img = source.imageData()
            if img is not None:
                pix = self._mime_image_to_pixmap(img)
                if pix is not None and not pix.isNull():
                    raw = self._pixmap_to_png_bytes(pix)
                    if raw:
                        self.image_dropped.emit(raw, 'image/png', '剪贴板图片')
                        return
        # 2. 文件 URL（资源管理器复制的图片文件）
        if source.hasUrls():
            handled = False
            for url in source.urls():
                p = url.toLocalFile() if hasattr(url, 'toLocalFile') else ''
                if p and p.lower().endswith(self._IMAGE_EXTS):
                    self.image_dropped.emit(p, 'image/png', p)
                    handled = True
            if handled:
                return
        # 其它情况走默认（粘贴文本）
        super(_SmartInput, self).insertFromMimeData(source)

    # ------------------------------------------------------------------ #
    # 拖拽图片
    # ------------------------------------------------------------------ #
    def dragEnterEvent(self, event):  # noqa: D401
        md = event.mimeData()
        if md is None:
            return super(_SmartInput, self).dragEnterEvent(event)
        if md.hasImage() or self._has_image_url(md):
            event.acceptProposedAction()
            return
        super(_SmartInput, self).dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: D401
        md = event.mimeData()
        if md is not None and (md.hasImage() or self._has_image_url(md)):
            event.acceptProposedAction()
            return
        super(_SmartInput, self).dragMoveEvent(event)

    def dropEvent(self, event):  # noqa: D401
        md = event.mimeData()
        if md is None:
            return super(_SmartInput, self).dropEvent(event)
        if md.hasImage():
            img = md.imageData()
            pix = self._mime_image_to_pixmap(img)
            if pix is not None and not pix.isNull():
                raw = self._pixmap_to_png_bytes(pix)
                if raw:
                    self.image_dropped.emit(raw, 'image/png', '拖入图片')
                    event.acceptProposedAction()
                    return
        if self._has_image_url(md):
            for url in md.urls():
                p = url.toLocalFile() if hasattr(url, 'toLocalFile') else ''
                if p and p.lower().endswith(self._IMAGE_EXTS):
                    self.image_dropped.emit(p, 'image/png', p)
            event.acceptProposedAction()
            return
        super(_SmartInput, self).dropEvent(event)

    @classmethod
    def _has_image_url(cls, mime_data):
        if not mime_data.hasUrls():
            return False
        for url in mime_data.urls():
            p = url.toLocalFile() if hasattr(url, 'toLocalFile') else ''
            if p and p.lower().endswith(cls._IMAGE_EXTS):
                return True
        return False

    @staticmethod
    def _mime_image_to_pixmap(img):
        """把 mime imageData 统一转成 QPixmap。"""
        if img is None:
            return None
        if isinstance(img, QtGui.QPixmap):
            return img
        if isinstance(img, QtGui.QImage):
            return QtGui.QPixmap.fromImage(img)
        # 某些后端会把图片包装为 QVariant，尝试 fromImage 兜底
        try:
            return QtGui.QPixmap.fromImage(QtGui.QImage(img))
        except Exception:  # pylint: disable=broad-except
            return None

    @staticmethod
    def _pixmap_to_png_bytes(pixmap):
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        if not pixmap.save(buf, 'PNG'):
            return b''
        return bytes(buf.data())

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