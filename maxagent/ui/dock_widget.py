#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主停靠面板：聊天界面 + 工具调用展示 + 设置入口。

UI 布局:
+-------------------------------------------+
|  [Profile: ▼ DeepSeek-Chat]   [⚙设置]     |   ← 顶部条
+-------------------------------------------+
|                                           |
|  💬 用户: 创建一个红色的茶壶              |
|  🤖 助手: 好的，我来创建...               |
|     ▶ 工具 create_teapot {radius:10}     |
|       ✓ {"name": "Teapot001"}            |
|     ▶ 工具 create_standard_material      |
|       ✓ {"name": "AgentStandard"}        |
|     已完成。                              |
|                                           |
+-------------------------------------------+
|  [输入框 (Ctrl+Enter 发送)]  [发送][停止]|
+-------------------------------------------+

线程模型:
- 所有 UI 槽函数都跑在主线程
- AgentWorker 跑在子线程，通过 Signal/Slot 与 UI 通信
- 工具执行通过 _run_tool_sync 在主线程（即 Max 线程）执行 pymxs
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import threading
import time
import traceback
from typing import Any
from typing import Dict
from typing import Optional

from ..agent import AgentWorker
from ..agent import Conversation
from ..config import ConfigManager
from ..llm_client import LLMClient
from ..qt_compat import QApplication
from ..qt_compat import QtCore
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets
from ..qt_compat import Signal
from ..tools import ToolDispatcher


# 简单的样式表
_STYLE = """
QTextBrowser, QPlainTextEdit, QTextEdit {
    background-color: #2b2b2b;
    color: #d4d4d4;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
    font-size: 11pt;
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
QComboBox {
    background-color: #3c3c3c;
    color: #d4d4d4;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 2px 8px;
    min-height: 22px;
}
QLabel { color: #d4d4d4; }
"""


class _ChatRenderer(object):
    """用 HTML 增量渲染聊天内容到 QTextBrowser。

    - 用户消息: 灰色块
    - 助手文本: 浅蓝色块
    - 工具调用: 折叠样式（⚙ icon + 参数 + 结果）
    - 错误: 红色块
    """

    def __init__(self, browser):
        self._browser = browser
        self._cur_assistant_html_pos = None

    def append_html(self, html):
        """追加一段 HTML 到末尾。"""
        cur = self._browser.toHtml()
        # 简单粗暴：直接 setHtml 会闪烁；用 textCursor + insertHtml 更平滑
        cursor = self._browser.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertHtml(html)
        self._browser.setTextCursor(cursor)
        self._browser.ensureCursorVisible()

    def add_user(self, text):
        safe = self._escape(text).replace('\n', '<br>')
        html = (
            '<div style="margin:8px 0;padding:8px 12px;'
            'background:#3a3a3a;border-radius:6px;">'
            '<b style="color:#aad4ff;">👤 你</b><br>{}</div>'
        ).format(safe)
        self.append_html(html)

    def add_assistant_start(self):
        """开始一段助手回复（流式追加 token 用）。"""
        html = (
            '<div style="margin:8px 0;padding:8px 12px;'
            'background:#2d3d2d;border-radius:6px;">'
            '<b style="color:#a8e6a8;">🤖 助手</b><br>'
            '<span id="assistant-stream"></span></div>'
        )
        self.append_html(html)

    def add_assistant_chunk(self, chunk):
        """流式追加 token。"""
        safe = self._escape(chunk).replace('\n', '<br>')
        cursor = self._browser.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertHtml(safe)
        self._browser.setTextCursor(cursor)
        self._browser.ensureCursorVisible()

    def add_tool_call(self, name, args_str, dangerous=False):
        try:
            args_obj = json.loads(args_str)
            args_pretty = json.dumps(
                args_obj, ensure_ascii=False, indent=2,
            )
        except (TypeError, ValueError):
            args_pretty = args_str
        icon = '⚠️' if dangerous else '🔧'
        color = '#ffaa66' if dangerous else '#cccccc'
        html = (
            '<div style="margin:4px 0 4px 16px;padding:6px 10px;'
            'background:#252525;border-left:3px solid {color};'
            'font-family:Consolas,monospace;font-size:10pt;">'
            '<b style="color:{color};">{icon} 调用工具:</b> '
            '<code>{name}</code>'
            '<pre style="margin:4px 0 0 0;color:#888;">{args}</pre>'
            '</div>'
        ).format(
            color=color, icon=icon,
            name=self._escape(name),
            args=self._escape(args_pretty),
        )
        self.append_html(html)

    def add_tool_result(self, name, ok, result_str):
        try:
            obj = json.loads(result_str)
            pretty = json.dumps(obj, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            pretty = result_str
        # 长结果折叠显示
        if len(pretty) > 600:
            pretty = pretty[:600] + '\n... (截断)'
        symbol = '✓' if ok else '✗'
        color = '#8fce8f' if ok else '#e57373'
        html = (
            '<div style="margin:0 0 4px 32px;padding:4px 10px;'
            'background:#1f1f1f;border-left:3px solid {color};'
            'font-family:Consolas,monospace;font-size:10pt;color:#bbb;">'
            '<b style="color:{color};">{sym}</b> '
            '<pre style="margin:2px 0 0 0;">{body}</pre></div>'
        ).format(
            color=color, sym=symbol,
            body=self._escape(pretty),
        )
        self.append_html(html)

    def add_status(self, text):
        html = (
            '<div style="margin:4px 0;color:#888;font-style:italic;'
            'font-size:10pt;">⋯ {}</div>'
        ).format(self._escape(text))
        self.append_html(html)

    def add_error(self, text):
        html = (
            '<div style="margin:8px 0;padding:8px 12px;'
            'background:#4a2a2a;border-radius:6px;color:#ffaaaa;">'
            '<b>⚠ 错误</b><br>{}</div>'
        ).format(self._escape(text).replace('\n', '<br>'))
        self.append_html(html)

    @staticmethod
    def _escape(s):
        if s is None:
            return ''
        return (
            str(s)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
        )


class MaxAgentDockWidget(QtWidgets.QWidget):
    """主聊天面板。

    Max 那边会用 QtMax.GetQMaxMainWindow() 当 parent，把这个 widget 包到一个
    QDockWidget 里贴在 Max 主窗口上。在非 Max 环境也能独立 show() 出来调试。
    """

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
        self._worker = None  # type: Optional[AgentWorker]
        # 为了避免短时间重复发，加个发送锁
        self._is_running = False

        self._build_ui()
        self._refresh_profiles()

    # ------------------------------------------------------------------ #
    # 构建 UI
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        # 顶部条
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

        # 聊天区
        self.chat = QtWidgets.QTextBrowser()
        self.chat.setOpenExternalLinks(True)
        outer.addWidget(self.chat, 1)
        self._renderer = _ChatRenderer(self.chat)

        # 状态栏
        self.status_label = QtWidgets.QLabel('准备就绪')
        self.status_label.setStyleSheet('color:#888;font-size:10pt;')
        outer.addWidget(self.status_label)

        # 输入区
        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(4)
        self.input_edit = QtWidgets.QPlainTextEdit()
        self.input_edit.setFixedHeight(72)
        self.input_edit.setPlaceholderText(
            '在这里输入指令... (Ctrl+Enter 发送)',
        )
        bottom.addWidget(self.input_edit, 1)
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
        bottom.addLayout(btn_col)
        outer.addLayout(bottom)

        # 快捷键 Ctrl+Enter
        try:
            shortcut = QtGui.QShortcut(
                QtGui.QKeySequence('Ctrl+Return'), self,
            )
            shortcut.activated.connect(self._on_send)
        except Exception:  # pylint: disable=broad-except
            # 某些 Qt 版本 QShortcut 在 QtWidgets 下
            try:
                shortcut = QtWidgets.QShortcut(
                    QtGui.QKeySequence('Ctrl+Return'), self,
                )
                shortcut.activated.connect(self._on_send)
            except Exception:  # pylint: disable=broad-except
                pass

        # 欢迎语
        self._renderer.append_html(
            '<div style="color:#888;padding:8px;">'
            '👋 你好，我是 <b>MaxAgent</b>。'
            '试试说："创建一个红色茶壶并加 TurboSmooth"，'
            '或者"列出场景里所有灯光"。'
            '</div>',
        )

    def _refresh_profiles(self):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        active = self._config.get_active_profile_name()
        for name in self._config.list_profile_names():
            self.profile_combo.addItem(name)
        # 选中当前 active
        idx = self.profile_combo.findText(active)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)

    def _build_llm_client(self):
        prof = self._config.get_active_profile()
        return LLMClient(profile=prof)

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
        # 延迟 import 避免循环
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self._config, parent=self)
        if dlg.exec_():
            # 用户保存了 → 重建客户端
            self._llm = self._build_llm_client()
            self._refresh_profiles()
            self._renderer.add_status('设置已保存')

    def _clear_history(self):
        self._conv.clear()
        self.chat.clear()
        self._renderer.append_html(
            '<div style="color:#888;padding:8px;">对话已清空</div>',
        )

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

        # 创建 worker
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
        # 流式时已经渲染过；这里只是个分段提示
        self._renderer.append_html('<br>')

    def _on_tool_started(self, name, args_str, _call_id):
        # 判断 dangerous
        from ..tools.registry import get_tool
        spec = get_tool(name)
        dangerous = bool(spec and spec.dangerous)
        self._renderer.add_tool_call(name, args_str, dangerous=dangerous)

    def _on_tool_finished(self, name, ok, result_str, _call_id):
        self._renderer.add_tool_result(name, ok, result_str)

    def _on_status(self, text):
        self.status_label.setText(text)

    def _on_finished(self):
        self.status_label.setText('完成')
        self._set_running(False)

    def _on_failed(self, err):
        self._renderer.add_error(err)
        self.status_label.setText('失败')
        self._set_running(False)

    # ------------------------------------------------------------------ #
    # 主线程同步工具执行（关键：pymxs 调用必须在这里发生）
    # ------------------------------------------------------------------ #
    def _run_tool_sync(self, tool_name, arguments):
        """Worker 子线程会通过 BlockingQueuedConnection 调进来。

        但 worker 实现里我们使用的是直接函数调用（_sync_tool_runner）；
        子线程会阻塞等本函数返回。为了真正回到主线程，需要用
        QMetaObject.invokeMethod + BlockingQueuedConnection。
        """
        # 用一个事件 + 容器，把跨线程同步执行做掉
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

        # 如果当前已经在主线程（罕见情况，单元测试可能这样），直接跑
        cur_thread = QtCore.QThread.currentThread()
        app = QApplication.instance()
        main_thread = app.thread() if app is not None else None
        if main_thread is None or cur_thread is main_thread:
            _run_in_main()
        else:
            # 用 QTimer.singleShot(0, ...) 切回主线程
            QtCore.QTimer.singleShot(0, _run_in_main)
            # 阻塞等主线程执行完
            done.wait(timeout=300.0)
            if not done.is_set():
                raise RuntimeError(
                    '工具 {} 在主线程执行超时(300s)'.format(tool_name),
                )

        if 'error' in result_box:
            raise result_box['error']
        return result_box.get('value')
