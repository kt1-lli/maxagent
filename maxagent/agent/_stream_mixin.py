#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AgentWorker 流式输出与 usage 的 mixin。

本模块仅提供方法，不定义独立类实例状态；所有 ``self.*`` 属性都由
``AgentWorker.__init__`` 初始化（如 ``_chunk_buf`` / ``_chunk_buf_lock``
/ ``_chunk_flush_chars`` / ``_price_in`` / ``_budget_guard`` 等）。

拆分自 worker.py，行为完全等价。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import time

from ..logger import get_logger

logger = get_logger(__name__)


class _StreamMixin(object):
    """流式 chunk 节流 + usage/预算处理。"""

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
                logger.debug('status_changed emit failed', exc_info=True)
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
            logger.debug('usage_received emit failed', exc_info=True)

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
                        logger.debug('system_notice warn emit failed', exc_info=True)
                elif snap.status == 'exceeded' and not self._budget_alerted:
                    self._budget_alerted = True
                    try:
                        self.system_notice.emit('error', snap.message)
                    except Exception:  # pylint: disable=broad-except
                        logger.debug('system_notice error emit failed', exc_info=True)
                    # 同时给 LLM 注入 system note 让它立刻收尾
                    try:
                        self._conv.add_system_note(
                            snap.message
                            + '\n请立即用一句话总结已完成的工作，'
                            '并停止任何新的工具调用。',
                        )
                    except Exception:  # pylint: disable=broad-except
                        logger.debug('add_system_note failed', exc_info=True)
            except Exception as exc:  # pylint: disable=broad-except
                logger.debug('BudgetGuard accumulate 异常: %s', exc)

    @staticmethod
    def _safe_json_dumps(obj):
        """安全序列化：遇到非 JSON-safe 对象回退为 repr。"""
        def _default(o):
            try:
                return repr(o)
            except Exception:  # pylint: disable=broad-except
                logger.debug('repr default failed', exc_info=True)
                return '<unserializable>'

        try:
            return json.dumps(obj, ensure_ascii=False, default=_default)
        except Exception:  # pylint: disable=broad-except
            # 兜底：保证一定能塞回 LLM
            logger.debug('json.dumps failed, fallback to error stub', exc_info=True)
            return json.dumps(
                {'ok': False, 'error': 'tool result not serializable'},
                ensure_ascii=False,
            )
