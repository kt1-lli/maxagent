#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DCC 世界观知识库入口。

调用方只需要：
    from maxagent.agent.knowledge.dcc import get_dcc_knowledge
    knowledge = get_dcc_knowledge()
    knowledge.get_basic_knowledge()

即可获取当前 DCC 的 L1 世界观。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ....dcc.runtime import current_dcc


class _TopicMapAdapter:
    """按当前 DCC 转发到对应 DCCKnowledge 的 topic 操作。"""

    def __init__(self):
        self._cache = None  # type: Optional[Any]

    def _knowledge(self):
        if self._cache is None:
            self._cache = get_dcc_knowledge()
        return self._cache

    def list_topics(self):
        # type: () -> List[str]
        return self._knowledge().list_topics()

    def lookup_topic(self, topic, sub_key=None):
        # type: (str, Optional[str]) -> Dict[str, Any]
        return self._knowledge().lookup_topic(topic, sub_key=sub_key)


def get_dcc_knowledge():
    # type: () -> "DCCKnowledge"
    """返回当前运行 DCC 对应的知识库对象。"""
    dcc = current_dcc()
    if dcc == '3dsmax':
        from .max import MAX_KNOWLEDGE
        return MAX_KNOWLEDGE
    if dcc == 'maya':
        from .maya import MAYA_KNOWLEDGE
        return MAYA_KNOWLEDGE
    # 未知环境：返回通用原则，不带任何 DCC 细节
    from .base import COMMON_KNOWLEDGE
    from .base import DCCKnowledge
    return DCCKnowledge(
        dcc_name='unknown',
        basic_knowledge=COMMON_KNOWLEDGE,
        topics={},
    )


# 兼容旧接口：lookup_max_knowledge 现在自动按 DCC 分发
def list_topics():
    # type: () -> List[str]
    return get_dcc_knowledge().list_topics()


def lookup_topic(topic, sub_key=None):
    # type: (str, Optional[str]) -> Dict[str, Any]
    return get_dcc_knowledge().lookup_topic(topic, sub_key=sub_key)


__all__ = [
    'get_dcc_knowledge',
    'list_topics',
    'lookup_topic',
]
