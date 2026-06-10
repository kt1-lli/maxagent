#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智能上下文压缩（Summarization Checkpoint）。

职责：
1. 在对话 token 数接近模型上限时，用 LLM 生成**结构化摘要**
   替代早期消息，保留核心上下文而不简单截断。
2. 支持**自动触发**和**手动触发**两种模式。
3. 摘要内容不只是文本，还包含关键事实（facts）、
   工具调用链摘要（tool_chain_digest）等结构化信息，
   提高 LLM 恢复对话的效率。

与现有机制的关系：
- ``Conversation.trim_to_token_budget()`` — 暴力裁剪，丢弃早期消息。
- SummarizationCheckpoint — 智能压缩，把早期消息"蒸馏"为摘要后保留。

实现策略：
- 压缩时保留最近 N 轮完整消息（默认 4 轮），只压缩更早的历史。
- 压缩后的摘要作为一条 system-role 或 user-role 的特殊消息
  插入到保留的消息之前。
- 摘要生成走**独立 LLM 调用**（不干扰当前对话线程），
  使用一个轻量级 prompt。
"""

from __future__ import absolute_import
from __future__ import print_function

import time
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from .logger import get_logger
from .model_capabilities import infer_context_window as estimate_context_window

logger = get_logger(__name__)


# ---------------------------------------------------------------------- #
# 数据模型
# ---------------------------------------------------------------------- #

@dataclass
class SummarizationCheckpoint(object):
    """摘要检查点，替代早期消息保留核心上下文。"""

    timestamp: float = field(default_factory=time.time)
    summary_text: str = ''
    key_facts: Dict[str, Any] = field(default_factory=dict)
    tool_chain_digest: List[str] = field(default_factory=list)
    original_message_count: int = 0
    original_token_estimate: int = 0
    compressed_token_estimate: int = 0

    def to_prompt_text(self):
        # type: () -> str
        """转为 LLM prompt 可用的文本格式。"""
        parts = ['【历史摘要】']
        parts.append(self.summary_text)
        if self.key_facts:
            parts.append('\n【关键事实】')
            for k, v in self.key_facts.items():
                parts.append('- {}: {}'.format(k, v))
        if self.tool_chain_digest:
            parts.append('\n【已执行操作】共 {} 步:'.format(
                len(self.tool_chain_digest),
            ))
            for step in self.tool_chain_digest:
                parts.append('  {}'.format(step))
        return '\n'.join(parts)

    def estimate_tokens(self):
        # type: () -> int
        """估算本摘要在 prompt 中的 token 数（粗略）。"""
        text = self.to_prompt_text()
        return len(text.encode('utf-8')) // 3  # 粗略 3bytes/token


# ---------------------------------------------------------------------- #
# 压缩器
# ---------------------------------------------------------------------- #

class ContextCompressor(object):
    """上下文压缩器。

    用法：
        compressor = ContextCompressor(llm_client)
        checkpoint = compressor.compress(
            messages, keep_recent=4,
        )
        # 然后把 checkpoint 替换 messages[:N-4] 即可
    """

    def __init__(self, llm_client, max_summary_tokens=512):
        self._llm = llm_client
        self._max_summary_tokens = max_summary_tokens

    def compress(self, all_messages, keep_recent=4):
        # type: (List[Dict[str, Any]], int) -> "tuple[SummarizationCheckpoint, int]"
        """对消息历史进行压缩。

        :param all_messages: Conversation 中的完整消息列表
        :param keep_recent: 保留最近的 N 条完整消息不压缩
        :returns: (checkpoint, removed_count)
            checkpoint — 生成的摘要对象
            removed_count — 被替换掉的消息数量
        """
        if len(all_messages) <= keep_recent + 1:
            return SummarizationCheckpoint(), 0

        # 1. 分离"待压缩历史"和"保留消息"
        to_compress = all_messages[:-keep_recent]
        original_count = len(to_compress)

        # 2. 构建压缩用 prompt（轻量级）
        prompt_text = self._build_compress_prompt(to_compress)

        # 3. 调用 LLM 生成摘要（独立调用，不干扰对话线程）
        t0 = time.time()
        try:
            summary_raw = self._call_llm_summarize(prompt_text)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                '摘要生成失败: %s，回退到简单压缩', exc,
            )
            summary_raw = self._fallback_summary(to_compress)
        logger.info(
            '摘要生成耗时 %.1fs，压缩了 %d 条消息',
            time.time() - t0, original_count,
        )

        # 4. 解析摘要，提取结构化信息
        checkpoint = self._parse_summary(summary_raw, original_count)

        return checkpoint, original_count

    def _build_compress_prompt(self, messages):
        # type: (List[Dict[str, Any]]) -> List[Dict[str, str]]
        """构建要求 LLM 生成摘要的 prompt。"""
        system_msg = (
            '你是对话摘要助手。请将以下对话历史压缩为结构化的摘要，'
            '保留所有关键事实、用户意图、已创建/修改的对象名称及其属性、'
            '以及已执行的操作链路。输出格式：\n'
            '1. 一段简要摘要文字\n'
            '2. 【关键事实】key: value 列表\n'
            '3. 【已执行操作】步骤列表\n'
            '尽量精简，但不要丢失对后续推理有用的信息。'
        )
        # 把消息转为文本摘要（只保留 role + content 截取）
        history_text = []
        for msg in messages:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            # 截断过长内容
            if len(content) > 500:
                content = content[:500] + '...(truncated)'
            history_text.append('[{}] {}'.format(role, content))
            # 如果有 tool_calls，也记录下
            tool_calls = msg.get('tool_calls')
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    fn = tc.get('function', {})
                    history_text.append(
                        '  -> tool: {}({})'.format(
                            fn.get('name', '?'),
                            fn.get('arguments', ''),
                        ),
                    )

        user_text = (
            '请压缩以下 {} 条对话消息：\n\n{}\n\n'
            '请按要求的格式输出摘要。'
        ).format(len(messages), '\n'.join(history_text))

        return [
            {'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': user_text},
        ]

    def _call_llm_summarize(self, prompt_messages):
        # type: (List[Dict[str, str]]) -> str
        """调用 LLM 生成摘要（轻量级参数）。"""
        if self._llm is None:
            raise RuntimeError('未提供 LLM 客户端')

        # 使用轻量级参数：短 max_tokens、正 temperature
        # 注意：这里不流式、不启用 tools，纯文本生成
        response = self._llm.chat(
            messages=prompt_messages,
            max_tokens=self._max_summary_tokens,
            temperature=0.3,  # 允许适度创造性，但不要太发散
            stream=False,
            tools_schema=None,
        )
        # 兼容不同返回格式
        if isinstance(response, dict):
            return response.get('content', '') or response.get('text', '')
        if isinstance(response, str):
            return response
        return str(response)

    def _parse_summary(self, raw_text, original_count):
        # type: (str, int) -> SummarizationCheckpoint
        """从 LLM 返回的文本中提取结构化信息。"""
        cp = SummarizationCheckpoint(
            summary_text=raw_text,
            original_message_count=original_count,
        )

        lines = raw_text.split('\n')
        section = 'summary'
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('【关键事实】'):
                section = 'facts'
                continue
            if stripped.startswith('【已执行操作】'):
                section = 'tools'
                continue

            if section == 'facts' and stripped.startswith('- '):
                kv = stripped[2:].split(':', 1)
                if len(kv) == 2:
                    cp.key_facts[kv[0].strip()] = kv[1].strip()

            if section == 'tools' and stripped.startswith(('-> ', '- ', '  ')):
                step = stripped.lstrip(' -<>').strip()
                if step:
                    cp.tool_chain_digest.append(step)

        cp.compressed_token_estimate = cp.estimate_tokens()
        return cp

    def _fallback_summary(self, messages):
        # type: (List[Dict[str, Any]]) -> str
        """LLM 调用失败时的回退策略：简单拼接。"""
        parts = []
        for msg in messages:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            if len(content) > 200:
                content = content[:200] + '...'
            parts.append('[{}] {}'.format(role, content))
        return '\n'.join(parts)


# ---------------------------------------------------------------------- #
# 触发器
# ---------------------------------------------------------------------- #

class AutoCompressTrigger(object):
    """基于 token 预算的自动压缩触发器。"""

    def __init__(self, budget, threshold_ratio=0.85):
        """
        :param budget: 最大 token 预算（从 model_capabilities 获取）
        :param threshold_ratio: 当用量达到预算的多少时触发压缩
        """
        self._budget = budget
        self._threshold = int(budget * threshold_ratio)
        self._last_trigger = 0

    def should_compress(self, current_tokens, last_compress_time=0):
        # type: (int, float) -> bool
        """判断是否应当触发压缩。

        条件：
        1. 当前用量 >= 阈值
        2. 距离上次压缩至少间隔 60 秒（避免连续触发）
        """
        if current_tokens < self._threshold:
            return False
        if time.time() - last_compress_time < 60:
            return False
        return True

    def next_threshold(self):
        """返回下次应当检查的 token 阈值。"""
        return self._threshold


def recommend_auto_compress(
    all_messages,              # type: List[Dict[str, Any]]
    model_id='',               # type: str
    base_url='',               # type: str
    user_threshold=0,          # type: int
):
    # type: (...) -> "tuple[bool, int, int]"
    """基于当前消息列表判断是否应当自动压缩。

    :returns: (should_compress, current_tokens, threshold)
    """
    # 当前 token 估算
    current = sum(
        len(m.get('content', '').encode('utf-8')) for m in all_messages
    ) // 3

    # 估算模型上下文窗口
    ctx_window = estimate_context_window(model_id, base_url)
    if ctx_window <= 0:
        ctx_window = 8192  # 兜底

    # 预算 = 窗口 - 系统 prompt 预留 - 输出预留
    budget = ctx_window - 1000 - 2000  # 预留 1K system + 2K output
    if budget <= 0:
        budget = ctx_window // 2

    # 用户配置阈值优先
    if user_threshold > 0:
        threshold = user_threshold
    else:
        threshold = int(budget * 0.85)

    return (current >= threshold, current, threshold)
