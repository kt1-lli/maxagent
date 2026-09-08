#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Max 端补充工具（源：ADN 3dsMax-Python-HowTos + BsKeyTools 增量算法）。

三个场景操作小工具（源 ADN-DevTech/3dsMax-Python-HowTos，教学级 -> 工具化）：
- max_transform_lock：setTransformLockFlags 批量锁/解锁变换（分通道）
- max_remove_materials：批量清空选中/全部对象材质
- max_analyze_object_speed：逐帧速度/加速度分析（speedsheet 增强，返回结构化数据）
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import List

from ...logger import get_logger
from ...runtime_helpers import IN_MAX
from ...runtime_helpers import rt
from ...tools.registry import tool


logger = get_logger(__name__)


def _ensure_in_max():
    """校验当前在 3ds Max 环境内。"""
    if not IN_MAX or rt is None:
        raise RuntimeError('当前不在 3ds Max 环境内，无法执行该工具')


def _normalize_names(value):
    # type: (Any) -> List[str]
    """把 str / list / tuple 统一为 list[str]。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [p.strip() for p in text.split(',') if p.strip()]


def _resolve_nodes(names, use_selection_if_empty=True):
    # type: (Any, bool) -> List[Any]
    """名字列表 -> 节点对象列表；为空时回退当前选择。"""
    name_list = _normalize_names(names)
    nodes = []
    if name_list:
        for name in name_list:
            node = rt.getNodeByName(name)
            if node is not None:
                nodes.append(node)
            else:
                logger.warning('节点不存在: %s', name)
    elif use_selection_if_empty:
        nodes = [n for n in (rt.selection or [])]
    return nodes


@tool(
    dcc=['3dsmax'],
    description='批量锁定/解锁对象变换（ADN HowTos transformlock 工具化）。'
                '支持分通道（all/pos/rot/scale），AI 防止误操作场景时使用。',
    category='transform',
    examples=[
        {
            'summary': '锁定选中对象全部变换',
            'args': {'lock': True},
        },
        {
            'summary': '解锁指定对象的旋转与缩放（保留位置锁定）',
            'args': {'names': 'Box001,Box002', 'lock': False, 'channels': 'rotation,scale'},
        },
    ],
    notes=[
        'names 为空时操作当前选择集。',
        'channels 可选：all / position / rotation / scale，可多选（逗号分隔）。',
        '锁定状态写进对象变换锁标志，保存场景后仍生效。',
    ],
    returns_desc='dict {"ok": True, "locked": 处理对象数, "channels": 实际应用的通道}',
    prerequisites=['names 或当前选择至少一项'],
)
def max_transform_lock(names=None, lock=True, channels='all'):
    # type: (Any, bool, Any) -> Dict[str, Any]
    """批量变换锁定。

    :param names: 对象名列表或逗号分隔字符串；空则用当前选择
    :param lock: True 锁定 / False 解锁
    :param channels: 通道列表（all/position/rotation/scale）
    :returns: dict {"ok": True, "locked": ..., "channels": [...]}
    """
    _ensure_in_max()

    valid_channels = ('all', 'position', 'rotation', 'scale')
    channel_list = [c.strip().lower() for c in _normalize_names(channels)] or ['all']
    for channel in channel_list:
        if channel not in valid_channels:
            raise ValueError(
                '未知通道 {}，可选：{}'.format(channel, list(valid_channels)),
            )
    nodes = _resolve_nodes(names)
    if not nodes:
        raise ValueError('没有可操作对象（names 为空且无选择集）')
    # setTransformLockFlags 需要 all 或按位组合标志名
    if 'all' in channel_list:
        flag = 'all'
    else:
        flag = list(channel_list)
    processed = 0
    for node in nodes:
        rt.setTransformLockFlags(node, rt.Name(flag) if isinstance(flag, str) else flag)
        processed += 1
    return {'ok': True, 'locked': processed, 'channels': channel_list}


@tool(
    dcc=['3dsmax'],
    description='批量清空对象材质（ADN HowTos removeallmaterials 工具化）。'
                '支持选中集或全场景，可选同时删除未使用材质。',
    category='material',
    examples=[
        {
            'summary': '清空当前选中对象的材质',
            'args': {},
        },
        {
            'summary': '清空全场景材质并删除未使用材质',
            'args': {'scope': 'scene', 'purge_unused': True},
        },
    ],
    notes=[
        'scope: selection（当前选择）/ scene（全部对象）。',
        'purge_unused=True 时额外删除场景中未被任何对象引用的材质。',
    ],
    returns_desc='dict {"ok": True, "cleared": 清理对象数, "purged": 删除的未使用材质数}',
    prerequisites=['selection 模式下需有选中对象'],
)
def max_remove_materials(scope='selection', purge_unused=False):
    # type: (str, bool) -> Dict[str, Any]
    """批量清空材质。

    :param scope: selection / scene
    :param purge_unused: 是否删除未使用材质
    :returns: dict {"ok": True, "cleared": ..., "purged": ...}
    """
    _ensure_in_max()
    scope_norm = str(scope or 'selection').lower()
    if scope_norm not in ('selection', 'scene'):
        raise ValueError('scope 仅支持 selection/scene: {}'.format(scope))
    nodes = (
        [n for n in (rt.selection or [])]
        if scope_norm == 'selection'
        else [n for n in rt.objects]
    )
    if not nodes:
        raise ValueError('没有可清理对象（selection 模式需先选中）')
    cleared = 0
    for node in nodes:
        try:
            node.material = None
            cleared += 1
        except (AttributeError, RuntimeError) as exc:
            logger.warning('清空材质失败 %s: %s', node.name, exc)
    purged = 0
    if purge_unused:
        used = set()
        for obj in rt.objects:
            material = obj.material
            if material is not None:
                used.add(str(material))
        for material in list(rt.sceneMaterials):
            if str(material) not in used:
                try:
                    rt.delete(material)
                    purged += 1
                except RuntimeError:
                    continue
    return {'ok': True, 'cleared': cleared, 'purged': purged}


@tool(
    dcc=['3dsmax'],
    description='逐帧分析对象速度/加速度（ADN HowTos speedsheet 增强）。'
                '不只导出文本，而是返回结构化数据（每帧速度、加速度、峰值与均值），供 AI 判断动画节奏。',
    category='animation',
    examples=[
        {
            'summary': '分析角色根骨骼在 1-24 帧的运动速度',
            'args': {'names': 'Bip001', 'start': 1, 'end': 24},
        },
    ],
    notes=[
        'names 多对象时分析其公共中心点（selection.center 语义）。',
        'start/end 缺省用动画范围。',
        '速度单位 = 场景单位/秒（乘以帧率）。',
        '加速度由相邻帧速度差分得出。',
    ],
    returns_desc=(
        'dict {"ok": True, "frames": 帧数, "average_speed": 均速, '
        '"peak_speed": 峰值, "per_frame": [{frame, speed, acceleration}]}'
    ),
    prerequisites=['names 至少一个有效对象'],
)
def max_analyze_object_speed(names, start=None, end=None):
    # type: (Any, int, int) -> Dict[str, Any]
    """逐帧速度分析。

    :param names: 对象名列表或逗号分隔字符串
    :param start: 起始帧；None 用动画范围起点
    :param end: 结束帧；None 用动画范围终点
    :returns: dict {"ok": True, "frames": ..., "average_speed": ..., "peak_speed": ..., "per_frame": [...]}
    """
    _ensure_in_max()
    nodes = _resolve_nodes(names, use_selection_if_empty=False)
    if not nodes:
        raise ValueError('必须指定至少一个有效对象')
    anim_start = int(rt.animationRange.start)
    anim_end = int(rt.animationRange.end)
    range_start = int(start) if start is not None else anim_start
    range_end = int(end) if end is not None else anim_end
    if range_start >= range_end:
        raise ValueError('start 必须小于 end: {} >= {}'.format(range_start, range_end))
    frame_rate = rt.FrameRate
    saved_selection = [n for n in (rt.selection or [])]
    rt.select(nodes)
    # select 后重新取选择集（真实 Max 里 selection 是带 center 的包装对象）
    live_selection = rt.selection
    per_frame = []
    previous_speed = None
    total_speed = 0.0
    peak_speed = 0.0
    try:
        for frame in range(range_start, range_end + 1):
            with rt.attime(frame):
                current_center = live_selection.center
            if frame > range_start:
                with rt.attime(frame - 1):
                    last_center = live_selection.center
                frame_speed = rt.distance(current_center, last_center) * frame_rate
            else:
                frame_speed = 0.0
            acceleration = (
                frame_speed - previous_speed if previous_speed is not None else 0.0
            )
            per_frame.append({
                'frame': frame,
                'speed': round(frame_speed, 4),
                'acceleration': round(acceleration, 4),
            })
            total_speed += frame_speed
            peak_speed = max(peak_speed, frame_speed)
            previous_speed = frame_speed
    finally:
        if saved_selection:
            rt.select(saved_selection)
    frame_count = range_end - range_start + 1
    average_speed = total_speed / float(frame_count)
    return {
        'ok': True,
        'frames': frame_count,
        'average_speed': round(average_speed, 4),
        'peak_speed': round(peak_speed, 4),
        'per_frame': per_frame,
    }


__all__ = [
    'max_transform_lock',
    'max_remove_materials',
    'max_analyze_object_speed',
]
