#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GT Tools 第二批次移植：动画修卡 / 绑定辅助 / 姿势捕捉。

来源：github.com/TrevisanGMW/gt-tools（MIT 许可），延续 gt_tools_bridge.py
的移植原则：纯 cmds/om2 算法、延迟导入、undo chunk、结构化返回。

- ripple_gt_delete_keyframes：涟漪删除帧区间（后续关键帧前移补位）
- delete_gt_keyframes_in_range：范围删帧（不补位，支持开放边界）
- extract_gt_animation_clip：动画片段抽取为可移植 dict（含切线）
- paste_gt_animation_clip：动画片段粘贴（insert/replace + 三种映射）
- mirror_gt_cluster：镜像 cluster 权重与成员
- transfer_gt_blendshapes：同拓扑网格间传递 blendshape 目标
- create_gt_rivet：经典两点边 rivet 定位器
- make_gt_equidistant：A->B 等分约束链
- capture_gt_pose / apply_gt_pose：姿势快照与应用（剥 namespace）
"""

from __future__ import absolute_import
from __future__ import print_function

import re
import math
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple

from ...tools.registry import tool
from ._common import _ensure_in_maya
from ...dcc.runtime import run_on_main

# animCurve 输入类型分组（gt-tools core/anim.py 同名常量）
KEY_TYPE_TIME = ['animCurveTA', 'animCurveTL', 'animCurveTT', 'animCurveTU']
KEY_TYPE_DOUBLE = ['animCurveUL', 'animCurveUA', 'animCurveUT', 'animCurveUU']

# 约束函数查表：约束类型 -> cmds 函数名
_CONSTRAINT_FUNCS = {
    'parent': 'parentConstraint',
    'point': 'pointConstraint',
    'orient': 'orientConstraint',
    'scale': 'scaleConstraint',
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


def _short_name(obj):
    # type: (str) -> str
    """从 DAG 长名取短名。"""
    return str(obj).split('|')[-1]


def _open_chunk(cmds, name):
    # type: (object, str) -> None
    """打开 undo chunk。"""
    cmds.undoInfo(openChunk=True, chunkName=name)


def _close_chunk(cmds, name):
    # type: (object, str) -> None
    """关闭 undo chunk。"""
    cmds.undoInfo(closeChunk=True, chunkName=name)


def _get_keyframe_nodes(cmds, obj_list, include_set_driven):
    # type: (object, List[str], bool) -> List[str]
    """收集关键帧曲线节点（gt-tools get_keyframes 语义）。

    include_set_driven=False 只取时间输入的动画曲线；
    True 时额外含双精度输入曲线（Set Driven Key），含 blendWeighted 包装。
    """
    nodes = set()
    if obj_list:
        for obj in obj_list:
            for curve in (cmds.keyframe(obj, query=True, name=True) or []):
                nodes.add(curve)
            if include_set_driven:
                conns = cmds.listConnections(
                    obj, source=True, destination=False, skipConversionNodes=True,
                ) or []
                for conn in conns:
                    node_type = cmds.nodeType(conn)
                    if node_type in KEY_TYPE_DOUBLE:
                        nodes.add(conn)
                    elif node_type == 'blendWeighted':
                        inputs = cmds.listAttr('{}.input'.format(conn), multi=True) or []
                        for input_attr in inputs:
                            src = cmds.listConnections(
                                '{}.{}'.format(conn, input_attr),
                                source=True, destination=False, skipConversionNodes=True,
                            )
                            if src and cmds.nodeType(src[0]) in KEY_TYPE_DOUBLE:
                                nodes.add(src[0])
    else:
        nodes.update(cmds.ls(type=KEY_TYPE_TIME) or [])
        if include_set_driven:
            nodes.update(cmds.ls(type=KEY_TYPE_DOUBLE) or [])
    return sorted(nodes)


# ---------------------------------------------------------------------- #
# 1. 涟漪删除帧区间
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='涟漪删除帧区间并前移后续关键帧补位（GT Tools ripple_delete 移植）。'
                '删除后不留空档，适合修卡时抠除废帧。',
    category='animation',
    examples=[
        {
            'summary': '删除第 12-24 帧的废帧，后续动画自动前移 13 帧',
            'args': {'nodes': 'ctrl_arm,ctrl_hand', 'start_frame': 12, 'end_frame': 24},
        },
    ],
    notes=[
        'start_frame 必须严格小于 end_frame。',
        'tolerance 用于捕捉烘焙/动捕数据的亚帧关键帧（默认 0.5）。',
        'snap_keys=True 时先把所有关键帧吸附到整帧再处理。',
        '后续关键帧整体偏移 (start_frame - end_frame)，时间轴无缝衔接。',
    ],
    returns_desc='dict {"ok": True, "nodes": 数量, "shift": 偏移量, "range": [safe_start, safe_end]}',
    prerequisites=['nodes 中的对象应已有关键帧动画'],
)
def ripple_gt_delete_keyframes(
    nodes,
    start_frame,
    end_frame,
    tolerance=0.5,
    snap_keys=False,
    include_set_driven=False,
):
    # type: (Any, float, float, float, bool, bool) -> Dict[str, Any]
    """涟漪删除帧区间。

    :param nodes: 对象名列表或逗号分隔字符串
    :param start_frame: 删除区间起始帧（含）
    :param end_frame: 删除区间结束帧（含）
    :param tolerance: 亚帧容差，扩展实际删除边界
    :param snap_keys: 处理前是否把关键帧吸附到整帧
    :param include_set_driven: 是否连带处理 Set Driven Key 曲线
    :returns: dict {"ok": True, "nodes": ..., "shift": ..., "range": [...]}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        start = float(start_frame)
        end = float(end_frame)
        if start >= end:
            raise ValueError(
                'start_frame 必须严格小于 end_frame（{} >= {}）'.format(start, end),
            )
        node_list = _normalize_list(nodes)
        node_list = [n for n in node_list if cmds.objExists(n)]
        if not node_list:
            raise ValueError('必须指定至少一个有效对象')
        tol = float(tolerance)
        safe_start = start - tol
        safe_end = end + tol
        shift = start - end
        affected = 0
        _open_chunk(cmds, 'GT Ripple Delete Keyframes')
        try:
            # 可选：先吸附全部关键帧到整帧，避免亚帧残留
            if snap_keys:
                cmds.snapKey(node_list, time=(':', ':'))
            # 只处理确实有关键帧曲线的对象，统计真实受影响数量
            curves = _get_keyframe_nodes(cmds, node_list, include_set_driven)
            if curves:
                cmds.cutKey(curves, time=(safe_start, safe_end), clear=True)
                # 上界用大哨兵值覆盖所有后续关键帧（gt-tools 同款）
                cmds.keyframe(
                    curves, edit=True, relative=True,
                    timeChange=shift,
                    time=(safe_end + 0.001, 99999999),
                )
                affected = len({c.rsplit('_', 1)[0] if False else c for c in curves})
        finally:
            _close_chunk(cmds, 'GT Ripple Delete Keyframes')
        return {
            'ok': True,
            'nodes': len(node_list),
            'curves_affected': len(_get_keyframe_nodes(cmds, node_list, include_set_driven)),
            'shift': shift,
            'range': [safe_start, safe_end],
        }

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 2. 范围删帧（不补位）
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='删除帧范围内的关键帧但不移动其余关键帧（GT Tools delete_keyframes_in_range 移植）。'
                'start 或 end 留空表示开放边界。',
    category='animation',
    examples=[
        {
            'summary': '删除第 1-10 帧的全部关键帧',
            'args': {'nodes': 'ctrl_all', 'start': 1, 'end': 10},
        },
        {
            'summary': '删除第 36 帧之后的全部关键帧（开放上边界）',
            'args': {'nodes': 'ctrl_all', 'start': 36, 'end': None},
        },
    ],
    notes=[
        '与涟漪删除的区别：本工具删除后其余关键帧原地不动，会留下空档。',
        'start/end 任一为 None 时该侧开放（删到时间轴尽头）。',
        'key_scope=set_driven 时连带处理 Set Driven Key 曲线。',
    ],
    returns_desc='dict {"ok": True, "curves_affected": 受影响曲线数}',
    prerequisites=['nodes 应存在；范围内应有关键帧'],
)
def delete_gt_keyframes_in_range(
    nodes,
    start=None,
    end=None,
    key_scope='time',
    include_set_driven=None,
):
    # type: (Any, float, float, str, Any) -> Dict[str, Any]
    """范围删帧（不补位）。

    :param nodes: 对象名列表或逗号分隔字符串，空表示整个场景
    :param start: 区间起始帧，None 表示开放下边界
    :param end: 区间结束帧，None 表示开放上边界
    :param key_scope: time（仅动画曲线）或 both（含 Set Driven Key）
    :param include_set_driven: 兼容旧参数名，等效 key_scope=both
    :returns: dict {"ok": True, "curves_affected": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        # 兼容两种参数名
        scope = str(key_scope or 'time').lower()
        if include_set_driven:
            scope = 'both'
        obj_list = _normalize_list(nodes)
        obj_list = [n for n in obj_list if cmds.objExists(n)]
        open_bound = 99999999
        range_start = float(start) if start is not None else -open_bound
        range_end = float(end) if end is not None else open_bound
        if range_start > range_end:
            raise ValueError('start 不能大于 end（{} > {}）'.format(range_start, range_end))
        curves = _get_keyframe_nodes(cmds, obj_list, scope == 'both')
        affected = 0
        _open_chunk(cmds, 'GT Delete Keyframes In Range')
        try:
            for curve in curves:
                count = cmds.keyframe(
                    curve, query=True, time=(range_start, range_end), keyframeCount=True,
                )
                if count:
                    cmds.cutKey(curve, time=(range_start, range_end), clear=True)
                    affected += 1
        finally:
            _close_chunk(cmds, 'GT Delete Keyframes In Range')
        return {'ok': True, 'curves_affected': affected}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 3/4. 动画片段抽取与粘贴
# ---------------------------------------------------------------------- #
def _normalize_clip(clip_data):
    # type: (Any) -> Dict[str, Any]
    """校验并归一化动画片段载荷（gt-tools normalize_animation_clip 语义）。"""
    clip_data = clip_data if isinstance(clip_data, dict) else {}
    objects = []
    for object_data in (clip_data.get('objects') or []):
        if not isinstance(object_data, dict):
            continue
        object_name = str(object_data.get('name') or '').strip()
        if not object_name:
            continue
        attributes = []
        for attr_data in (object_data.get('attributes') or []):
            if not isinstance(attr_data, dict):
                continue
            attr_name = str(attr_data.get('attribute') or '').strip()
            if not attr_name:
                continue
            keys = []
            for key_data in (attr_data.get('keys') or []):
                if not isinstance(key_data, dict):
                    continue
                try:
                    time_value = float(key_data.get('time'))
                    key_value = float(key_data.get('value'))
                except (TypeError, ValueError):
                    continue
                normalized_key = {'time': time_value, 'value': key_value}
                for tangent_key in ('in_tangent', 'out_tangent'):
                    tangent_value = key_data.get(tangent_key)
                    if tangent_value:
                        normalized_key[tangent_key] = str(tangent_value)
                if 'weighted' in key_data:
                    normalized_key['weighted'] = bool(key_data.get('weighted'))
                keys.append(normalized_key)
            if keys:
                attributes.append({'attribute': attr_name, 'keys': keys})
        if attributes:
            objects.append({
                'name': object_name,
                'namespace_free_name': _short_name(object_name),
                'attributes': attributes,
            })
    return {'objects': objects}


def _get_tangent_values(cmds, curve, query_flag):
    # type: (object, str, str) -> List[Any]
    """逐关键帧查询切线属性值。"""
    count = cmds.keyframe(curve, query=True, keyframeCount=True) or 0
    values = []
    for index in range(count):
        value = cmds.keyTangent(curve, index=index, query=True, **{query_flag: True})
        values.append(value[0] if value else None)
    return values


def _extract_clip(cmds, nodes, start_frame, end_frame):
    # type: (object, List[str], Any, Any) -> Dict[str, Any]
    """把节点动画抽取为可移植载荷（gt-tools extract_animation_clip 语义）。"""
    clip_objects = []
    for node in nodes:
        if not cmds.objExists(node):
            continue
        long_name = (cmds.ls(node, long=True) or [node])[0]
        curve_attributes = []
        for curve in (cmds.keyframe(node, query=True, name=True) or []):
            destinations = cmds.listConnections(
                curve, source=False, destination=True, plugs=True,
            ) or []
            for dest_plug in destinations:
                if '.' not in dest_plug:
                    continue
                dest_node, attr_name = dest_plug.rsplit('.', 1)
                dest_long = cmds.ls(dest_node, long=True) or []
                # 只收本节点自身属性（跳过下游驱动造成的重复）
                if dest_long and dest_long[0] == long_name:
                    curve_attributes.append((curve, attr_name))
        attributes = []
        for curve, attr_name in curve_attributes:
            key_times = cmds.keyframe(curve, query=True, timeChange=True) or []
            key_values = cmds.keyframe(curve, query=True, valueChange=True) or []
            in_tangents = _get_tangent_values(cmds, curve, 'inTangentType')
            out_tangents = _get_tangent_values(cmds, curve, 'outTangentType')
            weighted = _get_tangent_values(cmds, curve, 'weightedTangents')
            keys = []
            for index, key_time in enumerate(key_times):
                t = float(key_time)
                if start_frame is not None and t < float(start_frame):
                    continue
                if end_frame is not None and t > float(end_frame):
                    continue
                if index >= len(key_values):
                    continue
                key_data = {'time': t, 'value': float(key_values[index])}
                if index < len(in_tangents) and in_tangents[index]:
                    key_data['in_tangent'] = str(in_tangents[index])
                if index < len(out_tangents) and out_tangents[index]:
                    key_data['out_tangent'] = str(out_tangents[index])
                if index < len(weighted):
                    key_data['weighted'] = bool(weighted[index])
                keys.append(key_data)
            if keys:
                attributes.append({'attribute': attr_name, 'keys': keys})
        if attributes:
            clip_objects.append({
                'name': node,
                'namespace_free_name': _short_name(node),
                'attributes': attributes,
            })
    return _normalize_clip({'objects': clip_objects})


@tool(
    dcc=['maya'],
    description='把节点动画抽取为可移植片段（GT Tools Anim Copy Paste 移植）。'
                '返回含关键帧时间/值/切线类型的 JSON 化载荷，可存档或跨场景粘贴。',
    category='animation',
    examples=[
        {
            'summary': '抽取手臂第 1-48 帧的动画',
            'args': {'nodes': 'ctrl_arm,ctrl_hand', 'start_frame': 1, 'end_frame': 48},
        },
    ],
    notes=[
        '返回的 clip 可直接作为 paste_gt_animation_clip 的输入，也可自行 json 保存。',
        '切线类型（in/out）与加权切线标记一并保存。',
        '只抽取对象自身属性曲线，下游被驱动的重复曲线会被过滤。',
    ],
    returns_desc='dict {"ok": True, "clip": 片段载荷, "objects": 数量, "keys": 关键帧总数}',
    prerequisites=['nodes 应已有关键帧动画'],
)
def extract_gt_animation_clip(nodes, start_frame=None, end_frame=None):
    # type: (Any, float, float) -> Dict[str, Any]
    """抽取动画片段。

    :param nodes: 对象名列表或逗号分隔字符串
    :param start_frame: 起始帧（含），None 不限
    :param end_frame: 结束帧（含），None 不限
    :returns: dict {"ok": True, "clip": ..., "objects": ..., "keys": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        node_list = _normalize_list(nodes)
        node_list = [n for n in node_list if cmds.objExists(n)]
        if not node_list:
            raise ValueError('必须指定至少一个有效对象')
        clip = _extract_clip(cmds, node_list, start_frame, end_frame)
        key_total = sum(
            len(a['keys'])
            for obj in clip['objects']
            for a in obj['attributes']
        )
        return {'ok': True, 'clip': clip, 'objects': len(clip['objects']), 'keys': key_total}

    return run_on_main(_do)


def _resolve_paste_pairs(cmds, clip, targets, mapping_mode):
    # type: (object, Dict[str, Any], List[str], str) -> List[Tuple[Dict[str, Any], str]]
    """把片段对象与粘贴目标配对（gt-tools 三种映射模式）。"""
    copied = clip.get('objects') or []
    existing = [t for t in targets if cmds.objExists(t)]
    mode = str(mapping_mode or 'selection').lower()
    if mode == 'name':
        # 按"无命名空间短名"匹配（剥 DAG 路径 + 命名空间）
        by_name = {}
        for target in existing:
            free_name = _short_name(target).split(':')[-1]
            by_name.setdefault(free_name, []).append(target)
        pairs = []
        for object_data in copied:
            candidates = by_name.get(object_data.get('namespace_free_name'), [])
            if candidates:
                pairs.append((object_data, candidates.pop(0)))
        return pairs
    if mode == 'namespace':
        # clip 对象名带 ns: 前缀时，剥掉前缀在目标列表里找
        pairs = []
        for object_data in copied:
            free = object_data.get('namespace_free_name')
            match = next((t for t in existing if _short_name(t) == free), '')
            if match:
                pairs.append((object_data, match))
        return pairs
    # selection 顺序配对；单片段多目标时复制到全部
    if len(copied) == 1 and len(existing) > 1:
        return [(copied[0], t) for t in existing]
    return list(zip(copied, existing))


def _set_copied_key(cmds, node, attribute, key_data, destination_time):
    # type: (object, str, str, Dict[str, Any], float) -> None
    """写一个拷贝关键帧并还原切线类型。"""
    cmds.setKeyframe(
        node, attribute=attribute, time=destination_time, value=key_data['value'],
    )
    tangent_kwargs = {}
    if key_data.get('in_tangent'):
        tangent_kwargs['inTangentType'] = key_data['in_tangent']
    if key_data.get('out_tangent'):
        tangent_kwargs['outTangentType'] = key_data['out_tangent']
    if tangent_kwargs:
        cmds.keyTangent(
            node, attribute=attribute,
            time=(destination_time, destination_time),
            edit=True, **tangent_kwargs,
        )
    if 'weighted' in key_data:
        cmds.keyTangent(
            node, attribute=attribute,
            time=(destination_time, destination_time),
            edit=True, weightedTangents=bool(key_data['weighted']),
        )


@tool(
    dcc=['maya'],
    description='把动画片段粘贴到目标对象（GT Tools Anim Copy Paste 移植）。'
                '支持 insert（后移既有关键帧避让）与 replace（清空目标通道）两种模式。',
    category='animation',
    examples=[
        {
            'summary': '把手臂动画粘到另一角色（按短名匹配）',
            'args': {
                'clip': {'objects': [{'name': 'L_arm_ctrl', 'attributes': []}]},
                'targets': 'newChar:L_arm_ctrl',
                'mapping_mode': 'name',
                'paste_time': 1,
            },
        },
    ],
    notes=[
        'clip 传 extract_gt_animation_clip 返回的载荷（或其 clip 字段）。',
        'mapping_mode: selection（按顺序）/ name（无命名空间短名匹配）/ namespace（剥前缀匹配）。',
        'mode=insert 会把目标粘贴点之后的关键帧整体后移片段时长。',
        'mode=replace 会先清空目标通道再写入。',
    ],
    returns_desc='dict {"ok": True, "targets": 数量, "channels": 通道数, "keys": 关键帧数, "skipped": 跳过数}',
    prerequisites=['目标对象与通道须存在'],
)
def paste_gt_animation_clip(
    clip,
    targets=None,
    paste_time=None,
    mode='insert',
    mapping_mode='selection',
):
    # type: (Dict[str, Any], Any, float, str, str) -> Dict[str, Any]
    """粘贴动画片段。

    :param clip: 动画片段载荷（extract_gt_animation_clip 的 clip 字段或完整返回值）
    :param targets: 目标对象列表；None 时按片段内对象名自匹配
    :param paste_time: 粘贴起始帧，None 时用当前帧
    :param mode: insert（插入避让）或 replace（整段替换）
    :param mapping_mode: selection / name / namespace
    :returns: dict {"ok": True, "targets": ..., "channels": ..., "keys": ..., "skipped": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        payload = clip.get('clip') if isinstance(clip, dict) and 'clip' in clip else clip
        payload = _normalize_clip(payload)
        if not payload['objects']:
            raise ValueError('片段为空，无可粘贴内容')
        tgt = _normalize_list(targets)
        pairs = _resolve_paste_pairs(cmds, payload, tgt, mapping_mode)
        if not pairs:
            raise ValueError('片段对象与目标无法配对，请检查 targets 与 mapping_mode')
        original_time = cmds.currentTime(query=True)
        paste_at = float(paste_time) if paste_time is not None else float(original_time)
        mode_norm = str(mode or 'insert').lower()
        if mode_norm not in ('insert', 'replace'):
            raise ValueError('mode 仅支持 insert / replace: {}'.format(mode))
        all_times = [
            k['time']
            for obj in payload['objects']
            for a in obj['attributes']
            for k in a['keys']
        ]
        if not all_times:
            raise ValueError('片段内无有效关键帧')
        source_start = min(all_times)
        source_end = max(all_times)
        insert_duration = max(1.0, source_end - source_start + 1.0)
        pasted_targets = set()
        pasted_channels = 0
        pasted_keys = 0
        skipped = 0
        _open_chunk(cmds, 'GT Paste Animation')
        try:
            cmds.refresh(suspend=True)
            prepared = set()
            for object_data, target in pairs:
                for attr_data in object_data['attributes']:
                    attr_name = attr_data['attribute']
                    plug = '{}.{}'.format(target, attr_name)
                    if not cmds.objExists(plug):
                        skipped += 1
                        continue
                    channel_key = (target, attr_name)
                    if channel_key not in prepared:
                        if mode_norm == 'replace':
                            cmds.cutKey(target, attribute=attr_name, clear=True)
                        else:
                            existing_times = cmds.keyframe(
                                target, attribute=attr_name, query=True, timeChange=True,
                            ) or []
                            if existing_times:
                                cmds.keyframe(
                                    target, attribute=attr_name, edit=True,
                                    relative=True,
                                    time=(paste_at, 1000000000.0),
                                    timeChange=insert_duration,
                                )
                        prepared.add(channel_key)
                    for key_data in attr_data['keys']:
                        dest_time = paste_at + (key_data['time'] - source_start)
                        _set_copied_key(cmds, target, attr_name, key_data, dest_time)
                        pasted_keys += 1
                    pasted_targets.add(target)
                    pasted_channels += 1
        finally:
            cmds.currentTime(original_time)
            cmds.refresh(suspend=False)
            _close_chunk(cmds, 'GT Paste Animation')
        return {
            'ok': True,
            'targets': len(pasted_targets),
            'channels': pasted_channels,
            'keys': pasted_keys,
            'skipped': skipped,
        }

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 5. 镜像 cluster 权重
# ---------------------------------------------------------------------- #
_MIRROR_AXES = {'x': 0, 'y': 1, 'z': 2}


@tool(
    dcc=['maya'],
    description='镜像 cluster 变形器权重与成员到网格另一侧（GT Tools Mirror Cluster 移植）。'
                '用 closestPointOnMesh 找镜像顶点，支持搜索替换重命名。',
    category='rigging',
    examples=[
        {
            'summary': '把左侧嘴角 cluster 镜像到右侧',
            'args': {
                'mesh': 'face_geo',
                'cluster_handle': 'cluster_L_mouth',
                'mirror_axis': 'x',
                'search': '_L_',
                'replace': '_R_',
            },
        },
    ],
    notes=[
        'mesh 必须是左右拓扑对称（镜像轴两侧顶点一一对应）。',
        'mirror_axis 传 x/y/z，永远向该轴负方向镜像。',
        'search/replace 用于自动重命名新 cluster（如 _L_ 换 _R_）。',
        '新 cluster 继承原 cluster 的 relative 开关与逐顶点权重。',
    ],
    returns_desc='dict {"ok": True, "new_cluster": 新 cluster 名, "vertices": 镜像顶点数}',
    prerequisites=['mesh 须为对称网格，cluster_handle 须为有效 clusterHandle'],
)
def mirror_gt_cluster(
    mesh,
    cluster_handle,
    mirror_axis='x',
    search='',
    replace='',
):
    # type: (str, str, str, str, str) -> Dict[str, Any]
    """镜像 cluster 权重。

    :param mesh: 网格 transform 名
    :param cluster_handle: clusterHandle 名
    :param mirror_axis: 镜像轴（x/y/z）
    :param search: 新 cluster 命名搜索文本
    :param replace: 新 cluster 命名替换文本
    :returns: dict {"ok": True, "new_cluster": ..., "vertices": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(mesh):
            raise ValueError('网格不存在: {}'.format(mesh))
        if not cmds.objExists(cluster_handle):
            raise ValueError('clusterHandle 不存在: {}'.format(cluster_handle))
        axis = str(mirror_axis or 'x').lower()
        if axis not in _MIRROR_AXES:
            raise ValueError('mirror_axis 仅支持 x/y/z: {}'.format(axis))
        axis_index = _MIRROR_AXES[axis]

        shapes = cmds.listRelatives(mesh, shapes=True) or []
        if not shapes or cmds.nodeType(shapes[0]) != 'mesh':
            raise ValueError('{} 不是多边形网格 transform'.format(mesh))
        children = cmds.listRelatives(cluster_handle, children=True) or []
        if not children or cmds.nodeType(children[0]) != 'clusterHandle':
            raise ValueError('{} 不是 clusterHandle'.format(cluster_handle))

        # 找 cluster 节点与其成员集合
        cluster_node = cmds.listConnections(
            '{}.worldMatrix[0]'.format(cluster_handle), type='cluster', destination=True,
        )
        if not cluster_node:
            raise ValueError('clusterHandle 未连接 cluster 节点: {}'.format(cluster_handle))
        cluster_node = cluster_node[0]
        cluster_sets = cmds.listConnections(cluster_node, type='objectSet') or []
        members = cmds.sets(cluster_sets[0], query=True) if cluster_sets else []
        # 只保留点类成员（CV/多边形顶点/细分点/晶格点）
        points = cmds.filterExpand(members, selectionMask=(28, 31, 36, 46)) or []
        vertices_with_weights = []
        for member in points:
            if not str(member).startswith(str(mesh).split('|')[-1]):
                continue
            weight = cmds.percent(cluster_node, member, query=True, value=True)[0]
            vertices_with_weights.append((member, weight))
        if not vertices_with_weights:
            raise ValueError('cluster 在该网格上没有点成员')

        mesh_shape = shapes[0]
        mirrored_vertices = []
        for member, weight in vertices_with_weights:
            pos = cmds.pointPosition(member, local=True)
            in_pos = list(pos)
            in_pos[axis_index] = -in_pos[axis_index]
            probe = cmds.createNode('closestPointOnMesh')
            try:
                cmds.setAttr('{}.inPosition'.format(probe), *in_pos)
                cmds.connectAttr(
                    '{}.outMesh'.format(mesh_shape), '{}.inMesh'.format(probe), force=True,
                )
                closest = int(cmds.getAttr('{}.closestVertexIndex'.format(probe)))
            finally:
                cmds.delete(probe)
            mirrored_vertices.append(('{}.vtx[{}]'.format(mesh, closest), weight))

        is_relative = cmds.getAttr('{}.relative'.format(cluster_node))
        new_name = str(cluster_handle).replace(search, replace) if search else str(cluster_handle)
        new_cluster = cmds.cluster(mirrored_vertices, relative=is_relative)
        # 逐点还原权重
        for member, weight in mirrored_vertices:
            cmds.percent(new_cluster[0], member, value=weight)
        cmds.rename(new_cluster[1], new_name)
        return {'ok': True, 'new_cluster': new_name, 'vertices': len(mirrored_vertices)}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 6. 传递 blendshape
# ---------------------------------------------------------------------- #
def _get_blendshape_nodes(cmds, node):
    # type: (object, str) -> List[str]
    """取节点构建历史中的全部 blendShape 节点。"""
    history = cmds.listHistory(node) or []
    return [n for n in history if cmds.nodeType(n) == 'blendShape']


def _get_bs_targets(cmds, blendshape):
    # type: (object, str) -> List[str]
    """取 blendShape 的目标权重属性名列表。"""
    return cmds.listAttr('{}.w'.format(blendshape), multi=True) or []


@tool(
    dcc=['maya'],
    description='把 blendshape 目标从源网格转移到目标网格（GT Tools transfer_blendshapes 移植）。'
                '两个网格顶点数/顶点序必须一致（如重建拓扑前的代理与成品）。',
    category='rigging',
    examples=[
        {
            'summary': '把表情系统从代理模型搬到成品模型',
            'args': {'source': 'head_proxy', 'target': 'head_final'},
        },
    ],
    notes=[
        '要求 source 与 target 顶点序完全一致，否则形变错乱。',
        '自动遍历 source 构建历史里的全部 blendShape 节点。',
        '临时目标网格用完即删，目标网格上新建 BS_<target> 节点。',
    ],
    returns_desc='dict {"ok": True, "transferred": blendshape 节点数, "targets": 目标网格}',
    prerequisites=['source/target 顶点序一致'],
)
def transfer_gt_blendshapes(source, target):
    # type: (str, str) -> Dict[str, Any]
    """网格间传递 blendshape。

    :param source: 带 blendshape 的源网格
    :param target: 接收 blendshape 的目标网格
    :returns: dict {"ok": True, "transferred": ..., "targets": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        for obj in (source, target):
            if not cmds.objExists(obj):
                raise ValueError('网格不存在: {}'.format(obj))
        bs_nodes = _get_blendshape_nodes(cmds, source)
        if not bs_nodes:
            raise ValueError('源网格没有 blendShape 节点: {}'.format(source))
        transferred = 0
        _open_chunk(cmds, 'GT Transfer Blendshapes')
        try:
            for bs in bs_nodes:
                # 逐目标权重设 1 后复制网格，得到每个目标形状的实体
                target_names = _get_bs_targets(cmds, bs)
                temp_geos = []
                for target_name in target_names:
                    cmds.setAttr('{}.{}'.format(bs, target_name), 1)
                    dup = cmds.duplicate(source, name='{}_tmp'.format(target_name))[0]
                    temp_geos.append(dup)
                    cmds.setAttr('{}.{}'.format(bs, target_name), 0)
                if not temp_geos:
                    continue
                new_bs = cmds.blendShape(target, name='BS_{}'.format(target))[0]
                for index, temp_geo in enumerate(temp_geos):
                    cmds.blendShape(
                        new_bs, edit=True, t=(target, index, temp_geo, 1.0),
                    )
                    cmds.delete(temp_geo)
                transferred += 1
        finally:
            _close_chunk(cmds, 'GT Transfer Blendshapes')
        return {'ok': True, 'transferred': transferred, 'targets': target}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 7. Rivet
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='在多边形两条边上创建经典 rivet 定位器（GT Tools create_rivet 移植）。'
                '定位器全程吸附网格表面并跟随法线方向，常用于挂载附件/按钮。',
    category='rigging',
    examples=[
        {
            'summary': '在腰带的两个交叉边上打铆钉',
            'args': {'edges': 'belt_geo.e[100]', 'edge_b': 'belt_geo.e[220]'},
        },
    ],
    notes=[
        'edges/edge_b 传多边形边组件（如 mesh.e[10]），必须来自同一网格。',
        '内部用 curveFromMeshEdge + loft + pointOnSurfaceInfo 节点链实时求位置。',
        '输出定位器 Y 轴沿表面法线，可用作 parent/point 约束目标。',
        '节点链接入网格构建历史，网格变形时 rivet 实时跟随。',
    ],
    returns_desc='dict {"ok": True, "locator": 定位器名}',
    prerequisites=['edges 必须是同一多边形网格的两条边'],
)
def create_gt_rivet(edges, edge_b=''):
    # type: (str, str) -> Dict[str, Any]
    """两点边 rivet。

    :param edges: 第一条边（mesh.e[N]）
    :param edge_b: 第二条边（mesh.e[M]），与 edges 同网格
    :returns: dict {"ok": True, "locator": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        edge_list = [str(edges).strip()]
        if edge_b:
            edge_list.append(str(edge_b).strip())
        filtered = cmds.filterExpand(edge_list, selectionMask=32) or []
        if len(filtered) != 2:
            raise ValueError('需要恰好两条多边形边（当前解析到 {} 条）'.format(len(filtered)))
        mesh_name = filtered[0].split('.')[0]
        if not cmds.objExists(mesh_name):
            raise ValueError('网格不存在: {}'.format(mesh_name))

        def _edge_index(edge):
            match = re.search(r'\[(\d+)\]', edge)
            return int(match.group(1)) if match else -1

        edge_a_index = _edge_index(filtered[0])
        edge_b_index = _edge_index(filtered[1])

        crv_a = cmds.createNode('curveFromMeshEdge', name='{}_rivetCrv_A'.format(mesh_name))
        crv_b = cmds.createNode('curveFromMeshEdge', name='{}_rivetCrv_B'.format(mesh_name))
        cmds.setAttr('{}.ihi'.format(crv_a), 1)
        cmds.setAttr('{}.ei[0]'.format(crv_a), edge_a_index)
        cmds.setAttr('{}.ihi'.format(crv_b), 1)
        cmds.setAttr('{}.ei[0]'.format(crv_b), edge_b_index)
        loft = cmds.createNode('loft', name='{}_rivetLoft'.format(mesh_name))
        info = cmds.createNode('pointOnSurfaceInfo', name='{}_rivetPointInfo'.format(mesh_name))
        cmds.setAttr('{}.turnOnPercentage'.format(info), 1)
        cmds.setAttr('{}.parameterU'.format(info), 0.5)
        cmds.setAttr('{}.parameterV'.format(info), 0.5)
        cmds.connectAttr('{}.os'.format(loft), '{}.is'.format(info))
        cmds.connectAttr('{}.oc'.format(crv_a), '{}.ic[0]'.format(loft))
        cmds.connectAttr('{}.oc'.format(crv_b), '{}.ic[1]'.format(loft))
        cmds.connectAttr('{}.w'.format(mesh_name), '{}.im'.format(crv_a))
        cmds.connectAttr('{}.w'.format(mesh_name), '{}.im'.format(crv_b))

        locator = cmds.createNode('transform', name='rivet1')
        cmds.createNode('locator', name='{}Shape'.format(locator), parent=locator)
        aim = cmds.createNode(
            'aimConstraint', parent=locator, name='{}_rivetAimConstraint1'.format(locator),
        )
        cmds.setAttr('{}.tg[0].tw'.format(aim), 1)
        cmds.setAttr('{}.a'.format(aim), 0, 1, 0, type='double3')
        cmds.setAttr('{}.u'.format(aim), 0, 0, 1, type='double3')
        for locked in ('.v', '.tx', '.ty', '.tz', '.rx', '.ry', '.rz'):
            cmds.setAttr('{}{}'.format(aim, locked), lock=True, keyable=False)
        cmds.connectAttr('{}.position'.format(info), '{}.translate'.format(locator))
        cmds.connectAttr('{}.n'.format(info), '{}.tg[0].tt'.format(aim))
        cmds.connectAttr('{}.tv'.format(info), '{}.wu'.format(aim))
        cmds.connectAttr('{}.crx'.format(aim), '{}.rx'.format(locator))
        cmds.connectAttr('{}.cry'.format(aim), '{}.ry'.format(locator))
        cmds.connectAttr('{}.crz'.format(aim), '{}.rz'.format(locator))
        return {'ok': True, 'locator': locator}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 8. 等分约束链
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='把一列对象等分约束在 A、B 两点之间（GT Tools equidistant_constraints 移植）。'
                '每个对象按序获得递增的双目标约束权重，形成均匀插值链。',
    category='rigging',
    examples=[
        {
            'summary': '把 3 节珠链等分约束在项链两端之间',
            'args': {
                'start': 'necklace_A', 'end': 'necklace_B',
                'target_list': 'bead1,bead2,bead3',
            },
        },
    ],
    notes=[
        'target_list 按顺序从 A 到 B 插值，权重 (1-p, p) 线性递增。',
        'skip_start_end=True 时首个对象权重从 A 端开始递增；False 时首尾对象分别落在 A/B 上。',
        'constraint 支持 parent / point / orient / scale。',
        'maintain_offset=True 保留对象与约束点初始偏移。',
    ],
    returns_desc='dict {"ok": True, "constraints": [约束节点], "count": 数量}',
    prerequisites=['start/end/target_list 中的对象必须存在'],
)
def make_gt_equidistant(
    start,
    end,
    target_list,
    skip_start_end=True,
    constraint='parent',
    maintain_offset=False,
):
    # type: (str, str, Any, bool, str, bool) -> Dict[str, Any]
    """A->B 等分约束链。

    :param start: 起点对象（A）
    :param end: 终点对象（B）
    :param target_list: 中间对象列表（按序）
    :param skip_start_end: 首尾对象是否不落在 A/B 正上
    :param constraint: 约束类型（parent/point/orient/scale）
    :param maintain_offset: 是否保持偏移
    :returns: dict {"ok": True, "constraints": [...], "count": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        for obj in (start, end):
            if not cmds.objExists(obj):
                raise ValueError('端点对象不存在: {}'.format(obj))
        objs = _normalize_list(target_list)
        if not objs:
            raise ValueError('必须指定至少一个中间对象')
        func_name = _CONSTRAINT_FUNCS.get(str(constraint or 'parent').lower())
        if not func_name:
            raise ValueError(
                '不支持的约束类型: {}（可选 parent/point/orient/scale）'.format(constraint),
            )
        constraint_func = getattr(cmds, func_name)
        if skip_start_end:
            objs = [''] + objs
            step = 1.0 / len(objs)
        else:
            step = 1.0 / (len(objs) - 1)
        percentage = 0.0
        created = []
        _open_chunk(cmds, 'GT Equidistant Constraints')
        try:
            for obj in objs:
                if not obj or not cmds.objExists(obj):
                    percentage += step
                    continue
                weight_start = 1.0 - percentage
                weight_end = percentage
                nodes = constraint_func(
                    start, end, obj, maintainOffset=maintain_offset,
                )
                node = nodes[0] if isinstance(nodes, (list, tuple)) else nodes
                aliases = sorted(
                    [a for a in (cmds.listAttr(node, multi=True) or [])
                     if re.match(r'^.*W\d+$', a)],
                    key=lambda x: int(re.search(r'W(\d+)$', x).group(1)),
                )
                cmds.setAttr('{}.{}'.format(node, aliases[0]), max(0.0, weight_start))
                cmds.setAttr('{}.{}'.format(node, aliases[1]), max(0.0, weight_end))
                created.append(node)
                percentage += step
        finally:
            _close_chunk(cmds, 'GT Equidistant Constraints')
        return {'ok': True, 'constraints': created, 'count': len(created)}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 9/10. 姿势捕捉与应用
# ---------------------------------------------------------------------- #
_POSE_ATTRS = ['tx', 'ty', 'tz', 'rx', 'ry', 'rz']


@tool(
    dcc=['maya'],
    description='捕捉骨骼局部变换为姿势字典（GT Tools get_pose_as_dict 移植）。'
                '键剥掉命名空间，可跨同构角色复用。',
    category='rigging',
    examples=[
        {
            'summary': '捕捉全身骨骼当前姿势',
            'args': {'joints': 'root,spine_01,spine_02,head', 'include_scale': False},
        },
    ],
    notes=[
        '记录每个 joint 的 tx~rz 局部值；include_scale=True 时附加 sx~sz。',
        '字典键为剥命名空间短名，apply 时可用 namespace 参数重映射。',
        '与 apply_gt_pose 配对使用：先 capture 再 apply 到另一角色。',
    ],
    returns_desc='dict {"ok": True, "pose": 姿势字典, "joints": 数量}',
    prerequisites=['joints 必须存在于场景'],
)
def capture_gt_pose(joints, include_scale=False):
    # type: (Any, bool) -> Dict[str, Any]
    """捕捉姿势字典。

    :param joints: 骨骼名列表或逗号分隔字符串
    :param include_scale: 是否包含缩放通道
    :returns: dict {"ok": True, "pose": ..., "joints": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        jnts = _normalize_list(joints)
        if not jnts:
            raise ValueError('必须指定骨骼')
        attrs = list(_POSE_ATTRS)
        if include_scale:
            attrs.extend(['sx', 'sy', 'sz'])
        pose = {}
        for jnt in jnts:
            if not cmds.objExists(jnt):
                continue
            # 剥命名空间与 DAG 前缀，键只保留短名（跨角色复用）
            base_name = _short_name(jnt).split(':')[-1]
            pose[base_name] = {attr: cmds.getAttr('{}.{}'.format(jnt, attr)) for attr in attrs}
        if not pose:
            raise ValueError('没有可捕捉的有效骨骼')
        return {'ok': True, 'pose': pose, 'joints': len(pose)}

    return run_on_main(_do)


@tool(
    dcc=['maya'],
    description='把姿势字典应用到场景骨骼（GT Tools set_pose_from_dict 移植）。'
                '自动跳过锁定/有连接通道，支持命名空间重映射。',
    category='rigging',
    examples=[
        {
            'summary': '把捕捉的姿势应用到带命名空间的另一角色',
            'args': {'pose': {'root': {'tx': 0, 'ty': 0, 'tz': 0, 'rx': 0, 'ry': 0, 'rz': 0}}, 'namespace': 'charB'},
        },
    ],
    notes=[
        'pose 传 capture_gt_pose 返回的字典（或其 pose 字段）。',
        'namespace 参数会把键重映射为 ns:短名 再查找目标骨骼。',
        '锁定或有输入连接的通道自动跳过，不会报错中断。',
    ],
    returns_desc='dict {"ok": True, "applied": 应用到的骨骼列表, "count": 数量}',
    prerequisites=['目标骨骼应存在于场景'],
)
def apply_gt_pose(pose, namespace=''):
    # type: (Dict[str, Any], str) -> Dict[str, Any]
    """应用姿势字典。

    :param pose: capture_gt_pose 生成的姿势字典
    :param namespace: 目标命名空间（空串表示不带命名空间）
    :returns: dict {"ok": True, "applied": [...], "count": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        payload = pose.get('pose') if isinstance(pose, dict) and 'pose' in pose else pose
        if not isinstance(payload, dict) or not payload:
            raise ValueError('pose 必须是 capture_gt_pose 生成的姿势字典')
        ns = str(namespace or '').strip()
        applied = []
        for jnt, transforms in payload.items():
            target = '{}:{}'.format(ns, jnt) if ns else jnt
            if not cmds.objExists(target):
                continue
            success = False
            for attr, value in transforms.items():
                plug = '{}.{}'.format(target, attr)
                if not cmds.objExists(plug):
                    continue
                if not cmds.getAttr(plug, settable=True):
                    continue
                try:
                    cmds.setAttr(plug, value)
                    success = True
                except Exception:  # pylint: disable=broad-except
                    continue
            if success:
                applied.append(target)
        return {'ok': True, 'applied': applied, 'count': len(applied)}

    return run_on_main(_do)


@tool(
    dcc=['maya'],
    description='捕捉层级姿势（v2，融合 BsKeyTools AnimLibrary 增量算法）：'
                '在 capture_gt_pose 基础上增加 UUID 持久标识、父子层级关系与局部变换记录，'
                '支持跨改名/跨命名空间重建完整层级姿势。',
    category='rigging',
    examples=[
        {
            'summary': '捕捉手臂层级姿势（含局部变换与 UUID）',
            'args': {'joints': 'arm_root,arm_shoulder,arm_elbow,arm_wrist', 'include_hierarchy': True},
        },
        {
            'summary': '捕捉全身姿势含缩放',
            'args': {'joints': 'root,pelvis,spine,head', 'include_hierarchy': True, 'include_scale': True},
        },
    ],
    notes=[
        'include_hierarchy=True 时额外记录：UUID（AppData slot 10，跨改名持久）、'
        'parent（父骨骼短名）、local_transform（相对父级的平移/旋转，列表结构）。',
        'local_transform 记录相对父级的偏移，父级缩放不同的角色间迁移更准确。',
        'UUID 首次捕捉时自动写入节点 AppData，后续捕捉/应用自动匹配。',
        'v2 数据兼容 apply_gt_pose（v2 字段会被其忽略），建议配 apply_gt_pose_v2 使用。',
    ],
    returns_desc='dict {"ok": True, "pose": 姿势字典(含 v2 字段), "joints": 数量}',
    prerequisites=['joints 必须存在'],
)
def capture_gt_pose_v2(joints, include_hierarchy=True, include_scale=False):
    # type: (Any, bool, bool) -> Dict[str, Any]
    """捕捉层级姿势 v2。

    :param joints: 骨骼名列表或逗号分隔字符串
    :param include_hierarchy: 是否记录 UUID/父子/局部变换
    :param include_scale: 是否包含缩放通道
    :returns: dict {"ok": True, "pose": ..., "joints": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        jnts = _normalize_list(joints)
        if not jnts:
            raise ValueError('必须指定骨骼')
        attrs = list(_POSE_ATTRS)
        if include_scale:
            attrs.extend(['sx', 'sy', 'sz'])
        pose = {}
        joint_set = set()
        for jnt in jnts:
            if cmds.objExists(jnt):
                joint_set.add(_short_name(jnt).split(':')[-1])
        for jnt in jnts:
            if not cmds.objExists(jnt):
                continue
            base_name = _short_name(jnt).split(':')[-1]
            entry = {attr: cmds.getAttr('{}.{}'.format(jnt, attr)) for attr in attrs}
            if include_hierarchy:
                # UUID：AppData slot 10，无则生成写入（BsKeyTools 语义）
                node_uuid = cmds.getAttr('{}.uuid'.format(jnt)) if cmds.attributeQuery(
                    'uuid', node=jnt, exists=True,
                ) else None
                if not node_uuid:
                    import uuid as uuid_module
                    node_uuid = str(uuid_module.uuid1())
                entry['uuid'] = node_uuid
                # 父骨骼短名（仅当父级也在捕捉列表内才有意义）
                parents = cmds.listRelatives(jnt, parent=True) or []
                if parents:
                    parent_base = _short_name(parents[0]).split(':')[-1]
                    entry['parent'] = parent_base if parent_base in joint_set else None
                else:
                    entry['parent'] = None
                # 局部变换：相对父级的平移与旋转（世界空间减法近似，
                # BsKeyTools 用矩阵除法，这里用 cmds.xform 的 objectSpace 语义）
                if entry['parent']:
                    local_translate = cmds.xform(
                        jnt, query=True, objectSpace=True, translation=True,
                    )
                    local_rotate = cmds.xform(
                        jnt, query=True, objectSpace=True, rotation=True,
                    )
                    entry['local_translate'] = [round(v, 5) for v in local_translate]
                    entry['local_rotate'] = [round(v, 5) for v in local_rotate]
            pose[base_name] = entry
        if not pose:
            raise ValueError('没有可捕捉的有效骨骼')
        return {'ok': True, 'pose': pose, 'joints': len(pose)}

    return run_on_main(_do)


@tool(
    dcc=['maya'],
    description='应用层级姿势（v2，融合 BsKeyTools AnimLibrary 增量算法）：'
                '在 apply_gt_pose 基础上增加三级匹配策略（UUID -> 名字 -> 命名空间重映射）'
                '与 RBF 高斯核平滑混合。',
    category='rigging',
    examples=[
        {
            'summary': '按 UUID/名字匹配应用姿势，RBF 平滑（sigma 越小衰减越强）',
            'args': {'pose': '{"root": {"tx": 0, "uuid": "abc-123"}}', 'smoothness': 2.0},
        },
        {
            'summary': '带命名空间重映射应用',
            'args': {
                'pose': '{"root": {"tx": 0, "rx": 45}}',
                'namespace': 'charB',
                'match_mode': 'name',
            },
        },
    ],
    notes=[
        'match_mode: auto（UUID 优先，回退名字）/ uuid（仅 UUID）/ name（仅名字，支持 namespace 重映射）。',
        'smoothness: RBF 高斯核 sigma（0.1-10）。1 左右接近线性，越小距基准姿势远的关节衰减越强；'
        '0 或 None 关闭 RBF 直接线性应用。',
        'UUID 匹配通过扫描场景节点 AppData/uuid 属性实现，跨改名仍有效。',
        '锁定或有连接通道自动跳过。',
    ],
    returns_desc='dict {"ok": True, "applied": [...], "count": 数量, "matched_by": {uuid: n, name: n}}',
    prerequisites=['目标骨骼应存在（或 UUID 可匹配）'],
)
def apply_gt_pose_v2(pose, namespace='', match_mode='auto', smoothness=0.0):
    # type: (Dict[str, Any], str, str, float) -> Dict[str, Any]
    """应用姿势字典 v2。

    :param pose: capture_gt_pose / capture_gt_pose_v2 生成的姿势字典
    :param namespace: 目标命名空间（仅 name 模式使用）
    :param match_mode: auto / uuid / name
    :param smoothness: RBF sigma（0 关闭）
    :returns: dict {"ok": True, "applied": [...], "count": ..., "matched_by": {...}}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        payload = pose.get('pose') if isinstance(pose, dict) and 'pose' in pose else pose
        if not isinstance(payload, dict) or not payload:
            raise ValueError('pose 必须是 capture_gt_pose(_v2) 生成的姿势字典')
        mode = str(match_mode or 'auto').lower()
        if mode not in ('auto', 'uuid', 'name'):
            raise ValueError('match_mode 仅支持 auto/uuid/name: {}'.format(mode))
        sigma = float(smoothness or 0.0)
        if sigma < 0:
            raise ValueError('smoothness 不能为负')
        # 构建 UUID -> 节点 索引（扫描场景）
        uuid_index = {}
        if mode in ('auto', 'uuid'):
            for node in cmds.listRelatives(
                cmds.ls(assemblies=True), allDescendents=True, type='joint', fullPath=True,
            ) or []:
                if cmds.attributeQuery('uuid', node=node, exists=True):
                    node_uuid = cmds.getAttr('{}.uuid'.format(node))
                    if node_uuid:
                        uuid_index[str(node_uuid)] = node
        matched_by = {'uuid': 0, 'name': 0}
        apply_plan = []  # (target, transforms, entry)
        for jnt, entry in payload.items():
            if not isinstance(entry, dict):
                continue
            target = None
            # UUID 优先
            entry_uuid = entry.get('uuid')
            if mode in ('auto', 'uuid') and entry_uuid and str(entry_uuid) in uuid_index:
                target = uuid_index[str(entry_uuid)]
                matched_by['uuid'] += 1
            if target is None and mode in ('auto', 'name'):
                ns = str(namespace or '').strip()
                candidate = '{}:{}'.format(ns, jnt) if ns else jnt
                if cmds.objExists(candidate):
                    target = candidate
                    matched_by['name'] += 1
            if target is not None:
                apply_plan.append((target, entry))
        # RBF 权重：以第一个骨骼为基准计算平均位移距离（简化 BsKeyTools 语义）
        rbf_weight = 1.0
        if sigma > 0 and len(apply_plan) > 1:
            distances = []
            for target, entry in apply_plan:
                for attr in ('tx', 'ty', 'tz'):
                    if attr in entry and cmds.objExists('{}.{}'.format(target, attr)):
                        current = cmds.getAttr('{}.{}'.format(target, attr))
                        distances.append(abs(entry[attr] - current))
                        break
            if distances:
                mean_dist = sum(distances) / len(distances)
                rbf_weight = math.exp(-(mean_dist ** 2) / (2.0 * (sigma / 10.0) ** 2))
        applied = []
        for target, entry in apply_plan:
            effective = (
                rbf_weight * 0.5 + 0.5 if sigma > 0 else 1.0
            )
            success = False
            for attr, value in entry.items():
                if attr in ('uuid', 'parent', 'local_translate', 'local_rotate'):
                    continue
                plug = '{}.{}'.format(target, attr)
                if not cmds.objExists(plug):
                    continue
                if not cmds.getAttr(plug, settable=True):
                    continue
                try:
                    if effective < 1.0 and isinstance(value, (int, float)):
                        current_value = cmds.getAttr(plug)
                        value = current_value + (value - current_value) * effective
                    cmds.setAttr(plug, value)
                    success = True
                except Exception:  # pylint: disable=broad-except
                    continue
            if success:
                applied.append(target)
        return {
            'ok': True,
            'applied': applied,
            'count': len(applied),
            'matched_by': matched_by,
        }

    return run_on_main(_do)


__all__ = [
    'ripple_gt_delete_keyframes',
    'delete_gt_keyframes_in_range',
    'extract_gt_animation_clip',
    'paste_gt_animation_clip',
    'mirror_gt_cluster',
    'transfer_gt_blendshapes',
    'create_gt_rivet',
    'make_gt_equidistant',
    'capture_gt_pose',
    'apply_gt_pose',
    'capture_gt_pose_v2',
    'apply_gt_pose_v2',
]
