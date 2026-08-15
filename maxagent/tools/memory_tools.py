#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长期记忆 & 事件日志的 4 个 LLM 工具。

复刻 Knot Agent 的记忆调用接口：

- ``memory_read``：读取指定记忆文件（INSTRUCTIONS.md / MEMORY.md / topic/*.md）
- ``memory_search``：跨所有 topic 正文的稳定结论检索
- ``memory_write``：新增/编辑/删除 记忆文件（用户显式意图触发）
- ``event_search``：搜原始事件日志（具体行为/时间点/原始对话）

这四个工具被标记为 ``category='memory'``，全部 ``run_on_main_thread=False``
（纯文件 I/O 与字符串处理，不涉及 pymxs），可以在子线程直接执行。
"""

from __future__ import absolute_import
from __future__ import print_function

import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..logger import get_logger
from ..memory.events import search_events
from ..memory.search import search_memory
from ..memory.store import get_memory_store
from .registry import tool


logger = get_logger(__name__)


# ---------------------------------------------------------------------- #
# memory_read
# ---------------------------------------------------------------------- #

@tool(
    name='memory_read',
    description=(
        '读取长期记忆文件的完整或部分内容。\n\n'
        '合法 file_path：\n'
        '- "INSTRUCTIONS.md"：用户显式硬规则\n'
        '- "MEMORY.md"：用户画像 + AI 设定 + topic 索引（默认）\n'
        '- "topic/<slug>.md"：具体主题正文（slug 需匹配 [a-z][a-z0-9-]*）\n\n'
        '当 <auto-memory> 只给出 topic 指针，而正文细节会影响回答时，'
        '用本工具打开对应 topic 读取全文。'
        '大文件可用 offset/limit 分段读取。'
    ),
    category='memory',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[{"summary": "典型调用", "args": {"file_path": 'C:/Work/scene.max', "offset": '[10, 0, 0]', "limit": 10}}],
notes=['坐标/旋转类参数优先使用 JSON 字符串 "[x,y,z]" 格式。', 'file_path 建议使用绝对路径，目录不存在会自动创建。'],
returns_desc="dict {\"ok\": True, ...}"
)
def memory_read(file_path='MEMORY.md', offset=None, limit=None):
    """读取记忆文件。"""
    fp = (file_path or 'MEMORY.md').strip()
    try:
        text = get_memory_store().read(fp, offset=offset, limit=limit)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc), 'file_path': fp}
    return {
        'ok': True,
        'file_path': fp,
        'length': len(text),
        'text': text,
    }


# ---------------------------------------------------------------------- #
# memory_search
# ---------------------------------------------------------------------- #

@tool(
    name='memory_search',
    description=(
        '在长期记忆里搜索稳定结论（用户偏好/习惯/项目背景/角色设定/'
        '专有名词等）。跨所有 topic + INSTRUCTIONS + MEMORY 检索。\n\n'
        '触发场景：\n'
        '- 用户提到某个专有名词/项目名/人名，且非通用常识；\n'
        '- 用户说"上次/之前/那个方案/我的项目"等指代不明；\n'
        '- 自动注入的 MEMORY.md 只给了 topic 指针，需要更多细节；\n'
        '- 不确定相关内容在哪个 topic。\n\n'
        '本地无 embedding，使用关键词命中 + 中文子串 + 位置加权。\n'
        '至少提供 query 或 keyword 之一。'
    ),
    category='memory',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[{"summary": "典型调用", "args": {"query": 'Box.position', "keyword": '材质', "topk": 5}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def memory_search(query='', keyword='', topk=10):
    """搜索长期记忆。"""
    q = (query or '').strip()
    kw = (keyword or '').strip()
    if not q and not kw:
        return {'ok': False, 'error': 'query 与 keyword 至少提供一个'}
    try:
        n = int(topk) if topk else 10
    except (TypeError, ValueError):
        n = 10
    n = max(1, min(50, n))
    hits = search_memory(query=q, keyword=kw, topk=n)
    return {
        'ok': True,
        'query': q,
        'keyword': kw,
        'count': len(hits),
        'results': hits,
    }


# ---------------------------------------------------------------------- #
# memory_write
# ---------------------------------------------------------------------- #

@tool(
    name='memory_write',
    description=(
        '写入 / 修改 / 删除长期记忆文件。\n\n'
        'action 枚举：\n'
        '- "create"：整体创建/覆写文件（需要 file_path 与 new_content）\n'
        '- "edit"：局部字符串精准替换（需要 file_path、old_content、new_content）\n'
        '- "delete"：删除文件（需要 file_path）\n\n'
        '⚠️ 编辑硬性要求：\n'
        '  1. 先用 memory_read 读到最新原文，old_content 从中逐字复制；\n'
        '  2. old_content 建议 10~100 字，且必须在文件里唯一出现；\n'
        '  3. 严禁大段替换或凭印象猜内容。\n\n'
        '写入路由：\n'
        '- 用户明确说"记住/以后/默认/总是"→ 写 INSTRUCTIONS.md\n'
        '- 稳定结论、项目背景 → 写 topic/<slug>.md 并同步 MEMORY.md 索引'
    ),
    category='memory',
    dangerous=True,  # 写入型工具，走危险确认流
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[{"summary": "典型调用", "args": {"action": 'value', "file_path": 'C:/Work/scene.max', "old_content": '', "new_content": ''}}],
notes=['file_path 建议使用绝对路径，目录不存在会自动创建。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def memory_write(action, file_path, old_content='', new_content=''):
    """写入长期记忆。"""
    act = (action or '').strip().lower()
    fp = (file_path or '').strip()
    if not fp:
        return {'ok': False, 'error': 'file_path 不能为空'}
    store = get_memory_store()
    try:
        if act == 'create':
            store.create(fp, new_content or '')
            return {'ok': True, 'action': 'create', 'file_path': fp}
        if act == 'edit':
            store.edit(fp, old_content or '', new_content or '')
            return {'ok': True, 'action': 'edit', 'file_path': fp}
        if act == 'delete':
            removed = store.delete(fp)
            return {
                'ok': removed,
                'action': 'delete',
                'file_path': fp,
                'error': None if removed else '文件不存在',
            }
        return {'ok': False, 'error': 'action 必须是 create/edit/delete 之一'}
    except ValueError as exc:
        return {'ok': False, 'error': str(exc), 'file_path': fp, 'action': act}


# ---------------------------------------------------------------------- #
# event_search
# ---------------------------------------------------------------------- #

def _parse_iso_date(text):
    # type: (str) -> Optional[float]
    """把 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM:SS' 解析成本地时间戳。"""
    if not text:
        return None
    s = str(text).strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            return time.mktime(time.strptime(s, fmt))
        except ValueError:
            continue
    return None


@tool(
    name='event_search',
    description=(
        '在事件日志（原始对话/工具调用/时间点）里搜索。\n\n'
        '触发场景：\n'
        '- 用户问"上次/那天/之前/我们讨论过..."等具体历史行为；\n'
        '- 需要核对某个操作/决策是否真实发生过；\n'
        '- 追溯某段时间内的具体活动。\n\n'
        '与 memory_search 的区别：\n'
        '- memory_search 找"稳定结论"（画像/偏好/项目背景）\n'
        '- event_search 找"具体事件"（谁在何时说了/做了什么）\n\n'
        '参数：\n'
        '- query: 语义查询（关键词命中数排序）\n'
        '- keyword: 精确关键词（AND 语义）\n'
        '- start_date / end_date: "YYYY-MM-DD" 或 "YYYY-MM-DD HH:MM:SS"\n'
        '- kind: 只筛某种事件（如 user_input / assistant_reply / tool_call）\n'
        '- topk: 最多返回条数，按时间倒序'
    ),
    category='memory',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[{"summary": "典型调用", "args": {"query": 'Box.position', "keyword": '材质', "start_date": '', "end_date": '', "kind": '', "topk": 5}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def event_search_tool(query='', keyword='', start_date='', end_date='',
                      kind='', topk=10):
    """检索事件日志。"""
    q = (query or '').strip()
    kw = (keyword or '').strip()
    start_ts = _parse_iso_date(start_date)
    end_ts = _parse_iso_date(end_date)
    try:
        n = int(topk) if topk else 10
    except (TypeError, ValueError):
        n = 10
    n = max(1, min(50, n))
    events = search_events(
        keyword=kw,
        query=q,
        start_ts=start_ts,
        end_ts=end_ts,
        kind=(kind or '').strip() or None,
        topk=n,
    )
    return {
        'ok': True,
        'count': len(events),
        'events': events,
    }


__all__ = ['memory_read', 'memory_search', 'memory_write', 'event_search_tool']