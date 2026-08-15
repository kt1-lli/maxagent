#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""反思（self-reflection）入口工具。

提供给 LLM 调用的工具：
- ``reflect_on_outcome``：在任务（部分）失败或值得复盘时调用，沉淀经验
- ``list_reflections``：让 LLM 知道自己已记下哪些反思（避免重复）
- ``delete_reflection``：删除一条反思（dangerous=True，需用户确认）

设计理由（与 learn_rules / learn_tools 的关键区别）：
- 不弹审批窗：反思频次远高于规则提议，强制弹窗会打扰用户；反思副作用
  小（只影响 LLM 自己后续推理），用户可在「我的资源」里事后查看 / 删除
- 系统提示词注入受限：仅注入 30 天内的最近 10 条，超出限度自动淡出；
  确保反思不会无限累积导致 prompt 退化
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..logger import get_logger
from ..reflections_loader import delete_reflection as _delete_reflection
from ..reflections_loader import get_reflection as _get_reflection
from ..reflections_loader import list_reflections as _list_reflections
from ..reflections_loader import validate_reflection_payload
from ..reflections_loader import write_reflection
from .registry import tool


logger = get_logger(__name__)


@tool(
    name='reflect_on_outcome',
    description=(
        '把刚结束的任务中"哪里做得好/哪里出问题/下次怎么改进"沉淀成短期反思，'
        '在后续任务中作为辅助记忆注入你的 system prompt。\n'
        '何时调用：\n'
        '  ✓ 任务部分失败或反复返工（被用户多次纠正）\n'
        '  ✓ 任务成功但过程坎坷，值得记录避免下次再踩同样的坑\n'
        '  ✓ 用户明确说"记下这次的教训"\n'
        '不要调用：\n'
        '  ✗ 一次顺利完成的小任务（无反思价值）\n'
        '  ✗ 把"用户偏好/规则"当反思来记（用 suggest_rule_addition）\n'
        '  ✗ 把"工具修补"当反思来记（用 patch_learned_tool）\n'
        '字段要求：\n'
        '  - task_summary: 一句话概括做了什么（≤80 字）\n'
        '  - what_went_wrong: 出了什么问题，可为空（≤200 字）\n'
        '  - lessons: 下次怎么改，必填（≤200 字，最关键的字段）\n'
        '  - what_went_well: 做对的部分，可为空（≤100 字）\n'
        '  - tags: 标签列表，便于将来检索，如 ["rename", "pymxs"]'
    ),
    category='learn',
    dangerous=False,
    wrap_undo=False,
    # 仅写本地 JSON，不需要主线程 pymxs 调用
    run_on_main_thread=False,
    examples=[{"summary": "典型调用", "args": {"task_summary": 'value', "lessons": 'value', "what_went_wrong": '', "what_went_well": '', "tags": 'value'}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def reflect_on_outcome(
    task_summary,
    lessons,
    what_went_wrong='',
    what_went_well='',
    tags=None,
):
    # type: (str, str, str, str, Optional[List[str]]) -> Dict[str, Any]
    """沉淀一条反思到本地反思库。

    :param task_summary: 一句话任务概要
    :param lessons: 下次的改进经验（必填）
    :param what_went_wrong: 出问题的环节（可空）
    :param what_went_well: 做对的环节（可空）
    :param tags: 标签列表
    """
    payload = {
        'task_summary': task_summary or '',
        'lessons': lessons or '',
        'what_went_wrong': what_went_wrong or '',
        'what_went_well': what_went_well or '',
        'tags': list(tags or []),
    }
    try:
        validate_reflection_payload(payload)
    except ValueError as exc:
        logger.warning('reflect_on_outcome 校验失败: %s', exc)
        return {
            'saved': False,
            'error': str(exc),
        }

    try:
        reflection_id = write_reflection(payload)
    except (OSError, ValueError) as exc:
        logger.exception('reflect_on_outcome 落盘失败')
        return {
            'saved': False,
            'error': '{}: {}'.format(type(exc).__name__, exc),
        }

    logger.info('reflect_on_outcome 已沉淀: id=%s', reflection_id)
    return {
        'saved': True,
        'reflection_id': reflection_id,
        'message': '已记下本次反思，后续任务中将作为短期记忆参考。',
    }


@tool(
    name='list_reflections',
    description=(
        '列出最近的反思记录。'
        '在你打算 reflect_on_outcome 之前可先调用本工具，'
        '避免把同一类经验重复记录。'
    ),
    category='learn',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[{'summary': '列出最近反思记录', 'args': {}}],
    notes=[
        '返回反思记录的摘要。',
        '反思用于总结工具调用成功/失败经验，帮助后续决策。',
    ],
    returns_desc='dict {"count": 反思数量, "reflections": [...]}',
)
def list_reflections():
    """列出反思（按时间倒序，仅返回近 30 天内的）。"""
    rfls = _list_reflections(only_recent=True)
    out = []
    for r in rfls:
        out.append({
            'id': r.get('id'),
            'task_summary': r.get('task_summary'),
            'lessons': r.get('lessons'),
            'tags': r.get('tags') or [],
            'created_at': r.get('created_at'),
        })
    return {'count': len(out), 'reflections': out}


@tool(
    name='delete_reflection',
    description='删除一条之前沉淀的反思（用户/LLM 确认后调用）。',
    category='learn',
    dangerous=True,
    wrap_undo=False,
    # 删除盘上文件，与 dangerous 弹窗结合：会走 confirm_callback
    run_on_main_thread=True,
    examples=[{"summary": "典型调用", "args": {"reflection_id": 'value'}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def delete_reflection(reflection_id):
    """删除指定反思。

    :param reflection_id: 要删除的反思 ID
    """
    ok = _delete_reflection(reflection_id)
    logger.info(
        'delete_reflection: id=%s, ok=%s', reflection_id, ok,
    )
    return {'deleted': ok, 'reflection_id': reflection_id}


__all__ = [
    'reflect_on_outcome',
    'list_reflections',
    'delete_reflection',
]