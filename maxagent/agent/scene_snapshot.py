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

from ..dcc.runtime import current_dcc


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

    dcc = current_dcc()
    if dcc == 'maya':
        list_tool = 'list_maya_objects'
        list_args = {'object_type': '', 'limit': 30, 'detail': False}
        sel_tool = 'get_maya_selection'
        sel_args = {'detail': False}
        time_tool = 'get_current_frame'
        time_args = {}
    else:
        list_tool = 'list_objects'
        list_args = {'super_class': '', 'limit': 30, 'detail': False}
        sel_tool = 'get_selection'
        sel_args = {'detail': False}
        time_tool = 'get_time_info'
        time_args = {}

    try:
        # 获取场景对象列表（限制前 30 个，避免 token 爆炸）
        obj_result = sync_tool_runner(list_tool, list_args)
        # 获取当前选择集
        sel_result = sync_tool_runner(sel_tool, sel_args)
        # 获取时间信息
        time_result = sync_tool_runner(time_tool, time_args)
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


def diff_snapshots(before, after):
    # type: (Optional[Dict[str, Any]], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """对比两个快照，返回结构化 diff。

    只关心用户能感知的变化：
    - added: after 有 before 没有的对象
    - removed: before 有 after 没有的对象
    - moved: 同名对象位置变化超过阈值
    - selection_changed: 选择集变化
    - count_delta: 对象总数变化

    :param before: 执行前的快照
    :param after: 执行后的快照
    :returns: diff dict；若两侧任一为空，返回带 ``empty=True`` 的空 diff
    """
    empty = {
        'empty': True,
        'added': [],
        'removed': [],
        'moved': [],
        'selection_changed': False,
        'count_delta': 0,
    }
    if not before or not after:
        return empty

    before_objs = {
        o.get('name', ''): o for o in before.get('objects', []) if o.get('name')
    }
    after_objs = {
        o.get('name', ''): o for o in after.get('objects', []) if o.get('name')
    }

    added = []
    for name, obj in after_objs.items():
        if name not in before_objs:
            added.append({
                'name': name,
                'class': obj.get('class', ''),
                'position': obj.get('position'),
            })

    removed = []
    for name, obj in before_objs.items():
        if name not in after_objs:
            removed.append({
                'name': name,
                'class': obj.get('class', ''),
            })

    moved = []
    for name, obj in after_objs.items():
        if name not in before_objs:
            continue
        old_pos = before_objs[name].get('position')
        new_pos = obj.get('position')
        if not _pos_equal(old_pos, new_pos):
            moved.append({
                'name': name,
                'from': old_pos,
                'to': new_pos,
            })

    sel_changed = (
        set(before.get('selection', []) or []) !=
        set(after.get('selection', []) or [])
    )
    count_delta = int(after.get('object_count', 0)) - int(
        before.get('object_count', 0)
    )

    return {
        'empty': not (added or removed or moved or sel_changed),
        'added': added,
        'removed': removed,
        'moved': moved,
        'selection_changed': sel_changed,
        'count_delta': count_delta,
    }


def _pos_equal(a, b, tol=0.01):
    """判断两个位置是否近似相等（tol 单位，Max 默认单位）。"""
    if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)):
        return a == b
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        try:
            if abs(float(x) - float(y)) > tol:
                return False
        except (TypeError, ValueError):
            return False
    return True


def diff_to_prompt_text(diff):
    # type: (Optional[Dict[str, Any]]) -> str
    """把 diff 转为 LLM 友好的短文本，用于批次结束后的 verify 注入。

    只在真正有变化时返回文本；空 diff 返回空串，避免上下文污染。
    """
    if not diff or diff.get('empty'):
        return ''
    lines = ['【🔎 批次复核 · 场景实际变化】']

    added = diff.get('added') or []
    if added:
        lines.append('新增对象 ({}):'.format(len(added)))
        for obj in added[:10]:
            pos = obj.get('position')
            pos_str = (
                '({:.2f}, {:.2f}, {:.2f})'.format(*pos)
                if isinstance(pos, (list, tuple)) and len(pos) == 3
                else '未知位置'
            )
            lines.append('  + {}[{}] {}'.format(
                obj.get('name', ''), obj.get('class', ''), pos_str,
            ))
        if len(added) > 10:
            lines.append('  … 另有 {} 项省略'.format(len(added) - 10))

    removed = diff.get('removed') or []
    if removed:
        lines.append('删除对象 ({}):'.format(len(removed)))
        for obj in removed[:10]:
            lines.append('  - {}[{}]'.format(
                obj.get('name', ''), obj.get('class', ''),
            ))
        if len(removed) > 10:
            lines.append('  … 另有 {} 项省略'.format(len(removed) - 10))

    moved = diff.get('moved') or []
    if moved:
        lines.append('位置变化 ({}):'.format(len(moved)))
        for m in moved[:10]:
            from_pos = m.get('from')
            to_pos = m.get('to')

            def _fmt(p):
                if isinstance(p, (list, tuple)) and len(p) == 3:
                    return '({:.2f}, {:.2f}, {:.2f})'.format(*p)
                return '?'

            lines.append('  ~ {}: {} → {}'.format(
                m.get('name', ''), _fmt(from_pos), _fmt(to_pos),
            ))
        if len(moved) > 10:
            lines.append('  … 另有 {} 项省略'.format(len(moved) - 10))

    if diff.get('selection_changed'):
        lines.append('选择集已变更')

    lines.append(
        '→ 请对照你本轮的预期，判断上述变化是否符合任务要求。'
        '如果发现遗漏或错误（例如对象未按预期落位），请立即修正；'
        '如果一切符合预期，直接给出最终回复即可。'
    )
    return '\n'.join(lines)


__all__ = [
    'build_scene_snapshot',
    'snapshot_to_prompt_text',
    'diff_snapshots',
    'diff_to_prompt_text',
]
