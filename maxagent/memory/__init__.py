#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双层记忆系统对外入口。

架构（复刻自 Knot Agent 的记忆系统）
=====================================

**Layer 1 · 事件日志（events.py）**
- 原始对话按时间落盘为 JSONL，按天分片
- 提供 ``event_search`` 语义：keyword + 时间范围 + topk

**Layer 2 · 长期记忆（store.py + search.py）**
- ``INSTRUCTIONS.md``：用户显式要求长期保留的硬规则（自动注入）
- ``MEMORY.md``：用户画像 + AI 设定 + topic 索引（自动注入）
- ``topic/<slug>.md``：单主题正文，按需 memory_read / memory_search

**注入通道（injector.py）**
- 每轮 LLM 调用前，通过 ``get_prompt_addon()`` 拼进 system prompt

**写入触发（writer.py）**
- 显式意图（"记住/以后/默认/总是..."）→ 写 INSTRUCTIONS.md
- 稳定结论 → LLM 通过 memory_write 工具主动落 topic

存储位置：``get_memory_root()``（默认 ``~/.maxagent/memory``）
"""

from __future__ import absolute_import
from __future__ import print_function

from .events import EventLogger
from .events import get_event_logger
from .events import search_events
from .injector import build_auto_memory_addon
from .injector import get_prompt_addon
from .store import LongTermMemoryStore
from .store import get_memory_root
from .store import get_memory_store
from .writer import detect_explicit_memory_intent
from .writer import write_instruction_from_user_message

__all__ = [
    'EventLogger',
    'get_event_logger',
    'search_events',
    'LongTermMemoryStore',
    'get_memory_store',
    'get_memory_root',
    'build_auto_memory_addon',
    'get_prompt_addon',
    'detect_explicit_memory_intent',
    'write_instruction_from_user_message',
]
