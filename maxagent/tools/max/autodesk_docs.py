#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Autodesk 官方 Knowledge MCP 工具（限定 3ds Max 作用域）。

注册一个 LLM 可调用的工具：

- ``autodesk_max_docs``：把用户的问题转发给 Autodesk 官方 Knowledge MCP
  （``https://developer.api.autodesk.com/knowledge/public/v1/mcp``），只查
  3ds Max 相关文档并返回结果文本。

为什么单独暴露给 LLM 而不是内联到 web_search？
==============================================
- Autodesk Knowledge 是权威一手来源，回答 "3ds Max 里 xxx 参数怎么用 /
  某接口/宏怎么写" 这类问题时应优先调用；
- 通用联网搜索经常返回论坛、二手翻译，噪声大且未必是最新版本；
- 独立工具让 LLM 在 tool 选择阶段就能看到"这是 Autodesk 官方"，
  从而在需要准确答案时优先命中它。

运行线程
========
纯 HTTP 请求，不涉及 pymxs，因此 ``run_on_main_thread=False`` 放子线程执行。
"""

from __future__ import absolute_import
from __future__ import print_function

from ...autodesk_mcp import DEFAULT_LOCALE
from ...autodesk_mcp import search_max_knowledge
from ...logger import get_logger
from ...tools.registry import tool


logger = get_logger(__name__)


@tool(
    dcc=['3dsmax'],
    name='autodesk_max_docs',
    description=(
        'Autodesk 官方 3ds Max 知识库检索：当你需要 3ds Max 的权威、最新、'
        '一手文档（MAXScript / pymxs / 参数说明 / SDK / 版本行为差异等）时'
        '优先调用此工具，而不是通用联网搜索。'
        '\n\n'
        '本工具连接 Autodesk 官方 Knowledge MCP 端点，作用域已强制限定为 3ds Max，'
        '不会返回 Maya / Revit 等其它产品线的内容。'
        '\n\n'
        '调用时把用户的问题浓缩为 1~2 句关键词组合（英文命中率更高，中文也可）。'
        '不要把整段无关背景塞进 query。示例：'
        '"Biped_Object class" / "Biped Vertical_Horizontal_Turn"。'
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
            'summary': '查询 Biped 相关类的官方文档',
            'args': {
                'query': 'Biped_Object class',
            },
        },
        {
            'summary': '用中文 locale 查询 MAXScript 数组用法',
            'args': {
                'query': 'MAXScript array',
                'locale': 'CHS',
                'limit': 3,
            },
        },
    ],
    notes=[
        'query 越具体命中率越高，建议用英文关键词（如 "Biped_Object class"）。',
        '远端单次响应上限约 16KB，若结果明显被截断，请收窄 query 或减少 limit。',
        'locale 默认 ENU 覆盖最全；需要中文帮助时可传 CHS，但部分文档可能仍为英文。',
    ],
    returns_desc=(
        'dict {"ok": bool, "text": str, "tool": str, "query": str, '
        '"scope": "3ds Max", "locale": str, "limit": int?, "raw": Any, '
        '"error": str?}'
    ),
)
def autodesk_max_docs(query, locale=DEFAULT_LOCALE, limit=None, timeout=15.0):
    """检索 Autodesk 官方 3ds Max 文档。

    :param query: 检索关键词（自然语言，会被自动加上 "3ds Max:" 前缀）
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
    result = search_max_knowledge(q, timeout=t, locale=loc, limit=n)
    if not result.get('ok'):
        logger.debug('autodesk_max_docs 调用失败: %s', result.get('error'))
    return result


__all__ = ['autodesk_max_docs']

