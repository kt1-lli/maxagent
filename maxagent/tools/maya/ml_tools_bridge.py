#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ML Tools 第三批次移植：Morgan Loomis ml_tools 动画/绑定工具集。

来源：github.com/morganloomis/ml_tools（MIT 风格许可），延续 gt_tools_bridge
系列移植原则：纯 cmds/om2 算法、延迟导入、undo chunk、结构化返回。
原库大量依赖选中状态与 Dragger 交互，本移植统一改为显式参数驱动，
便于 AI 直接调用：

- ml_breakdown：中间帧权重（向下一帧/上一帧/均值加权当前关键帧）
- ml_hold：创建保持帧（next/previous/current/average 四种取值模式）
- ml_snap：迭代矩阵对齐（支持偏移矩阵与逐分量保留）
- ml_match_bake：源运动烘焙到目标，保留关键帧时间与切线（matchBake）
- ml_copy_animation：节点间直接复制动画（范围临时键 + rotateOrder 同步）
- ml_minimize_rotation：欧拉最短路径化（临时帧 + filterCurve）
- analyze_ml_rotation_orders：六种旋转次序万向锁检测与推荐
- transfer_ml_keytimes：把源关键帧时间轴传递到目标（烘焙 + 剪除）
- constrain_ml_skinned_vertex：蒙皮顶点矩阵约束（blendMatrix 节点链）
- sample_ml_arc：逐帧弧线采样（世界/相机空间 + 间距统计）
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

# 旋转次序索引与字符串对照（Maya rotateOrder 枚举顺序）
ROTATE_ORDERS = ['xyz', 'yzx', 'zxy', 'xzy', 'yxz', 'zyx']

# animCurve 节点类型前缀
_ANIM_CURVE_PREFIX = 'animCurve'


def _cmds():
    """延迟导入 maya.cmds。"""
    import maya.cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    return maya.cmds


def _om2():
    """延迟导入 maya.api.OpenMaya（om2）。"""
    from maya.api import OpenMaya  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    return OpenMaya


def _oma2():
    """延迟导入 maya.api.OpenMayaAnim（om2 动画模块）。"""
    from maya.api import OpenMayaAnim  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    return OpenMayaAnim


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


def _get_world_matrix(cmds, node):
    # type: (object, str) -> List[float]
    """读节点世界矩阵（16 元素行主序）。"""
    return list(cmds.getAttr('{}.worldMatrix'.format(node)))


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


def _get_hold_tangents(cmds):
    # type: (object) -> Tuple[str, str]
    """按当前全局出切线设置推导保持帧的最佳切线组合（getHoldTangentType 语义）。"""
    try:
        tangent_type = cmds.keyTangent(query=True, g=True, ott=True)[0]
    except Exception:  # pylint: disable=broad-except
        return 'auto', 'auto'
    if tangent_type == 'linear':
        return 'linear', 'linear'
    if tangent_type == 'step':
        return 'linear', 'step'
    if tangent_type in ('plateau', 'spline'):
        return 'plateau', 'plateau'
    return 'auto', 'auto'


def _get_curves(cmds, node):
    # type: (object, str) -> List[str]
    """取节点上的动画曲线列表。"""
    return cmds.keyframe(node, query=True, name=True) or []


# ---------------------------------------------------------------------- #
# 1. 中间帧权重（Breakdown）
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='把关键帧值向下一帧/上一帧/前后均值按权重加权（ML Tools Breakdown 移植）。'
                '经典 pose-to-pose 加中间姿势工具。',
    category='animation',
    examples=[
        {
            'summary': '把手臂第 12 帧关键帧向下一帧加权 20%',
            'args': {'nodes': 'ctrl_arm', 'time': 12, 'direction': 'next', 'weight': 0.2},
        },
    ],
    notes=[
        'time 指定的帧上必须已有关键帧，否则该帧被跳过。',
        'direction: next（向下帧加权）/ previous（向上帧加权）/ average（向前后均值加权）。',
        'weight 0~1，0 不动 1 完全对齐目标帧；>1 允许过冲。',
    ],
    returns_desc='dict {"ok": True, "updated": [{curve, time, old, new}], "count": 数量}',
    prerequisites=['指定帧上应已有关键帧'],
)
def ml_breakdown(nodes, time=None, direction='next', weight=0.2):
    # type: (Any, Any, str, float) -> Dict[str, Any]
    """中间帧加权。

    :param nodes: 对象名列表或逗号分隔字符串
    :param time: 目标关键帧时间；None 用当前帧
    :param direction: next / previous / average
    :param weight: 加权系数
    :returns: dict {"ok": True, "updated": [...], "count": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if direction not in ('next', 'previous', 'average'):
            raise ValueError('direction 仅支持 next/previous/average: {}'.format(direction))
        node_list = [n for n in _normalize_list(nodes) if cmds.objExists(n)]
        if not node_list:
            raise ValueError('必须指定至少一个有效对象')
        target_times = _normalize_list(time)
        if not target_times:
            target_times = [cmds.currentTime(query=True)]
        weight_value = float(weight)
        updated = []
        _open_chunk(cmds, 'ML Breakdown')
        try:
            for node in node_list:
                for curve in _get_curves(cmds, node):
                    for frame in target_times:
                        frame = float(frame)
                        current = cmds.keyframe(
                            curve, time=(frame,), query=True, valueChange=True,
                        )
                        if not current:
                            continue
                        old_value = current[0]
                        next_time = cmds.findKeyframe(curve, time=(frame,), which='next')
                        next_value = cmds.keyframe(
                            curve, time=(next_time,), query=True, valueChange=True,
                        )[0]
                        prev_time = cmds.findKeyframe(curve, time=(frame,), which='previous')
                        prev_value = cmds.keyframe(
                            curve, time=(prev_time,), query=True, valueChange=True,
                        )[0]
                        if direction == 'next':
                            target_value = next_value
                        elif direction == 'previous':
                            target_value = prev_value
                        else:
                            target_value = (next_value + prev_value) / 2.0
                        new_value = old_value + (target_value - old_value) * weight_value
                        cmds.keyframe(curve, time=(frame,), edit=True, valueChange=new_value)
                        updated.append({
                            'curve': curve, 'time': frame,
                            'old': old_value, 'new': new_value,
                        })
        finally:
            _close_chunk(cmds, 'ML Breakdown')
        return {'ok': True, 'updated': updated, 'count': len(updated)}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 2. 保持帧（Hold）
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='创建保持帧：区间两端键值拉平并剪掉中间帧（ML Tools Hold 移植）。'
                '支持 next/previous/current/average 四种取值来源。',
    category='animation',
    examples=[
        {
            'summary': '把第 20-30 帧做成保持（值取自当前帧）',
            'args': {'nodes': 'ctrl_all', 'mode': 'current', 'start': 20, 'end': 30},
        },
        {
            'summary': '从当前帧到下一关键帧之间做保持（值取下一键）',
            'args': {'nodes': 'ctrl_arm', 'mode': 'next'},
        },
    ],
    notes=[
        'mode: next（取下一关键帧值）/ previous（取上一关键帧值）/ current（取当前帧属性值）/ average（区间均值）。',
        'start/end 省略时逐曲线以当前帧的前后关键帧为界。',
        '区间中间的既有关键帧会被剪除，两端切线按全局设置自动匹配。',
    ],
    returns_desc='dict {"ok": True, "curves": 受影响曲线数, "range": [start, end]}',
    prerequisites=['节点应存在；区间端点应有或可创建关键帧'],
)
def ml_hold(nodes, mode='next', start=None, end=None):
    # type: (Any, str, float, float) -> Dict[str, Any]
    """创建保持帧。

    :param nodes: 对象名列表或逗号分隔字符串
    :param mode: next / previous / current / average
    :param start: 区间起始帧；None 时逐曲线用当前帧前一键
    :param end: 区间结束帧；None 时逐曲线用当前帧后一键
    :returns: dict {"ok": True, "curves": ..., "range": [...]}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if mode not in ('next', 'previous', 'current', 'average'):
            raise ValueError('mode 仅支持 next/previous/current/average: {}'.format(mode))
        node_list = [n for n in _normalize_list(nodes) if cmds.objExists(n)]
        if not node_list:
            raise ValueError('必须指定至少一个有效对象')
        explicit_range = start is not None and end is not None
        if explicit_range and float(start) >= float(end):
            raise ValueError('start 必须小于 end')
        current_time = cmds.currentTime(query=True)
        itt, ott = _get_hold_tangents(cmds)
        affected = 0
        used_range = [float(start) if start is not None else None,
                      float(end) if end is not None else None]
        _open_chunk(cmds, 'ML Hold')
        try:
            for node in node_list:
                for curve in _get_curves(cmds, node):
                    # 逐曲线解析区间与取值
                    if explicit_range:
                        hold_start = float(start)
                        hold_end = float(end)
                    else:
                        hold_start = cmds.findKeyframe(
                            curve, time=(current_time,), which='previous',
                        )
                        hold_end = cmds.findKeyframe(
                            curve, time=(current_time,), which='next',
                        )
                    if mode in ('next', 'previous'):
                        find_from = hold_end if mode == 'next' else hold_start
                        source_time = cmds.findKeyframe(
                            curve, time=(find_from,), which=mode,
                        )
                        if mode == 'next' and source_time < hold_end:
                            source_time = hold_end
                        if mode == 'previous' and source_time > hold_start:
                            source_time = hold_start
                        value = cmds.keyframe(
                            curve, time=(source_time,), query=True, valueChange=True,
                        )[0]
                    elif mode == 'current':
                        plug = cmds.listConnections(
                            '{}.output'.format(curve), source=False, plugs=True,
                        )
                        value = cmds.getAttr(plug[0]) if plug else None
                        if value is None:
                            continue
                    else:
                        values = cmds.keyframe(
                            curve, time=(hold_start, hold_end),
                            query=True, valueChange=True,
                        ) or []
                        if not values:
                            continue
                        value = sum(values) / len(values)
                    # 区间中间帧剪除，两端拉平取值
                    if (hold_end - hold_start) > 1:
                        cmds.cutKey(curve, time=(hold_start + 0.1, hold_end - 0.1))
                    cmds.keyframe(
                        curve, time=(hold_start, hold_end),
                        edit=True, valueChange=value,
                    )
                    cmds.keyTangent(
                        curve, time=(hold_start, hold_end), edit=True, itt=itt, ott=ott,
                    )
                    affected += 1
                    if used_range[0] is None or hold_start < used_range[0]:
                        used_range[0] = hold_start
                    if used_range[1] is None or hold_end > used_range[1]:
                        used_range[1] = hold_end
        finally:
            _close_chunk(cmds, 'ML Hold')
        return {'ok': True, 'curves': affected, 'range': used_range}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 3. 迭代矩阵对齐（Snap）
# ---------------------------------------------------------------------- #
def _matrix_from_input(cmds, om2, target):
    # type: (object, object, Any) -> List[float]
    """把目标解析为 16 元素矩阵：对象名 / 16 列表 / 3 元素位置。"""
    if isinstance(target, (list, tuple)):
        if len(target) == 16:
            return [float(x) for x in target]
        if len(target) == 3:
            position = [float(x) for x in target]
            return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0,
                    position[0], position[1], position[2], 1]
        raise ValueError('target 列表长度必须是 3（位置）或 16（矩阵）: {}'.format(len(target)))
    if cmds.objExists(str(target)):
        return _get_world_matrix(cmds, str(target))
    raise ValueError('target 无法解析: {}'.format(target))


@tool(
    dcc=['maya'],
    description='把节点对齐到目标（对象/矩阵/位置），迭代求解直到世界矩阵收敛'
                '（ML Tools Snap 移植）。支持偏移矩阵与逐分量保留。',
    category='rigging',
    examples=[
        {
            'summary': '把手控件对齐到 IK 手柄',
            'args': {'node': 'ctrl_hand', 'target': 'ikHandle_arm'},
        },
        {
            'summary': '对齐但保留当前世界位置（仅对齐朝向）',
            'args': {'node': 'ctrl_foot', 'target': 'ikHandle_leg', 'position': False},
        },
    ],
    notes=[
        'target 可传对象名、16 元素矩阵或 3 元素世界坐标。',
        'offset 传对象名或 16 元素矩阵，先乘到目标矩阵上（用于保持偏移对齐）。',
        'position=False 保留节点当前世界位置仅对齐朝向；orientation=False 反之。',
        '迭代上限 4 次，收敛容差 0.0001（处理旋转次序导致的非正交误差）。',
    ],
    returns_desc='dict {"ok": True, "iterations": 实际迭代次数, "converged": 是否收敛}',
    prerequisites=['node 必须存在'],
)
def ml_snap(
    node,
    target,
    offset=None,
    position=True,
    orientation=True,
    iteration_max=4,
):
    # type: (str, Any, Any, bool, bool, int) -> Dict[str, Any]
    """迭代矩阵对齐。

    :param node: 待对齐节点
    :param target: 目标（对象名/矩阵/位置）
    :param offset: 偏移（对象名或矩阵）
    :param position: 是否对齐位置
    :param orientation: 是否对齐朝向
    :param iteration_max: 最大迭代次数
    :returns: dict {"ok": True, "iterations": ..., "converged": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        om2 = _om2()
        if not cmds.objExists(node):
            raise ValueError('节点不存在: {}'.format(node))
        matrix = _matrix_from_input(cmds, om2, target)
        if offset is not None:
            offset_matrix = _matrix_from_input(cmds, om2, offset)
            combined = om2.MMatrix(offset_matrix) * om2.MMatrix(matrix)
            matrix = [x for x in combined]
        if not position:
            # 仅对齐朝向：用当前世界位置覆盖平移分量
            current = _get_world_matrix(cmds, node)
            for index, value in zip((12, 13, 14), current[12:15]):
                matrix[index] = value
        if not orientation:
            # 仅对齐位置：用当前旋转-缩放块覆盖上半区
            current = _get_world_matrix(cmds, node)
            for row in range(3):
                for col in range(3):
                    matrix[row * 4 + col] = current[row * 4 + col]
        max_iter = max(1, int(iteration_max or 1))
        iterations = 0
        converged = False
        _open_chunk(cmds, 'ML Snap')
        try:
            for iteration in range(max_iter):
                iterations = iteration + 1
                cmds.xform(node, matrix=matrix, worldSpace=True)
                if max_iter == 1:
                    converged = True
                    break
                result = _get_world_matrix(cmds, node)
                if all(math.isclose(a, b, rel_tol=0.0001)
                       for a, b in zip(matrix, result)):
                    converged = True
                    break
        finally:
            _close_chunk(cmds, 'ML Snap')
        return {'ok': True, 'iterations': iterations, 'converged': converged}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 4. 保切线烘焙迁移（matchBake）
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='把源对象运动烘焙到目标，逐关键帧还原时间与切线（ML Tools matchBake 移植）。'
                '是 reparent/空间切换/约束转换的底层核心。',
    category='animation',
    examples=[
        {
            'summary': '把 IK 腿的运动迁移到 FK 控件并保留关键帧节奏',
            'args': {
                'sources': 'ikHandle_leg', 'destinations': 'ctrl_leg',
                'start': 1, 'end': 48,
            },
        },
    ],
    notes=[
        'sources 与 destinations 按顺序一一配对。',
        'bake_on_ones=True 时逐帧烘焙（不保留关键帧节奏）。',
        'maintain_offset=True 保留源与目标间的初始偏移。',
        '默认保留切线类型与切线权重，旋转曲线自动过欧拉滤波。',
        '帧范围缺省用播放范围。',
    ],
    returns_desc='dict {"ok": True, "pairs": 配对数, "frames": 烘焙帧数}',
    prerequisites=['源与目标必须存在'],
)
def ml_match_bake(
    sources,
    destinations,
    start=None,
    end=None,
    bake_on_ones=False,
    maintain_offset=False,
    preserve_tangent_weight=True,
    translate=True,
    rotate=True,
):
    # type: (Any, Any, float, float, bool, bool, bool, bool, bool) -> Dict[str, Any]
    """保切线烘焙迁移。

    :param sources: 源对象列表
    :param destinations: 目标对象列表（与源按序配对）
    :param start: 起始帧；None 用播放范围
    :param end: 结束帧；None 用播放范围
    :param bake_on_ones: 是否逐帧烘焙
    :param maintain_offset: 是否保持偏移
    :param preserve_tangent_weight: 是否保留切线权重
    :param translate: 是否迁移平移
    :param rotate: 是否迁移旋转
    :returns: dict {"ok": True, "pairs": ..., "frames": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        src_list = [n for n in _normalize_list(sources) if cmds.objExists(n)]
        dst_list = [n for n in _normalize_list(destinations) if cmds.objExists(n)]
        if not src_list or not dst_list:
            raise ValueError('源与目标都必须至少有一个有效对象')
        if len(src_list) != len(dst_list):
            raise ValueError(
                '源({})与目标({})数量不一致，无法按序配对'.format(len(src_list), len(dst_list)),
            )
        range_start, range_end = _resolve_range(cmds, start, end)
        attributes = []
        if translate:
            attributes.extend(['translateX', 'translateY', 'translateZ'])
        if rotate:
            attributes.extend(['rotateX', 'rotateY', 'rotateZ'])
        if not attributes:
            raise ValueError('translate 与 rotate 至少开启一项')
        # 每对源/目标的数据容器
        duplicates = {}
        keytimes = {}
        itt = {}
        ott = {}
        weighted = {}
        itw = {}
        otw = {}
        all_key_times = [range_start, range_end]
        for source, destination in zip(src_list, dst_list):
            dup = cmds.duplicate(destination, name='temp#', parentOnly=True)[0]
            for attr in attributes:
                cmds.setAttr('{}.{}'.format(dup, attr), lock=False, keyable=True)
            cmds.parentConstraint(source, dup, maintainOffset=maintain_offset)
            cmds.cutKey(destination, attribute=attributes,
                        time=(range_start, range_end))
            duplicates[destination] = dup
            keytimes[destination] = {}
            itt[destination] = {}
            ott[destination] = {}
            weighted[destination] = {}
            itw[destination] = {}
            otw[destination] = {}
            if not bake_on_ones:
                for attr in attributes:
                    times = cmds.keyframe(
                        source, attribute=attr, time=(range_start, range_end),
                        query=True, timeChange=True,
                    )
                    if not times:
                        continue
                    times = [float(t) for t in times]
                    keytimes[destination][attr] = times
                    all_key_times.extend(times)
                    try:
                        in_types = cmds.keyTangent(
                            source, attribute=attr, time=(range_start, range_end),
                            query=True, inTangentType=True,
                        )
                        out_types = cmds.keyTangent(
                            source, attribute=attr, time=(range_start, range_end),
                            query=True, outTangentType=True,
                        )
                    except RuntimeError:
                        in_types = ['auto'] * len(times)
                        out_types = ['auto'] * len(times)
                    # fixed 切线无法直接设置，退化为 spline
                    itt[destination][attr] = [
                        'spline' if t == 'fixed' else t for t in in_types
                    ]
                    ott[destination][attr] = [
                        'spline' if t == 'fixed' else t for t in out_types
                    ]
                    is_weighted = cmds.keyTangent(
                        source, attribute=attr, query=True, weightedTangents=True,
                    )
                    if preserve_tangent_weight and is_weighted and is_weighted[0]:
                        weighted[destination][attr] = True
                        in_weights = cmds.keyTangent(
                            source, attribute=attr, time=(range_start, range_end),
                            query=True, inWeight=True,
                        )
                        out_weights = cmds.keyTangent(
                            source, attribute=attr, time=(range_start, range_end),
                            query=True, outWeight=True,
                        )
                        itw[destination][attr] = [float(w) for w in in_weights or []]
                        otw[destination][attr] = [float(w) for w in out_weights or []]
                    # 端点未设键则补 spline 端键
                    if range_start not in times:
                        times.insert(0, range_start)
                        itt[destination][attr].insert(0, 'spline')
                        ott[destination][attr].insert(0, 'spline')
                        if attr in weighted[destination]:
                            itw[destination][attr].insert(0, 1.0)
                            otw[destination][attr].insert(0, 1.0)
                    if range_end not in times:
                        times.append(range_end)
                        itt[destination][attr].append('spline')
                        ott[destination][attr].append('spline')
                        if attr in weighted[destination]:
                            itw[destination][attr].append(1.0)
                            otw[destination][attr].append(1.0)
        if bake_on_ones:
            frames = list(range(int(range_start), int(range_end) + 1))
        else:
            frames = sorted(set(all_key_times))
        current_time = cmds.currentTime(query=True)
        _open_chunk(cmds, 'ML MatchBake')
        try:
            for frame in frames:
                cmds.currentTime(frame, edit=True)
                for destination in dst_list:
                    for attr in attributes:
                        try:
                            value = cmds.getAttr(
                                '{}.{}'.format(duplicates[destination], attr),
                            )
                            if bake_on_ones:
                                cmds.setKeyframe(
                                    destination, attribute=attr, time=frame,
                                    value=value, itt='spline', ott='spline',
                                )
                            elif attr in keytimes[destination] and frame in keytimes[destination][attr]:
                                # 关键帧索引与切线列表同步前移消费
                                index = keytimes[destination][attr].index(frame)
                                in_type = itt[destination][attr].pop(index)
                                out_type = ott[destination][attr].pop(index)
                                keytimes[destination][attr].remove(frame)
                                cmds.setKeyframe(
                                    destination, attribute=attr, time=frame,
                                    value=value, itt=in_type, ott=out_type,
                                )
                        except Exception:  # pylint: disable=broad-except
                            continue
            # 切线权重恢复需在设键之后统一做
            if not bake_on_ones and preserve_tangent_weight:
                for destination in dst_list:
                    for attr in attributes:
                        if attr not in weighted[destination]:
                            continue
                        cmds.keyTangent(
                            destination, attribute=attr,
                            edit=True, weightedTangents=True,
                        )
                        for index, frame in enumerate(keytimes[destination].get(attr, [])):
                            cmds.keyTangent(
                                destination, attribute=attr, time=(frame,),
                                edit=True, absolute=True,
                                inWeight=itw[destination][attr][index],
                                outWeight=otw[destination][attr][index],
                            )
        finally:
            cmds.currentTime(current_time, edit=True)
            _close_chunk(cmds, 'ML MatchBake')
        cmds.delete(list(duplicates.values()))
        if rotate:
            curves = cmds.listConnections(dst_list, type='animCurve') or []
            if curves:
                cmds.filterCurve(curves)
        return {'ok': True, 'pairs': len(dst_list), 'frames': len(frames)}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 5. 直接复制动画
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='源对象动画直接复制到目标（ML Tools Copy Animation 移植）。'
                '范围复制时用临时端键保证首尾帧完整，并同步旋转次序。',
    category='animation',
    examples=[
        {
            'summary': '整段复制并后移 48 帧',
            'args': {'source': 'ctrl_arm', 'destinations': 'ctrl_arm_R', 'offset': 48},
        },
        {
            'summary': '只复制第 1-24 帧到另一角色',
            'args': {
                'source': 'charA:ctrl_arm', 'destinations': 'charB:ctrl_arm',
                'start': 1, 'end': 24, 'paste_method': 'replace',
            },
        },
    ],
    notes=[
        'paste_method: replace（覆盖）/ insert（插入）/ merge（合并）/ replaceCompletely（整段替换）。',
        '指定 start/end 时对缺端键的曲线先打临时键，复制后删除。',
        'rotate_order=True 时同步目标的旋转次序，避免欧拉翻转。',
        '动画层模式未移植（原工具 addToLayer 分支）。',
    ],
    returns_desc='dict {"ok": True, "destinations": 目标数, "curves": 源曲线数}',
    prerequisites=['源与目标必须存在'],
)
def ml_copy_animation(
    source,
    destinations,
    paste_method='replace',
    offset=0,
    start=None,
    end=None,
    rotate_order=True,
):
    # type: (str, Any, str, float, float, float, bool) -> Dict[str, Any]
    """节点间直接复制动画。

    :param source: 源对象
    :param destinations: 目标列表
    :param paste_method: replace / insert / merge / replaceCompletely
    :param offset: 时间偏移帧数
    :param start: 起始帧（与 end 成对使用）
    :param end: 结束帧
    :param rotate_order: 是否同步旋转次序
    :returns: dict {"ok": True, "destinations": ..., "curves": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(source):
            raise ValueError('源不存在: {}'.format(source))
        dst_list = [n for n in _normalize_list(destinations) if cmds.objExists(n)]
        if not dst_list:
            raise ValueError('必须指定至少一个有效目标')
        valid_methods = ('replace', 'insert', 'merge', 'replaceCompletely')
        method = str(paste_method or 'replace')
        if method not in valid_methods:
            raise ValueError('paste_method 仅支持 {}: {}'.format(valid_methods, method))
        curves = _get_curves(cmds, source)
        if rotate_order:
            for destination in dst_list:
                try:
                    if cmds.getAttr('{}.rotateOrder'.format(destination), keyable=True):
                        cmds.setAttr(
                            '{}.rotateOrder'.format(destination),
                            cmds.getAttr('{}.rotateOrder'.format(source)),
                        )
                except Exception:  # pylint: disable=broad-except
                    continue
        _open_chunk(cmds, 'ML Copy Animation')
        temp_start = []
        temp_end = []
        try:
            if method == 'replaceCompletely' or start is None or end is None:
                cmds.copyKey(source)
                for destination in dst_list:
                    cmds.pasteKey(destination, option=method, timeOffset=float(offset))
            else:
                range_start = float(start)
                range_end = float(end)
                if range_start >= range_end:
                    raise ValueError('start 必须小于 end')
                for curve in curves:
                    start_key = cmds.keyframe(
                        curve, time=(range_start,), query=True, timeChange=True,
                    )
                    end_key = cmds.keyframe(
                        curve, time=(range_end,), query=True, timeChange=True,
                    )
                    # 缺端键先打临时键，保证复制区间完整
                    if not start_key:
                        cmds.setKeyframe(curve, time=(range_start,), insert=True)
                        temp_start.append(curve)
                    if not end_key:
                        cmds.setKeyframe(curve, time=(range_end,), insert=True)
                        temp_end.append(curve)
                cmds.copyKey(source, time=(range_start, range_end))
                for destination in dst_list:
                    cmds.pasteKey(
                        destination, option=method, time=(range_start, range_end),
                        copies=1, connect=0, timeOffset=float(offset),
                    )
                if temp_start:
                    cmds.cutKey(temp_start, time=(range_start,))
                if temp_end:
                    cmds.cutKey(temp_end, time=(range_end,))
        finally:
            _close_chunk(cmds, 'ML Copy Animation')
        return {'ok': True, 'destinations': len(dst_list), 'curves': len(curves)}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 6. 欧拉最短路径化
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='把旋转动画值化到最接近零的等价角（ML Tools minimizeRotationCurves 移植）。'
                '解决 720 度翻转、层混合跳变问题。',
    category='animation',
    examples=[
        {
            'summary': '修掉手臂旋转曲线的多圈翻转',
            'args': {'nodes': 'ctrl_arm'},
        },
    ],
    notes=[
        '对 rotateX/Y/Z 三条曲线整体生效（需三条齐全）。',
        '在首键前一帧打零值临时键后过欧拉滤波，再删除临时键。',
        '翻转值会被化简到 ±180 度内的最短路径。',
    ],
    returns_desc='dict {"ok": True, "nodes": 处理节点数, "curves": 曲线数}',
    prerequisites=['节点应有旋转动画'],
)
def ml_minimize_rotation(nodes):
    # type: (Any) -> Dict[str, Any]
    """欧拉最短路径化。

    :param nodes: 对象名列表或逗号分隔字符串
    :returns: dict {"ok": True, "nodes": ..., "curves": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        node_list = [n for n in _normalize_list(nodes) if cmds.objExists(n)]
        if not node_list:
            raise ValueError('必须指定至少一个有效对象')
        rotate_attrs = ('rotateX', 'rotateY', 'rotateZ')
        processed = 0
        curve_total = 0
        _open_chunk(cmds, 'ML Minimize Rotation')
        try:
            for node in node_list:
                curves = cmds.keyframe(node, attribute=rotate_attrs, query=True, name=True)
                if not curves or len(curves) < 3:
                    continue
                # 临时零键 + 欧拉滤波 + 删临时键
                key_times = cmds.keyframe(curves, query=True, timeChange=True)
                temp_frame = sorted(key_times)[0] - 1
                cmds.setKeyframe(curves, time=(temp_frame,), value=0)
                cmds.filterCurve(curves)
                cmds.cutKey(curves, time=(temp_frame,))
                processed += 1
                curve_total += len(curves)
        finally:
            _close_chunk(cmds, 'ML Minimize Rotation')
        return {'ok': True, 'nodes': processed, 'curves': curve_total}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 7. 万向锁检测
# ---------------------------------------------------------------------- #
def _gimbal_tolerance(cmds, node, rotate_order):
    # type: (object, str, str) -> float
    """计算节点在指定旋转次序下的万向锁接近度（0 安全 -> 1 触锁）。"""
    cmds.xform(node, preserve=True, rotateOrder=rotate_order)
    mid_axis = rotate_order[1]
    mid_value = cmds.getAttr('{}.r{}'.format(node, mid_axis))
    return abs(((mid_value + 90) % 180) - 90) / 90.0


@tool(
    dcc=['maya'],
    description='检测节点在六种旋转次序下的万向锁接近度并推荐最优次序'
                '（ML Tools convertRotationOrder 移植）。',
    category='rigging',
    examples=[
        {
            'summary': '分析肩部控件应该用哪种旋转次序',
            'args': {'node': 'ctrl_shoulder'},
        },
    ],
    notes=[
        '返回值 0 表示远离万向锁，1 表示已触锁（中间轴接近 ±90 度）。',
        'recommend 为容差最小（最安全）的次序。',
        '检测基于当前姿势，动画中间帧可能触发不同结论，可对关键姿势分别检测。',
        '不会修改原节点，检测在临时副本上进行。',
    ],
    returns_desc='dict {"ok": True, "current": 当前次序, "scores": [{order, gimbal}], "recommend": 推荐次序}',
    prerequisites=['node 必须存在'],
)
def analyze_ml_rotation_orders(node):
    # type: (str) -> Dict[str, Any]
    """旋转次序万向锁分析。

    :param node: 待分析节点
    :returns: dict {"ok": True, "current": ..., "scores": [...], "recommend": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(node):
            raise ValueError('节点不存在: {}'.format(node))
        current_index = cmds.getAttr('{}.rotateOrder'.format(node))
        current_order = ROTATE_ORDERS[int(current_index)]
        # 无子副本上逐次序试算，避免影响原节点
        dup = cmds.duplicate(node, name='ml_roo_temp#', parentOnly=True)[0]
        scores = []
        try:
            for order in ROTATE_ORDERS:
                tolerance = _gimbal_tolerance(cmds, dup, order)
                scores.append({'order': order, 'gimbal': tolerance})
        finally:
            cmds.delete(dup)
        best = min(scores, key=lambda item: item['gimbal'])
        return {
            'ok': True,
            'current': current_order,
            'scores': scores,
            'recommend': best['order'],
        }

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 8. 关键帧时间轴传递
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='把源对象的关键帧时间轴传递到目标：目标先逐帧烘焙，'
                '再剪除非源关键帧时刻（ML Tools transferKeytimes 移植）。',
    category='animation',
    examples=[
        {
            'summary': '把 A 的打键节奏传给 B',
            'args': {'source': 'ctrl_A', 'destinations': 'ctrl_B'},
        },
    ],
    notes=[
        '目标会被逐帧烘焙后再剪键，原始动画会被覆盖，必要时先备份。',
        '源子帧关键帧（如 12.5）目标会保留整帧键 + 该子帧键。',
        '剪除后目标的时间轴与源一致，值来自目标自身烘焙结果。',
    ],
    returns_desc='dict {"ok": True, "range": [start, end], "attrs": 传递通道数}',
    prerequisites=['源与目标必须存在'],
)
def transfer_ml_keytimes(source, destinations):
    # type: (str, Any) -> Dict[str, Any]
    """关键帧时间轴传递。

    :param source: 源对象
    :param destinations: 目标列表
    :returns: dict {"ok": True, "range": [...], "attrs": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(source):
            raise ValueError('源不存在: {}'.format(source))
        dst_list = [n for n in _normalize_list(destinations) if cmds.objExists(n)]
        if not dst_list:
            raise ValueError('必须指定至少一个有效目标')
        attributes = cmds.listAttr(source, keyable=True, unlocked=True) or []
        keytimes = {}
        range_start = None
        range_end = None
        for attr in attributes:
            times = cmds.keyframe(source, attribute=attr, query=True, timeChange=True)
            if not times:
                continue
            times = [float(t) for t in times]
            if range_start is None or times[0] < range_start:
                range_start = times[0]
            if range_end is None or times[-1] > range_end:
                range_end = times[-1]
            keytimes[attr] = set(times)
        if not keytimes:
            raise ValueError('源没有关键帧: {}'.format(source))
        _open_chunk(cmds, 'ML Transfer Keytimes')
        try:
            # 先逐帧烘焙目标
            cmds.bakeResults(
                dst_list, time=(range_start, range_end), sampleBy=1,
                preserveOutsideKeys=True, simulation=True,
            )
            # 欧拉滤波防翻转
            curves = cmds.listConnections(dst_list, type='animCurve') or []
            if curves:
                cmds.filterCurve(curves)
            # 剪除非源关键帧时刻
            for attr in keytimes:
                for frame in range(int(range_start), int(range_end)):
                    if float(frame) not in keytimes[attr]:
                        cmds.cutKey(dst_list, attribute=attr, time=(frame,))
        finally:
            _close_chunk(cmds, 'ML Transfer Keytimes')
        return {
            'ok': True,
            'range': [range_start, range_end],
            'attrs': len(keytimes),
        }

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 9. 蒙皮顶点矩阵约束
# ---------------------------------------------------------------------- #
def _get_skin_weights_at_vertex(om2, oma2, vertex_component, tolerance):
    # type: (object, object, str, float) -> Tuple[str, Dict[int, float], List[float]]
    """读蒙皮顶点权重（om2）。

    :returns: (skinCluster 节点名, {influence索引: 权重}, 顶点世界位置)
    """
    selection = om2.MSelectionList()
    selection.add(vertex_component)
    dag_path, component = selection.getComponent(0)
    vertex_position = om2.MFnSingleIndexedComponent(component).getPosition(0)
    mesh_name = dag_path.partialPathName()
    # 找蒙皮节点
    import maya.cmds as mc  # pylint: disable=import-outside-toplevel
    skin = mc.listHistory(mesh_name) or []
    skin = [n for n in skin if mc.nodeType(n) == 'skinCluster']
    if not skin:
        raise RuntimeError('顶点所在网格没有 skinCluster: {}'.format(vertex_component))
    skin = skin[0]
    skin_selection = om2.MSelectionList()
    skin_selection.add(skin)
    skin_fn = oma2.MFnSkinCluster(skin_selection.getDependNode(0))
    weights, influence_ids = skin_fn.getWeights(dag_path, component)
    result = {}
    for index, influence_id in enumerate(influence_ids):
        weight = weights[index]
        if weight > tolerance:
            result[int(influence_id)] = weight
    return skin, result, [vertex_position.x, vertex_position.y, vertex_position.z]


@tool(
    dcc=['maya'],
    description='把 transform 约束跟随一个蒙皮顶点（ML Tools skinConstraint 移植）。'
                '用蒙皮权重构建 blendMatrix 节点链驱动 offsetParentMatrix，全程节点化无逐帧求值。',
    category='rigging',
    examples=[
        {
            'summary': '让腰带扣跟随裙子蒙皮顶点',
            'args': {'vertex': 'dress_geo.vtx[1200]', 'node': 'buckle_ctrl'},
        },
    ],
    notes=[
        'vertex 必须是蒙皮网格顶点（如 mesh.vtx[12]）。',
        '输出经 node 的 offsetParentMatrix 生效，不添加约束节点。',
        '最大权重关节决定朝向基准，其余按权重混合。',
        '权重低于 0.1 的影响关节被忽略，避免节点爆炸。',
    ],
    returns_desc='dict {"ok": True, "blend": blendMatrix 节点, "influences": 参与关节数}',
    prerequisites=['顶点必须属于蒙皮网格'],
)
def constrain_ml_skinned_vertex(vertex, node):
    # type: (str, str) -> Dict[str, Any]
    """蒙皮顶点矩阵约束。

    :param vertex: 蒙皮顶点组件（mesh.vtx[N]）
    :param node: 待约束的 transform
    :returns: dict {"ok": True, "blend": ..., "influences": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        om2 = _om2()
        oma2 = _oma2()
        if not cmds.objExists(node):
            raise ValueError('节点不存在: {}'.format(node))
        if '.vtx[' not in str(vertex):
            raise ValueError('vertex 必须是顶点组件（如 mesh.vtx[12]）: {}'.format(vertex))
        weight_threshold = 0.1
        skin, influence_weights, point = _get_skin_weights_at_vertex(
            om2, oma2, str(vertex), weight_threshold,
        )
        if not influence_weights:
            raise RuntimeError('顶点没有有效蒙皮权重: {}'.format(vertex))
        largest_inf = max(influence_weights, key=influence_weights.get)
        # 默认矩阵：最大权重关节朝向 + 顶点位置平移
        world_mat = list(cmds.getAttr('{}.matrix[{}]'.format(skin, largest_inf)))
        world_mat[12], world_mat[13], world_mat[14] = point[0], point[1], point[2]
        default_matrix = om2.MMatrix(world_mat)
        node_short = str(node).split('|')[-1].split(':')[-1]
        blend = cmds.createNode('blendMatrix', name='{}_skinBlend'.format(node_short))
        target_index = 0
        created = []
        _open_chunk(cmds, 'ML Skinned Vertex Constraint')
        try:
            for influence_id, weight in influence_weights.items():
                # offset = default * inv(influence_now)，使约束在创建姿势下无偏移
                influence_matrix = om2.MMatrix(
                    cmds.getAttr('{}.matrix[{}]'.format(skin, influence_id)),
                )
                local_matrix = default_matrix * influence_matrix.inverse()
                offset_node = cmds.createNode(
                    'holdMatrix',
                    name='{}_offset_inf{}'.format(node_short, influence_id),
                )
                cmds.setAttr(
                    '{}.inMatrix'.format(offset_node),
                    [x for x in local_matrix], type='matrix',
                )
                mult = cmds.createNode(
                    'multMatrix', name='{}_mult_inf{}'.format(node_short, influence_id),
                )
                cmds.connectAttr(
                    '{}.outMatrix'.format(offset_node),
                    '{}.matrixIn[0]'.format(mult),
                )
                matrix_plug = '{}.matrix[{}]'.format(skin, influence_id)
                conns = cmds.listConnections(
                    matrix_plug, source=True, destination=False, plugs=True,
                )
                source_plug = conns[0] if conns else matrix_plug
                cmds.connectAttr(source_plug, '{}.matrixIn[1]'.format(mult))
                created.extend([offset_node, mult])
                if influence_id == largest_inf:
                    cmds.connectAttr(
                        '{}.matrixSum'.format(mult),
                        '{}.inputMatrix'.format(blend),
                    )
                else:
                    cmds.connectAttr(
                        '{}.matrixSum'.format(mult),
                        '{}.target[{}].targetMatrix'.format(blend, target_index),
                    )
                    cmds.setAttr(
                        '{}.target[{}].weight'.format(blend, target_index), weight,
                    )
                    try:
                        cmds.setAttr(
                            '{}.target[{}].useShear'.format(blend, target_index), False,
                        )
                    except Exception:  # pylint: disable=broad-except
                        cmds.setAttr(
                            '{}.target[{}].shearWeight'.format(blend, target_index), 0,
                        )
                    target_index += 1
            cmds.connectAttr(
                '{}.outputMatrix'.format(blend),
                '{}.offsetParentMatrix'.format(node),
            )
        finally:
            _close_chunk(cmds, 'ML Skinned Vertex Constraint')
        return {'ok': True, 'blend': blend, 'influences': len(influence_weights)}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 10. 弧线采样
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='逐帧采样节点世界/相机空间位置并输出间距统计（ML Tools arcTracer 数据化移植）。'
                '用于检查弧线质量、找出间距突变的帧。',
    category='animation',
    examples=[
        {
            'summary': '分析手腕第 1-24 帧的弧线',
            'args': {'nodes': 'ctrl_wrist', 'start': 1, 'end': 24},
        },
        {
            'summary': '以相机视角分析（构图层面的弧线）',
            'args': {
                'nodes': 'ctrl_wrist', 'start': 1, 'end': 24,
                'space': 'camera', 'camera': 'shotCam',
            },
        },
    ],
    notes=[
        'space=world 返回世界坐标；camera 需传相机名，返回相机前方 near_clip*1.2 处投影点。',
        'spacing 为相邻帧间距，max_spacing_frame/min_spacing_frame 标出突变位置。',
        'arc_length 为总弧长；sample_by 可隔帧采样。',
        '只读分析，不创建任何节点。',
    ],
    returns_desc='dict {"ok": True, "points": {节点: [[x,y,z],...]}, "stats": {节点: {...}}}',
    prerequisites=['nodes 必须存在'],
)
def sample_ml_arc(
    nodes,
    start=None,
    end=None,
    space='world',
    camera='',
    sample_by=1,
):
    # type: (Any, float, float, str, str, int) -> Dict[str, Any]
    """弧线采样分析。

    :param nodes: 对象名列表或逗号分隔字符串
    :param start: 起始帧；None 用播放范围
    :param end: 结束帧；None 用播放范围
    :param space: world / camera
    :param camera: 相机名（space=camera 时必填）
    :param sample_by: 采样步长
    :returns: dict {"ok": True, "points": {...}, "stats": {...}}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        node_list = [n for n in _normalize_list(nodes) if cmds.objExists(n)]
        if not node_list:
            raise ValueError('必须指定至少一个有效对象')
        space_norm = str(space or 'world').lower()
        if space_norm not in ('world', 'camera'):
            raise ValueError('space 仅支持 world/camera: {}'.format(space))
        step = max(1, int(sample_by or 1))
        range_start, range_end = _resolve_range(cmds, start, end)
        frames = list(range(int(range_start), int(range_end) + 1, step))
        near_clip = 1.0
        if space_norm == 'camera':
            if not camera or not cmds.objExists(camera):
                raise ValueError('space=camera 时必须提供有效相机名')
            near_clip = max(cmds.getAttr('{}.nearClipPlane'.format(camera)), 1.0)
        current_time = cmds.currentTime(query=True)
        points = {}
        stats = {}
        try:
            for node in node_list:
                samples = []
                for frame in frames:
                    cmds.currentTime(frame, edit=True)
                    position = cmds.xform(
                        node, query=True, worldSpace=True, rotatePivot=True,
                    )
                    if space_norm == 'camera':
                        cam_position = cmds.xform(
                            camera, query=True, worldSpace=True, rotatePivot=True,
                        )
                        cam_rotation = cmds.xform(
                            camera, query=True, worldSpace=True, rotation=True,
                        )
                        vec = [position[i] - cam_position[i] for i in range(3)]
                        length = math.sqrt(sum(v * v for v in vec)) or 1.0
                        offset = near_clip * 1.2
                        # 把对象方向向量按相机旋转转到相机局部，再放回相机前方
                        import maya.OpenMaya as om1  # pylint: disable=import-outside-toplevel
                        vec_vec = om1.MVector(*vec)
                        euler = om1.MEulerRotation(
                            math.radians(cam_rotation[0]),
                            math.radians(cam_rotation[1]),
                            math.radians(cam_rotation[2]),
                        )
                        local = vec_vec.rotateBy(euler.inverse())
                        projected = [
                            cam_position[i] + local[i] * offset for i in range(3)
                        ]
                        samples.append([round(v, 4) for v in projected])
                    else:
                        samples.append([round(v, 4) for v in position])
                # 间距统计
                spacings = []
                for index in range(1, len(samples)):
                    dist = math.sqrt(sum(
                        (samples[index][i] - samples[index - 1][i]) ** 2
                        for i in range(3)
                    ))
                    spacings.append(round(dist, 4))
                arc_length = round(sum(spacings), 4)
                max_frame = None
                min_frame = None
                if spacings:
                    max_index = spacings.index(max(spacings))
                    min_index = spacings.index(min(spacings))
                    max_frame = frames[max_index + 1]
                    min_frame = frames[min_index + 1]
                points[node] = samples
                stats[node] = {
                    'frames': frames,
                    'spacing': spacings,
                    'arc_length': arc_length,
                    'max_spacing': max(spacings) if spacings else 0.0,
                    'max_spacing_frame': max_frame,
                    'min_spacing': min(spacings) if spacings else 0.0,
                    'min_spacing_frame': min_frame,
                }
        finally:
            cmds.currentTime(current_time, edit=True)
        return {'ok': True, 'points': points, 'stats': stats}

    return run_on_main(_do)


__all__ = [
    'ml_breakdown',
    'ml_hold',
    'ml_snap',
    'ml_match_bake',
    'ml_copy_animation',
    'ml_minimize_rotation',
    'analyze_ml_rotation_orders',
    'transfer_ml_keytimes',
    'constrain_ml_skinned_vertex',
    'sample_ml_arc',
]
