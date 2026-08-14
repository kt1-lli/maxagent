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

from ..logger import get_logger
from ..user_tools_loader import delete_user_tool
from ..user_tools_loader import list_user_tools
from ..user_tools_loader import reload_user_tool
from ..user_tools_loader import validate_code
from ..user_tools_loader import validate_name
from ..user_tools_loader import write_tool
from .registry import tool


logger = get_logger(__name__)


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
        '撰写工具前，建议先用 list_class_tree / get_class_info 反射 Max 类树，'
        '并用 search_max_docs 查询官方文档确认 API 签名，降低代码错误率。\n'
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
        logger.warning('propose_new_tool 校验失败: name=%s, error=%s', name, exc)
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

    logger.info('propose_new_tool 触发审批: name=%s', name)
    cb = _APPROVAL_CB or _default_approval
    try:
        verdict = cb(proposal) or {}
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception('propose_new_tool 审批回调异常: name=%s', name)
        return {
            'approved': False,
            'error': '审批回调异常: {}'.format(exc),
            'stage': 'callback',
        }

    if not verdict.get('approved'):
        logger.info(
            'propose_new_tool 用户拒绝: name=%s, reason=%s',
            name, verdict.get('reason', ''),
        )
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
        logger.warning(
            'propose_new_tool 编辑后校验失败: name=%s, error=%s', name, exc,
        )
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
        logger.exception('propose_new_tool 落盘失败: name=%s', name)
        return {
            'approved': False,
            'error': '落盘失败: {}'.format(exc),
            'stage': 'write',
        }

    # 热加载到 registry
    try:
        reload_user_tool(name)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception('propose_new_tool 热加载失败: name=%s', name)
        return {
            'approved': True,
            'saved': True,
            'loaded': False,
            'load_error': '{}: {}'.format(type(exc).__name__, exc),
            'py_path': py_path,
            'message': '工具已保存但热加载失败，下次启动会重试',
        }

    logger.info('propose_new_tool 完成: name=%s, py_path=%s', name, py_path)
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
    description='列出所有从用户互动中学到的自定义工具，含使用统计。',
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
            'success_count': meta.get('success_count', 0),
            'error_count': meta.get('error_count', 0),
            'last_used_at': meta.get('last_used_at'),
            'last_ok': meta.get('last_ok'),
            'created_at': meta.get('created_at'),
        })
    return {'count': len(out), 'tools': out}


@tool(
    name='patch_learned_tool',
    description=(
        '修补一个之前 propose 过的自定义工具的源码。'
        '调用此工具会弹出审批对话框给用户查看新代码并决定是否覆盖。'
        '何时调用：当某个 user tool 调用频繁失败（list_learned_tools '
        '中 error_count 高），或用户明确说"修一下 X 工具"时。\n'
        '严格限制：\n'
        ' - 只能修改用户自己 propose 的工具，无法修改内置工具\n'
        ' - rationale 必须如实说明本次修补针对的具体问题（给用户看）\n'
        ' - 新代码必须保持相同的工具名（@tool 装饰器里的 name=...）'
    ),
    category='learn',
    dangerous=True,
    wrap_undo=False,
    # 必须主线程：弹窗 + registry 操作
    run_on_main_thread=True,
)
def patch_learned_tool(name, new_code, rationale):
    # type: (str, str, str) -> dict
    """提议修补一个已学习工具的源码，由用户审批后落盘 + 热加载。

    :param name: 要修补的工具名（必须已存在于 user_tools/ 目录）
    :param new_code: 完整的新版 Python 源码（不是 diff）
    :param rationale: 本次修补针对的具体问题，例如
        "之前未处理空场景 → 修正为先 isValidNode 判断再 delete"
    """
    # 早期校验：必须是已存在的 user tool
    try:
        validate_name(name)
    except ValueError as exc:
        return {
            'approved': False,
            'error': str(exc),
            'stage': 'validate_name',
        }

    items = list_user_tools(include_meta=False)
    existing = next((it for it in items if it['name'] == name), None)
    if existing is None:
        logger.warning('patch_learned_tool 拒绝：工具不存在 name=%s', name)
        return {
            'approved': False,
            'error': (
                'patch_learned_tool 只能修改 user_tools/ 下的已学习工具。'
                '工具 {!r} 不存在或是内置工具——内置工具不可修改。'
            ).format(name),
            'stage': 'not_user_tool',
        }

    if not (rationale or '').strip():
        return {
            'approved': False,
            'error': 'rationale 不能为空，必须说明本次修补的具体原因',
            'stage': 'validate_rationale',
        }

    try:
        validate_code(new_code)
    except ValueError as exc:
        logger.warning('patch_learned_tool 校验失败 name=%s: %s', name, exc)
        return {
            'approved': False,
            'error': str(exc),
            'stage': 'validate_code',
        }

    # 读出旧源码用于审批弹窗对照展示
    old_code = ''
    py_path = existing.get('py_path')
    if py_path:
        try:
            with open(py_path, 'r', encoding='utf-8') as fh:
                old_code = fh.read()
        except OSError:
            old_code = ''

    # 旧 description 沿用（patch 不改 description；要改请走 propose 同名覆盖）
    old_meta = {}
    meta_path_guess = py_path + '.meta.json' if py_path else None
    # write_tool 的实际 meta 路径与 py_path 同目录同名 + .meta.json
    if meta_path_guess:
        # 实际路径见 user_tools_loader._tool_meta_path：os.path.join(base, name + META_SUFFIX)
        # 而 py_path = base + '/' + name + '.py'，所以替换后缀即可
        meta_path = py_path[:-3] + '.meta.json' if py_path.endswith('.py') \
            else None
        if meta_path:
            try:
                with open(meta_path, 'r', encoding='utf-8') as fh:
                    old_meta = json.load(fh) or {}
            except (OSError, ValueError):
                old_meta = {}

    proposal = {
        'name': name,
        'description': old_meta.get('description', ''),
        'code': new_code,
        'rationale': '【修补已有工具】{}\n\n旧代码摘要（前 200 字）：\n{}'.format(
            rationale, (old_code[:200] + '...') if len(old_code) > 200 else old_code,
        ),
    }

    logger.info('patch_learned_tool 触发审批: name=%s', name)
    cb = _APPROVAL_CB or _default_approval
    try:
        verdict = cb(proposal) or {}
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception('patch_learned_tool 审批回调异常: name=%s', name)
        return {
            'approved': False,
            'error': '审批回调异常: {}'.format(exc),
            'stage': 'callback',
        }

    if not verdict.get('approved'):
        logger.info(
            'patch_learned_tool 用户拒绝: name=%s, reason=%s',
            name, verdict.get('reason', ''),
        )
        return {
            'approved': False,
            'reason': verdict.get('reason', '用户已拒绝'),
            'stage': 'rejected',
        }

    final_code = verdict.get('edited_code') or new_code
    try:
        validate_code(final_code)
    except ValueError as exc:
        return {
            'approved': False,
            'error': '用户编辑后代码校验失败: {}'.format(exc),
            'stage': 'post_edit_validate',
        }

    # 复用原 meta 关键字段（保留 created_at / use_count 历史，
    # 仅刷新 patched_at 标识本次修补）。
    new_meta = dict(old_meta)
    new_meta['name'] = name
    new_meta['approved_by_user'] = True
    new_meta['patched_at'] = time.time()
    new_meta['patch_rationale'] = rationale

    try:
        new_py_path = write_tool(name, final_code, new_meta)
    except (OSError, ValueError) as exc:
        logger.exception('patch_learned_tool 落盘失败: name=%s', name)
        return {
            'approved': False,
            'error': '落盘失败: {}'.format(exc),
            'stage': 'write',
        }

    try:
        reload_user_tool(name)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception('patch_learned_tool 热加载失败: name=%s', name)
        return {
            'approved': True,
            'saved': True,
            'loaded': False,
            'load_error': '{}: {}'.format(type(exc).__name__, exc),
            'py_path': new_py_path,
            'message': '工具已修补但热加载失败，下次启动会重试',
        }

    logger.info('patch_learned_tool 完成: name=%s', name)
    return {
        'approved': True,
        'saved': True,
        'loaded': True,
        'name': name,
        'py_path': new_py_path,
        'message': '工具 {} 已修补并立即可用'.format(name),
    }


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
    logger.info('delete_learned_tool: name=%s, ok=%s', name, ok)
    return {'deleted': ok, 'name': name}


__all__ = [
    'propose_new_tool',
    'patch_learned_tool',
    'list_learned_tools',
    'delete_learned_tool',
    'set_approval_callback',
]
