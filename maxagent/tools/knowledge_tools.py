#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3ds Max 领域知识查询工具。

提供给 LLM 调用的工具：
- ``lookup_max_knowledge``：按主题查询 Max 详细知识（L2 知识库）
- ``list_max_knowledge_topics``：列出所有可查主题

设计理由（为什么不全塞 system prompt）：
- L2 知识库容量约 4~6KB，每轮对话都塞会消耗 ~1500 token，长期烧钱
- LLM 真正"不确定参数细节"的场景占比不高，按需查更划算
- 与 L1（必塞）形成互补：L1 教世界观，L2 答细节
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import Optional

from ..agent.max_knowledge import list_topics
from ..agent.max_knowledge import lookup_topic
from ..logger import get_logger
from .registry import tool


logger = get_logger(__name__)


@tool(
    name='lookup_max_knowledge',
    description=(
        '查询 3ds Max 内置领域知识：常用 primitive 参数、修改器列表、'
        '灯光/相机类型、材质赋值、单位/坐标系、pivot 操作等。\n'
        '何时调用（强烈推荐）：\n'
        '  ✓ 用户要求创建/修改 Max 对象，但你不确定该对象的具体参数名'
        '（如"box 的高度沿哪个轴？"、"修改器栈第几个是顶？"）\n'
        '  ✓ 涉及第三方渲染器（V-Ray / Corona / Arnold）参数差异\n'
        '  ✓ 你想用某个 API 但只是"印象中应该这样写"——查一下避免幻觉\n'
        '不要调用：\n'
        '  ✗ 用户问的是通用编程问题（与 Max 无关）\n'
        '  ✗ 已经在 system prompt 的"世界观速查"里能找到答案的内容\n'
        '参数：\n'
        '  - topic: 主题名，可选 primitive / light / camera / modifier /'
        ' material / units / render / pivot\n'
        '  - sub_key: 可选子键，如 topic="primitive", sub_key="box"'
        ' 精确返回 Box 参数；不传则返回主题下所有条目。\n'
        '返回：found=True 时含 content/items；found=False 时含 suggestion'
        ' 和 available_topics，便于你换个查法。'
    ),
    category='knowledge',
    dangerous=False,
    wrap_undo=False,
    # 纯字典查询，不需要主线程 pymxs
    run_on_main_thread=False,
)
def lookup_max_knowledge(topic, sub_key=None):
    # type: (str, Optional[str]) -> Dict[str, Any]
    """按主题查询 Max 知识库。

    :param topic: 主题 slug，如 'primitive' / 'modifier'
    :param sub_key: 可选子键，如 'box' / 'bend'
    :returns: 知识条目字典（结构见 max_knowledge.lookup_topic）
    """
    result = lookup_topic(topic, sub_key=sub_key)
    if result.get('found'):
        logger.info(
            'lookup_max_knowledge hit: topic=%s sub_key=%s',
            topic, sub_key,
        )
    else:
        logger.info(
            'lookup_max_knowledge miss: topic=%s sub_key=%s',
            topic, sub_key,
        )
    return result


@tool(
    name='list_max_knowledge_topics',
    description=(
        '列出当前知识库支持查询的所有主题（slug）。'
        '在你不确定该用哪个 topic 关键词时先调用这个，再调 '
        'lookup_max_knowledge。'
    ),
    category='knowledge',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
)
def list_max_knowledge_topics():
    # type: () -> Dict[str, Any]
    """返回所有可查的知识主题。"""
    topics = list_topics()
    return {
        'count': len(topics),
        'topics': topics,
    }


__all__ = [
    'lookup_max_knowledge',
    'list_max_knowledge_topics',
]
