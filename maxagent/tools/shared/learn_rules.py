#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档自进化（learn-to-rule）入口。

提供 LLM 可调用的工具：

- ``suggest_rule_addition``：保守模式下，LLM 在自己踩坑/纠错后才主动
  提议新规则。调用本工具会弹出审批对话框给用户审阅。
- ``list_learned_rules``：让 LLM 知道自己已沉淀过哪些规则，避免重复提议。
- ``delete_learned_rule``：删除一条已学规则（dangerous=True，需用户确认）。

设计与 ``learn_tools.py`` 高度对齐：
- 弹窗审批回调由 UI 层注入 ``set_rule_approval_callback``。
- 主线程执行（``run_on_main_thread=True``）保证 ``QDialog.exec_()`` 安全。
- 所有失败路径都返回结构化字典，便于 LLM 解读后转告用户。
"""

from __future__ import absolute_import
from __future__ import print_function

import time
from typing import Any
from typing import Dict
from typing import Optional

from ...logger import get_logger
from ...user_rules_loader import delete_rule as _delete_rule
from ...user_rules_loader import get_rule as _get_rule
from ...user_rules_loader import list_rules as _list_rules
from ...user_rules_loader import validate_rule_content
from ...user_rules_loader import validate_rule_id
from ...user_rules_loader import write_rule
from ..registry import tool


logger = get_logger(__name__)


# 全局审批回调：由 UI 层在初始化时注入。
_APPROVAL_CB = None  # type: Optional[Any]


def set_rule_approval_callback(cb):
    """注入主线程审批回调。

    :param cb: 可调用对象，签名::

            cb(proposal: dict) -> dict

        proposal 字段:
            id, title, content, good_example, bad_example,
            tags, rationale, source_session_sid (可选)
        返回字段:
            approved: bool
            edited_title: str
            edited_content: str
            edited_good_example: str
            edited_bad_example: str
            reason: str
    """
    global _APPROVAL_CB  # pylint: disable=global-statement
    _APPROVAL_CB = cb


def _default_approval(proposal):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """默认审批回调（无 UI 时拒绝）。"""
    return {
        'approved': False,
        'reason': '未注入审批 UI，自动拒绝。',
        'edited_title': proposal.get('title', ''),
        'edited_content': proposal.get('content', ''),
        'edited_good_example': proposal.get('good_example', ''),
        'edited_bad_example': proposal.get('bad_example', ''),
    }


@tool(
    name='suggest_rule_addition',
    description=(
        '提议把本次对话中沉淀出的一条经验/坑点固化为 LLM 长期规则。'
        '调用此工具会弹出审批对话框，让用户审阅并决定是否保存。'
        '何时调用：只在你确实踩过坑（如颜色参数错乱、API 拼写错误）'
        '或用户明确指出"以后注意 X"时才提议；'
        '不要为通用常识或一次性场景提议规则。'
        '规则内容应当简短、可操作、面向未来的 LLM。\n'
        '提议格式要求:\n'
        ' - rule_id: 小写字母 + 数字 + 下划线，如 color_uppercase_required\n'
        ' - title: 一句话标题（≤30 字）\n'
        ' - content: 1-3 句具体规则（≤200 字）\n'
        ' - good_example / bad_example: 各一行代码示例（可选但强烈推荐）\n'
        ' - tags: 标签列表，如 ["material", "pymxs"]\n'
        ' - rationale: 你为什么要沉淀这条规则（给用户看）'
    ),
    category='learn',
    dangerous=True,
    wrap_undo=False,
    run_on_main_thread=True,
    examples=[{"summary": "典型调用", "args": {"rule_id": 'value', "title": 'value', "content": 'value', "good_example": '', "bad_example": '', "tags": 'value', "rationale": ''}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def suggest_rule_addition(
    rule_id,
    title,
    content,
    good_example='',
    bad_example='',
    tags=None,
    rationale='',
):
    # type: (str, str, str, str, str, Optional[list], str) -> Dict[str, Any]
    """提议保存一条新的用户规则。

    :param rule_id: 规则 ID（小写字母+数字+下划线，2-41 字符）
    :param title: 一句话标题
    :param content: 规则正文（1-3 句）
    :param good_example: 正例代码（可选）
    :param bad_example: 反例代码（可选）
    :param tags: 标签列表（可选）
    :param rationale: 提议理由，给用户看（可选）
    """
    # 早期校验
    try:
        validate_rule_id(rule_id)
    except ValueError as exc:
        logger.warning(
            'suggest_rule_addition rule_id 校验失败: id=%s, error=%s',
            rule_id, exc,
        )
        return {
            'approved': False,
            'error': str(exc),
            'stage': 'validate_id',
        }
    try:
        validate_rule_content(content)
    except ValueError as exc:
        logger.warning(
            'suggest_rule_addition 内容校验失败: id=%s, error=%s',
            rule_id, exc,
        )
        return {
            'approved': False,
            'error': str(exc),
            'stage': 'validate_content',
        }
    if not (title or '').strip():
        logger.warning('suggest_rule_addition 标题为空: id=%s', rule_id)
        return {
            'approved': False,
            'error': '规则标题不能为空',
            'stage': 'validate_title',
        }

    # 同 ID 已存在则在 proposal 里标注，让用户决定是否覆盖
    existing = _get_rule(rule_id)

    proposal = {
        'id': rule_id,
        'title': title or '',
        'content': content or '',
        'good_example': good_example or '',
        'bad_example': bad_example or '',
        'tags': list(tags or []),
        'rationale': rationale or '',
        'existing': existing,
    }

    logger.info(
        'suggest_rule_addition 触发审批: id=%s, overwrite=%s',
        rule_id, bool(existing),
    )
    cb = _APPROVAL_CB or _default_approval
    try:
        verdict = cb(proposal) or {}
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception(
            'suggest_rule_addition 审批回调异常: id=%s', rule_id,
        )
        return {
            'approved': False,
            'error': '审批回调异常: {}'.format(exc),
            'stage': 'callback',
        }

    if not verdict.get('approved'):
        logger.info(
            'suggest_rule_addition 用户拒绝: id=%s, reason=%s',
            rule_id, verdict.get('reason', ''),
        )
        return {
            'approved': False,
            'reason': verdict.get('reason', '用户已拒绝'),
            'stage': 'rejected',
        }

    # 用户批准：使用编辑后的字段（如有）
    final = {
        'title': verdict.get('edited_title') or title,
        'content': verdict.get('edited_content') or content,
        'good_example': verdict.get('edited_good_example', good_example),
        'bad_example': verdict.get('edited_bad_example', bad_example),
        'tags': list(tags or []),
        'rationale': rationale or '',
        'created_at': time.time(),
        'approved_by_user': True,
        'enabled': True,
    }
    try:
        validate_rule_content(final['content'])
    except ValueError as exc:
        logger.warning(
            'suggest_rule_addition 编辑后校验失败: id=%s, error=%s',
            rule_id, exc,
        )
        return {
            'approved': False,
            'error': '编辑后内容校验失败: {}'.format(exc),
            'stage': 'post_edit_validate',
        }

    try:
        path = write_rule(rule_id, final)
    except (OSError, ValueError) as exc:
        logger.exception('suggest_rule_addition 落盘失败: id=%s', rule_id)
        return {
            'approved': False,
            'error': '落盘失败: {}'.format(exc),
            'stage': 'write',
        }

    logger.info(
        'suggest_rule_addition 完成: id=%s, path=%s', rule_id, path,
    )
    return {
        'approved': True,
        'saved': True,
        'rule_id': rule_id,
        'path': path,
        'message': (
            '规则已保存，下一轮对话起会自动注入到 system prompt。'
        ),
    }


@tool(
    name='list_learned_rules',
    description=(
        '列出所有从用户互动中沉淀的自定义规则。'
        '在你打算 suggest_rule_addition 之前可先调用此工具，'
        '避免重复提议同一条规则。'
    ),
    category='learn',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
    examples=[{'summary': '列出所有已学习规则', 'args': {}}],
    notes=[
        '返回已学习规则列表。',
        '规则通常来自用户显式教导或自动反思沉淀。',
    ],
    returns_desc='dict {"count": 规则数量, "rules": [...]}',
)
def list_learned_rules():
    """列出已学规则。"""
    rules = _list_rules(only_enabled=False)
    out = []
    for r in rules:
        out.append({
            'id': r.get('id'),
            'title': r.get('title'),
            'tags': r.get('tags') or [],
            'enabled': r.get('enabled', True),
            'created_at': r.get('created_at'),
        })
    return {'count': len(out), 'rules': out}


@tool(
    name='delete_learned_rule',
    description='删除一条之前沉淀的自定义规则。',
    category='learn',
    dangerous=True,
    wrap_undo=False,
    run_on_main_thread=True,
    examples=[{"summary": "典型调用", "args": {"rule_id": 'value'}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def delete_learned_rule(rule_id):
    """删除指定的规则。

    :param rule_id: 要删除的规则 ID
    """
    ok = _delete_rule(rule_id)
    logger.info('delete_learned_rule: id=%s, ok=%s', rule_id, ok)
    return {'deleted': ok, 'rule_id': rule_id}


__all__ = [
    'suggest_rule_addition',
    'list_learned_rules',
    'delete_learned_rule',
    'set_rule_approval_callback',
]