#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MaxAgentDockWidget 的「Token 用量与上下文预算」子域 mixin。

从 ``dock_widget.py`` 抽出的一组围绕顶部状态条（context_label /
usage_label）与 profile 价格/上下文预算相关的辅助方法。

这些方法之间只通过 ``self._config`` / ``self._conv`` / ``self._usage_session``
/ ``self.context_label`` / ``self.usage_label`` / ``self._renderer``
交互，与其它子域（会话、worker 信号、发送/输入区）不耦合，因此可以
独立成 mixin。抽取仅按功能拆分文件，行为与原实现一致。
"""

from __future__ import absolute_import
from __future__ import print_function

from ..logger import get_logger
from .emoji_compat import ee as _ee


logger = get_logger(__name__)


class _UsageBudgetMixin(object):
    """顶部 usage / context 状态条与 profile 计费/预算读取。"""

    # ------------------------------------------------------------------ #
    # profile 计费/预算/循环上限读取
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # 顶部状态条：context_label / usage_label
    # ------------------------------------------------------------------ #

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
