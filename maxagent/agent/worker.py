#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 工作线程。

    线程模型：
    - UI 主线程: Qt 主线程，也是 DCC 主线程。DCC API 调用必须在这里执行。
    - LLM 子线程: 跑在 QThread 里，负责长耗时的 LLM HTTP 调用。

    工具调用流程：
    1. 用户输入 → 主线程把消息塞进 Conversation → 启动 worker
    2. Worker 在子线程发起 LLM 调用（流式）
    3. LLM 流式返回时，通过信号 chunk_received 把 token 推回主线程显示
    4. 如果 LLM 决定调用工具，子线程通过 tool_call_requested 信号请求主线程执行
    5. 主线程同步执行工具（DCC API），把结果通过 tool_call_completed 写回 worker
    6. Worker 拿到结果继续调 LLM，直到模型给出最终回复

    为什么不直接在子线程里跑 dispatcher？
    → DCC API 严禁跨线程调用，所有场景操作必须在主线程执行，否则会崩溃。

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

from ..attachments import build_user_content
from ..attachments import model_supports_vision
from ..llm_client import LLMClient
from ..llm_client import LLMError
from ..llm_client import LLMRateLimitError
from ..llm_client import build_client_from_profile
from ..logger import get_logger
from ..qt_compat import QtCore
from ..qt_compat import Signal
from ..tools import build_openai_tools_schema
from ..tools import ToolDispatcher
from ..tools import ToolExecutionError
from .conversation import Conversation
from .scene_snapshot import build_scene_snapshot
from .scene_snapshot import diff_snapshots
from .scene_snapshot import diff_to_prompt_text
from .scene_snapshot import snapshot_to_prompt_text
from .task_context import get_task_prompt
from .task_context import TaskContextManager
from ..learning.skill_generator import propose_skill_from_recorder
from ..macro_recorder import MacroRecorder
from ..session_memory import get_session_memory_mgr
from ..summarization_checkpoint import ContextCompressor


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
    # 建议保存 Skill: (skill_manifest_dict, impl_code_str)
    skill_proposed = Signal(dict, str)
    # 状态文本: (status_text) - 用于 UI 显示"思考中..."等
    status_changed = Signal(str)
    # 历史被裁剪: (removed_count, current_tokens, budget_tokens)
    history_trimmed = Signal(int, int, int)
    # LLM 用量回报: (prompt_tokens, completion_tokens, total_tokens, cost_usd)
    # cost_usd 为 -1 时表示 profile 未配价格 / 不可估算
    usage_received = Signal(int, int, int, float)
    # 系统级通知（对话流内持久气泡）：(level, message)
    # level: 'info' / 'warn' / 'error'
    # 用途：fallback 切换、配额告警、备用链耗尽、工具禁用等需要用户
    # 长期知晓的事件。与 status_changed 的区别：status_changed 是短暂
    # 的状态栏文本，system_notice 是永久留在对话历史里的气泡。
    system_notice = Signal(str, str)
    # Todo 清单更新：(session_id, snapshot_dict)
    # 由 tools.todo_tools 的变更回调触发；snapshot_dict 含 items/revision/counts
    # UI 侧维持"每会话单张 Todo 卡"的策略，接到通知就地更新，不新增气泡。
    todo_updated = Signal(str, dict)
    # 本轮进度更新（崩溃防丢）：无参
    # 触发时机：每次向 Conversation 追加 assistant / tool_result 后立即发出。
    # UI 侧接到信号立即调用 _save_current_session 落盘，保证长任务
    # 中途 Max 崩溃时已完成的步骤不丢失。session_mgr.save 是 tmp+rename
    # 原子写，重复触发无副作用。
    turn_progress = Signal()

    def __init__(self, llm_client, conversation, dispatcher,
                 max_tool_loops=MAX_TOOL_LOOPS,
                 max_history_tokens=32000,
                 price_input_per_1m=0.0,
                 price_output_per_1m=0.0,
                 vision_enabled=True,
                 vision_whitelist=None,
                 tools_enabled=True,
                 config_manager=None,
                 parent=None):
        # type: (LLMClient, Conversation, ToolDispatcher, int, int, float, float, bool, Optional[List[str]], bool, Any, Any) -> None
        super(AgentWorker, self).__init__(parent)
        self._llm = llm_client
        self._conv = conversation
        self._dispatcher = dispatcher
        self._max_loops = int(max_tool_loops)
        self._max_history_tokens = int(max_history_tokens)
        self._price_in = float(price_input_per_1m or 0.0)
        self._price_out = float(price_output_per_1m or 0.0)
        # 配置管理器：用于在触发 rate limit 时按 profile.fallback_profile_names
        # 顺序切换到备用 profile。None 时禁用 fallback 行为。
        self._config_manager = config_manager
        # 本轮已尝试过的 profile 名（去重防止循环切换）
        self._fallback_visited = set()  # type: set
        # 上一次触发 fallback 的时间戳（monotonic），用于冷却回归判断。
        # 距上次 fallback 超过 _FALLBACK_COOLDOWN_SEC 后新一轮对话会
        # 清空 visited，重新从主 profile 开始尝试。0 表示尚未触发过。
        self._last_fallback_time = 0.0
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
        # 任务情景记忆：跨轮次持久化用户核心意图，防止 LLM 在工具调用
        # 间隙丢失上下文或偏离主线。纯内存，会话结束自动清空。
        self._task_ctx = TaskContextManager()  # type: TaskContextManager

        # 视觉截图触发标记：当用户请求包含"看看/复核/效果/截图/渲染/画面/
        # 检查"等视觉验证关键词、且当前 profile 支持视觉时，会自动截取
        # 视口并附加到最后一条 user 消息。本轮 attach 后置 True 避免重复。
        self._need_vision = False
        self._vision_attached_this_turn = False

        # ---------- 会话级操作回放 (Macro Recorder) ----------
        self._macro_recorder = MacroRecorder()  # type: MacroRecorder

        # ---------- 智能上下文压缩 (Summarization Checkpoint) ----------
        self._context_compressor = ContextCompressor(
            llm_client=self._llm,
        )  # type: ContextCompressor
        # 标记本轮是否已触发过自动压缩（每轮对话仅一次）
        self._compress_triggered = False

        # ---------- 跨会话持久化学习 (Session Memory) ----------
        self._session_memory_mgr = get_session_memory_mgr()
        # 将 session memory 作为 system prompt 附加提供者
        # 与外部 provider 合并：本类原有的 _sys_addon_provider 保持不变，
        # 只是在需要时把 memory addon 也拼接进去。
        self._external_sys_addon_provider = None  # type: Optional[Any]

        # ---------- 双层记忆（长期记忆 + 事件日志） ----------
        # 事件日志：原始对话/工具调用按时间落盘（按天分片 JSONL）
        # 长期记忆：INSTRUCTIONS.md + MEMORY.md + topic/*.md 每轮自动注入
        try:
            from ..memory import get_event_logger
            self._event_logger = get_event_logger()
        except Exception:  # pylint: disable=broad-except
            self._event_logger = None

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

        # ---------- Todo 清单集成 ----------
        # tools/todo_tools.py 里的 _STORE 是模块级单例，通过 change
        # callback 把变更推送给 worker，再由 worker 发 Qt signal 给 UI。
        # 注册全程 best-effort：即使 tools 模块尚未加载，也不影响主流程。
        try:
            from ..tools import todo_tools as _todo
            _todo.set_change_callback(self._on_todo_change)
        except Exception:  # pylint: disable=broad-except
            logger.debug('todo_tools 注册回调失败（已忽略）')

        # ---------- 场景启动扫描 / 项目记忆 / 预算守卫 ----------
        # 只在会话首轮触发扫描/项目记忆注入，跨轮次保持缓存。
        self._scene_scan_done = False
        self._project_mem_loaded_path = ''  # 已注入过的场景路径
        # 预算守卫：按 config 里的 session_token_budget / session_usd_budget
        # 初始化。0 表示不设限。price_* 从当前 profile 折算 USD。
        self._budget_guard = None  # type: Optional[Any]
        self._budget_alerted = False  # exceeded 状态只中止一次
        try:
            from .budget_guard import BudgetGuard
            cfg = None
            if self._config_manager is not None:
                cfg = getattr(self._config_manager, 'config', None)
            tok_bud = int(getattr(cfg, 'session_token_budget', 0) or 0)
            usd_bud = float(getattr(cfg, 'session_usd_budget', 0.0) or 0.0)
            self._budget_guard = BudgetGuard(
                tokens_budget=tok_bud,
                usd_budget=usd_bud,
                price_in=self._price_in,
                price_out=self._price_out,
            )
        except Exception:  # pylint: disable=broad-except
            self._budget_guard = None

    # ------------------------------------------------------------------ #
    # 主线程辅助
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # 备用 Profile 切换（触发 rate limit / 服务不可用时使用）
    # ------------------------------------------------------------------ #
    # fallback 冷却回归：距上次 fallback 超过该秒数后，新一轮对话会重置
    # visited 集合，让主 profile 有机会被再次尝试。取 60 秒是经验值：
    # - 大部分 429 是分钟级配额，60s 足以让配额窗口滑动
    # - 组织级 TPD（日）配额不会在 60s 内恢复，但重置后仍会立刻再次
    #   命中 429 触发切换，只多花一次 LLM 请求成本，代价可接受。
    # ------------------------------------------------------------------ #
    _FALLBACK_COOLDOWN_SEC = 60.0

    def _maybe_reset_fallback_cooldown(self):
        """新一轮对话开始时判断是否重置 visited 集合。

        条件：距上次 fallback 触发已超过 _FALLBACK_COOLDOWN_SEC。
        效果：让主 profile 重新回到候选，避免整个 worker 生命周期都锁
              死在备用链上、错过主 profile 已恢复的窗口。
        """
        if not self._fallback_visited:
            return
        if self._last_fallback_time <= 0:
            return
        elapsed = time.monotonic() - self._last_fallback_time
        if elapsed < self._FALLBACK_COOLDOWN_SEC:
            return
        logger.info(
            'fallback cooldown expired (%.1fs > %.1fs), reset visited=%s',
            elapsed, self._FALLBACK_COOLDOWN_SEC,
            sorted(self._fallback_visited),
        )
        self._fallback_visited.clear()
        # 尝试切回原始主 profile（如果 config 中有记录）
        try:
            active = (
                self._config_manager.get_active_profile()
                if self._config_manager else None
            )
            primary = getattr(self, '_primary_profile_name', '') or ''
            if (
                primary
                and active is not None
                and active.name != primary
                and self._config_manager is not None
                and self._config_manager.get_profile(primary) is not None
            ):
                primary_prof = self._config_manager.get_profile(primary)
                new_client = build_client_from_profile(
                    primary_prof,
                    getattr(self._config_manager, 'config', None),
                )
                self._llm = new_client
                try:
                    self._context_compressor._llm = new_client
                except Exception:  # pylint: disable=broad-except
                    pass
                try:
                    self._config_manager.set_active_profile(primary)
                except Exception:  # pylint: disable=broad-except
                    pass
                logger.info('fallback cooldown: 已回归主 profile %s', primary)
                self.system_notice.emit(
                    'info',
                    '冷却期已过（{}秒），已尝试回归主 Profile: {}'.format(
                        int(self._FALLBACK_COOLDOWN_SEC), primary,
                    ),
                )
        except Exception:  # pylint: disable=broad-except
            logger.exception('fallback cooldown 回归主 profile 失败')

    # ------------------------------------------------------------------ #
    def _try_switch_to_fallback(self):
        """尝试切换到当前 profile 的下一个可用备用 profile。

        :returns: 切换成功返回 (new_profile_name, exhausted=False)；
                  没有可用备用返回 (None, exhausted=True)。
        """
        if self._config_manager is None:
            return None, True
        try:
            active = self._config_manager.get_active_profile()
        except Exception:  # pylint: disable=broad-except
            return None, True
        if active is None:
            return None, True
        # 记录当前 profile 已访问，避免循环
        if active.name not in self._fallback_visited:
            self._fallback_visited.add(active.name)
        fallback_names = list(
            getattr(active, 'fallback_profile_names', None) or [],
        )
        # 按顺序找第一个未访问且存在的 profile
        target_prof = None
        for name in fallback_names:
            if name in self._fallback_visited:
                continue
            prof = self._config_manager.get_profile(name)
            if prof is None:
                continue
            target_prof = prof
            break
        if target_prof is None:
            return None, True
        # 构建新 client 并热替换
        try:
            app_cfg = getattr(self._config_manager, 'config', None)
            new_client = build_client_from_profile(target_prof, app_cfg)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                'fallback build client failed: profile=%s err=%s',
                target_prof.name, exc,
            )
            return None, True
        self._llm = new_client
        self._fallback_visited.add(target_prof.name)
        # 记录主 profile 名（用于冷却期后回归）与本次 fallback 时间戳
        if not getattr(self, '_primary_profile_name', ''):
            self._primary_profile_name = active.name
        self._last_fallback_time = time.monotonic()
        # 同步刷新上下文压缩器的 llm_client
        try:
            self._context_compressor._llm = new_client
        except Exception:  # pylint: disable=broad-except
            pass
        # 同步 config manager 的 active profile（让后续调用与 UI 状态一致）
        try:
            self._config_manager.set_active_profile(target_prof.name)
        except Exception:  # pylint: disable=broad-except
            pass
        logger.info(
            'LLM fallback switched: -> %s (visited=%s)',
            target_prof.name, sorted(self._fallback_visited),
        )
        return target_prof.name, False

    def set_sync_tool_runner(self, runner):
        """注入主线程同步工具执行器。

        runner 签名: ``runner(tool_name: str, arguments: dict) -> Any``
        runner 必须保证在 UI/Max 主线程执行（用 invokeMethod 或 QTimer）。
        """
        self._sync_tool_runner = runner

    def set_system_prompt_addon_provider(self, provider):
        """注入额外 system prompt 提供者。

        内部会将外部 provider 与 SessionMemory 自动合并——
        每轮调用时先取 session_memory 的 addon，再追加外部 provider
        的 addon（如果有），因此外部调用方无需感知 memory 注入逻辑。

        :param provider: 可调用对象，签名 ``provider(user_input: str) -> str``
            在每轮 LLM 调用前被调用，返回拼到 system 消息末尾的额外文本。
            返回空字符串则不附加。
        """
        self._external_sys_addon_provider = provider

        def _merged_provider(user_input):
            parts = []
            # 1) session memory addon（跨会话持久化学习）
            try:
                mem_addon = self._session_memory_mgr.get_system_prompt_addon()
            except Exception:  # pylint: disable=broad-except
                mem_addon = ''
            if mem_addon:
                parts.append(mem_addon)
            # 2) 长期记忆自动注入（INSTRUCTIONS.md + MEMORY.md）
            try:
                from ..memory import build_auto_memory_addon
                lt_addon = build_auto_memory_addon()
            except Exception:  # pylint: disable=broad-except
                lt_addon = ''
            if lt_addon:
                parts.append(lt_addon)
            # 3) 外部自定义 addon
            if self._external_sys_addon_provider is not None:
                try:
                    ext_addon = self._external_sys_addon_provider(user_input)
                except Exception:  # pylint: disable=broad-except
                    ext_addon = ''
                if ext_addon:
                    parts.append(ext_addon)
            return '\n'.join(parts)

        self._sys_addon_provider = _merged_provider

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

    def _maybe_attach_viewport(self):
        # type: () -> None
        """自动触发视口截图并追加到当前 user 消息。

        触发条件（简化后）：
        - 用户本轮请求命中视觉关键词（self._need_vision=True）
        - 当前 LLMProfile 显式声明 vision_supported=True
        - 本轮最多只触发一次（避免多次工具调用间重复截图）

        实现注意：截图函数内部通过 run_on_main 投递到 Max 主线程，
        但本方法仍由 worker 子线程调用，因此是安全的。
        """
        if not self._need_vision:
            return
        if self._vision_attached_this_turn:
            return
        profile = getattr(self._llm, '_profile', None)
        if profile is None:
            return
        if not bool(getattr(profile, 'vision_supported', False)):
            return
        try:
            from ..tools.max.viewport_capture import capture_viewport_attachment
            att = capture_viewport_attachment(name='auto_viewport.png')
            if att is None:
                logger.debug('自动视口截图返回空，跳过')
                return
            # 把附件追加到本轮最后一条 user 消息（即当前用户输入）
            for msg in reversed(self._conv.messages):
                if msg.role == 'user':
                    if msg.attachments is None:
                        msg.attachments = []
                    msg.attachments.append(att)
                    self._vision_attached_this_turn = True
                    logger.info(
                        '自动追加视口截图: model=%s',
                        getattr(profile, 'model', ''),
                    )
                    return
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('自动视口截图失败（已忽略）: %s', exc)

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
        # Todo 会话初始化：让 tools/todo_tools 的单例知道当前活跃会话
        # id，并清空上一轮遗留清单（避免"上一轮的 done 项"串到新任务里
        # 干扰 LLM）。使用 session_id 或 id(self) 作为 key，保证唯一。
        try:
            from ..tools import todo_tools as _todo
            sid = getattr(self, '_session_id', '') or 'sess_{}'.format(
                id(self),
            )
            _todo.set_active_session(sid)
            _todo.reset_todo(sid)
        except Exception:  # pylint: disable=broad-except
            logger.debug('todo_tools 会话切换失败（已忽略）')
        # fallback 冷却：距上次 fallback 触发超过 _FALLBACK_COOLDOWN_SEC 时，
        # 清空 visited 集合，让主 profile 有机会被再次尝试。这样偶发 429
        # 过去后能自动回归高质量 provider，不必让整个 worker 生命周期都锁在
        # 备用链上。冷却期间保持 visited 生效，避免频繁切换来回振荡。
        self._maybe_reset_fallback_cooldown()
        # 记录用户输入到事件日志（Layer 1：原始对话按时间落盘）
        if self._event_logger is not None and user_input:
            try:
                self._event_logger.log(
                    'user_input',
                    payload={'text': user_input},
                    session_id=getattr(self, '_session_id', '') or '',
                )
            except Exception:  # pylint: disable=broad-except
                pass
        # 检测显式长期记忆意图（记住/以后/默认/总是/必须/严禁 等），
        # 命中即追加到 INSTRUCTIONS.md（Layer 2：长期记忆写入）
        try:
            from ..memory import write_instruction_from_user_message
            write_instruction_from_user_message(
                user_input,
                session_id=getattr(self, '_session_id', '') or '',
            )
        except Exception:  # pylint: disable=broad-except
            pass
        # 把用户输入立刻写入对话历史（在调用线程也安全，因为 _conv 修改时序明确）
        if not skip_add_user:
            self._conv.add_user(user_input)

        # 任务解析：基于用户输入自动创建 MissionCard，帮助 LLM 在
        # 多轮工具调用中保持主线不偏离。
        self._parse_and_set_mission_card(user_input or '')

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
    # 场景启动扫描 + 项目记忆注入（首轮一次性）
    # ------------------------------------------------------------------ #
    def _maybe_inject_startup_context(self):
        """会话首轮的 pre-flight：把场景概况和项目记忆注入 system。

        - 场景启动扫描（#4）：一次 build_scene_snapshot，LLM 就能知道
          当前场景有哪些对象、多少 mesh/相机/灯光，避免开局反问。
        - 项目级记忆（#14）：按当前 .max 文件路径读取历史 notes 并注入。

        两者都受 config 开关控制，都是 best-effort：任何异常不影响主流程。
        只在 worker 生命周期内触发一次。
        """
        if self._scene_scan_done:
            return
        self._scene_scan_done = True

        cfg = None
        if self._config_manager is not None:
            cfg = getattr(self._config_manager, 'config', None)

        # 场景启动扫描
        if getattr(cfg, 'enable_scene_startup_scan', True):
            try:
                snap = build_scene_snapshot(self._sync_tool_runner)
                text = snapshot_to_prompt_text(snap)
                if text:
                    self._conv.add_system_note(
                        '## 场景启动扫描（会话初始状态）\n' + text
                        + '\n（以上是 Agent 启动时的场景快照，'
                        '后续每 3 轮会自动刷新一次。）',
                    )
                    logger.info('场景启动扫描已注入 system prompt')
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning('场景启动扫描失败: %s', exc)

        # 项目级记忆
        if getattr(cfg, 'enable_project_memory', True):
            try:
                from ..memory import project_memory as pm
                scene_path = pm.current_scene_path()
                if scene_path:
                    pm.bump_open_count(scene_path)
                    mem = pm.load_project_memory(scene_path)
                    txt = pm.to_prompt_text(mem)
                    if txt:
                        self._conv.add_system_note(txt)
                        self._project_mem_loaded_path = scene_path
                        logger.info(
                            '项目记忆已注入: %s (notes=%d)',
                            scene_path, len(mem.get('notes') or []),
                        )
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning('项目记忆注入失败: %s', exc)

    # ------------------------------------------------------------------ #
    # 任务解析与任务卡管理
    # ------------------------------------------------------------------ #
    def _parse_and_set_mission_card(self, user_input):
        # type: (str) -> None
        """基于用户输入解析任务意图，自动创建/更新 MissionCard。

        同时用关键词判定本轮是否需要视口截图（供 _maybe_attach_viewport 使用）。
        """
        text = (user_input or '').strip()

        # 本轮视觉截图判定：命中关键词才触发（简化版，替代原 TaskPlanner）
        self._vision_attached_this_turn = False
        if text:
            vision_kw = ('看看', '看一下', '复核', '效果', '截图',
                         '画面', '检查', '看下', '瞧瞧')
            self._need_vision = any(k in text for k in vision_kw)
        else:
            self._need_vision = False

        if not text:
            return

        import re

        # 空间关系词表
        spatial_keywords = [
            '上', '上面', '顶上', '下', '下面', '底下',
            '里', '里面', '内部', '外', '外面', '旁边',
            '周围', '中间', '中央', '放到', '放在', '摆到',
            '贴到', '嵌入', '对齐', '居中', '吸附',
        ]
        # 创建动词
        create_keywords = ['创建', '新建', '建立一个', '做一个', '生成',
                           '添加', '放', '摆']
        # 修改动词
        modify_keywords = ['改', '修改', '调整', '设置', '变', '换']
        # 查询动词
        query_keywords = ['查', '看看', '有什么', '在哪里', '多少',
                          '列出', '显示']
        # 布局动词
        layout_keywords = ['排成', '排列', '阵列', '围绕', '环绕',
                           '等距', '分布']

        has_spatial = any(kw in text for kw in spatial_keywords)
        has_create = any(kw in text for kw in create_keywords)
        has_modify = any(kw in text for kw in modify_keywords)
        has_query = any(kw in text for kw in query_keywords)
        has_layout = any(kw in text for kw in layout_keywords)

        # 提取参考对象名：常见模式 "{对象}上" / "在 {对象}"
        ref_obj = ''
        ref_patterns = [
            r'在\s*([A-Za-z0-9_\u4e00-\u9fa5]+(?:\s*\d+)*)\s*(?:上|上面|里|里面)',
            r'([A-Za-z0-9_\u4e00-\u9fa5]+(?:\s*\d+)*)\s*(?:上|上面|里|里面)',
            r'(?:放到|放在|摆到|对齐)\s*([A-Za-z0-9_\u4e00-\u9fa5]+(?:\s*\d+)*)',
        ]
        for pat in ref_patterns:
            m = re.search(pat, text)
            if m:
                ref_obj = m.group(1).strip()
                break

        # 确定任务类型
        if has_layout:
            mission = 'layout'
            total_steps = 3
        elif has_create and has_spatial:
            mission = 'create_and_place'
            total_steps = 4
        elif has_create:
            mission = 'create'
            total_steps = 1
        elif has_modify:
            mission = 'modify'
            total_steps = 2
        elif has_query:
            mission = 'query'
            total_steps = 1
        else:
            # 无法识别，清空旧任务卡（避免残留误导）
            self._task_ctx.clear()
            return

        # 提取目标对象（简单启发式：创建类取对象类型，修改类取名称）
        target_obj = ''
        if has_create:
            obj_types = ['球', '盒子', '立方体', '茶壶', '圆柱',
                         '圆锥', '平面', '圆环', '管状体',
                         'sphere', 'box', 'teapot', 'cylinder',
                         'cone', 'plane', 'torus', 'tube']
            for ot in obj_types:
                if ot in text.lower():
                    target_obj = ot
                    break

        self._task_ctx.create(
            mission=mission,
            target_object=target_obj,
            reference_object=ref_obj,
            spatial_relation='on_top' if '上' in text or '顶' in text else '',
            total_steps=total_steps,
        )

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
        # ---------- 首轮启动扫描 + 项目记忆注入 ----------
        # 只在整个 worker 生命周期做一次，不逐轮重复。
        self._maybe_inject_startup_context()

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

            # 预算硬停（#12）：exceeded 状态阻止发起新的 LLM 请求。
            # _budget_alerted 由 _emit_usage 上一轮设置，避免重复提示。
            if self._budget_alerted:
                logger.warning(
                    'Budget exceeded, aborting further LLM calls '
                    '(loop=%d)', loop_idx,
                )
                self.failed.emit('会话预算已用尽，请在设置中调高上限或开新会话')
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

            # 场景快照注入（B4）：每 3 轮采集一次场景真实状态，
            # 作为 "锚点" 校正 LLM 的空间推理，减少因记忆漂移导致的
            # 位置/对象状态幻觉。跳过第 1 轮（场景尚未改变）
            if loop_idx > 0 and loop_idx % 3 == 0:
                snap = build_scene_snapshot(self._sync_tool_runner)
                snap_text = snapshot_to_prompt_text(snap)
                if snap_text:
                    self._conv.add_system_note(snap_text)
                    if logger.isEnabledFor(10):
                        logger.debug(
                            'Scene snapshot injected at loop=%d',
                            loop_idx + 1,
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

            # 自动上下文压缩检查
            if not self._compress_triggered and self._context_compressor is not None:
                try:
                    all_msgs = self._conv.to_openai_messages()
                    from ..summarization_checkpoint import recommend_auto_compress
                    should_compress, cur_tks, threshold = recommend_auto_compress(
                        all_msgs,
                        model_id=getattr(self._llm, '_model', '') or '',
                        base_url=getattr(self._llm, '_base_url', '') or '',
                    )
                    if should_compress:
                        self._compress_triggered = True
                        checkpoint, removed = self._context_compressor.compress(
                            all_msgs, keep_recent=4,
                        )
                        if removed > 0:
                            self._conv.insert_summary_checkpoint(checkpoint)
                            messages = self._conv.to_openai_messages()
                            logger.info(
                                '自动压缩历史: removed=%d threshold=%d',
                                removed, threshold,
                            )
                except Exception as exc:  # pylint: disable=broad-except
                    logger.warning('自动压缩检查异常: %s', exc)

            # 视觉感知自动触发：若当前规划步骤需要视觉验证，且当前
            # profile 声明支持视觉输入，则自动截取视口并追加到本轮
            # user 消息。截图发生在 _apply_attachments 之前，确保附件
            # 能随消息一起进入 OpenAI 视觉协议。
            self._maybe_attach_viewport()
            # 附件追加后必须重新生成 messages，否则图片不会进入请求体
            messages = self._conv.to_openai_messages()

            # 多模态附件合并：把 user 消息的图片附件按视觉协议拼进
            # content（不支持视觉时降级为纯文本提示）。必须在 token
            # 裁剪之后做——裁剪走的是文本估算，图片 base64 不参与。
            messages = self._apply_attachments(messages)
            # 注入任务情景记忆：若有未完成任务，将任务卡追加到
            # 最后一条 user message 中作为上下文锚点。
            task_prompt = get_task_prompt(self._task_ctx)
            if task_prompt and messages:
                # 找到最后一条 user message，在前面追加任务卡。
                # 注意：视觉启用时 content 是 OpenAI 视觉协议 list，
                # 不能直接做字符串拼接，需要按 list 形态合并。
                for idx in range(len(messages) - 1, -1, -1):
                    if messages[idx].get('role') == 'user':
                        orig = messages[idx].get('content') or ''
                        if isinstance(orig, list):
                            # 视觉协议 list：在第一个 text 项前追加任务卡
                            merged = task_prompt + '\n\n---\n\n'
                            new_content = []
                            inserted = False
                            for item in orig:
                                if not inserted and item.get('type') == 'text':
                                    new_content.append({
                                        'type': 'text',
                                        'text': merged + item.get('text', ''),
                                    })
                                    inserted = True
                                else:
                                    new_content.append(dict(item))
                            if not inserted:
                                # list 中没有 text 项，在头部插入
                                new_content.insert(0, {
                                    'type': 'text',
                                    'text': merged,
                                })
                            messages[idx] = {
                                'role': 'user',
                                'content': new_content,
                            }
                        else:
                            messages[idx] = {
                                'role': 'user',
                                'content': task_prompt + '\n\n---\n\n' + orig,
                            }
                        break
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
            # reasoning_mode 预计算：供下方 DeepSeek 引导和 LLM 调用共用
            reasoning_mode = tools_schema is not None and len(tools_schema) > 0
            # DeepSeek thinking 引导：对支持 reasoning 的 DeepSeek 模型，
            # 在 system prompt 末尾追加 "请充分思考" 的轻量提示，促使模型
            # 在工具规划轮次生成更完整的 reasoning_content，减少空间任务
            # 中的位置计算错误。
            model_name = getattr(self._llm, '_model', '') or ''
            if 'deepseek' in model_name.lower() and reasoning_mode:
                think_guide = (
                    '\n【💡 提示：当前支持深度思考模式，请在规划工具调用时'
                    '充分利用 reasoning 能力，详细分析空间位置和参数关系，'
                    '确保工具参数准确无误后再执行。】'
                )
                if messages and messages[0].get('role') == 'system':
                    base = messages[0].get('content') or ''
                    messages[0] = {
                        'role': 'system',
                        'content': base + think_guide,
                    }
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

                # reasoning_mode=True：工具调用/规划轮次锁定低温度 0.1，
                # 减少参数幻觉和过度联想；最终回复轮次（无 tools）保持原温度
                resp = self._llm.chat(
                    messages=messages,
                    tools=tools_schema,
                    stream=True,
                    on_delta=self._on_text_chunk,
                    cancel_check=self._cancel_event.is_set,
                    reasoning_mode=reasoning_mode,
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
            except LLMRateLimitError as exc:
                # 触发速率限制/服务过载 或 账户级不可用（欠费/Key 无效等）：
                # 都尝试切换到备用 profile 后重试
                self._flush_chunk_buf()
                kind = getattr(exc, 'kind', 'rate_limit')
                if kind == 'account':
                    scenario = '账户级不可用（HTTP {}）'.format(exc.status_code)
                    status_prefix = '⚠ 账户不可用，已切换到备用 Profile: '
                else:
                    scenario = '上游速率限制（HTTP {}）'.format(exc.status_code)
                    status_prefix = '⚠ 触发速率限制，已切换到备用 Profile: '
                new_name, exhausted = self._try_switch_to_fallback()
                if new_name is not None:
                    notice_msg = (
                        '{}，已切换到备用 Profile: {}，将继续本次对话。'
                        '\n原始错误：{}'.format(scenario, new_name, exc)
                    )
                    self.status_changed.emit(status_prefix + new_name)
                    # 同时发一条持久气泡，避免用户只在状态栏看到瞬时提示
                    self.system_notice.emit('warn', notice_msg)
                    logger.warning(
                        'LLM fallback triggered kind=%s status=%s to=%s',
                        kind, exc.status_code, new_name,
                    )
                    continue
                # 备用链耗尽或未配置：视为不可恢复
                if kind == 'account':
                    err_reason = (
                        '当前 Profile 账户不可用（欠费/Key 无效/权限/模型缺失），'
                        '且备用 Profile 链已耗尽或未配置。'
                        '请到设置中检查 Key/余额，或为当前 Profile 配置备用链。'
                    )
                else:
                    err_reason = (
                        '上游速率限制且备用 Profile 链已耗尽或未配置，'
                        '本次对话中断。请到设置中为当前 Profile 配置备用链，'
                        '或稍后重试。'
                    )
                self.system_notice.emit('error', err_reason)
                self.failed.emit(
                    'LLM 调用失败（{}，无可用备用 Profile）: {}'.format(
                        scenario, exc,
                    ),
                )
                return
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
            # 崩溃防丢：assistant 消息落 conversation 后立即通知 UI 落盘。
            # 这样即使下一步 tool_call 期间 Max 崩溃，本轮已完成的思考/
            # 工具调用 id 也不会丢失，重启后能继续或至少看到中断位置。
            self.turn_progress.emit()

            # 把整段文本通知 UI（即使是流式也再发一次完整版，方便 UI 收尾）
            if content:
                self.text_message_complete.emit(content)

            # 没有工具调用 → 整轮结束
            if not tool_calls:
                # ⚡ 立即通知 UI 恢复"发送"按钮状态，避免用户感知到后台
                # 学习/反思/技能提议带来的 2~5 秒延迟。这些是"最佳努力"
                # 的收尾任务，用户不需要等它们完成才能进行下一轮对话。
                self.finished.emit()

                # 记录本轮最终 assistant 回复到事件日志（轻量本地 IO）
                if self._event_logger is not None and content:
                    try:
                        self._event_logger.log(
                            'assistant_reply',
                            payload={'text': content},
                            session_id=getattr(self, '_session_id', '') or '',
                        )
                    except Exception:  # pylint: disable=broad-except
                        pass
                # 会话记忆学习（可能触发 LLM 摘要调用，耗时）
                try:
                    self._session_memory_mgr.learn_from_session(
                        self._conv, session_id=getattr(self, '_session_id', '') or '',
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    logger.warning('Session memory 学习异常: %s', exc)
                # 自动反思（同步 LLM 调用，主要延迟源）
                self._reflect_session()
                # 技能提议（可能扫描历史 + LLM 判断）
                self._propose_skill_from_session()
                return

            # 批次级 Verify：仅对含状态变更工具的批次做前后场景快照对比
            _mutating_prefixes = (
                'create_', 'modify_', 'set_', 'move_', 'delete_',
                'apply_', 'rotate_', 'scale_', 'add_', 'assign_',
                'rename_', 'group_', 'collapse_',
            )
            _has_mutation = False
            for _tc in tool_calls:
                try:
                    _tname = (_tc.get('function') or {}).get('name') or ''
                except Exception:  # pylint: disable=broad-except
                    _tname = ''
                if _tname.startswith(_mutating_prefixes):
                    _has_mutation = True
                    break

            _before_snap = None
            if _has_mutation:
                try:
                    _before_snap = build_scene_snapshot(self._sync_tool_runner)
                except Exception as _exc:  # pylint: disable=broad-except
                    logger.debug('批次前快照采集失败（已忽略）: %s', _exc)
                    _before_snap = None

            # ---------- 操作确认清单（#10）+ 整轮 Undo（#11） ---------- #
            # 1) 按 config.approval_threshold 判定是否需要用户批准
            # 2) 用 UndoBatch 把整批操作合成 Max 的单次 Undo
            _cfg = None
            if self._config_manager is not None:
                _cfg = getattr(self._config_manager, 'config', None)
            _approval_needed = False
            _approved_calls = tool_calls
            if getattr(_cfg, 'enable_approval_queue', False) and tool_calls:
                try:
                    from .approval_queue import build_plan
                    from ..macro_recorder import describe_action
                    _thres = int(getattr(_cfg, 'approval_threshold', 3) or 3)
                    _plan = build_plan(
                        tool_calls,
                        approval_threshold=_thres,
                        describe_fn=describe_action,
                    )
                    if _plan.needs_approval:
                        _approval_needed = True
                        # UI 层需要监听 system_notice 展示批准对话框；
                        # 当前实现走"默认放行 + 显式告知"策略：把清单
                        # 以 warn 气泡展示，用户可通过 Ctrl+Z 撤销。
                        # （后续若 UI 补上批准对话框，可改为阻塞等待）
                        try:
                            _msg = (
                                '📋 本批操作 {}，已直接执行；如需回退请按'
                                ' Ctrl+Z（会作为单次 Undo 一并撤销）。\n'
                                '{}'.format(
                                    _plan.summary_text(),
                                    '\n'.join(
                                        '  · ' + it.description
                                        for it in _plan.items[:8]
                                    ),
                                )
                            )
                            self.system_notice.emit('warn', _msg)
                        except Exception:  # pylint: disable=broad-except
                            pass
                except Exception as _exc:  # pylint: disable=broad-except
                    logger.debug('approval_queue 判定异常（放行）: %s', _exc)

            # UndoBatch 把整批 tool_calls 合并成 Max 的一次 Undo 记录
            _undo_ctx = None
            if _has_mutation and getattr(_cfg, 'wrap_undo', True):
                try:
                    from .approval_queue import UndoBatch
                    _undo_ctx = UndoBatch(
                        label='MaxAgent Batch #{}'.format(loop_idx + 1),
                    )
                except Exception:  # pylint: disable=broad-except
                    _undo_ctx = None

            # 有工具调用 → 逐个执行，结果写回历史（可选包 UndoBatch）
            if _undo_ctx is not None:
                with _undo_ctx:
                    for tc in tool_calls:
                        if self._cancel_event.is_set():
                            self.failed.emit('用户取消')
                            return
                        self._exec_one_tool_call(tc)
            else:
                for tc in tool_calls:
                    if self._cancel_event.is_set():
                        self.failed.emit('用户取消')
                        return
                    self._exec_one_tool_call(tc)

            # 批次执行完后采集 after 快照并 diff，非空则注入 system note
            if _has_mutation and _before_snap is not None:
                try:
                    _after_snap = build_scene_snapshot(self._sync_tool_runner)
                    _diff = diff_snapshots(_before_snap, _after_snap)
                    if _diff and not _diff.get('empty', True):
                        _diff_text = diff_to_prompt_text(_diff)
                        if _diff_text:
                            self._conv.add_system_note(_diff_text)
                            logger.info(
                                'batch verify diff injected '
                                '(added=%d removed=%d moved=%d)',
                                len(_diff.get('added') or []),
                                len(_diff.get('removed') or []),
                                len(_diff.get('moved') or []),
                            )
                except Exception as _exc:  # pylint: disable=broad-except
                    logger.debug('批次后快照/diff 失败（已忽略）: %s', _exc)

            # 任务卡推进：每完成一批工具调用，推进任务步骤
            if self._task_ctx.is_active():
                self._task_ctx.advance_step()
                # 若任务已完成，清空任务卡避免残留
                if not self._task_ctx.is_active():
                    self._task_ctx.complete()

            # 第一批工具执行完成后注入"完成即停"软提示（每轮对话仅一次）：
            # 防止 LLM 看到工具成功结果后产生"既然能创建球，那再顺便
            # 加个灯光让画面好看一点"的扩展冲动，强制它优先完成确认
            # 回复，不做未被显式要求的额外操作。
            #
            # 同时给出"空间完成原则"反向提醒：当用户明确说了"放到/上面/
            # 对齐/沿着"等空间动词时，create_* 工具调用完成 ≠ 任务完成，
            # 必须继续摆放/对齐 + 复核——避免把空间任务的创建步骤当成
            # 终点提早收工，对象遗留在世界原点。
            if not restraint_hinted:
                restraint_hinted = True
                self._conv.add_system_note(
                    self._get_restraint_hint(),
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
        try:
            self._session_memory_mgr.learn_from_session(
                self._conv, session_id=getattr(self, '_session_id', '') or '',
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('Session memory 学习异常: %s', exc)
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

        # 记录工具调用事件（Layer 1）
        if self._event_logger is not None:
            try:
                self._event_logger.log(
                    'tool_call',
                    payload={
                        'name': name,
                        'arguments': args,
                        'call_id': call_id,
                    },
                    session_id=getattr(self, '_session_id', '') or '',
                )
            except Exception:  # pylint: disable=broad-except
                pass

        # 走主线程注入的同步执行器（DCC API 必须在主线程）
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
            # 崩溃防丢：错误消息也要立刻落盘，重启后能看到失败点
            self.turn_progress.emit()
            self.tool_finished.emit(
                name, False,
                json.dumps({'error': err}, ensure_ascii=False),
                call_id,
            )
            return

        ok = True
        try:
            result = self._sync_tool_runner(name, args)
            result_dict = {
                'ok': True,
                'data': result,
                'error': None,
                'suggestion': None,
            }
        except ToolExecutionError as exc:
            ok = False
            result_dict = {
                'ok': False,
                'data': None,
                'error': str(exc),
                'suggestion': '请检查工具参数与当前场景状态，修正后重试。',
                'tool': name,
            }
        except Exception as exc:  # pylint: disable=broad-except
            ok = False
            result_dict = {
                'ok': False,
                'data': None,
                'error': '{}: {}'.format(type(exc).__name__, exc),
                'suggestion': '工具执行异常，请检查参数合法性及场景状态后重试。',
                'tool': name,
            }

        # 自动状态复核（D1）：对修改类工具，把复核信息直接融入 result，
        # 让 LLM 在下一轮能立刻看到"预期 vs 实际"对比。
        if ok and self._sync_tool_runner is not None:
            verify_info = self._auto_verify_tool_result(name, args, result)
            if verify_info:
                result_dict['__verification__'] = verify_info

        # 把结果序列化成字符串塞回 conversation（OpenAI 协议要求 content 是字符串）
        content_str = self._safe_json_dumps(result_dict)
        self._conv.add_tool_result(
            tool_call_id=call_id,
            name=name,
            content=content_str,
        )
        # 崩溃防丢：单步 tool_result 落盘。长任务中途 Max 崩溃时，
        # 已完成的步骤会保留在磁盘上，重启后用户能看到执行到哪一步。
        self.turn_progress.emit()
        self.tool_finished.emit(name, ok, content_str, call_id)
        self._macro_recorder.record(name, args, ok)
        # 记录工具结果事件（Layer 1）
        if self._event_logger is not None:
            try:
                self._event_logger.log(
                    'tool_result',
                    payload={
                        'name': name,
                        'ok': ok,
                        'call_id': call_id,
                        'data': result_dict,
                    },
                    session_id=getattr(self, '_session_id', '') or '',
                )
            except Exception:  # pylint: disable=broad-except
                pass

    # ------------------------------------------------------------------ #
    # Todo 回调（tools/todo_tools.py 变更时触发）
    # ------------------------------------------------------------------ #
    def _on_todo_change(self, session_id, snapshot):
        # type: (str, Dict[str, Any]) -> None
        """Todo 单例内部变更时的回调，转发为 Qt signal。

        运行线程：调用发生在执行 todo_write/todo_update_status 的线程里，
        与本 worker 的 dispatcher 线程相同。Signal 走 QueuedConnection
        到 UI 主线程处理，无跨线程安全问题。
        """
        try:
            self.todo_updated.emit(session_id or '', snapshot or {})
        except Exception:  # pylint: disable=broad-except
            logger.debug('todo_updated emit 失败（已忽略）')

    # ------------------------------------------------------------------ #
    # 自动状态复核
    # ------------------------------------------------------------------ #
    def _get_restraint_hint(self):
        # type: () -> str
        """根据当前 DCC 生成工具执行后的 restraint hint 文本。"""
        from .conversation import current_dcc

        if current_dcc() == 'maya':
            info_tool = 'get_maya_object_info'
        else:
            info_tool = 'get_object_info'

        return (
            '✅ 上一批工具已执行完毕。现在请按以下两种情形之一处理：\n'
            '【情形 A·字面请求】用户原始请求只是"创建一个 X"或'
            '类似无空间词的简单创建：\n'
            '  → **立即**给出简短的中文确认回复（如"已为你创建'
            '一个球"）并结束本轮；\n'
            '  → **不要**追加灯光、相机、材质、地面、修改器等'
            '未被显式要求的操作。\n'
            '【情形 B·空间请求】用户原始请求包含位置/对齐/堆叠/'
            '排列等空间动词（如"放到桌子上"、"沿 X 轴排列"、'
            '"和 A 对齐"）：\n'
            '  → 创建只是第一步，**禁止**直接回"已完成"！\n'
            '  → 必须继续：① 用 ' + info_tool + ' 拿参考对象的'
            '位置/包围盒；② 计算并设置新对象的 transform；'
            '③ 用 ' + info_tool + ' 复核结果；\n'
            '  → 最终回复必须报告关键数值（如"杯子已放置在 '
            'Table01 顶面中心 (12.3, 45.6, 78.9)"），让用户'
            '能立刻判断对错。\n'
            '请先判断当前任务属于 A 还是 B，再决定是收工还是'
            '继续动作。如果属于 B 但发现参考对象有多个候选，'
            '先停手询问用户而不是乱猜。'
        )

    def _auto_verify_tool_result(self, tool_name, args, result):
        # type: (str, Dict[str, Any], Any) -> Optional[Dict[str, Any]]
        """对修改类工具执行后查询真实状态进行对比。

        :param tool_name: 工具名
        :param args: 工具参数
        :param result: 工具返回结果
        :returns: 复核信息 dict 或 None（不需要复核时）
        """
        # 仅对影响场景状态的工具执行复核
        stateful_prefixes = (
            'create_',
            'modify_',
            'set_',
            'move_',
            'delete_',
            'apply_',
        )
        if not tool_name.startswith(stateful_prefixes):
            return None

        # 提取对象名：优先从 tools_schema 的参数映射，或者从 result 中推断
        target_name = ''  # type: str
        if isinstance(args, dict):
            # 常见参数名映射
            for key in ('name', 'object_name', 'target', 'node_name'):
                val = args.get(key)
                if val and isinstance(val, str):
                    target_name = val
                    break
        if not target_name and isinstance(result, dict):
            # result 中可能返回了创建的对象名
            target_name = result.get('name') or ''

        if not target_name:
            # 无法确定对象名，无法进行复核
            return None

        try:
            # 通过主线程执行器查询对象真实状态
            from .conversation import current_dcc
            info_tool = (
                'get_maya_object_info'
                if current_dcc() == 'maya'
                else 'get_object_info'
            )
            verify_result = self._sync_tool_runner(
                info_tool,
                {'name': target_name},
            )
        except Exception as exc:  # pylint: disable=broad-except
            return {
                'target': target_name,
                'status': 'query_failed',
                'error': str(exc),
            }

        # _sync_tool_runner 返回工具原始结果（不含 ok/data 外壳）。
        # Maya get_maya_object_info -> {'exists': bool, 'name': ..., 'position': ...}
        # Max get_object_info     -> {'found': bool, 'name': ..., ...} 或类似
        info = verify_result if isinstance(verify_result, dict) else {}
        # 兼容不同 DCC 的"存在性"字段
        exists = info.get('exists')
        if exists is None:
            exists = info.get('found')
        if exists is None:
            # 没有明确布尔字段：若返回了 name/type/position 中任一，视为存在
            exists = any(k in info for k in ('name', 'type', 'position'))

        if exists:
            return {
                'target': target_name,
                'status': 'verified',
                'current_position': info.get('position'),
                'current_rotation': (
                    info.get('rotation')
                    or info.get('rotation_euler')
                ),
                'current_scale': info.get('scale'),
                'current_material': info.get('material'),
                'note': (
                    '以上为此对象执行 {} 后的真实状态。'
                    '请对比你的预期值，若有偏差请修正。'.format(
                        tool_name,
                    )
                ),
            }
        return {
            'target': target_name,
            'status': 'not_found',
            'note': (
                '复核时未找到对象 {}，可能已被删除或重命名。'
                .format(target_name)
            ),
        }

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

        # ---------- 预算守卫（#12） ---------- #
        # 累计到 BudgetGuard；触发 warn/exceeded 时通过 system_notice
        # 把告警气泡推到对话流。exceeded 会同时置 _budget_alerted，
        # _run_loop 下一轮开头会检查并中止。
        if self._budget_guard is not None:
            try:
                snap = self._budget_guard.accumulate(usage or {})
                if snap.status == 'warn' and snap.message:
                    try:
                        self.system_notice.emit('warn', snap.message)
                    except Exception:  # pylint: disable=broad-except
                        pass
                elif snap.status == 'exceeded' and not self._budget_alerted:
                    self._budget_alerted = True
                    try:
                        self.system_notice.emit('error', snap.message)
                    except Exception:  # pylint: disable=broad-except
                        pass
                    # 同时给 LLM 注入 system note 让它立刻收尾
                    try:
                        self._conv.add_system_note(
                            snap.message
                            + '\n请立即用一句话总结已完成的工作，'
                            '并停止任何新的工具调用。',
                        )
                    except Exception:  # pylint: disable=broad-except
                        pass
            except Exception as exc:  # pylint: disable=broad-except
                logger.debug('BudgetGuard accumulate 异常: %s', exc)

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
            '你正在为一个 DCC AI 助手压缩对话历史。请阅读以下完整对话，'
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

    def _reflect_session(self):
        # type: () -> None
        """会话结束时的最佳努力自动反思。

        收集本轮会话的关键信息，调用 LLM 生成反思建议，并将结果写入
        事件日志。不直接写入长期记忆，而是作为 memory_proposal 等待
        用户确认。所有异常都会被吞掉，不能影响主路径关闭会话。
        """
        session_id = getattr(self, '_session_id', '') or ''
        try:
            # 1. 判断是否有必要反思：过短且无工具调用则跳过
            user_msgs = [
                m for m in self._conv.messages if m.role == 'user'
            ]
            tool_calls = [
                m for m in self._conv.messages
                if m.role == 'assistant' and m.tool_calls
            ]
            if len(user_msgs) <= 1 and not tool_calls:
                logger.debug('会话过短且无工具调用，跳过自动反思')
                return

            # 2. 收集最近 3 条用户输入
            recent_user_inputs = [
                (m.content or '') for m in user_msgs[-3:]
            ]

            # 3. 统计工具调用成功/失败比例
            tool_stats = self._collect_tool_stats()

            # 4. 检测是否使用了 Skill 或收到用户纠正
            has_skill = self._detect_skill_usage()
            has_correction = self._detect_user_correction()

            # 5. 构造反思 prompt
            reflection_prompt = self._build_reflection_prompt(
                recent_user_inputs=recent_user_inputs,
                tool_stats=tool_stats,
                has_skill=has_skill,
                has_correction=has_correction,
            )

            # 6. 调用 LLM 生成反思结果（子线程调用，不阻塞主线程）
            resp = self._llm.chat(
                messages=reflection_prompt,
                tools=None,
                stream=False,
                temperature=0.3,
            )
            reflection_text = ''
            if isinstance(resp, dict):
                reflection_text = resp.get('content', '') or ''
            elif isinstance(resp, str):
                reflection_text = resp

            if not reflection_text:
                return

            # 7. 尝试解析 JSON；如果解析失败，按整段文本作为 summary
            reflection = self._parse_reflection(reflection_text)

            # 8. 更新本轮命中 Skill 的 telemetry
            self._update_skill_telemetry(tool_stats)

            # 9. 写入事件日志
            if self._event_logger is not None:
                self._event_logger.log(
                    'session_reflection',
                    payload=reflection,
                    session_id=session_id,
                )
            logger.info(
                '会话反思完成: summary=%s confidence=%s topic=%s',
                reflection.get('summary', '')[:30],
                reflection.get('confidence', 0),
                reflection.get('topic', ''),
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug('自动反思失败（已忽略）: %s', exc)

    def _collect_tool_stats(self):
        # type: () -> Dict[str, Any]
        """统计本轮会话中的工具调用成功/失败情况。"""
        total = 0
        success = 0
        failed = 0
        tool_names = []
        for m in self._conv.messages:
            if m.role != 'tool':
                continue
            total += 1
            tool_names.append(m.name or '')
            try:
                payload = json.loads(m.content or '{}')
                if payload.get('ok'):
                    success += 1
                else:
                    failed += 1
            except (TypeError, ValueError):
                failed += 1
        return {
            'total': total,
            'success': success,
            'failed': failed,
            'tool_names': tool_names,
        }

    def _propose_skill_from_session(self):
        # type: () -> None
        """从本轮 Macro Recorder 生成 Skill 建议并发射信号。

        触发门槛（满足任一即跳过）：
        1. 全局开关 ``enable_skill_proposal`` 未启用（默认关闭）
        2. Macro Recorder 为空
        3. 成功动作数 < ``skill_proposal_min_actions``（默认 3）
        4. 所有动作都是只读/查询类工具（get_* / list_* / find_* 等）
        5. 已存在同名 Skill，或触发词与已有 Skill 高度重叠
        生成结果通过 skill_proposed 信号交给 UI 层确认保存。
        """
        try:
            # 门槛 1：全局开关
            cfg = None
            if self._config_manager is not None:
                cfg = getattr(self._config_manager, 'config', None)
            if cfg is None or not getattr(cfg, 'enable_skill_proposal', False):
                return

            # 门槛 2：recorder 非空
            if self._macro_recorder is None or self._macro_recorder.is_empty():
                return

            # 门槛 3+4：提前统计成功动作，过滤纯查询会话
            try:
                actions = self._macro_recorder.session.to_dict().get(
                    'actions', [],
                )
            except Exception:  # pylint: disable=broad-except
                actions = []
            success_actions = [
                a for a in actions
                if a.get('ok', True) and a.get('tool')
            ]
            min_actions = int(
                getattr(cfg, 'skill_proposal_min_actions', 3) or 3,
            )
            if len(success_actions) < max(1, min_actions):
                return
            # 只读工具前缀：查询类操作不算"值得沉淀的流程"
            readonly_prefixes = (
                'get_', 'list_', 'find_', 'check_', 'query_',
                'build_scene_semantic_graph', 'diff_scene_snapshots',
            )
            has_write = False
            for a in success_actions:
                tool = (a.get('tool') or '')
                if not tool.startswith(readonly_prefixes):
                    has_write = True
                    break
            if not has_write:
                return

            session_id = getattr(self, '_session_id', '') or ''
            proposal = propose_skill_from_recorder(
                self._macro_recorder,
                user_input=self._current_user_input or '',
                session_id=session_id,
            )
            if not proposal:
                return
            manifest = proposal.get('manifest')
            impl_code = proposal.get('impl_code', '')
            if not (manifest and manifest.get('instructions')):
                return

            # 门槛 5：与已有 Skill 去重（同名或触发词交集）
            try:
                from ..skills import SkillManager
                existing = SkillManager().list_all_skills()
            except Exception:  # pylint: disable=broad-except
                existing = []
            new_name = (manifest.get('name') or '').strip().lower()
            new_kws = set(
                (kw or '').strip().lower()
                for kw in (manifest.get('trigger_keywords') or [])
                if kw
            )
            for sk in existing:
                if new_name and new_name == (sk.name or '').strip().lower():
                    logger.debug('Skill 提议已存在同名，跳过: %s', new_name)
                    return
                old_kws = set(
                    (kw or '').strip().lower()
                    for kw in (sk.trigger_keywords or [])
                    if kw
                )
                if new_kws and old_kws and (new_kws & old_kws):
                    logger.debug(
                        'Skill 提议触发词与已有 Skill 重叠，跳过: %s',
                        new_kws & old_kws,
                    )
                    return

            logger.info(
                '生成 Skill 建议: %s (动作数=%d)',
                manifest.get('name'), len(success_actions),
            )
            self.skill_proposed.emit(manifest, impl_code)
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug('生成 Skill 建议失败（已忽略）: %s', exc)

    def _detect_skill_usage(self):
        # type: () -> bool
        """检测本轮会话是否涉及 Skill 调用或学习。"""
        for m in self._conv.messages:
            if m.role != 'user':
                continue
            text = (m.content or '').lower()
            if any(kw in text for kw in ['skill', '技能', '学习', '记住']):
                return True
        # 检查事件日志中是否有 skill 相关事件
        if self._event_logger is not None:
            try:
                events = self._event_logger.search(
                    kind='skill_call', topk=1,
                    start_ts=time.time() - 3600,
                )
                if events:
                    return True
            except Exception:  # pylint: disable=broad-except
                pass
        return False

    def _update_skill_telemetry(self, tool_stats):
        # type: (Dict[str, Any]) -> None
        """根据本轮会话更新命中 Skill 的运行统计。

        命中规则：任意 user 消息包含某 enabled skill 的 trigger_keyword。
        会话成功结束则 success_count +1；存在工具失败则 fail_count +1。
        仅更新已有 skill，不创建新 skill。
        """
        try:
            from ..skills import SkillManager
            mgr = SkillManager()
            skills = mgr.list_skills()
            if not skills:
                return
            user_texts = [
                (m.content or '').lower()
                for m in self._conv.messages if m.role == 'user'
            ]
            matched = []
            for sk in skills:
                for kw in (sk.trigger_keywords or []):
                    kw_lower = kw.lower()
                    if any(kw_lower in t for t in user_texts):
                        matched.append(sk)
                        break
            if not matched:
                return
            failed = tool_stats.get('failed', 0) > 0
            for sk in matched:
                sk.use_count += 1
                if failed:
                    sk.fail_count += 1
                else:
                    sk.success_count += 1
                sk.updated_at = time.time()
                try:
                    mgr.save(sk, overwrite=True)
                except Exception:  # pylint: disable=broad-except
                    pass
            logger.debug(
                '更新 Skill telemetry: matched=%d failed=%s',
                len(matched), failed,
            )
        except Exception:  # pylint: disable=broad-except
            pass

    def _detect_user_correction(self):
        # type: () -> bool
        """检测本轮是否有用户纠正助手的迹象。"""
        correction_keywords = [
            '不对', '错了', '不是', '重新', '改一下', '不是这样',
            '不要', '别', '撤销', '不是这样的', '请重新',
        ]
        for m in self._conv.messages:
            if m.role != 'user':
                continue
            text = (m.content or '').lower()
            if any(kw in text for kw in correction_keywords):
                return True
        return False

    def _build_reflection_prompt(self, recent_user_inputs, tool_stats,
                                 has_skill, has_correction):
        # type: (List[str], Dict[str, Any], bool, bool) -> List[Dict[str, str]]
        """构造生成反思建议的 LLM prompt。"""
        user_inputs_text = '\n'.join(
            '{}. {}'.format(i + 1, t)
            for i, t in enumerate(recent_user_inputs)
        )
        tool_summary = (
            '工具调用总数: {total}，成功: {success}，失败: {failed}，'
            '涉及工具: {tools}'.format(
                total=tool_stats.get('total', 0),
                success=tool_stats.get('success', 0),
                failed=tool_stats.get('failed', 0),
                tools=', '.join(tool_stats.get('tool_names', [])),
            )
        )
        system_msg = (
            '你是一名会话分析助手。请基于以下本轮 DCC AI 助手与'
            '用户的交互信息，生成一段结构化的反思建议，用于决定是否'
            '更新长期记忆。\n'
            '请严格按以下 JSON 格式输出（不要包含 markdown 代码块标记）：\n'
            '{\n'
            '  "summary": "用一句话概括本轮用户的核心需求",\n'
            '  "memory_proposal": "如果观察到值得写入长期记忆的偏好、'
            '习惯或模式，请具体描述；否则写空字符串",\n'
            '  "confidence": 0.0到1.0之间的数字，表示这个建议的可信度,\n'
            '  "topic": "建议写入的 topic 名，如 user-preferences；'
            '若无可写则写空字符串"\n'
            '}\n'
            '注意：只有稳定、跨会话可复用的偏好或工作模式才值得写入'
            '长期记忆；一次性请求或临时表达请返回空 memory_proposal。'
        )
        user_msg = (
            '最近用户输入（由新到旧）：\n{}\n\n{}\n\n'
            '是否使用了 Skill/学习相关表达: {}\n'
            '是否检测到用户纠正: {}\n\n'
            '请生成反思建议。'
        ).format(
            user_inputs_text,
            tool_summary,
            '是' if has_skill else '否',
            '是' if has_correction else '否',
        )
        return [
            {'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': user_msg},
        ]

    def _parse_reflection(self, text):
        # type: (str) -> Dict[str, Any]
        """解析 LLM 返回的反思文本，失败时降级为简单结构。"""
        cleaned = text.strip()
        # 去掉可能的 markdown 代码块
        if cleaned.startswith('```'):
            lines = cleaned.split('\n')
            if lines[0].startswith('```'):
                lines = lines[1:]
            if lines and lines[-1].startswith('```'):
                lines = lines[:-1]
            cleaned = '\n'.join(lines).strip()
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return {
                    'summary': str(parsed.get('summary', '')),
                    'memory_proposal': str(parsed.get('memory_proposal', '')),
                    'confidence': float(parsed.get('confidence', 0.0) or 0.0),
                    'topic': str(parsed.get('topic', '')),
                }
        except (TypeError, ValueError):
            pass
        # 兜底：把整段文本作为 summary
        return {
            'summary': cleaned,
            'memory_proposal': '',
            'confidence': 0.0,
            'topic': '',
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