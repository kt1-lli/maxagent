#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cost 预算保护（Cost Budget Guard）——按会话累计 token / USD 上限告警。

**动机**：Kimi TPD 429 让用户吃过亏——一天 150 万 token 上限，
Agent 一旦进入长循环工具调用很容易撞墙。此模块在 worker 每轮 LLM
调用返回后累计 usage，并按用户配置的软/硬阈值做三级响应：

- **50%** ：不打扰
- **80%** ：告警一次，UI 显示琥珀色 system 气泡
- **100%**：硬停，向 LLM 追加 system note 要求立刻收尾并阻止下一轮调用

**用途**：只做统计和判定，不主动改 conversation 或 emit 信号；worker
根据 ``check_budget()`` 结果决定行为。这样单测可以完全脱离 Qt。
"""

from __future__ import absolute_import
from __future__ import print_function

from dataclasses import dataclass
from dataclasses import field
from typing import Dict


# 阈值百分比
_WARN_PCT = 0.80
_HARD_PCT = 1.00


@dataclass
class BudgetSnapshot(object):
    """当前预算使用状态快照。"""

    tokens_used: int = 0
    usd_used: float = 0.0
    tokens_budget: int = 0
    usd_budget: float = 0.0
    # 状态：'ok' / 'warn' / 'exceeded'
    status: str = 'ok'
    # 告警文本（仅 warn/exceeded 时非空）
    message: str = ''

    def to_dict(self):
        return {
            'tokens_used': self.tokens_used,
            'usd_used': round(self.usd_used, 4),
            'tokens_budget': self.tokens_budget,
            'usd_budget': round(self.usd_budget, 4),
            'status': self.status,
            'message': self.message,
        }


class BudgetGuard(object):
    """会话级预算累计器。

    典型用法：

        guard = BudgetGuard(tokens_budget=100000, usd_budget=1.0,
                            price_in=0.5, price_out=1.5)
        for _ in range(loops):
            usage = llm.chat(...)
            snap = guard.accumulate(usage)
            if snap.status == 'exceeded':
                break
            elif snap.status == 'warn':
                notify_user(snap.message)
    """

    def __init__(self, tokens_budget=0, usd_budget=0.0,
                 price_in=0.0, price_out=0.0):
        # type: (int, float, float, float) -> None
        self._tok_budget = max(0, int(tokens_budget or 0))
        self._usd_budget = max(0.0, float(usd_budget or 0.0))
        self._price_in = max(0.0, float(price_in or 0.0))
        self._price_out = max(0.0, float(price_out or 0.0))
        self._tok_used = 0
        self._usd_used = 0.0
        # 记录已发出过的告警等级，避免重复告警
        self._warned_tokens = False
        self._warned_usd = False

    # ------------------------------------------------------------------ #
    # 累计
    # ------------------------------------------------------------------ #

    def accumulate(self, usage):
        # type: (Dict) -> BudgetSnapshot
        """把一次 LLM 调用的 usage dict 计入累计。

        usage 兼容 OpenAI 风格：{prompt_tokens, completion_tokens, total_tokens}
        """
        usage = usage or {}
        pt = int(usage.get('prompt_tokens') or 0)
        ct = int(usage.get('completion_tokens') or 0)
        # 有些 provider 只给 total_tokens；分不清 in/out 时按 6:4 估算
        total = int(usage.get('total_tokens') or (pt + ct))
        if pt == 0 and ct == 0 and total > 0:
            pt = int(total * 0.6)
            ct = total - pt

        self._tok_used += (pt + ct)
        # USD = tokens/1M * price
        self._usd_used += (
            (pt / 1_000_000.0) * self._price_in
            + (ct / 1_000_000.0) * self._price_out
        )
        return self.check_budget()

    # ------------------------------------------------------------------ #
    # 判定
    # ------------------------------------------------------------------ #

    def check_budget(self):
        # type: () -> BudgetSnapshot
        """判定当前预算状态。"""
        snap = BudgetSnapshot(
            tokens_used=self._tok_used,
            usd_used=self._usd_used,
            tokens_budget=self._tok_budget,
            usd_budget=self._usd_budget,
            status='ok',
            message='',
        )

        # 计算触发状态：token 与 usd 取更严格者
        status_priority = {'ok': 0, 'warn': 1, 'exceeded': 2}

        def _bump(new_status, msg):
            if status_priority[new_status] > status_priority[snap.status]:
                snap.status = new_status
                snap.message = msg

        if self._tok_budget > 0:
            ratio = self._tok_used / float(self._tok_budget)
            if ratio >= _HARD_PCT:
                _bump('exceeded',
                      '⛔ Token 预算已用完（{}/{}），已阻止新的 LLM 调用。'
                      .format(self._tok_used, self._tok_budget))
            elif ratio >= _WARN_PCT and not self._warned_tokens:
                self._warned_tokens = True
                _bump('warn',
                      '⚠️ Token 预算已用 {:.0%}（{}/{}），请尽快收尾。'
                      .format(ratio, self._tok_used, self._tok_budget))

        if self._usd_budget > 0:
            ratio = self._usd_used / float(self._usd_budget)
            if ratio >= _HARD_PCT:
                _bump('exceeded',
                      '⛔ USD 预算已用完（${:.4f}/${:.4f}），已阻止新的 LLM 调用。'
                      .format(self._usd_used, self._usd_budget))
            elif ratio >= _WARN_PCT and not self._warned_usd:
                self._warned_usd = True
                _bump('warn',
                      '⚠️ USD 预算已用 {:.0%}（${:.4f}/${:.4f}），请尽快收尾。'
                      .format(ratio, self._usd_used, self._usd_budget))

        return snap

    # ------------------------------------------------------------------ #
    # 只读访问
    # ------------------------------------------------------------------ #

    @property
    def tokens_used(self):
        return self._tok_used

    @property
    def usd_used(self):
        return self._usd_used

    def is_exceeded(self):
        # type: () -> bool
        return self.check_budget().status == 'exceeded'

    def reset(self):
        """新会话开始时清零。"""
        self._tok_used = 0
        self._usd_used = 0.0
        self._warned_tokens = False
        self._warned_usd = False
