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

from ....dcc.runtime import current_dcc


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


__all__ = ['get_dcc_knowledge']
