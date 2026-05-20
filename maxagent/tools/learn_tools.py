#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具自进化（learn-to-tool）入口。

提供一个 LLM 可调用的工具 ``propose_new_tool``：
- LLM 在用户多次让 agent 做同类操作后，可主动提议把流程固化为 Python 工具。
- 调用此工具时弹出审批对话框，用户可查看代码、修改、批准或拒绝。
- 批准后落盘到 ``user_tools/<name>.py`` 并热加载，下次启动也可用。

安全策略（C1 半自动）：
- 必须用户在弹窗里点"批准"才能执行（弹窗必然在主线程运行，
  因为本工具 run_on_main_thread=True）。
- 弹窗里显示完整源码，用户可阅读 / 修改。
- 默认给生成的工具加 dangerous=True 标记，每次调用还会再问一次
  （用户可在 .meta.json 里手动改成 False）。
- 通过 ``MAX_CODE_BYTES`` 限制源码大小、必要的语法检查。

辅助工具：
- ``list_learned_tools``：让 LLM 知道自己已学到哪些工具
- ``delete_learned_tool``：删除一个学到的工具
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..user_tools_loader import delete_user_tool
from ..user_tools_loader import list_user_tools
from ..user_tools_loader import reload_user_tool
from ..user_tools_loader import validate_code
from ..user_tools_loader import validate_name
from ..user_tools_loader import write_tool
from .registry import tool


# 全局审批回调：由 UI 层在初始化时注入，签名见 set_approval_callback。
_APPROVAL_CB = None


def set_approval_callback(cb):
    """注入主线程审批回调。

    :param cb: 可调用对象，签名::

            cb(proposal: dict) -> dict

        proposal 字段:
            name, description, code, source_session_sid (可选)
        返回字段:
            approved: bool
            edited_code: str           (用户可能修改过)
            edited_description: str
            reason: str                (拒绝时的说明)

        cb 必须确保在 Qt 主线程执行（弹窗 + waitForFinish）。
    """
    global _APPROVAL_CB  # pylint: disable=global-statement
    _APPROVAL_CB = cb


def _default_approval(proposal):
    """默认审批回调（无 UI 时拒绝）。"""
    return {
        'approved': False,
        'reason': '未注入审批 UI，自动拒绝。',
        'edited_code': proposal.get('code', ''),
        'edited_description': proposal.get('description', ''),
    }


@tool(
    name='propose_new_tool',
    description=(
        '提议把当前流程固化为一个新的 Python 工具，下次同类需求可直接调用。'
        '调用此工具会弹出审批对话框给用户查看代码并决定是否保存。'
        '何时调用：当用户多次让你做类似操作（如批量改名、定制化清理），'
        '或用户明确说"以后帮我把这个做成一个工具"时。\n'
        '工具代码必须遵守:\n'
        ' - 用 @tool(name=..., description=...) 装饰一个函数\n'
        ' - 函数参数对应工具入参，用 type hint + docstring :param: 描述\n'
        ' - 函数体可调用 pymxs.runtime / 其他已注册工具完成任务\n'
        ' - 函数返回值要可 JSON 序列化（dict/list/str/number/bool）\n'
        ' - 不要包含恶意代码（弹窗会展示给用户看）'
    ),
    category='learn',
    dangerous=True,
    wrap_undo=False,
    # 必须主线程：弹窗、registry 操作都需要
    run_on_main_thread=True,
)
def propose_new_tool(name, description, code, rationale=''):
    # type: (str, str, str, str) -> dict
    """提议保存一个新工具，由用户审批后落盘 + 热加载。

    :param name: 工具名（小写字母 + 数字 + 下划线，2-41 字符），
        例如 cleanup_temp_objects
    :param description: 给未来的 LLM 看的工具用途说明
    :param code: 完整的 Python 源码，必须包含一个 @tool 装饰的函数。
        可以省略 ``from maxagent.tools.registry import tool`` 这一行，
        系统会自动注入。
    :param rationale: （可选）你为什么要把这个流程做成工具，给用户看
    """
    # 早期校验
    try:
        validate_name(name)
        validate_code(code)
    except ValueError as exc:
        return {
            'approved': False,
            'error': str(exc),
            'stage': 'validate',
        }

    proposal = {
        'name': name,
        'description': description or '',
        'code': code,
        'rationale': rationale or '',
    }

    cb = _APPROVAL_CB or _default_approval
    try:
        verdict = cb(proposal) or {}
    except Exception as exc:  # pylint: disable=broad-except
        return {
            'approved': False,
            'error': '审批回调异常: {}'.format(exc),
            'stage': 'callback',
        }

    if not verdict.get('approved'):
        return {
            'approved': False,
            'reason': verdict.get('reason', '用户已拒绝'),
            'stage': 'rejected',
        }

    # 用户批准，可能还修改了代码
    final_code = verdict.get('edited_code') or code
    final_desc = verdict.get('edited_description') or description
    try:
        validate_code(final_code)
    except ValueError as exc:
        return {
            'approved': False,
            'error': '用户编辑后代码校验失败: {}'.format(exc),
            'stage': 'post_edit_validate',
        }

    meta = {
        'name': name,
        'description': final_desc,
        'rationale': rationale,
        'created_at': time.time(),
        'approved_by_user': True,
        'use_count': 0,
    }
    try:
        py_path = write_tool(name, final_code, meta)
    except (OSError, ValueError) as exc:
        return {
            'approved': False,
            'error': '落盘失败: {}'.format(exc),
            'stage': 'write',
        }

    # 热加载到 registry
    try:
        reload_user_tool(name)
    except Exception as exc:  # pylint: disable=broad-except
        return {
            'approved': True,
            'saved': True,
            'loaded': False,
            'load_error': '{}: {}'.format(type(exc).__name__, exc),
            'py_path': py_path,
            'message': '工具已保存但热加载失败，下次启动会重试',
        }

    return {
        'approved': True,
        'saved': True,
        'loaded': True,
        'name': name,
        'py_path': py_path,
        'message': '工具已学习并立即可用，下次同类请求直接调用 ' + name,
    }


@tool(
    name='list_learned_tools',
    description='列出所有从用户互动中学到的自定义工具。',
    category='learn',
    dangerous=False,
    wrap_undo=False,
    run_on_main_thread=False,
)
def list_learned_tools():
    """列出已学习的工具。"""
    items = list_user_tools(include_meta=True)
    out = []
    for it in items:
        meta = it.get('meta') or {}
        out.append({
            'name': it['name'],
            'description': meta.get('description', ''),
            'use_count': meta.get('use_count', 0),
            'created_at': meta.get('created_at'),
        })
    return {'count': len(out), 'tools': out}


@tool(
    name='delete_learned_tool',
    description='删除一个之前学到的自定义工具。',
    category='learn',
    dangerous=True,
    wrap_undo=False,
    run_on_main_thread=True,
)
def delete_learned_tool(name):
    """删除指定的学习工具。

    :param name: 要删除的工具名
    """
    ok = delete_user_tool(name)
    return {'deleted': ok, 'name': name}


__all__ = [
    'propose_new_tool',
    'list_learned_tools',
    'delete_learned_tool',
    'set_approval_callback',
]
