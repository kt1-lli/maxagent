#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具执行后的场景状态自动复核。

从 agent/worker.py 中抽出, 作为可独立测试的纯逻辑模块:
- decide_verify_target(tool_name, args, result) -> 目标对象名或 None
- build_verify_info(tool_name, target_name, info)  -> 结构化复核结论
- auto_verify(tool_name, args, result, sync_runner, info_tool)  -> 端到端复核入口

sync_runner 通过依赖注入传入, 便于 mock 测试与不同 DCC 复用。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger('maxagent.agent.verify')


# 需要复核的工具名前缀 (影响场景状态的写操作)
STATEFUL_PREFIXES = (
    'create_',
    'modify_',
    'set_',
    'move_',
    'delete_',
    'apply_',
)

# 从参数里定位"目标对象名"字段的候选 key
TARGET_KEYS = ('name', 'object_name', 'target', 'node_name')


def decide_verify_target(tool_name, args, result):
    # type: (str, Dict[str, Any], Any) -> Optional[str]
    """从工具参数/返回值提取要复核的对象名。

    仅对写操作 (STATEFUL_PREFIXES) 返回目标名; 只读/非命名操作返回 None。
    """
    if not isinstance(tool_name, str) or not tool_name.startswith(STATEFUL_PREFIXES):
        return None

    if isinstance(args, dict):
        for key in TARGET_KEYS:
            val = args.get(key)
            if val and isinstance(val, str):
                return val

    if isinstance(result, dict):
        val = result.get('name')
        if val and isinstance(val, str):
            return val

    return None


def build_verify_info(tool_name, target_name, info):
    # type: (str, str, Optional[Dict[str, Any]]) -> Dict[str, Any]
    """把 info_tool 的返回值转成结构化复核结论。

    存在性判定策略: 只有明确拿到 exists=False / found=False 才判 not_found。
    其它情况 (info 是 None / 空 dict / 缺字段) 一律按 verified 处理,
    避免主线程复核工具抖动造成的误报。
    """
    normalized = info if isinstance(info, dict) else {}
    exists_val = normalized.get('exists')
    found_val = normalized.get('found')
    explicitly_missing = (exists_val is False) or (found_val is False)

    if explicitly_missing:
        return {
            'target': target_name,
            'status': 'not_found',
            'note': (
                '复核时未找到对象 {}, 可能已被删除或重命名。'
                .format(target_name)
            ),
        }
    return {
        'target': target_name,
        'status': 'verified',
        'current_position': normalized.get('position'),
        'current_rotation': (
            normalized.get('rotation') or normalized.get('rotation_euler')
        ),
        'current_scale': normalized.get('scale'),
        'current_material': normalized.get('material'),
        'note': (
            '以上为此对象执行 {} 后的真实状态。'
            '请对比你的预期值, 若有偏差请修正。'.format(tool_name)
        ),
    }


def auto_verify(tool_name, args, result, sync_runner, info_tool):
    # type: (str, Dict[str, Any], Any, Optional[Callable], str) -> Optional[Dict[str, Any]]
    """自动复核入口。

    :param tool_name: 刚执行完的工具名
    :param args: 该工具入参
    :param result: 该工具原始返回
    :param sync_runner: (tool_name, args_dict) -> dict, 由 worker 注入的主线程执行器
    :param info_tool: 查询对象状态的工具名, 如 'get_maya_object_info' / 'get_object_info'
    :returns: 复核信息 dict; 不需要复核时返回 None
    """
    if sync_runner is None:
        return None

    target_name = decide_verify_target(tool_name, args, result)
    if not target_name:
        return None

    try:
        verify_result = sync_runner(info_tool, {'name': target_name})
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            'auto_verify sync_runner failed tool=%s target=%s err=%s',
            tool_name,
            target_name,
            exc,
        )
        return {
            'target': target_name,
            'status': 'query_failed',
            'error': str(exc),
        }

    if verify_result is None or (
        isinstance(verify_result, dict) and not verify_result
    ):
        logger.debug(
            'auto_verify sync_runner returned empty tool=%s target=%s: %r',
            tool_name,
            target_name,
            verify_result,
        )

    return build_verify_info(tool_name, target_name, verify_result)
