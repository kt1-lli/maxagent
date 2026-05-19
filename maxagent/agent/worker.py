#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 工作线程。

线程模型：
- UI 主线程: Qt 主线程，也是 Max 主线程。pymxs 调用必须在这里执行。
- LLM 子线程: 跑在 QThread 里，负责长耗时的 LLM HTTP 调用。

工具调用流程：
1. 用户输入 → 主线程把消息塞进 Conversation → 启动 worker
2. Worker 在子线程发起 LLM 调用（流式）
3. LLM 流式返回时，通过信号 chunk_received 把 token 推回主线程显示
4. 如果 LLM 决定调用工具，子线程通过 tool_call_requested 信号请求主线程执行
5. 主线程同步执行工具（pymxs），把结果通过 tool_call_completed 写回 worker
6. Worker 拿到结果继续调 LLM，直到模型给出最终回复

为什么不直接在子线程里跑 dispatcher？
→ pymxs 严禁跨线程调用，所有 Max API 必须在主线程执行，否则会崩溃。

线程间同步用 QMetaObject.invokeMethod + BlockingQueuedConnection，简单可靠。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import threading
import time
import traceback
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..llm_client import LLMClient
from ..llm_client import LLMError
from ..qt_compat import QObject
from ..qt_compat import QThread
from ..qt_compat import Signal
from ..tools import build_openai_tools_schema
from ..tools import ToolDispatcher
from ..tools import ToolExecutionError
from .conversation import Conversation


# 工具调用循环的最大轮数，防止 LLM 死循环
MAX_TOOL_LOOPS = 16


class AgentWorker(QObject):
    """跑在子线程的 Agent 主控对象。

    使用方式:
        worker = AgentWorker(llm_client, conversation, dispatcher)
        worker.chunk_received.connect(ui.on_chunk)
        worker.tool_started.connect(ui.on_tool_started)
        worker.tool_finished.connect(ui.on_tool_finished)
        worker.finished.connect(ui.on_finished)
        worker.failed.connect(ui.on_failed)
        worker.run_in_thread(user_input)
    """

    # 流式 token 增量
    chunk_received = Signal(str)
    # 一次完整的 assistant 文本回复完成（不含 tool_calls 的中间消息）
    text_message_complete = Signal(str)
    # 工具调用开始: (tool_name, args_json_str, call_id)
    tool_started = Signal(str, str, str)
    # 工具调用结束: (tool_name, ok, result_json_str, call_id)
    tool_finished = Signal(str, bool, str, str)
    # 整轮完成
    finished = Signal()
    # 失败: (error_message)
    failed = Signal(str)
    # 状态文本: (status_text) - 用于 UI 显示"思考中..."等
    status_changed = Signal(str)

    def __init__(self, llm_client, conversation, dispatcher,
                 max_tool_loops=MAX_TOOL_LOOPS, parent=None):
        # type: (LLMClient, Conversation, ToolDispatcher, int, Any) -> None
        super(AgentWorker, self).__init__(parent)
        self._llm = llm_client
        self._conv = conversation
        self._dispatcher = dispatcher
        self._max_loops = int(max_tool_loops)
        # 在 worker 自身的线程上运行
        self._thread = None  # type: Optional[QThread]
        # 取消标志（跨线程共享）
        self._cancel_event = threading.Event()
        # 工具同步执行回调（由 UI 主线程注入）
        self._sync_tool_runner = None  # type: Optional[Any]

    # ------------------------------------------------------------------ #
    # 主线程辅助
    # ------------------------------------------------------------------ #
    def set_sync_tool_runner(self, runner):
        """注入主线程同步工具执行器。

        runner 签名: ``runner(tool_name: str, arguments: dict) -> Any``
        runner 必须保证在 UI/Max 主线程执行（用 invokeMethod 或 QTimer）。
        """
        self._sync_tool_runner = runner

    def cancel(self):
        """请求取消当前对话轮（下一次工具结束/LLM 流式分块时生效）。"""
        self._cancel_event.set()

    def reset_cancel(self):
        self._cancel_event.clear()

    # ------------------------------------------------------------------ #
    # 启动入口
    # ------------------------------------------------------------------ #
    def run_in_thread(self, user_input):
        """启动一个子线程跑 LLM 对话循环。

        :param user_input: 用户输入文本
        """
        self.reset_cancel()
        # 把用户输入立刻写入对话历史（在调用线程也安全，因为 _conv 修改时序明确）
        self._conv.add_user(user_input)

        thread = QThread()
        # 用闭包跑入口函数
        def _entry():
            try:
                self._run_loop()
            except Exception as exc:  # pylint: disable=broad-except
                tb = traceback.format_exc()
                self.failed.emit('Worker 异常: {}\n{}'.format(exc, tb))
            finally:
                # 子线程退出
                try:
                    thread.quit()
                except Exception:  # pylint: disable=broad-except
                    pass

        # 把 worker 移到子线程
        self.moveToThread(thread)
        thread.started.connect(_entry)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        thread.start()

    # ------------------------------------------------------------------ #
    # 子线程：核心 LLM + 工具循环
    # ------------------------------------------------------------------ #
    def _run_loop(self):
        """LLM <-> 工具循环，最多 max_tool_loops 轮。"""
        tools_schema = build_openai_tools_schema()

        for loop_idx in range(self._max_loops):
            if self._cancel_event.is_set():
                self.failed.emit('用户取消')
                return

            self.status_changed.emit(
                '思考中... (第 {} 轮)'.format(loop_idx + 1),
            )

            messages = self._conv.to_openai_messages()
            try:
                resp = self._llm.chat(
                    messages=messages,
                    tools=tools_schema,
                    stream=True,
                    on_text_chunk=self._on_text_chunk,
                )
            except LLMError as exc:
                self.failed.emit('LLM 调用失败: {}'.format(exc))
                return
            except Exception as exc:  # pylint: disable=broad-except
                tb = traceback.format_exc()
                self.failed.emit(
                    'LLM 调用异常: {}\n{}'.format(exc, tb),
                )
                return

            # 解析返回
            content = resp.get('content') or ''
            tool_calls = resp.get('tool_calls') or []
            finish_reason = resp.get('finish_reason') or ''

            # 把 assistant 消息记入历史
            self._conv.add_assistant(
                content=content if content else None,
                tool_calls=tool_calls if tool_calls else None,
            )

            # 把整段文本通知 UI（即使是流式也再发一次完整版，方便 UI 收尾）
            if content:
                self.text_message_complete.emit(content)

            # 没有工具调用 → 整轮结束
            if not tool_calls:
                self.finished.emit()
                return

            # 有工具调用 → 逐个执行，结果写回历史
            for tc in tool_calls:
                if self._cancel_event.is_set():
                    self.failed.emit('用户取消')
                    return
                self._exec_one_tool_call(tc)

            # 结束本轮，进入下一轮 LLM 调用让它读到工具结果
            if finish_reason and finish_reason != 'tool_calls':
                # 非 tool_calls 但又含 tool_calls 是异常情况，强制再让模型回复一次
                continue

        # 超过最大轮数仍未结束
        self.failed.emit(
            '工具调用循环超过 {} 轮，已强制中止。请检查指令是否过于复杂或'
            '存在 LLM 死循环。'.format(self._max_loops),
        )

    # ------------------------------------------------------------------ #
    # 子线程内部：单个工具调用
    # ------------------------------------------------------------------ #
    def _exec_one_tool_call(self, tc):
        """执行单个工具调用并把结果写回 conversation。"""
        call_id = tc.get('id', '')
        fn = tc.get('function') or {}
        name = fn.get('name', '')
        args_str = fn.get('arguments', '{}') or '{}'
        try:
            args = json.loads(args_str)
            if not isinstance(args, dict):
                args = {}
        except (TypeError, ValueError):
            args = {}

        self.tool_started.emit(name, args_str, call_id)

        # 走主线程注入的同步执行器（pymxs 必须在主线程）
        if self._sync_tool_runner is None:
            err = '未注入 sync_tool_runner，无法在主线程执行工具'
            self._conv.add_tool_result(
                tool_call_id=call_id,
                name=name,
                content=json.dumps(
                    {'ok': False, 'error': err},
                    ensure_ascii=False,
                ),
            )
            self.tool_finished.emit(
                name, False,
                json.dumps({'error': err}, ensure_ascii=False),
                call_id,
            )
            return

        ok = True
        try:
            result = self._sync_tool_runner(name, args)
            result_dict = {'ok': True, 'result': result}
        except ToolExecutionError as exc:
            ok = False
            result_dict = {
                'ok': False,
                'error': str(exc),
                'tool': name,
            }
        except Exception as exc:  # pylint: disable=broad-except
            ok = False
            result_dict = {
                'ok': False,
                'error': '{}: {}'.format(type(exc).__name__, exc),
                'tool': name,
            }

        # 把结果序列化成字符串塞回 conversation（OpenAI 协议要求 content 是字符串）
        content_str = self._safe_json_dumps(result_dict)
        self._conv.add_tool_result(
            tool_call_id=call_id,
            name=name,
            content=content_str,
        )
        self.tool_finished.emit(name, ok, content_str, call_id)

    # ------------------------------------------------------------------ #
    # 工具/辅助
    # ------------------------------------------------------------------ #
    def _on_text_chunk(self, chunk):
        """LLM 流式返回 token 时由 LLMClient 调用（仍在子线程）。"""
        if chunk:
            self.chunk_received.emit(chunk)

    @staticmethod
    def _safe_json_dumps(obj):
        """安全序列化：遇到非 JSON-safe 对象回退为 repr。"""
        def _default(o):
            try:
                return repr(o)
            except Exception:  # pylint: disable=broad-except
                return '<unserializable>'

        try:
            return json.dumps(obj, ensure_ascii=False, default=_default)
        except Exception:  # pylint: disable=broad-except
            # 兜底：保证一定能塞回 LLM
            return json.dumps(
                {'ok': False, 'error': 'tool result not serializable'},
                ensure_ascii=False,
            )
