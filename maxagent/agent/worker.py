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
from ..logger import get_logger
from ..qt_compat import QtCore
from ..qt_compat import Signal
from ..tools import build_openai_tools_schema
from ..tools import ToolDispatcher
from ..tools import ToolExecutionError
from .conversation import Conversation


QObject = QtCore.QObject
QThread = QtCore.QThread

logger = get_logger(__name__)


# 工具调用循环的默认最大轮数。批量场景（如"测试所有工具"）可能调用
# 几十次工具，所以放宽到 40。具体值可由 LLMProfile.max_tool_loops 覆盖。
MAX_TOOL_LOOPS = 40

# 接近上限时提前 N 轮注入"请收尾"软提示，让 LLM 优雅总结而不是被硬截断
SOFT_LIMIT_REMAINING = 4


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
    # 历史被裁剪: (removed_count, current_tokens, budget_tokens)
    history_trimmed = Signal(int, int, int)
    # LLM 用量回报: (prompt_tokens, completion_tokens, total_tokens, cost_usd)
    # cost_usd 为 -1 时表示 profile 未配价格 / 不可估算
    usage_received = Signal(int, int, int, float)

    def __init__(self, llm_client, conversation, dispatcher,
                 max_tool_loops=MAX_TOOL_LOOPS,
                 max_history_tokens=32000,
                 price_input_per_1m=0.0,
                 price_output_per_1m=0.0,
                 vision_enabled=True,
                 vision_whitelist=None,
                 tools_enabled=True,
                 parent=None):
        # type: (LLMClient, Conversation, ToolDispatcher, int, int, float, float, bool, Optional[List[str]], bool, Any) -> None
        super(AgentWorker, self).__init__(parent)
        self._llm = llm_client
        self._conv = conversation
        self._dispatcher = dispatcher
        self._max_loops = int(max_tool_loops)
        self._max_history_tokens = int(max_history_tokens)
        self._price_in = float(price_input_per_1m or 0.0)
        self._price_out = float(price_output_per_1m or 0.0)
        # 视觉/多模态开关：False 时 user 消息里的图片附件不发给 LLM，
        # 只在本地气泡里展示并附"[图片] N 张"提示给模型，避免把 base64
        # 喂给纯文本模型导致 400 / token 浪费。
        self._vision_enabled = bool(vision_enabled)
        self._vision_whitelist = list(vision_whitelist or [])
        # Function Calling 总开关：对应 profile.supports_tools 字段。
        # False 时整轮 LLM 调用都不带 tools / tool_choice 字段，避免视觉专用
        # 网关（如 tokenhub vita）因 tools 字段直接返回 5xx upstream_error。
        # 这是在 v3 之前的版本里被错误地"只写不读"的字段——UI 勾选无效。
        self._tools_enabled = bool(tools_enabled)
        # 在 worker 自身的线程上运行
        self._thread = None  # type: Optional[QThread]
        # 取消标志（跨线程共享）
        self._cancel_event = threading.Event()
        # 工具同步执行回调（由 UI 主线程注入）
        self._sync_tool_runner = None  # type: Optional[Any]
        # 可选：额外的 system prompt 提供者，签名 () -> str
        # 每轮 LLM 调用前会拿一次，把返回的字符串拼到 system 消息末尾。
        # 用于注入"已学技能摘要"这类需要最新状态的内容。
        self._sys_addon_provider = None  # type: Optional[Any]
        # 当前轮的用户输入（供 sys_addon_provider 用于触发词匹配）
        self._current_user_input = ''

        # 工具过滤回调：签名 (tool_name: str) -> bool；返回 False 时该工具
        # 不会被纳入本轮 LLM 的 tools schema，相当于"本轮临时屏蔽"。
        # 用于联网开关：联网关闭时屏蔽 web_* 工具，避免 LLM 误用。
        self._tools_filter = None  # type: Optional[Any]

        # ---------- chunk 节流（合并主线程信号风暴） ----------
        # LLM 流式每个 token 一个 chunk，在子线程里先攒到 buffer，
        # 满足任一条件再 emit：累计字节 >= _chunk_flush_chars
        # 或距离上次 flush 时间 >= _chunk_flush_interval 秒。
        # 配合 UI 端 QPlainTextEdit 的 O(chunk) 增量追加 + 高度调整 30Hz
        # 节流，每秒 emit ≤7 次，主线程几乎无感。
        self._chunk_buf = []  # type: list
        self._chunk_buf_lock = threading.Lock()
        self._chunk_last_flush = 0.0
        self._chunk_flush_chars = 256
        self._chunk_flush_interval = 0.15  # 150ms

        # _start_requested 信号是否已经 connect 过 _qt_entry
        # 用标志位避免重复 connect 报 warning，也避免首次 disconnect 报 warning
        self._started_connected = False

        # 首个流式 chunk 是否已到达：用于切换 UI 状态文本
        self._first_chunk_seen = False

        # ---------- DEBUG 监控指标（每轮 LLM 调用周期重置） ----------
        # _chunk_count: 该轮收到的 raw chunk 数（每个 token 一个）
        # _chunk_emit_count: 该轮真实 emit 到主线程的合并 chunk 数
        # _chunk_first_ts: 第一个 chunk 到达时间，用于算端到端首字延迟
        # _llm_call_started_ts: 本轮 LLM HTTP 请求开始时间
        # 这些只在 logger 处于 DEBUG 时才有意义；INFO 级别会汇总输出。
        self._chunk_count = 0
        self._chunk_emit_count = 0
        self._chunk_first_ts = 0.0
        self._llm_call_started_ts = 0.0

    # ------------------------------------------------------------------ #
    # 主线程辅助
    # ------------------------------------------------------------------ #
    def set_sync_tool_runner(self, runner):
        """注入主线程同步工具执行器。

        runner 签名: ``runner(tool_name: str, arguments: dict) -> Any``
        runner 必须保证在 UI/Max 主线程执行（用 invokeMethod 或 QTimer）。
        """
        self._sync_tool_runner = runner

    def set_system_prompt_addon_provider(self, provider):
        """注入额外 system prompt 提供者。

        :param provider: 可调用对象，签名 ``provider(user_input: str) -> str``
            在每轮 LLM 调用前被调用，返回拼到 system 消息末尾的额外文本。
            返回空字符串则不附加。
        """
        self._sys_addon_provider = provider

    def set_tools_filter(self, filter_func):
        """注入工具过滤回调。

        :param filter_func: 可调用对象，签名 ``(tool_name: str) -> bool``。
            返回 False 时该工具不会被纳入本轮 LLM 的 tools schema。
            传 None 则不过滤（默认）。
        """
        self._tools_filter = filter_func

    def _apply_attachments(self, messages):
        # type: (List[Dict[str, Any]]) -> List[Dict[str, Any]]
        """把 user 消息里的附件按 OpenAI 视觉协议合并进 content。

        策略：
        - ``vision_enabled=False`` 或 当前 model 不在白名单：纯文本降级
          （在文本末尾追加"[图片] N 张"提示，让 LLM 知道有图但看不到）
        - 命中白名单：把 content 重写为 list 形态，含 image_url 段
        - to_openai_messages() 与 conv.messages 顺序一一对应，按索引回填

        视觉模型多轮稳定性（针对 tokenhub vita 等敏感网关）：
        - **图片瘦身**：仅最后一条带附件的 user 消息保留完整 image_url，
          更早的图片附件降级为占位文本"[此前已展示过的图片 N 张]"。
          理由：模型在第一轮已经把图描述出来进了 assistant 历史，多轮重发
          只会撑爆请求体并触发 4xx。
        - **格式统一**：视觉路径下，所有 user 消息（含纯文本）都包成
          ``[{"type":"text","text":...}]`` 数组形态，避免"历史含图、当前
          纯文本"的混合 content 让 vita 网关返回 invalid_params。

        本方法不会改动原 ``conv.messages``，只重写传入的 ``messages``
        副本，供本轮 HTTP 请求使用。
        """
        msgs = self._conv.messages
        has_any_attachment = any(
            getattr(m, 'attachments', None) and m.role == 'user'
            for m in msgs
        )

        # 延迟 import：避免 worker 在不需要附件时也加载 attachments 模块
        from ..attachments import build_user_content
        from ..attachments import model_supports_vision

        model_name = getattr(self._llm, '_model', '') or ''
        can_vision = (
            self._vision_enabled
            and model_supports_vision(model_name, self._vision_whitelist)
        )

        # 完全无附件 + 非视觉模型：直接透传，零开销路径
        if not has_any_attachment and not can_vision:
            return messages

        # 找到"最后一条带附件的 user 消息"和"最后一条 user 消息"在 user
        # 序列中的索引（0-based），用于决定是否保留图片：
        # - 仅当当前轮 user（即最后一条 user）就是带附件那条时，才保留图片
        # - 否则把所有历史图片全部降级为占位文本（多轮稳定性核心策略）
        last_att_user_pos = -1
        last_user_pos = -1
        user_counter = -1
        for m in msgs:
            if m.role != 'user':
                continue
            user_counter += 1
            last_user_pos = user_counter
            if getattr(m, 'attachments', None):
                last_att_user_pos = user_counter
        # 当且仅当"最新一条 user 自己带附件"时才保留图片
        keep_pos = last_att_user_pos if (
            last_att_user_pos >= 0 and last_att_user_pos == last_user_pos
        ) else -1

        out = list(messages)
        att_user_pos = -1  # 当前遍历到第几条 user
        # 按 user 顺序匹配 conv.messages 里的源消息
        src_user_iter = iter(m for m in msgs if m.role == 'user')

        for i, om in enumerate(out):
            if om.get('role') != 'user':
                continue
            try:
                src_msg = next(src_user_iter)
            except StopIteration:
                break
            att_user_pos += 1

            atts = getattr(src_msg, 'attachments', None)
            if atts:
                keep = (att_user_pos == keep_pos)
                new_content = build_user_content(
                    text=src_msg.content or '',
                    attachments=atts,
                    can_vision=can_vision,
                    keep_images=keep,
                    force_multimodal=can_vision,
                )
                new_om = dict(om)
                new_om['content'] = new_content
                out[i] = new_om
            elif can_vision:
                # 视觉路径下，纯文本 user 也包成 list 形态以保证格式统一
                text = om.get('content')
                # 跳过非 str（保险起见——理论上 user content 总是 str）
                if isinstance(text, list):
                    continue
                new_content = build_user_content(
                    text=text or '',
                    attachments=None,
                    can_vision=True,
                    keep_images=True,
                    force_multimodal=True,
                )
                new_om = dict(om)
                new_om['content'] = new_content
                out[i] = new_om

        if can_vision:
            logger.debug(
                'vision_enabled model=%s rewrote user msgs '
                '(keep_pos=%d, total_user=%d)',
                model_name, keep_pos, att_user_pos + 1,
            )
        else:
            logger.debug(
                'vision_disabled model=%s images degraded to text notice',
                model_name,
            )
        return out

    def cancel(self):
        """请求取消当前对话轮（下一次工具结束/LLM 流式分块时生效）。"""
        self._cancel_event.set()
        # DEBUG 埋点：用户主动取消（排查"卡住后点停止"路径）
        logger.debug('worker_cancel requested')

    def reset_cancel(self):
        self._cancel_event.clear()

    # ------------------------------------------------------------------ #
    # 启动入口（标准 Qt worker pattern）
    # ------------------------------------------------------------------ #
    # 内部信号：从主线程跨线程触发 _qt_entry（worker 已 moveToThread 后，
    # 这种 signal->slot 一定走 QueuedConnection，slot 必然在子线程跑）。
    _start_requested = Signal()

    @QtCore.Slot()
    def _qt_entry(self):
        """真正的子线程入口（被 _start_requested 触发）。

        必须用 @Slot 装饰，让 Qt 通过 metaobject 识别，确保 signal->slot
        走 QueuedConnection。普通 Python 闭包/未装饰方法在某些 PySide
        版本里会被当作 DirectConnection，导致 _run_loop 仍然在主线程跑，
        进而把 LLM HTTP 流式调用阻塞到 Max 主线程上 —— 这是"等 LLM 时
        整个 Max UI 卡住"的根因。
        """
        # 诊断埋点：明确告知 _run_loop 跑在哪个线程，方便定位卡顿根因。
        # 上线稳定后可以删除。
        try:
            app = QtCore.QCoreApplication.instance()
            cur = QtCore.QThread.currentThread()
            main = app.thread() if app is not None else None
            in_main = (main is not None and cur is main)
            logger.info(
                '_qt_entry running in %s thread (tid=%s, qthread=%s)',
                'MAIN' if in_main else 'WORKER',
                threading.get_ident(),
                int(id(cur)),
            )
        except Exception:  # pylint: disable=broad-except
            pass

        try:
            self._run_loop()
        except Exception as exc:  # pylint: disable=broad-except
            tb = traceback.format_exc()
            logger.exception('Worker 异常: %s', exc)
            self.failed.emit('Worker 异常: {}\n{}'.format(exc, tb))
        finally:
            # 通知 thread 退出事件循环（thread.quit 是线程安全的）
            t = self._thread
            if t is not None:
                try:
                    t.quit()
                except Exception:  # pylint: disable=broad-except
                    pass

    def run_in_thread(self, user_input, skip_add_user=False):
        """启动一个子线程跑 LLM 对话循环。

        :param user_input: 用户输入文本
        :param skip_add_user: 调用方已经手动 ``conv.add_user(...)``（带附件）
            时设为 True，避免重复追加同一条 user 消息。
        """
        self.reset_cancel()
        self._current_user_input = user_input or ''
        # 把用户输入立刻写入对话历史（在调用线程也安全，因为 _conv 修改时序明确）
        if not skip_add_user:
            self._conv.add_user(user_input)

        thread = QThread()
        # 1. worker 移到子线程：之后 worker 的 @Slot 在 QueuedConnection 下
        #    会被 thread 的事件循环调度执行
        self.moveToThread(thread)
        # 2. thread 的 finished 自清理；不要 connect started 到普通 callable，
        #    那样在某些 PySide 版本里会变成 DirectConnection（主线程跑）
        thread.finished.connect(thread.deleteLater)
        # 3. _start_requested 是 worker 自己的 signal，emit 时 worker 已经
        #    属于子线程，slot 又是 worker 自己的 @Slot，必走 QueuedConnection，
        #    在子线程事件循环里调度。只 connect 一次，后续复用。
        if not self._started_connected:
            self._start_requested.connect(
                self._qt_entry, QtCore.Qt.QueuedConnection,
            )
            self._started_connected = True
        self._thread = thread
        thread.start()
        # 4. thread 启动后才能 emit；emit 时 worker 的 affinity 已是子线程，
        #    Qt 会把这次调用排到子线程事件循环
        self._start_requested.emit()

    # ------------------------------------------------------------------ #
    # 子线程：核心 LLM + 工具循环
    # ------------------------------------------------------------------ #
    def _run_loop(self):
        """LLM <-> 工具循环，最多 max_tool_loops 轮。

        策略：
        - 正常情况：每轮 LLM 给 tool_calls → 执行 → 进下一轮，直到无 tool_calls
        - 接近上限（剩 SOFT_LIMIT_REMAINING 轮）：注入软提示让 LLM 收尾
        - 真超限：保留已经写入 conversation 的所有工具结果，仅发出告警
          让用户看到部分成果，而不是丢失整轮上下文
        """
        tools_schema = build_openai_tools_schema()
        # Function Calling 总开关：profile.supports_tools=False 时整条
        # tools 链路熔断——既不发 schema，也不允许过滤后的子集。这是
        # 视觉专用网关（tokenhub vita 等）唯一可靠的工作模式。
        if not self._tools_enabled:
            if tools_schema:
                logger.info(
                    'Function Calling 已禁用 (profile.supports_tools=False)，'
                    '本轮屏蔽 %d 个工具不发送 tools 字段',
                    len(tools_schema),
                )
            tools_schema = []
        # 应用工具过滤（如本轮关闭联网时屏蔽 web_*）
        if self._tools_filter is not None and tools_schema:
            try:
                tools_schema = [
                    s for s in tools_schema
                    if self._tools_filter(
                        (s.get('function') or {}).get('name', ''),
                    )
                ]
                logger.info(
                    'tools_filter applied: %d tools enabled this turn',
                    len(tools_schema),
                )
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning('tools_filter 异常，回退全量: %s', exc)
                tools_schema = build_openai_tools_schema()
        # 标记是否已注入软提示，避免重复
        soft_warned = False
        # 字面理解 / 完成即停 软提示是否已注入。仅在本轮第一次出现
        # 工具调用且全部成功后注入一次，引导 LLM"做完就停"，避免
        # 用户说"创建一个球"被 LLM 顺手补成"球+灯+相机"的过度联想。
        restraint_hinted = False

        for loop_idx in range(self._max_loops):
            if self._cancel_event.is_set():
                self.failed.emit('用户取消')
                return

            remaining = self._max_loops - loop_idx
            self.status_changed.emit(
                '思考中... (第 {}/{} 轮)'.format(
                    loop_idx + 1, self._max_loops,
                ),
            )

            # 接近上限时主动提示 LLM 收尾
            if (not soft_warned
                    and remaining <= SOFT_LIMIT_REMAINING
                    and self._max_loops > SOFT_LIMIT_REMAINING):
                soft_warned = True
                self._conv.add_system_note(
                    '⚠️ 提示：你已使用了 {}/{} 轮工具调用，剩余 {} 轮。'
                    '请尽快总结已完成的工作并给出最终回复，'
                    '避免继续发起非必要的工具调用。'.format(
                        loop_idx, self._max_loops, remaining,
                    ),
                )

            messages = self._conv.to_openai_messages()
            # 每轮 LLM 调用前按 token 预算裁剪历史
            # 通用策略：保护 system + 最近 4 条 + tool_call 配对
            if self._max_history_tokens > 0:
                try:
                    cut = self._conv.trim_to_token_budget(
                        max_tokens=self._max_history_tokens,
                        keep_recent=4,
                    )
                    if cut > 0:
                        # 裁完后重新生成 messages
                        messages = self._conv.to_openai_messages()
                        cur_tokens = self._conv.estimate_total_tokens()
                        self.history_trimmed.emit(
                            cut, cur_tokens, self._max_history_tokens,
                        )
                except Exception as exc:  # pylint: disable=broad-except
                    logger.warning('trim_to_token_budget 异常: %s', exc)
            # 多模态附件合并：把 user 消息的图片附件按视觉协议拼进
            # content（不支持视觉时降级为纯文本提示）。必须在 token
            # 裁剪之后做——裁剪走的是文本估算，图片 base64 不参与。
            messages = self._apply_attachments(messages)
            # 注入额外 system prompt（如已学技能摘要）
            if self._sys_addon_provider is not None:
                try:
                    addon = self._sys_addon_provider(
                        self._current_user_input,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    logger.warning('sys_addon_provider 异常: %s', exc)
                    addon = ''
                if addon and messages and messages[0].get('role') == 'system':
                    base = messages[0].get('content') or ''
                    messages[0] = {
                        'role': 'system',
                        'content': base + '\n' + addon,
                    }
                elif addon:
                    messages.insert(
                        0, {'role': 'system', 'content': addon},
                    )
            try:
                self.status_changed.emit(
                    '正在请求 LLM 推理…（流式输出，首字可能需几秒）',
                )
                # 首个 chunk 到达时再切换状态文本
                self._first_chunk_seen = False
                # DEBUG 监控：重置本轮指标
                self._chunk_count = 0
                self._chunk_emit_count = 0
                self._chunk_first_ts = 0.0
                self._llm_call_started_ts = time.time()
                # DEBUG 埋点：发出请求摘要
                if logger.isEnabledFor(10):
                    logger.debug(
                        '→ LLM call loop=%d/%d msgs=%d tools=%d',
                        loop_idx + 1, self._max_loops,
                        len(messages),
                        len(tools_schema or []),
                    )
                resp = self._llm.chat(
                    messages=messages,
                    tools=tools_schema,
                    stream=True,
                    on_delta=self._on_text_chunk,
                    cancel_check=self._cancel_event.is_set,
                )
                # DEBUG 埋点：本轮流式收尾统计
                if self._llm_call_started_ts > 0:
                    elapsed = time.time() - self._llm_call_started_ts
                    ttf = (
                        (self._chunk_first_ts - self._llm_call_started_ts)
                        if self._chunk_first_ts > 0 else -1.0
                    )
                    rate = (
                        self._chunk_count / elapsed
                        if elapsed > 0 else 0.0
                    )
                    compress = (
                        (self._chunk_count / self._chunk_emit_count)
                        if self._chunk_emit_count > 0 else 0.0
                    )
                    logger.info(
                        '← LLM done loop=%d elapsed=%.2fs '
                        'ttf=%.2fs chunks=%d emit=%d rate=%.0f tok/s '
                        'compress=%.1fx',
                        loop_idx + 1, elapsed, ttf,
                        self._chunk_count, self._chunk_emit_count,
                        rate, compress,
                    )
            except LLMError as exc:
                # LLM 出错前，先把已经收到的流式残片送达 UI
                self._flush_chunk_buf()
                # 用户主动取消用 LLMError("用户取消") 表达，区别于其他错误
                if '用户取消' in str(exc):
                    self.failed.emit('用户取消')
                    return
                self.failed.emit('LLM 调用失败: {}'.format(exc))
                return
            except Exception as exc:  # pylint: disable=broad-except
                self._flush_chunk_buf()
                tb = traceback.format_exc()
                self.failed.emit(
                    'LLM 调用异常: {}\n{}'.format(exc, tb),
                )
                return

            # 解析返回
            content = resp.get('content') or ''

            # 该轮 LLM 流式已结束：先把 chunk 残留 buffer 全部 flush 给 UI，
            # 避免后续 text_message_complete / 工具气泡先于尾巴文字到达
            self._flush_chunk_buf()

            # 把 usage 信息派发给 UI（如果后端返回了）
            usage = resp.get('usage') or {}
            if usage:
                self._emit_usage(usage)
            # LLMClient 返回的 tool_calls 是扁平格式 {id, name, arguments(dict)}
            # 需要还原为 OpenAI 原生 {id, type, function:{name, arguments(json_str)}}
            # 才能塞回 conversation 让下一轮 LLM 读懂
            flat_calls = resp.get('tool_calls') or []
            tool_calls = []
            for tc in flat_calls:
                args_obj = tc.get('arguments')
                if isinstance(args_obj, str):
                    args_str = args_obj
                else:
                    try:
                        args_str = json.dumps(
                            args_obj or {}, ensure_ascii=False,
                        )
                    except (TypeError, ValueError):
                        args_str = '{}'
                tool_calls.append({
                    'id': tc.get('id') or '',
                    'type': 'function',
                    'function': {
                        'name': tc.get('name') or '',
                        'arguments': args_str,
                    },
                })
            finish_reason = resp.get('finish_reason') or ''

            # 把 assistant 消息记入历史（含 DeepSeek thinking 模式的
            # reasoning_content；多轮对话必须把它原样回传给 API）
            self._conv.add_assistant(
                content=content if content else None,
                tool_calls=tool_calls if tool_calls else None,
                reasoning_content=resp.get('reasoning_content') or None,
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

            # 第一批工具执行完成后注入"完成即停"软提示（每轮对话仅一次）：
            # 防止 LLM 看到工具成功结果后产生"既然能创建球，那再顺便
            # 加个灯光让画面好看一点"的扩展冲动，强制它优先完成确认
            # 回复，不做未被显式要求的额外操作。
            if not restraint_hinted:
                restraint_hinted = True
                self._conv.add_system_note(
                    '✅ 上一批工具已执行完毕。请严格按字面理解用户的'
                    '原始请求：如果用户当前请求已被满足，**立即**给出'
                    '简短的中文确认回复（如"已为你创建一个球"）并结束本轮，'
                    '**不要**追加创建灯光、相机、材质、地面、修改器等'
                    '未被显式要求的操作；如果你确实认为还需要其它工具'
                    '才能完成用户的原始请求，请在回复里先用一句话'
                    '说明意图再调用，而不是直接动手。',
                )
                logger.info(
                    'restraint hint injected after tool batch '
                    '(loop=%d, tools=%d)',
                    loop_idx + 1, len(tool_calls),
                )

            # 结束本轮，进入下一轮 LLM 调用让它读到工具结果
            if finish_reason and finish_reason != 'tool_calls':
                # 非 tool_calls 但又含 tool_calls 是异常情况，强制再让模型回复一次
                continue

        # 超过最大轮数仍未结束：发 failed 但保留对话，让用户能看到已完成的部分
        self.failed.emit(
            '⚠️ 工具调用已达到最大轮数 {} 轮，已暂停。\n\n'
            '当前轮内已执行的工具结果已经保留在对话历史里，'
            '你可以继续输入"继续"让模型基于这些结果给出总结，'
            '或在「设置」中调高 max_tool_loops。'.format(self._max_loops),
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
        """LLM 流式返回 token 时由 LLMClient 调用（仍在子线程）。

        节流策略（避免主线程信号风暴）：
        - 把 token 攒进 buffer
        - 满足"累计字符数达到阈值"或"距离上次 flush 超过时间窗"任一条件，
          才把合并后的字符串一次性 emit 到主线程
        - 一轮 LLM 调用结束（文本完整、工具调用前、轮末）必须显式 flush，
          否则会丢尾巴
        """
        if not chunk:
            return
        # DEBUG 监控：累计 raw chunk 数 + 记录首字时间
        self._chunk_count += 1
        if self._chunk_first_ts <= 0:
            self._chunk_first_ts = time.time()
        # 首字到达：通知 UI 切到"生成中"状态，让用户看到推理已开始
        if not getattr(self, '_first_chunk_seen', False):
            self._first_chunk_seen = True
            try:
                self.status_changed.emit('生成回复中…')
            except Exception:  # pylint: disable=broad-except
                pass
        with self._chunk_buf_lock:
            self._chunk_buf.append(chunk)
            total = sum(len(c) for c in self._chunk_buf)
            now = time.time()
            if (total >= self._chunk_flush_chars
                    or (now - self._chunk_last_flush)
                    >= self._chunk_flush_interval):
                merged = ''.join(self._chunk_buf)
                self._chunk_buf = []
                self._chunk_last_flush = now
            else:
                merged = ''
        if merged:
            self._chunk_emit_count += 1
            self.chunk_received.emit(merged)

    def _flush_chunk_buf(self):
        """强制把残留 buffer 全部 emit 出去，保证一轮文本完整。"""
        with self._chunk_buf_lock:
            if not self._chunk_buf:
                return
            merged = ''.join(self._chunk_buf)
            self._chunk_buf = []
            self._chunk_last_flush = time.time()
        if merged:
            self._chunk_emit_count += 1
            self.chunk_received.emit(merged)

    def _emit_usage(self, usage):
        """从后端返回的 usage dict 计算成本并发信号。

        :param usage: 形如 ``{"prompt_tokens": int, "completion_tokens": int, ...}``
        """
        try:
            pt = int(usage.get('prompt_tokens') or 0)
            ct = int(usage.get('completion_tokens') or 0)
            tt = int(usage.get('total_tokens') or (pt + ct))
        except (TypeError, ValueError):
            return
        cost = -1.0
        if self._price_in > 0 or self._price_out > 0:
            cost = (
                pt * self._price_in / 1_000_000.0
                + ct * self._price_out / 1_000_000.0
            )
        try:
            self.usage_received.emit(pt, ct, tt, cost)
        except Exception:  # pylint: disable=broad-except
            pass

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

    # ------------------------------------------------------------------ #
    # 对话压缩（方案 B 手动 + 方案 D 自动）
    # ------------------------------------------------------------------ #
    def compress_history(self, keep_recent=2):
        """同步请求 LLM 生成历史摘要并压缩对话。

        通用化做法：发一个独立的非工具、非流式 LLM 调用，让模型读完
        当前所有 messages，输出一段 200-400 字的摘要，再用摘要替换
        早期消息。所有 OpenAI 兼容模型都能完成。

        :param keep_recent: 保留的最近消息条数
        :return: dict 形如 {'ok': bool, 'removed': int, 'summary': str, 'error': str?}
        """
        if len(self._conv) <= keep_recent + 1:
            return {
                'ok': False,
                'removed': 0,
                'summary': '',
                'error': '对话太短，无需压缩',
            }

        # 构造摘要提示词：把当前历史作为输入，要求模型输出纯文本摘要
        summary_instruction = (
            '你正在为一个 3ds Max AI 助手压缩对话历史。请阅读以下完整对话，'
            '输出一段 200~400 字的中文摘要，要求：\n'
            '1. 保留用户的核心目标和已确立的偏好；\n'
            '2. 保留已成功创建/修改的关键场景对象（名称、关键属性）；\n'
            '3. 保留尚未完成、需要后续跟进的事项；\n'
            '4. 用要点形式列出，不要客套；\n'
            '5. 仅输出摘要正文，不要包含"以下是摘要"等元描述。'
        )

        # 把历史以 user 角色塞给摘要模型，避免它误以为自己就是那个 agent
        history_dump = self._dump_history_for_summary()
        summary_msgs = [
            {'role': 'system', 'content': summary_instruction},
            {
                'role': 'user',
                'content': '【需要压缩的对话历史】\n' + history_dump,
            },
        ]

        try:
            resp = self._llm.chat(
                messages=summary_msgs,
                tools=None,
                stream=False,
            )
        except LLMError as exc:
            return {
                'ok': False, 'removed': 0, 'summary': '',
                'error': '生成摘要失败: {}'.format(exc),
            }
        except Exception as exc:  # pylint: disable=broad-except
            return {
                'ok': False, 'removed': 0, 'summary': '',
                'error': '生成摘要异常: {}'.format(exc),
            }

        summary = (resp.get('content') or '').strip()
        if not summary:
            return {
                'ok': False, 'removed': 0, 'summary': '',
                'error': '模型未返回摘要内容',
            }

        ok, removed = self._conv.replace_with_summary(
            summary, keep_recent=keep_recent,
        )
        return {
            'ok': ok,
            'removed': removed,
            'summary': summary,
            'error': '' if ok else '可压缩内容不足',
        }

    def _dump_history_for_summary(self):
        """把当前 messages dump 成易读文本，供摘要 prompt 使用。"""
        lines = []
        for m in self._conv.messages:
            role = m.role
            if role == 'user':
                lines.append('[用户] ' + (m.content or ''))
            elif role == 'assistant':
                if m.tool_calls:
                    names = []
                    for tc in m.tool_calls:
                        fn = (tc.get('function') or {})
                        names.append(fn.get('name') or '?')
                    lines.append(
                        '[助手] (调用工具: {}) {}'.format(
                            ', '.join(names), m.content or '',
                        ),
                    )
                else:
                    lines.append('[助手] ' + (m.content or ''))
            elif role == 'tool':
                # 工具结果可能很长，截断一下
                content = m.content or ''
                if len(content) > 300:
                    content = content[:300] + '...(truncated)'
                lines.append(
                    '[工具结果 {}] {}'.format(m.name or '?', content),
                )
            elif role == 'system':
                # 中途 system note 也写入摘要上下文
                content = m.content or ''
                if len(content) > 200:
                    content = content[:200] + '...'
                lines.append('[系统提示] ' + content)
        return '\n'.join(lines)
