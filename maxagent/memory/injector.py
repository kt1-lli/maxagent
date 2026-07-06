#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每轮 LLM 调用前的自动注入：<instructions> + <auto-memory>。

注入格式（对齐 Knot Agent）
==========================
::

    <memory-system>
    ...记忆使用规则...
    <long-term-memory>
    <instructions>
    (INSTRUCTIONS.md 全文)
    </instructions>
    <auto-memory>
    (MEMORY.md 全文)
    </auto-memory>
    </long-term-memory>
    </memory-system>

由 ``AgentWorker.set_system_prompt_addon_provider`` 挂进 sys addon 链。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Optional

from ..logger import get_logger
from .store import LongTermMemoryStore
from .store import get_memory_store

logger = get_logger(__name__)

# 每次注入的最大字符数上限（避免长期记忆污染 system prompt token 预算）
_MAX_INSTRUCTIONS_CHARS = 6000
_MAX_INDEX_CHARS = 4000

_HEADER = (
    '<memory-system>\n'
    '为了提升协作深度，你已接入一套持久化记忆系统。\n'
    '- <instructions> 内是用户显式要求长期保留的硬规则，必须遵守。\n'
    '- <auto-memory> 内是用户画像与主题指针，仅作背景参考。\n'
    '- 需要更多主题细节时使用 memory_read/memory_search 工具；\n'
    '- 追溯具体行为/时间点/原始对话请使用 event_search 工具。\n'
    '- 记忆是"某时刻为真"的快照，与当前上下文冲突时以当前为准。\n'
    '<long-term-memory>\n'
)

_FOOTER = (
    '</long-term-memory>\n'
    '</memory-system>\n'
)


def _clip(text, limit):
    # type: (str, int) -> str
    if not text:
        return ''
    if len(text) <= limit:
        return text
    return text[:limit] + '\n... [记忆过长已截断，使用 memory_read 读取完整内容]'


def build_auto_memory_addon(store=None):
    # type: (Optional[LongTermMemoryStore]) -> str
    """构建一次注入文本；无内容时返回空串。"""
    st = store or get_memory_store()
    try:
        instructions = _clip(st.read_instructions(), _MAX_INSTRUCTIONS_CHARS)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('读取 INSTRUCTIONS.md 失败: %s', exc)
        instructions = ''
    try:
        index = _clip(st.read_index(), _MAX_INDEX_CHARS)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('读取 MEMORY.md 失败: %s', exc)
        index = ''
    if not instructions and not index:
        return ''
    parts = [_HEADER]
    if instructions:
        parts.append('<instructions>\n')
        parts.append(instructions.rstrip('\n') + '\n')
        parts.append('</instructions>\n')
    if index:
        parts.append('<auto-memory>\n')
        parts.append(index.rstrip('\n') + '\n')
        parts.append('</auto-memory>\n')
    parts.append(_FOOTER)
    return ''.join(parts)


def get_prompt_addon(user_input=''):
    # type: (str) -> str
    """兼容 ``sys_addon_provider(user_input)`` 签名的适配器。"""
    _ = user_input  # 目前不做基于输入的按需过滤，保留占位
    return build_auto_memory_addon()


__all__ = ['build_auto_memory_addon', 'get_prompt_addon']
