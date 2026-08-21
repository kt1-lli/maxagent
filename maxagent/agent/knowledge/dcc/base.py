#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DCC 通用知识库基础设施。

把原 ``agent.max_knowledge`` 中 L1/L2 的骨架提取到这里，
供 ``max.py`` / ``maya.py`` 复用，并实现与 DCC 无关的查询 API。
"""

from __future__ import absolute_import
from __future__ import print_function

from difflib import SequenceMatcher
from typing import Any
from typing import Dict
from typing import Optional


# 通用提示前缀：任何 DCC 都通用的高层级原则，防止 LLM 在未知 DCC 里胡写
COMMON_KNOWLEDGE = """\
==============================================================
🛡️ 通用 DCC 助手原则
==============================================================
- 你运行在某款数字内容创作软件（DCC）内部，通过调用工具直接操作场景。
- 所有改变场景状态的工具调用都应谨慎；对高风险操作保留修改前的概要信息。
- 优先使用当前 DCC 暴露的官方 Python API，不要假设跨 DCC API 同名同行为。
- 如果不确定某个 API 在当前 DCC 中的具体签名，先查询场景或先使用低风险探测。
"""


class DCCKnowledge:
    """单 DCC 知识库包装器。"""

    def __init__(self, dcc_name, basic_knowledge, topics):
        # type: (str, str, Dict[str, Any]) -> None
        self.dcc_name = dcc_name
        self.basic_knowledge = basic_knowledge
        self.topics = topics

    def get_basic_knowledge(self):
        # type: () -> str
        """返回 L1 必塞 system prompt 的基础常识文本。"""
        return self.basic_knowledge

    def list_topics(self):
        # type: () -> list[str]
        """返回 L2 知识库的所有可查主题名。"""
        return sorted(self.topics.keys())

    def lookup_topic(self, topic, sub_key=None):
        # type: (str, Optional[str]) -> Dict[str, Any]
        """按主题查询 L2 知识库。"""
        if not topic:
            return {
                'found': False,
                'error': 'topic 参数不能为空',
                'available_topics': self.list_topics(),
            }

        norm = topic.strip().lower()
        if norm not in self.topics:
            suggestion = self._find_closest_topic(norm)
            return {
                'found': False,
                'topic': topic,
                'available_topics': self.list_topics(),
                'suggestion': suggestion,
                'message': (
                    '未找到主题 "{}"（DCC: {}）。可用主题: {}{}'
                ).format(
                    topic, self.dcc_name,
                    ', '.join(self.list_topics()),
                    ('；最接近: ' + suggestion) if suggestion else '',
                ),
            }

        bucket = self.topics[norm]
        if sub_key is None:
            return {
                'found': True,
                'topic': norm,
                'summary': bucket.get('_summary', ''),
                'keys': sorted([k for k in bucket.keys() if k != '_summary']),
                'items': {k: v for k, v in bucket.items() if k != '_summary'},
            }

        sub_norm = sub_key.strip().lower()
        if sub_norm in bucket:
            return {
                'found': True,
                'topic': norm,
                'sub_key': sub_norm,
                'content': bucket[sub_norm],
            }

        return {
            'found': False,
            'topic': norm,
            'sub_key': sub_key,
            'available_keys': sorted(
                [k for k in bucket.keys() if k != '_summary'],
            ),
            'message': (
                '主题 "{}" 下没有子键 "{}"（DCC: {}）。可用子键: {}'
            ).format(
                norm, sub_key, self.dcc_name,
                ', '.join(k for k in bucket.keys() if k != '_summary'),
            ),
        }

    def _find_closest_topic(self, query):
        # type: (str) -> str
        """简单字符串相似度查找最接近的主题名。"""
        best = ''
        best_ratio = 0.0
        for t in self.topics:
            ratio = SequenceMatcher(None, query, t).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best = t
        return best if best_ratio >= 0.4 else ''
