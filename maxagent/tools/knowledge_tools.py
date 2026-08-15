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
    examples=[{"summary": "典型调用", "args": {"topic": 'value', "sub_key": 'value'}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
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
    examples=[{'summary': '列出知识库所有主题', 'args': {}}],
    notes=[
        '返回 Max 官方文档知识库中的主题列表。',
        '如需检索具体内容，请使用 search_max_docs。',
    ],
    returns_desc='dict {"count": 主题数量, "topics": [...]}',
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
    'search_max_docs',
    'search_knowledge',
    'list_knowledge_sources',
    'add_knowledge_source',
    'remove_knowledge_source',
]


# ================================================================== #
# BM25 全文检索工具（A 场景 + D 场景）
# ================================================================== #

def _format_hits(hits, max_chars_per_hit=500):
    """把 BM25 命中结果格式化成 LLM 友好的文本。"""
    if not hits:
        return {'found': False, 'hits': [], 'text': '（无命中）'}
    lines = []
    struct = []
    for i, h in enumerate(hits, 1):
        meta = h.get('meta') or {}
        heading = meta.get('heading_path') or ''
        src_name = meta.get('display_name') or meta.get('source_id') or ''
        raw_text = h.get('text') or ''
        text = raw_text[:max_chars_per_hit]
        if len(raw_text) > max_chars_per_hit:
            text += '\n...（后续内容已截断）'
        head = '【{i}】{src}{arrow}{heading}  (score={score:.2f})'.format(
            i=i,
            src=src_name,
            arrow=' > ' if heading else '',
            heading=heading,
            score=h.get('score', 0.0),
        )
        lines.append(head + '\n' + text)
        struct.append({
            'rank': i,
            'score': h.get('score', 0.0),
            'source': src_name,
            'heading_path': heading,
            'text': raw_text,
            'doc_id': h.get('doc_id'),
        })
    return {
        'found': True,
        'count': len(hits),
        'hits': struct,
        'text': '\n\n---\n\n'.join(lines),
    }


@tool(
    name='search_max_docs',
    description=(
        '全文检索本地打包的 3ds Max Python Help 文档（BM25 引擎，离线）。\n'
        '何时调用（强烈推荐）：\n'
        '  ✓ 你要写 MAXScript / pymxs 但不确定 API 精确签名（如 "Noise 修改器类名到底是啥"、'
        '"targetSpot 有哪些参数"）\n'
        '  ✓ 用户描述的 Max 概念你不熟悉，需要查官方文档做背书\n'
        '  ✓ 出现"未知修改器类型"、"没有这个属性"等错误后，查一下正确写法\n'
        '不要调用：\n'
        '  ✗ 用户问的是通用 Python 编程问题\n'
        '  ✗ 已在 lookup_max_knowledge 的 L2 词条里能找到的常见参数\n'
        '返回文本已格式化，可直接引用；每条含 source / heading_path 便于溯源。'
    ),
    category='knowledge',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[{"summary": "典型调用", "args": {"query": 'Box.position', "topk": 5}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def search_max_docs(query, topk=3):
    # type: (str, int) -> Dict[str, Any]
    """检索 Max 官方文档（A 场景）。

    :param query: 查询关键词，中英文均可（英文命中率更高）
    :param topk: 返回条数，默认 3
    """
    q = (query or '').strip()
    if not q:
        return {'found': False, 'error': 'query 不能为空', 'text': ''}
    try:
        n = max(1, min(10, int(topk)))
    except (TypeError, ValueError):
        n = 3
    try:
        from ..knowledge import get_maxhelp_index  # pylint: disable=import-outside-toplevel
        idx = get_maxhelp_index()
        hits = idx.search(q, topk=n)
        result = _format_hits(hits)
        logger.info(
            'search_max_docs query=%r topk=%d found=%d',
            q, n, result.get('count', 0),
        )
        return result
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('search_max_docs 失败: %s', exc)
        return {'found': False, 'error': str(exc), 'text': ''}


@tool(
    name='search_knowledge',
    description=(
        '全文检索用户导入的知识库（md / txt 文档，BM25 引擎，离线）。\n'
        '这里的内容是用户主动导入的教程、SOP、参考资料——问题涉及'
        '"用户团队的规范"、"某个具体项目的说明"、"用户之前记录的经验"时'
        '优先调用本工具，而不是 search_max_docs（那是 Autodesk 官方内容）。\n'
        '如果用户库为空则直接返回 found=False，请改用其它工具。'
    ),
    category='knowledge',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[{"summary": "典型调用", "args": {"query": 'Box.position', "topk": 5}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def search_knowledge(query, topk=3):
    # type: (str, int) -> Dict[str, Any]
    """检索用户扩展知识库（D 场景）。"""
    q = (query or '').strip()
    if not q:
        return {'found': False, 'error': 'query 不能为空', 'text': ''}
    try:
        n = max(1, min(10, int(topk)))
    except (TypeError, ValueError):
        n = 3
    try:
        from ..knowledge import get_user_index  # pylint: disable=import-outside-toplevel
        idx = get_user_index()
        if not idx.list_sources():
            return {
                'found': False,
                'error': '用户知识库为空，未导入任何 md/txt 文档',
                'text': '',
            }
        hits = idx.search(q, topk=n)
        return _format_hits(hits)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('search_knowledge 失败: %s', exc)
        return {'found': False, 'error': str(exc), 'text': ''}


@tool(
    name='list_knowledge_sources',
    description=(
        '列出用户知识库中已导入的所有数据源（文件 / 目录）。'
        '想知道"我导入过啥"时调用；也可用来告诉用户当前库空不空。'
    ),
    category='knowledge',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[{'summary': '列出知识库数据源', 'args': {}}],
    notes=[
        '返回当前已配置的知识源（文件/代码库）列表。',
        '可通过 add_knowledge_source 新增知识源。',
    ],
    returns_desc='dict {"count": 数量, "sources": [...]}',
)
def list_knowledge_sources():
    # type: () -> Dict[str, Any]
    """列出用户库数据源。"""
    try:
        from ..knowledge import get_user_index  # pylint: disable=import-outside-toplevel
        idx = get_user_index()
        return {
            'count': len(idx.list_sources()),
            'sources': idx.list_sources(),
            'stats': idx.stats(),
        }
    except Exception as exc:  # pylint: disable=broad-except
        return {'count': 0, 'error': str(exc)}


@tool(
    name='add_knowledge_source',
    description=(
        '向用户知识库导入一个 md 或 txt 文档（或包含多篇文档的目录）。\n'
        '导入后：\n'
        '  ✓ 文件/目录会被复制到 MaxAgent 配置目录的副本区，原文件可安全删除\n'
        '  ✓ 自动建立 BM25 索引，立刻可用 search_knowledge 检索\n'
        '  ✓ 重启 Max 后索引会持久保留\n'
        '何时调用：\n'
        '  ✓ 用户说"把这个文档加进知识库"、"导入这篇 SOP/教程/规范"\n'
        '  ✓ 用户想批量导入某个目录下的所有 md/txt\n'
        '参数：\n'
        '  - path: 文件或目录的绝对路径\n'
        '  - kind: "auto"（自动判断文件/目录，默认） / "file" / "dir"\n'
        '  - display_name: 可选，展示用名称\n'
        '  - tags: 可选，字符串列表，用于给文档打标签\n'
        '返回包含 source_id，后续可用 remove_knowledge_source 删除。'
    ),
    category='knowledge',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[{"summary": "典型调用", "args": {"path": 'value', "kind": 'auto', "display_name": 'value', "tags": 'value'}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def add_knowledge_source(path, kind='auto', display_name=None, tags=None):
    # type: (str, str, Optional[str], Optional[List[str]]) -> Dict[str, Any]
    """导入用户知识库数据源。"""
    if not path or not str(path).strip():
        return {'ok': False, 'error': 'path 不能为空'}
    if tags is None:
        tags = []
    try:
        from ..knowledge import add_user_source  # pylint: disable=import-outside-toplevel
        result = add_user_source(
            str(path).strip(),
            kind=kind or 'auto',
            display_name=display_name,
            tags=list(tags) if tags else [],
        )
        logger.info(
            'add_knowledge_source path=%r kind=%r ok=%s',
            path, kind, result.get('ok'),
        )
        return result
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('add_knowledge_source 失败: %s', exc)
        return {'ok': False, 'error': str(exc)}


@tool(
    name='remove_knowledge_source',
    description=(
        '从用户知识库中删除一个已导入的数据源。\n'
        '会同时删除配置目录中的副本和索引，不可恢复。\n'
        'source_id 可通过 list_knowledge_sources 查询。'
    ),
    category='knowledge',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[{"summary": "典型调用", "args": {"source_id": 'value'}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def remove_knowledge_source(source_id):
    # type: (str) -> Dict[str, Any]
    """删除用户知识库数据源。"""
    if not source_id:
        return {'ok': False, 'error': 'source_id 不能为空'}
    try:
        from ..knowledge import remove_user_source  # pylint: disable=import-outside-toplevel
        result = remove_user_source(str(source_id))
        logger.info(
            'remove_knowledge_source source_id=%r ok=%s',
            source_id, result.get('ok'),
        )
        return result
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('remove_knowledge_source 失败: %s', exc)
        return {'ok': False, 'error': str(exc)}