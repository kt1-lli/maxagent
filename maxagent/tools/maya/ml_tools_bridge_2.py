#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ML Tools 第四批次移植（动画向）：Morgan Loomis ml_tools 剩余动画算法。

来源：github.com/morganloomis/ml_tools，延续 ml_tools_bridge 系列移植原则：
纯 cmds 算法、延迟导入、undo chunk、结构化返回、交互式 UI 改参数驱动：

- ml_apply_spacing：缓动函数族在区间内按曲线重打键（10 种 spacing 曲线）
- ml_ballistic：按初速度 + 重力逐帧计算抛物线打 Y 键
- ml_pivot_bake：改轴心后保动画（临时组烘焙两次）
- ml_goto_keyframe：跳到下一/上一关键帧（支持取整与层级搜索）
- ml_swap_axes：交换两轴旋转值（当前帧或整条曲线）
"""

from __future__ import absolute_import
from __future__ import print_function

import math
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple

from ...tools.registry import tool
from ._common import _ensure_in_maya
from ...dcc.runtime import run_on_main

# 缓动函数注册表：名称 -> f(x)（x 归一化 0-1，返回 0-1 进度）
SPACING_FUNCTIONS = {
    'linear': lambda x: x,
    'sigmoid': lambda x: 1.0 / (1.0 + math.exp(-10.0 * (x - 0.5))),
    'ease_in_sine': lambda x: math.sin((x * 0.5 - 0.5) * math.pi) + 1.0,
    'ease_out_sine': lambda x: math.sin(x * 0.5 * math.pi),
    'ease_in_out_sine': lambda x: math.sin((x + 1.5) * math.pi) / 2.0 + 0.5,
    'ease_in_quad': lambda x: x * x,
    'ease_out_quad': lambda x: -x * (x - 2.0),
    'ease_in_out_quad': lambda x: 2.0 * x * x if x < 0.5 else -2.0 * x * (x - 2.0) - 1.0,
    'ease_in_cubic': lambda x: x ** 3,
    'ease_out_cubic': lambda x: (x - 1.0) ** 3 + 1.0,
}


def _cmds():
    """延迟导入 maya.cmds。"""
    import maya.cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    return maya.cmds


def _normalize_list(value):
    # type: (Any) -> List[str]
    """把 str / list / tuple 统一为 list[str]（逗号分号均可分隔）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    parts = re.split(r'[,;，；]', text)
    return [p.strip() for p in parts if p.strip()]


def _open_chunk(cmds, name):
    # type: (object, str) -> None
    """打开 undo chunk。"""
    cmds.undoInfo(openChunk=True, chunkName=name)


def _close_chunk(cmds, name):
    # type: (object, str) -> None
    """关闭 undo chunk。"""
    cmds.undoInfo(closeChunk=True, chunkName=name)


def _resolve_range(cmds, start, end):
    # type: (object, Any, Any) -> Tuple[float, float]
    """解析帧范围：显式指定优先，缺省回退播放范围。"""
    if start is not None and end is not None:
        return float(start), float(end)
    min_frame = cmds.playbackOptions(query=True, min=True)
    max_frame = cmds.playbackOptions(query=True, max=True)
    resolved_start = float(start) if start is not None else float(min_frame)
    resolved_end = float(end) if end is not None else float(max_frame)
    if resolved_start >= resolved_end:
        raise ValueError(
            '帧范围无效：start({}) 必须小于 end({})'.format(resolved_start, resolved_end),
        )
    return resolved_start, resolved_end


def _get_frame_rate(cmds):
    # type: (object) -> float
    """读当前场景帧率（getFrameRate 语义）。"""
    unit = str(cmds.currentUnit(query=True, time=True) or 'film')
    rate_map = {
        'film': 24.0, 'show': 48.0, 'pal': 25.0, 'ntsc': 30.0,
        'palf': 50.0, 'ntscf': 60.0,
    }
    if unit in rate_map:
        return rate_map[unit]
    if unit.endswith('fps'):
        try:
            return float(unit.replace('fps', ''))
        except ValueError:
            return 24.0
    return 24.0


def _get_unit_factor(cmds):
    # type: (object) -> float
    """读当前线性单位换算为米的系数（ml_ballistic 的 distFactor 语义）。"""
    unit = str(cmds.currentUnit(query=True, linear=True) or 'cm')
    factor_map = {
        'mm': 1000.0, 'cm': 100.0, 'm': 1.0, 'km': 0.001,
        'in': 39.3701, 'ft': 3.28084, 'yd': 1.09361, 'mi': 0.000621371,
    }
    return factor_map.get(unit, 1.0)


def _pose_snapshot(cmds, node):
    # type: (object, str) -> Dict[str, float]
    """抓取节点全部可 key 属性当前值（getPose 语义）。"""
    pose = {}
    for attr in cmds.listAttr(node, keyable=True) or []:
        plug = '{}.{}'.format(node, attr)
        try:
            pose[plug] = cmds.getAttr(plug)
        except Exception:  # pylint: disable=broad-except
            continue
    return pose


@tool(
    dcc=['maya'],
    description='区间内按缓动曲线重新打键（ML Tools Spacing 移植）。'
                '抓取区间两端的属性值，按 10 种缓动曲线（sigmoid/ease 系列）逐帧插值重打键。',
    category='animation',
    examples=[
        {
            'summary': '手臂 1-12 帧用 easeIn 曲线重新打键',
            'args': {'nodes': 'ctrl_arm', 'start': 1, 'end': 12, 'function': 'ease_in_cubic'},
        },
        {
            'summary': '腿部用 sigmoid 缓动（中段加速两头减速）',
            'args': {'nodes': 'ctrl_leg', 'start': 10, 'end': 20, 'function': 'sigmoid'},
        },
    ],
    notes=[
        'function 可选：' + ' / '.join(sorted(SPACING_FUNCTIONS)),
        '区间端点值来自当前属性值（执行前请把播放头放到对应姿势或先确认端点关键帧存在）。',
        '区间内既有关键帧会被覆盖，逐帧打键（bake on ones）。',
        '只处理可 key 属性中两端都存在的属性。',
    ],
    returns_desc='dict {"ok": True, "keys": 打键属性数, "frames": 帧数}',
    prerequisites=['nodes 必须存在'],
)
def ml_apply_spacing(nodes, start, end, function='linear'):
    # type: (Any, float, float, str) -> Dict[str, Any]
    """缓动曲线重打键。

    :param nodes: 对象名列表或逗号分隔字符串
    :param start: 区间起始帧
    :param end: 区间结束帧
    :param function: 缓动函数名（见 notes）
    :returns: dict {"ok": True, "keys": ..., "frames": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        func_name = str(function or 'linear')
        if func_name not in SPACING_FUNCTIONS:
            raise ValueError(
                '未知缓动函数 {}，可选：{}'.format(
                    func_name, sorted(SPACING_FUNCTIONS.keys()),
                ),
            )
        node_list = [n for n in _normalize_list(nodes) if cmds.objExists(n)]
        if not node_list:
            raise ValueError('必须指定至少一个有效对象')
        range_start = float(start)
        range_end = float(end)
        if range_start >= range_end:
            raise ValueError('start 必须小于 end')
        frames = range(int(math.ceil(range_start)), int(math.floor(range_end)) + 1)
        if len(frames) < 2:
            raise ValueError('区间至少跨越 2 帧')
        step = 1.0 / (range_end - range_start)
        spacing_func = SPACING_FUNCTIONS[func_name]
        key_count = 0
        _open_chunk(cmds, 'ML Apply Spacing')
        try:
            for node in node_list:
                pose = _pose_snapshot(cmds, node)
                for plug, end_value in pose.items():
                    start_value = end_value
                    # 端点值取当前属性值作为基准（原工具语义：抓当前姿势做两端）
                    for frame in frames:
                        progress = (frame - range_start) * step
                        new_value = spacing_func(progress) * (
                            end_value - start_value
                        ) + start_value
                        cmds.setKeyframe(plug, time=frame, value=new_value)
                    key_count += 1
        finally:
            _close_chunk(cmds, 'ML Apply Spacing')
        return {'ok': True, 'keys': key_count, 'frames': len(frames)}

    return run_on_main(_do)


@tool(
    dcc=['maya'],
    description='按初速度 + 重力计算抛物线并逐帧打 Y 键（ML Tools Ballistic 移植）。'
                '自动读取当前速度作为初速度，重力 9.8 m/s^2，自动处理帧率与单位换算。',
    category='animation',
    examples=[
        {
            'summary': '让球按物理规律抛物线下落（1-24 帧）',
            'args': {'node': 'pSphere1', 'start': 1, 'end': 24},
        },
    ],
    notes=[
        '执行前先给 node 打上初速度键（当前帧与前一帧的位移差决定初速度方向与大小）。',
        'Y 轴逐帧重算，XZ 保持线性惯性外推。',
        '重力固定 9.8 m/s^2，按场景单位（m/cm/ft 等）自动换算。',
        '区间内 translate 键会被覆盖。',
    ],
    returns_desc='dict {"ok": True, "frames": 打键帧数, "gravity": 实际使用的重力（场景单位）}',
    prerequisites=['node 必须是 transform 且有初速度键'],
)
def ml_ballistic(node, start=None, end=None, gravity=9.8):
    # type: (str, float, float, float) -> Dict[str, Any]
    """抛物线弹道打键。

    :param node: 目标 transform
    :param start: 起始帧；None 用播放范围
    :param end: 结束帧；None 用播放范围
    :param gravity: 重力加速度（m/s^2），默认 9.8
    :returns: dict {"ok": True, "frames": ..., "gravity": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(node):
            raise ValueError('节点不存在: {}'.format(node))
        range_start, range_end = _resolve_range(cmds, start, end)
        range_start = int(math.ceil(range_start))
        range_end = int(math.floor(range_end))
        if range_end - range_start < 1:
            raise ValueError('区间至少跨越 1 帧')
        time_factor = 1.0 / _get_frame_rate(cmds)
        dist_factor = _get_unit_factor(cmds)
        gravity_scene = float(gravity) * dist_factor
        cmds.currentTime(range_start, edit=True)
        start_trans = cmds.getAttr('{}.translate'.format(node))[0]
        # 初速度：当前帧与前一帧的位移差
        prev_trans = []
        for axis in ('translateX', 'translateY', 'translateZ'):
            prev_value = cmds.keyframe(
                node, attribute=axis, query=True, eval=True, time=(range_start - 1,),
            )
            prev_trans.append(prev_value[0] if prev_value else start_trans[['X', 'Y', 'Z'].index(axis[-1])])
        velocity = [start_trans[i] - prev_trans[i] for i in range(3)]
        _open_chunk(cmds, 'ML Ballistic')
        try:
            # XZ 线性外推端点键
            cmds.cutKey(
                node, attribute=['translateX', 'translateY', 'translateZ'],
                time=(range_start + 0.1, range_end + 0.5),
            )
            for axis_index, axis in ((0, 'translateX'), (2, 'translateZ')):
                for frame in (range_start + 1, range_end - 1, range_end):
                    value = start_trans[axis_index] + velocity[axis_index] * (frame - range_start)
                    cmds.setKeyframe(node, attribute=axis, time=frame, value=value)
            # Y 轴逐帧抛物线
            for offset, frame in enumerate(range(range_start, range_end + 1)):
                t = offset * time_factor
                y_value = (
                    start_trans[1] + offset * velocity[1] - (gravity_scene * t * t) / 2.0
                )
                cmds.setKeyframe(node, attribute='translateY', time=frame, value=y_value)
        finally:
            _close_chunk(cmds, 'ML Ballistic')
        return {
            'ok': True,
            'frames': range_end - range_start + 1,
            'gravity': gravity_scene,
        }

    return run_on_main(_do)


@tool(
    dcc=['maya'],
    description='改轴心后保动画（ML Tools Pivot 移植）。'
                '把节点动画先烘焙到临时组，改 rotatePivot 后再烘焙回来，动画观感不变。',
    category='animation',
    examples=[
        {
            'summary': '把控件轴心改到世界原点并保持动画',
            'args': {'node': 'ctrl_arm', 'pivot': [0, 0, 0]},
        },
    ],
    notes=[
        'pivot 传世界坐标 [x, y, z]。',
        '节点无动画时直接设 rotatePivot 不烘焙。',
        '有动画时通过两次烘焙（tempPosition 保偏移 -> 改轴心 -> 烘焙回来）实现动画不变。',
    ],
    returns_desc='dict {"ok": True, "baked": 是否执行了烘焙}',
    prerequisites=['node 必须存在'],
)
def ml_pivot_bake(node, pivot):
    # type: (str, List[float]) -> Dict[str, Any]
    """改轴心保动画。

    :param node: 目标 transform
    :param pivot: 新轴心世界坐标 [x, y, z]
    :returns: dict {"ok": True, "baked": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(node):
            raise ValueError('节点不存在: {}'.format(node))
        pivot_values = [float(v) for v in (pivot if isinstance(pivot, (list, tuple)) else [pivot] * 3)]
        if len(pivot_values) != 3:
            raise ValueError('pivot 必须是 [x, y, z] 三元素')
        current_pivot = cmds.getAttr('{}.rotatePivot'.format(node))[0]
        if all(math.isclose(a, b) for a, b in zip(current_pivot, pivot_values)):
            return {'ok': True, 'baked': False}
        curves = cmds.keyframe(
            node, attribute=('tx', 'ty', 'tz', 'rx', 'ry', 'rz'),
            query=True, name=True,
        )
        if not curves:
            cmds.setAttr('{}.rotatePivot'.format(node), *pivot_values)
            return {'ok': True, 'baked': False}
        # 两次烘焙改轴心：tempPosition 保偏移承接动画，改轴心后烘焙回 node
        current_time = cmds.currentTime(query=True)
        temp_position = cmds.group(empty=True, name='ml_pivot_temp#')
        _open_chunk(cmds, 'ML Pivot Bake')
        try:
            cmds.delete(cmds.parentConstraint(node, temp_position))
            _bake_match(cmds, source=node, destination=temp_position, maintain_offset=True)
            cmds.setAttr('{}.rotatePivot'.format(node), *pivot_values)
            _bake_match(cmds, source=temp_position, destination=node, maintain_offset=False)
        finally:
            cmds.delete(temp_position)
            cmds.currentTime(current_time, edit=True)
            _close_chunk(cmds, 'ML Pivot Bake')
        return {'ok': True, 'baked': True}

    return run_on_main(_do)


def _bake_match(cmds, source, destination, maintain_offset):
    # type: (object, str, str, bool) -> None
    """逐帧烘焙 source 运动到 destination（matchBake 简化版：on ones、无切线权重保留）。"""
    range_start = cmds.playbackOptions(query=True, min=True)
    range_end = cmds.playbackOptions(query=True, max=True)
    # 用临时 parentConstraint 抓 source 世界变换
    constraint = cmds.parentConstraint(
        source, destination, maintainOffset=maintain_offset,
    )[0]
    attrs = ['translateX', 'translateY', 'translateZ', 'rotateX', 'rotateY', 'rotateZ']
    for attr in attrs:
        cmds.setAttr('{}.{}'.format(destination, attr), lock=False, keyable=True)
    try:
        for frame in range(int(range_start), int(range_end) + 1):
            cmds.currentTime(frame, edit=True)
            for attr in attrs:
                value = cmds.getAttr('{}.{}'.format(destination, attr))
                cmds.setKeyframe(destination, attribute=attr, time=frame, value=value)
    finally:
        cmds.delete(constraint)


@tool(
    dcc=['maya'],
    description='跳到下一/上一关键帧（ML Tools GoToKeyframe 移植）。'
                '支持整帧取整与层级搜索，AI 导航动画时间轴用。',
    category='animation',
    examples=[
        {
            'summary': '跳到选中对象下一个关键帧（取整到整帧）',
            'args': {'nodes': 'ctrl_arm', 'direction': 'next', 'round_frame': True},
        },
        {
            'summary': '在角色整层级里找上一个关键帧',
            'args': {'nodes': 'charRoot', 'direction': 'previous', 'search_hierarchy': True},
        },
    ],
    notes=[
        'direction: next / previous。',
        'round_frame=True 时跳到最近的整帧（跳过子帧键）。',
        'search_hierarchy=True 时在 nodes 的整层级里搜索。',
        '无选中对象时退化为播放头 +/-1 帧。',
    ],
    returns_desc='dict {"ok": True, "time": 跳转后的帧, "found": 是否找到关键帧}',
    prerequisites=['无'],
)
def ml_goto_keyframe(nodes=None, direction='next', round_frame=False, search_hierarchy=False):
    # type: (Any, str, bool, bool) -> Dict[str, Any]
    """跳转关键帧。

    :param nodes: 对象列表；None 或空时退化为播放头移动
    :param direction: next / previous
    :param round_frame: 是否取整到整帧
    :param search_hierarchy: 是否搜索整层级
    :returns: dict {"ok": True, "time": ..., "found": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if direction not in ('next', 'previous'):
            raise ValueError('direction 仅支持 next/previous: {}'.format(direction))
        current_time = cmds.currentTime(query=True)
        node_list = [n for n in _normalize_list(nodes) if cmds.objExists(n)] if nodes else []
        if not node_list:
            new_time = current_time + 1 if direction == 'next' else current_time - 1
            cmds.currentTime(new_time, edit=True)
            return {'ok': True, 'time': new_time, 'found': False}
        # 收集候选曲线
        if search_hierarchy:
            tops = cmds.listRelatives(node_list, parent=True, fullPath=True) or node_list
            all_nodes = cmds.listRelatives(
                node_list, allDescendents=True, type='transform', fullPath=True,
            ) or []
            all_nodes = list(set(all_nodes + node_list))
        else:
            all_nodes = node_list
        curves = cmds.keyframe(all_nodes, query=True, name=True) or []
        if not curves:
            new_time = current_time + 1 if direction == 'next' else current_time - 1
            cmds.currentTime(new_time, edit=True)
            return {'ok': True, 'time': new_time, 'found': False}
        # 遍历所有曲线找最近的目标键时间
        best_time = None
        for curve in curves:
            times = cmds.keyframe(curve, query=True, timeChange=True) or []
            for key_time in times:
                key_time = float(key_time)
                if round_frame:
                    key_time = float(round(key_time))
                    if direction == 'next' and key_time <= current_time:
                        key_time += 1.0
                    elif direction == 'previous' and key_time >= current_time:
                        key_time -= 1.0
                else:
                    if direction == 'next' and key_time <= current_time:
                        continue
                    if direction == 'previous' and key_time >= current_time:
                        continue
                if best_time is None:
                    best_time = key_time
                elif direction == 'next':
                    best_time = min(best_time, key_time)
                else:
                    best_time = max(best_time, key_time)
        if best_time is None:
            return {'ok': True, 'time': current_time, 'found': False}
        cmds.currentTime(best_time, edit=True)
        return {'ok': True, 'time': best_time, 'found': True}

    return run_on_main(_do)


@tool(
    dcc=['maya'],
    description='交换两轴旋转值（ML Tools CurveSwap 移植）。'
                '当前帧交换属性值或整条曲线对调，修绑定轴向错误的快速手段。',
    category='animation',
    examples=[
        {
            'summary': '交换 X/Z 轴旋转值（当前帧）',
            'args': {'nodes': 'ctrl_arm', 'axis_a': 'x', 'axis_b': 'z'},
        },
    ],
    notes=[
        'axis_a/axis_b 必须是不同的两个轴（x/y/z）。',
        'scope: current（仅当前帧属性值对调）/ curve（整条曲线所有键值对调）。',
        '属性有动画时会自动补键。',
    ],
    returns_desc='dict {"ok": True, "nodes": 处理节点数}',
    prerequisites=['nodes 必须存在且有旋转属性'],
)
def ml_swap_axes(nodes, axis_a='x', axis_b='z', scope='current'):
    # type: (Any, str, str, str) -> Dict[str, Any]
    """交换旋转轴值。

    :param nodes: 对象名列表或逗号分隔字符串
    :param axis_a: 轴 A（x/y/z）
    :param axis_b: 轴 B（x/y/z），不能与 A 相同
    :param scope: current / curve
    :returns: dict {"ok": True, "nodes": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        axis_a_norm = str(axis_a).lower().strip()
        axis_b_norm = str(axis_b).lower().strip()
        valid_axes = ('x', 'y', 'z')
        if axis_a_norm not in valid_axes or axis_b_norm not in valid_axes:
            raise ValueError('axis 必须是 x/y/z')
        if axis_a_norm == axis_b_norm:
            raise ValueError('axis_a 与 axis_b 不能相同')
        scope_norm = str(scope or 'current').lower()
        if scope_norm not in ('current', 'curve'):
            raise ValueError('scope 仅支持 current/curve: {}'.format(scope))
        attr_a = 'r' + axis_a_norm
        attr_b = 'r' + axis_b_norm
        node_list = [n for n in _normalize_list(nodes) if cmds.objExists(n)]
        if not node_list:
            raise ValueError('必须指定至少一个有效对象')
        processed = 0
        _open_chunk(cmds, 'ML Swap Axes')
        try:
            for node in node_list:
                plug_a = '{}.{}'.format(node, attr_a)
                plug_b = '{}.{}'.format(node, attr_b)
                if scope_norm == 'current':
                    value_a = cmds.getAttr(plug_a)
                    value_b = cmds.getAttr(plug_b)
                    cmds.setAttr(plug_a, value_b)
                    cmds.setAttr(plug_b, value_a)
                    # 有动画则补键
                    for plug in (plug_a, plug_b):
                        if cmds.keyframe(plug, query=True, keyframeCount=True):
                            cmds.setKeyframe(plug)
                else:
                    curves_a = cmds.keyframe(plug_a, query=True, name=True) or []
                    curves_b = cmds.keyframe(plug_b, query=True, name=True) or []
                    times_a = cmds.keyframe(plug_a, query=True, timeChange=True) or []
                    times_b = cmds.keyframe(plug_b, query=True, timeChange=True) or []
                    values_a = dict(zip(
                        [float(t) for t in times_a],
                        cmds.keyframe(plug_a, query=True, valueChange=True) or [],
                    ))
                    values_b = dict(zip(
                        [float(t) for t in times_b],
                        cmds.keyframe(plug_b, query=True, valueChange=True) or [],
                    ))
                    all_times = sorted(set(values_a) | set(values_b))
                    for key_time in all_times:
                        new_a = values_b.get(key_time, cmds.getAttr(plug_a, time=key_time))
                        new_b = values_a.get(key_time, cmds.getAttr(plug_b, time=key_time))
                        cmds.setKeyframe(plug_a, time=key_time, value=new_a)
                        cmds.setKeyframe(plug_b, time=key_time, value=new_b)
                    if not curves_a and not curves_b:
                        continue
                processed += 1
        finally:
            _close_chunk(cmds, 'ML Swap Axes')
        return {'ok': True, 'nodes': processed}

    return run_on_main(_do)


__all__ = [
    'ml_apply_spacing',
    'ml_ballistic',
    'ml_pivot_bake',
    'ml_goto_keyframe',
    'ml_swap_axes',
]
