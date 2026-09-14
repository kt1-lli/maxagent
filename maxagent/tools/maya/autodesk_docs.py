#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Autodesk 官方 Knowledge MCP 工具（限定 Maya 作用域）。

注册一个 LLM 可调用的工具：

- ``autodesk_maya_docs``：把用户的问题转发给 Autodesk 官方 Knowledge MCP
  （``https://developer.api.autodesk.com/knowledge/public/v1/mcp``），只查
  Maya 相关文档并返回结果文本。

与 3ds Max 侧 ``autodesk_max_docs`` 的关系
==========================================
共用 ``maxagent/autodesk_mcp.py`` 客户端，通过 ``PRODUCT_SCOPES`` 区分
产品作用域（Maya → product_code=MAYA，3ds Max → 3DSMAX，均取自官方
get_available_products 目录）。本文件只做 Maya 侧的注册与文案，不重复
实现传输协议。

运行线程
========
纯 HTTP 请求，不涉及 maya.cmds，因此 ``run_on_main_thread=False`` 放
子线程执行。
"""

from __future__ import absolute_import
from __future__ import print_function

from ...autodesk_mcp import DEFAULT_LOCALE
from ...autodesk_mcp import search_dcc_knowledge
from ...logger import get_logger
from ...tools.registry import tool


logger = get_logger(__name__)


@tool(
    dcc=['maya'],
    name='autodesk_maya_docs',
    description=(
        'Autodesk 官方 Maya 知识库检索：当你需要 Maya 的权威、最新、'
        '一手文档（MEL / Maya Python (cmds) / 节点说明 / SDK / 版本行为差异等）'
        '时优先调用此工具，而不是通用联网搜索。'
        '\n\n'
        '本工具连接 Autodesk 官方 Knowledge MCP 端点，作用域已强制限定为 Maya，'
        '不会返回 3ds Max / Revit 等其它产品线的内容。'
        '\n\n'
        '调用时把用户的问题浓缩为 1~2 句关键词组合（英文命中率更高，中文也可）。'
        '不要把整段无关背景塞进 query。示例：'
        '"polyCube node" / "skinCluster weights" / "MEL ls command flags"。'
        '\n\n'
        '参数：\n'
        '- query: 关键词，越具体命中率越高。\n'
        '- locale: Autodesk 帮助中心语言码。默认 "ENU"（英文，覆盖最全）；'
        '需要中文可传 "CHS"，日文 "JPN"，其它常见值：CHT/JPN/KOR/DEU/FRA/ESP/ITA/PTB/RUS。\n'
        '- limit: 返回条数（可选）。远端单次响应上限约 16KB，超过会被截断；'
        '若首次结果被截断，重新调用时把 query 收窄或把 limit 调小到 3~5。'
    ),
    category='web',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[
        {
            'summary': '查询 polyCube 节点的官方文档',
            'args': {
                'query': 'polyCube node',
            },
        },
        {
            'summary': '用中文 locale 查询 skinCluster 用法',
            'args': {
                'query': 'skinCluster weights',
                'locale': 'CHS',
                'limit': 3,
            },
        },
    ],
    notes=[
        'query 越具体命中率越高，建议用英文关键词（如 "polyCube node"）。',
        '远端单次响应上限约 16KB，若结果明显被截断，请收窄 query 或减少 limit。',
        'locale 默认 ENU 覆盖最全；需要中文帮助时可传 CHS，但部分文档可能仍为英文。',
    ],
    returns_desc=(
        'dict {"ok": bool, "text": str, "tool": str, "query": str, '
        '"scope": "Maya", "locale": str, "limit": int?, "raw": Any, '
        '"error": str?}'
    ),
)
def autodesk_maya_docs(query, locale=DEFAULT_LOCALE, limit=None, timeout=15.0):
    """检索 Autodesk 官方 Maya 文档。

    :param query: 检索关键词（自然语言，会被自动加上 "Maya:" 前缀）
    :param locale: Autodesk locale 码，默认 ENU（英文）；可传 CHS/JPN/DEU/FRA/... 等
    :param limit: 期望返回条数（None=不限，服务端上限约 16KB）
    :param timeout: HTTP 超时秒数（默认 15）
    """
    q = (query or '').strip()
    if not q:
        return {'ok': False, 'error': 'query 不能为空', 'text': ''}
    try:
        t = float(timeout) if timeout is not None else 15.0
    except (TypeError, ValueError):
        t = 15.0
    t = max(3.0, min(60.0, t))
    loc = (str(locale).strip() if locale else '') or DEFAULT_LOCALE
    n = None
    if limit is not None:
        try:
            n = int(limit)
            if n <= 0:
                n = None
        except (TypeError, ValueError):
            n = None
    result = search_dcc_knowledge(q, dcc='maya', timeout=t, locale=loc, limit=n)
    if not result.get('ok'):
        logger.debug('autodesk_maya_docs 调用失败: %s', result.get('error'))
    return result


__all__ = ['autodesk_maya_docs']
