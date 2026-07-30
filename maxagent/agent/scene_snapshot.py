#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""场景快照（Scene Snapshot）：为 LLM 提供当前场景的轻量状态摘要。

设计目标：
- 在 LLM 多轮工具调用过程中，LLM 只能看到"自己做过的操作结果"，
  容易对"其他对象的状态"产生幻觉（以为某个对象还在原位，实际已被移动）。
- 定期注入场景完整状态（对象列表、位置、选择集），作为"真实锚点"，
  校正 LLM 的空间推理。
- 轻量级：只列对象名 + transform.position，不展开材质/修改器，
  控制 token 消耗。

触发策略（由 worker 中的 loop_idx 驱动）：
- 第 1 轮（loop_idx=0）：不放 snapshot（用户刚提问，场景可能未改变）
- 第 3 轮（loop_idx=2）：放 snapshot（一批工具已执行，状态可能已变）
- 第 6 轮（loop_idx=5）：放 snapshot（更多工具后再次校正）
- 以此类推：每 3 轮放一次

使用方式：
  snapshot = build_scene_snapshot(runner)
  # runner 是 sync_tool_runner 签名 (tool_name, args) -> result
  # 返回结构化 dict，由 worker 序列化为 system note 文本注入
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional


def build_scene_snapshot(sync_tool_runner):
    # type: (Callable[[str, Dict[str, Any]], Any]) -> Optional[Dict[str, Any]]
    """通过主线程工具执行器获取场景快照。

    :param sync_tool_runner: 签名 (tool_name, args) -> result_dict 的 callable，
        必须在主线程执行（即 worker 的 _sync_tool_runner）。
    :returns: 快照 dict，包含对象列表、选择集、时间信息；若 runner 为
        None 或执行失败则返回 None。
    """
    if sync_tool_runner is None:
        return None

    try:
        # 获取场景对象列表（限制前 30 个，避免 token 爆炸）
        obj_result = sync_tool_runner(
            'list_objects',
            {'super_class': '', 'limit': 30, 'detail': False},
        )
        # 获取当前选择集
        sel_result = sync_tool_runner(
            'get_selection',
            {'detail': False},
        )
        # 获取时间信息
        time_result = sync_tool_runner('get_time_info', {})
    except Exception:  # pylint: disable=broad-except
        return None

    snapshot = {}  # type: Dict[str, Any]

    # 对象列表精简：只保留 name + class + position
    if isinstance(obj_result, dict) and obj_result.get('ok'):
        raw = obj_result.get('data', {})
        snapshot['objects'] = _summarize_objects(raw.get('items', []))
        snapshot['object_count'] = raw.get('total', 0)

    # 选择集
    if isinstance(sel_result, dict) and sel_result.get('ok'):
        raw = sel_result.get('data', {})
        snapshot['selection'] = [
            item.get('name', '')
            for item in raw.get('items', [])
        ]

    # 时间
    if isinstance(time_result, dict) and time_result.get('ok'):
        snapshot['time'] = time_result.get('data', {})

    return snapshot if snapshot else None


def _summarize_objects(items):
    # type: (List[Dict[str, Any]]) -> List[Dict[str, Any]]
    """将对象列表精简为 LLM 友好的摘要格式。"""
    out = []
    for item in items:
        name = item.get('name', '')
        klass = item.get('class', '')
        pos = item.get('position')
        out.append({'name': name, 'class': klass, 'position': pos})
    return out


def snapshot_to_prompt_text(snapshot):
    # type: (Optional[Dict[str, Any]]) -> str
    """将快照 dict 转为 LLM 友好的 system note 文本。

    :param snapshot: build_scene_snapshot 的返回值
    :returns: 提示文本；若 snapshot 为 None 或空则返回空串。
    """
    if not snapshot:
        return ''
    lines = ['【🌍 场景快照（当前状态锚点）】']

    # 选择集
    sel = snapshot.get('selection', [])
    if sel:
        lines.append('当前选中: {}'.format(', '.join(sel)))
    else:
        lines.append('当前选中: 无')

    # 对象总数
    total = snapshot.get('object_count', 0)
    lines.append('场景对象总数: {}'.format(total))

    # 对象列表
    objs = snapshot.get('objects', [])
    if objs:
        lines.append('关键对象位置:')
        for obj in objs:
            name = obj.get('name', '')
            klass = obj.get('class', '')
            pos = obj.get('position')
            pos_str = (
                '({:.2f}, {:.2f}, {:.2f})'.format(*pos)
                if isinstance(pos, (list, tuple)) and len(pos) == 3
                else '未知'
            )
            lines.append('  - {}[{}] {}'.format(name, klass, pos_str))

    # 时间
    time_info = snapshot.get('time', {})
    if time_info:
        cur = time_info.get('current')
        if cur is not None:
            lines.append('当前帧: {}'.format(cur))

    # 防幻觉提醒
    lines.append(
        '→ 以上为此刻场景真实状态。如果你的记忆与上述数据冲突，'
        '请以本快照为准，勿依赖过时的内部假设。'
    )
    return '\n'.join(lines)


__all__ = [
    'build_scene_snapshot',
    'snapshot_to_prompt_text',
]
