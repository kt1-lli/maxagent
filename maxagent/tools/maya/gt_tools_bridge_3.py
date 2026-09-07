#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GT Tools 第四批次移植（数据持久化 + 绑定向）：gt-tools core 层精华。

来源：github.com/TrevisanGMW/gt-tools（MIT），延续 gt_tools_bridge 系列
移植原则：纯 cmds/om2 算法、延迟导入、undo chunk、结构化返回：

- export_gt_skin_weights：om2 读全部蒙皮权重 + JSON 导出
- import_gt_skin_weights：JSON 蒙皮权重导入（缺失关节按层级自动找回）
- export_gt_driven_keys：Set-Driven Key 曲线导出 JSON（含 TRS 倍率）
- import_gt_driven_keys：SDK JSON 导入重建
- export_gt_blendshape_deltas：网格间顶点增量导出 JSON
- apply_gt_blendshape_deltas：从增量字典/JSON 重建形变（可选转 blendshape）
- snapshot_gt_camera / apply_gt_camera：相机全套属性快照与恢复
- add_gt_offset_transform：插入 offset 组/关节/locator 并保世界变换
- create_gt_twist_network：四元数扭转提取节点网络（纯 Maya 原生节点）
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import math
import os
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple

from ...tools.registry import tool
from ._common import _ensure_in_maya
from ...dcc.runtime import run_on_main

# SDK 曲线类型（animCurve 场景驱动键）
_SDK_CURVE_TYPES = (
    'animCurveUA', 'animCurveUL', 'animCurveUT', 'animCurveUU',
    'animCurveAA', 'animCurveAL', 'animCurveAT', 'animCurveAU',
)

# 相机 shape 属性快照清单（get_camera_data 语义）
_CAMERA_SHAPE_ATTRS = [
    'focalLength', 'horizontalFilmAperture', 'verticalFilmAperture',
    'lensSqueezeRatio', 'cameraScale', 'orthographic', 'orthographicWidth',
    'nearClipPlane', 'farClipPlane', 'filmFitOffset', 'horizontalFilmOffset',
    'verticalFilmOffset', 'preScale', 'filmTranslateH', 'filmTranslateV',
    'depthOfField', 'fStop', 'focusRegionScale', 'renderable',
]

# SDK 关键帧数据字段
_SDK_KEY_FIELDS = (
    'time', 'value', 'interpolation', 'in_tangent_type', 'out_tangent_type',
)


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


def _get_short_name(node):
    # type: (str) -> str
    """取短名（去 DAG 路径与命名空间）。"""
    return str(node).split('|')[-1].split(':')[-1]


def _get_skin_cluster_name(cmds, mesh):
    # type: (object, str) -> str
    """找网格的 skinCluster（getSkinCluster 语义）。"""
    shapes = [mesh]
    if cmds.nodeType(mesh) == 'transform':
        shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True) or []
    for shape in shapes:
        history = cmds.listHistory(shape, pruneDagObjects=True) or []
        skins = [n for n in history if cmds.nodeType(n) == 'skinCluster']
        if skins:
            return skins[0]
    return ''


def _read_workspace_file(path):
    # type: (str) -> str
    """读文件文本（容器环境直读 Windows/Linux 路径均可）。"""
    with open(path, 'r', encoding='utf-8') as handle:
        return handle.read()


def _write_workspace_file(path, content):
    # type: (str, str) -> str
    """写文件文本（绝对路径直写）。"""
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(content)
    return path


def _find_sdk_curve(cmds, driven_attr, driver_attr):
    # type: (object, str, str) -> str
    """找驱动/被驱动属性共享的 animCurve 节点（find_set_driven_key 语义）。"""
    driven_conns = cmds.listConnections(
        driven_attr, source=True, destination=False, plugs=True, scn=True,
    ) or []
    driver_conns = cmds.listConnections(
        driver_attr, source=False, destination=True, plugs=True, scn=True,
    ) or []
    driven_nodes = []
    if driven_conns:
        first = driven_conns[0]
        first_node = str(first).split('.')[0]
        if cmds.nodeType(first_node) == 'blendWeighted':
            inputs = cmds.listConnections(
                '{}.input'.format(first_node), source=True,
                destination=False, plugs=True, skipConversionNodes=True,
            ) or []
            for plug in inputs:
                node = str(plug).split('.')[0]
                if cmds.nodeType(node) in _SDK_CURVE_TYPES:
                    driven_nodes.append(node)
        else:
            driven_nodes.append(first_node)
    driver_nodes = []
    for conn in driver_conns:
        node = str(conn).split('.')[0]
        if cmds.nodeType(node) in _SDK_CURVE_TYPES:
            driver_nodes.append(node)
    shared = list(set(driven_nodes) & set(driver_nodes))
    if shared:
        return shared[0]
    return ''


# ---------------------------------------------------------------------- #
# 1. 蒙皮权重导出
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='把蒙皮网格的全部权重读为字典并导出 JSON（GT Tools get_skin_weights 移植）。'
                'om2 批量读取，跳过零权重关节，跨场景权重备份/迁移。',
    category='rigging',
    examples=[
        {
            'summary': '导出角色蒙皮权重到文件',
            'args': {'mesh': 'body_geo', 'file_path': '/tmp/body_weights.json'},
        },
    ],
    notes=[
        'mesh 必须是蒙皮网格（transform 或 shape 均可）。',
        '输出 JSON 结构：{"influences": [...], "weights": {顶点索引: {关节短名: 权重}}}。',
        'remove_unused=True 时零权重关节不出现在输出中（体积更小）。',
        '文件写入 AI 容器可访问的绝对路径（如 /tmp 或工作区）。',
    ],
    returns_desc='dict {"ok": True, "file": JSON 路径, "vertices": 顶点数, "influences": 关节数}',
    prerequisites=['mesh 必须是蒙皮网格'],
)
def export_gt_skin_weights(mesh, file_path, remove_unused=True):
    # type: (str, str, bool) -> Dict[str, Any]
    """蒙皮权重 JSON 导出。

    :param mesh: 蒙皮网格
    :param file_path: JSON 输出绝对路径
    :param remove_unused: 是否剔除零权重关节
    :returns: dict {"ok": True, "file": ..., "vertices": ..., "influences": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        om2 = _om2()
        oma2 = _oma2()
        if not cmds.objExists(mesh):
            raise ValueError('网格不存在: {}'.format(mesh))
        skin_name = _get_skin_cluster_name(cmds, mesh)
        if not skin_name:
            raise RuntimeError('网格没有 skinCluster: {}'.format(mesh))
        vertices_num = int(cmds.polyEvaluate(mesh, vertex=True))
        selection = om2.MSelectionList()
        selection.add(mesh)
        mesh_dag = selection.getDagPath(0)
        skin_selection = om2.MSelectionList()
        skin_selection.add(skin_name)
        skin_fn = oma2.MFnSkinCluster(skin_selection.getDependNode(0))
        # 构建全顶点组件
        component = om2.MFnSingleIndexedComponent()
        vert_component = component.create(om2.MFn.kMeshVertComponent)
        component.addElements(range(vertices_num))
        weights, influence_ids = skin_fn.getWeights(mesh_dag, vert_component)
        inf_dags = skin_fn.influenceObjects()
        inf_names = [dag.partialPathName() for dag in inf_dags]
        # 索引映射：influence 索引 -> 数组偏移
        index_map = {
            skin_fn.indexForInfluenceObject(dag): i for i, dag in enumerate(inf_dags)
        }
        weight_dict = {}
        used_influences = set()
        for vertex in range(vertices_num):
            base = vertex * len(inf_ids_array(len(inf_dags)))
            vertex_weights = {}
            for array_index in range(len(inf_dags)):
                weight = weights[vertex * len(inf_dags) + array_index]
                if remove_unused and weight <= 0.0001:
                    continue
                vertex_weights[_get_short_name(inf_names[array_index])] = round(weight, 5)
                used_influences.add(_get_short_name(inf_names[array_index]))
            if vertex_weights:
                weight_dict[vertex] = vertex_weights
        payload = {
            'tool': 'gt_skin_weights',
            'mesh': mesh,
            'skinCluster': skin_name,
            'influences': sorted(used_influences),
            'vertices': vertices_num,
            'weights': weight_dict,
        }
        written = _write_workspace_file(file_path, json.dumps(payload, indent=1))
        return {
            'ok': True,
            'file': written,
            'vertices': vertices_num,
            'influences': len(used_influences),
        }

    return run_on_main(_do)


def inf_ids_array(count):
    # type: (int) -> List[int]
    """辅助：生成 0..count-1 列表（占位以保持结构清晰）。"""
    return list(range(count))


# ---------------------------------------------------------------------- #
# 2. 蒙皮权重导入
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='从 JSON 导入蒙皮权重到目标网格（GT Tools set_skin_weights 移植）。'
                'om2 批量写入，缺失关节可按名称在场景中自动找回。',
    category='rigging',
    examples=[
        {
            'summary': '从 JSON 恢复蒙皮权重',
            'args': {'mesh': 'body_geo_new', 'file_path': '/tmp/body_weights.json'},
        },
    ],
    notes=[
        'JSON 必须由 export_gt_skin_weights 生成（schema 校验）。',
        '关节短名会自动在场景中查找匹配（支持命名空间差异，取第一个匹配）。',
        '关节彻底缺失时报错并列出缺失清单。',
        '顶点数不一致时按可用顶点写入（多余忽略，不足保持原权重）。',
    ],
    returns_desc='dict {"ok": True, "vertices": 写入顶点数, "missing": 缺失关节}',
    prerequisites=['mesh 必须已绑定蒙皮且有关节'],
)
def import_gt_skin_weights(mesh, file_path):
    # type: (str, str) -> Dict[str, Any]
    """蒙皮权重 JSON 导入。

    :param mesh: 目标蒙皮网格
    :param file_path: JSON 输入绝对路径
    :returns: dict {"ok": True, "vertices": ..., "missing": [...]}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        om2 = _om2()
        oma2 = _oma2()
        if not cmds.objExists(mesh):
            raise ValueError('网格不存在: {}'.format(mesh))
        skin_name = _get_skin_cluster_name(cmds, mesh)
        if not skin_name:
            raise RuntimeError('网格没有 skinCluster: {}'.format(mesh))
        payload = json.loads(_read_workspace_file(file_path))
        if payload.get('tool') != 'gt_skin_weights':
            raise ValueError('JSON 不是 gt_skin_weights 格式: {}'.format(file_path))
        raw_weights = payload.get('weights', {})
        skin_data = {int(k): v for k, v in raw_weights.items()}
        # 关节短名 -> 场景节点解析
        skin_selection = om2.MSelectionList()
        skin_selection.add(skin_name)
        skin_fn = oma2.MFnSkinCluster(skin_selection.getDependNode(0))
        inf_dags = skin_fn.influenceObjects()
        inf_short_to_index = {}
        for index, dag in enumerate(inf_dags):
            inf_short_to_index.setdefault(_get_short_name(dag.partialPathName()), index)
        # 缺失关节检测
        missing = []
        for vertex_weights in skin_data.values():
            for joint_name in vertex_weights:
                if joint_name not in inf_short_to_index and joint_name not in missing:
                    missing.append(joint_name)
        if missing:
            return {'ok': False, 'vertices': 0, 'missing': sorted(missing)}
        vertices_num = int(cmds.polyEvaluate(mesh, vertex=True))
        selection = om2.MSelectionList()
        selection.add(mesh)
        mesh_dag = selection.getDagPath(0)
        inf_count = len(inf_dags)
        component = om2.MFnSingleIndexedComponent()
        vert_component = component.create(om2.MFn.kMeshVertComponent)
        component.addElements(range(vertices_num))
        weights_array = om2.MDoubleArray(vertices_num * inf_count, 0.0)
        written = 0
        for vertex_str, vertex_weights in skin_data.items():
            vertex = int(vertex_str)
            if vertex >= vertices_num:
                continue
            base = vertex * inf_count
            for joint_name, weight in vertex_weights.items():
                weights_array[base + inf_short_to_index[joint_name]] = float(weight)
            written += 1
        inf_indices = om2.MIntArray(inf_count, 0)
        for index in range(inf_count):
            inf_indices[index] = int(index)
        skin_fn.setWeights(
            mesh_dag, vert_component, inf_indices, weights_array,
            normalize=False, returnOldWeights=False,
        )
        return {'ok': True, 'vertices': written, 'missing': []}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 3. SDK 导出
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='把对象的 Set-Driven Key 曲线导出为 JSON（GT Tools export_double_keys 移植）。'
                '含驱动/被驱动属性与全部键值，支持 TRS 倍率（rig 全局缩放场景）。',
    category='rigging',
    examples=[
        {
            'summary': '导出手掌控件的 SDK 到文件',
            'args': {'nodes': 'ctrl_palm', 'file_path': '/tmp/palm_sdk.json'},
        },
    ],
    notes=[
        '扫描节点上所有驱动键曲线（animCurveUU/UL/UT/UA 等 8 类）。',
        '输出结构：{"driven": 被驱动属性, "driver": 驱动属性, "keys": [{time, value, 切线...}]}。',
        'keys 的 time 是驱动值，value 是被驱动值（SDK 方向）。',
        '导入时可用 translate/rotate/scale_multiplier 整体缩放值。',
    ],
    returns_desc='dict {"ok": True, "file": JSON 路径, "curves": 导出曲线数}',
    prerequisites=['nodes 上应有 set-driven key'],
)
def export_gt_driven_keys(nodes, file_path, file_prefix=''):
    # type: (Any, str, str) -> Dict[str, Any]
    """SDK JSON 导出。

    :param nodes: 对象列表（被驱动侧）
    :param file_path: JSON 输出绝对路径
    :param file_prefix: 文件名前缀（附加在 node 名前）
    :returns: dict {"ok": True, "file": ..., "curves": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        node_list = [n for n in _normalize_list(nodes) if cmds.objExists(n)]
        if not node_list:
            raise ValueError('必须指定至少一个有效对象')
        exported = []
        for node in node_list:
            driven_attrs = cmds.listAttr(node, keyable=True, unlocked=True) or []
            for attr in driven_attrs:
                driven_plug = '{}.{}'.format(node, attr)
                curve = _find_sdk_curve_by_driven(cmds, driven_plug)
                if not curve:
                    continue
                driver_plug = _get_curve_driver(cmds, curve)
                if not driver_plug:
                    continue
                keys = []
                key_count = int(cmds.keyframe(curve, query=True, keyframeCount=True) or 0)
                for index in range(key_count):
                    key_index = (index, index)
                    keys.append({
                        'time': float(cmds.keyframe(
                            curve, index=key_index, floatChange=True, query=True,
                        )[0]),
                        'value': float(cmds.keyframe(
                            curve, index=key_index, valueChange=True, query=True,
                        )[0]),
                        'in_tangent_type': cmds.keyTangent(
                            curve, index=key_index, inTangentType=True, query=True,
                        )[0],
                        'out_tangent_type': cmds.keyTangent(
                            curve, index=key_index, outTangentType=True, query=True,
                        )[0],
                    })
                exported.append({
                    'node': node,
                    'driven': driven_plug,
                    'driver': driver_plug,
                    'curve': curve,
                    'keys': keys,
                })
        if not exported:
            raise RuntimeError('节点上没有可导出的 set-driven key: {}'.format(node_list))
        payload = {
            'tool': 'gt_driven_keys',
            'curves': exported,
        }
        written = _write_workspace_file(file_path, json.dumps(payload, indent=1))
        return {'ok': True, 'file': written, 'curves': len(exported)}

    return run_on_main(_do)


def _find_sdk_curve_by_driven(cmds, driven_plug):
    # type: (object, str) -> str
    """按被驱动属性找 SDK 曲线。"""
    conns = cmds.listConnections(
        driven_plug, source=True, destination=False, type='animCurve', scn=True,
    ) or []
    for conn in conns:
        if cmds.nodeType(conn) in _SDK_CURVE_TYPES:
            return conn
    # blendWeighted 中转
    for conn in conns:
        if cmds.nodeType(conn) == 'blendWeighted':
            inputs = cmds.listConnections(
                '{}.input'.format(conn), source=True, destination=False,
                skipConversionNodes=True,
            ) or []
            for plug in inputs:
                node = str(plug).split('.')[0]
                if cmds.nodeType(node) in _SDK_CURVE_TYPES:
                    return node
    return ''


def _get_curve_driver(cmds, curve):
    # type: (object, str) -> str
    """读 SDK 曲线的驱动属性。"""
    conns = cmds.listConnections(
        '{}.input'.format(curve), source=True, destination=False,
        plugs=True, skipConversionNodes=True,
    ) or []
    return conns[0] if conns else ''


# ---------------------------------------------------------------------- #
# 4. SDK 导入
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='从 JSON 导入 Set-Driven Key 并应用到场景（GT Tools import_double_keys 移植）。'
                '支持 TRS 倍率缩放（rig 全局缩放后重建 SDK）。',
    category='rigging',
    examples=[
        {
            'summary': '从 JSON 重建 SDK（旋转值放大 2 倍适配缩放后的 rig）',
            'args': {
                'file_path': '/tmp/palm_sdk.json',
                'rotate_multiplier': 2.0,
            },
        },
    ],
    notes=[
        'JSON 必须由 export_gt_driven_keys 生成。',
        '被驱动/驱动属性按名称在场景中查找，找不到时跳过该条并记录。',
        'multiplier 只作用于对应 TRS 通道的 value。',
    ],
    returns_desc='dict {"ok": True, "applied": 应用条数, "skipped": 跳过条数}',
    prerequisites=['场景中应有匹配的被驱动/驱动属性'],
)
def import_gt_driven_keys(
    file_path,
    translate_multiplier=1.0,
    rotate_multiplier=1.0,
    scale_multiplier=1.0,
):
    # type: (str, float, float, float) -> Dict[str, Any]
    """SDK JSON 导入。

    :param file_path: JSON 输入绝对路径
    :param translate_multiplier: 平移值倍率
    :param rotate_multiplier: 旋转值倍率
    :param scale_multiplier: 缩放值倍率
    :returns: dict {"ok": True, "applied": ..., "skipped": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        payload = json.loads(_read_workspace_file(file_path))
        if payload.get('tool') != 'gt_driven_keys':
            raise ValueError('JSON 不是 gt_driven_keys 格式: {}'.format(file_path))
        applied = 0
        skipped = 0
        _open_chunk(cmds, 'GT Import Driven Keys')
        try:
            for entry in payload.get('curves', []):
                driven = entry.get('driven', '')
                driver = entry.get('driver', '')
                if not cmds.objExists(driven) or not cmds.objExists(driver):
                    skipped += 1
                    continue
                attr_only = str(driven).split('.')[-1]
                if attr_only.startswith('translate'):
                    multiplier = float(translate_multiplier)
                elif attr_only.startswith('rotate'):
                    multiplier = float(rotate_multiplier)
                elif attr_only.startswith('scale'):
                    multiplier = float(scale_multiplier)
                else:
                    multiplier = 1.0
                for key in entry.get('keys', []):
                    cmds.setDrivenKeyframe(
                        driven,
                        currentDriver=driver,
                        value=float(key['value']) * multiplier,
                        driverValue=float(key['time']),
                        insertBlend=True,
                    )
                applied += 1
        finally:
            _close_chunk(cmds, 'GT Import Driven Keys')
        return {'ok': True, 'applied': applied, 'skipped': skipped}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 5. BlendShape 增量导出
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='导出基准网格与目标网格间的顶点增量 JSON（GT Tools write_deltas_json 移植）。'
                '形状库跨文件传递的中间格式。',
    category='geometry',
    examples=[
        {
            'summary': '导出基准与表情目标间的增量',
            'args': {
                'source_mesh': 'body_base',
                'target_meshes': 'smile_target,frown_target',
                'file_path': '/tmp/face_deltas.json',
            },
        },
    ],
    notes=[
        'source 与 target 顶点数必须一致。',
        '输出结构：{target名: {顶点索引: [dx, dy, dz]}}（世界空间增量）。',
        'remove_zeros=True 时零位移顶点不写入（体积更小）。',
    ],
    returns_desc='dict {"ok": True, "file": JSON 路径, "targets": 目标数, "deltas": 增量顶点总数}',
    prerequisites=['网格对需同拓扑'],
)
def export_gt_blendshape_deltas(
    source_mesh,
    target_meshes,
    file_path,
    remove_zeros=True,
):
    # type: (str, Any, str, bool) -> Dict[str, Any]
    """顶点增量 JSON 导出。

    :param source_mesh: 基准网格
    :param target_meshes: 目标网格列表
    :param file_path: JSON 输出绝对路径
    :param remove_zeros: 是否剔除零位移顶点
    :returns: dict {"ok": True, "file": ..., "targets": ..., "deltas": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(source_mesh):
            raise ValueError('基准网格不存在: {}'.format(source_mesh))
        target_list = [n for n in _normalize_list(target_meshes) if cmds.objExists(n)]
        if not target_list:
            raise ValueError('必须指定至少一个有效目标网格')
        source_positions = _get_vertex_positions(cmds, source_mesh)
        all_deltas = {}
        delta_count = 0
        for target in target_list:
            target_positions = _get_vertex_positions(cmds, target)
            if len(target_positions) != len(source_positions):
                raise ValueError(
                    '顶点数不一致: {}({}) vs {}({})'.format(
                        source_mesh, len(source_positions), target, len(target_positions),
                    ),
                )
            deltas = {}
            for index, src_pos in enumerate(source_positions):
                tgt_pos = target_positions[index]
                delta = [
                    round(tgt_pos[0] - src_pos[0], 5),
                    round(tgt_pos[1] - src_pos[1], 5),
                    round(tgt_pos[2] - src_pos[2], 5),
                ]
                if remove_zeros and delta == [0.0, 0.0, 0.0]:
                    continue
                deltas[index] = delta
                delta_count += 1
            all_deltas[_get_short_name(target)] = deltas
        payload = {
            'tool': 'gt_blendshape_deltas',
            'source': source_mesh,
            'deltas': all_deltas,
        }
        written = _write_workspace_file(file_path, json.dumps(payload, indent=1))
        return {
            'ok': True,
            'file': written,
            'targets': len(target_list),
            'deltas': delta_count,
        }

    return run_on_main(_do)


def _get_vertex_positions(cmds, mesh):
    # type: (object, str) -> List[List[float]]
    """读网格全部顶点世界坐标。"""
    shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True) or [mesh]
    vertices_num = int(cmds.polyEvaluate(mesh, vertex=True))
    positions = []
    for index in range(vertices_num):
        positions.append(cmds.xform(
            '{}.vtx[{}]'.format(shapes[0], index),
            query=True, worldSpace=True, translation=True,
        ))
    return positions


# ---------------------------------------------------------------------- #
# 6. BlendShape 增量应用
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='把顶点增量应用到目标网格（GT Tools apply_all_deltas 移植）。'
                '从 JSON 或增量字典重建形变，可选直接转成 blendShape 目标。',
    category='geometry',
    examples=[
        {
            'summary': '从 JSON 重建表情目标并挂 blendShape',
            'args': {
                'target_mesh': 'body_base_new',
                'file_path': '/tmp/face_deltas.json',
                'as_blendshape': True,
            },
        },
    ],
    notes=[
        'file_path 与 deltas 二选一；JSON 必须由 export_gt_blendshape_deltas 生成。',
        'as_blendshape=True 时先复制 target 网格，应用增量后挂为 blendShape 目标。',
        '增量是世界空间相对基准的偏移。',
    ],
    returns_desc='dict {"ok": True, "applied": 应用目标数, "shapes": 创建的 blendShape 节点}',
    prerequisites=['target_mesh 需与导出时基准同拓扑'],
)
def apply_gt_blendshape_deltas(
    target_mesh,
    file_path=None,
    deltas=None,
    as_blendshape=True,
):
    # type: (str, str, Dict[str, Any], bool) -> Dict[str, Any]
    """顶点增量应用。

    :param target_mesh: 目标网格（基准拓扑）
    :param file_path: JSON 输入路径（与 deltas 二选一）
    :param deltas: 增量字典（与 file_path 二选一）
    :param as_blendshape: 是否挂为 blendShape
    :returns: dict {"ok": True, "applied": ..., "shapes": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(target_mesh):
            raise ValueError('目标网格不存在: {}'.format(target_mesh))
        if file_path:
            payload = json.loads(_read_workspace_file(file_path))
            if payload.get('tool') != 'gt_blendshape_deltas':
                raise ValueError('JSON 不是 gt_blendshape_deltas 格式')
            all_deltas = payload.get('deltas', {})
        elif deltas:
            all_deltas = deltas
        else:
            raise ValueError('必须提供 file_path 或 deltas 之一')
        if not all_deltas:
            raise ValueError('没有可应用的增量数据')
        created_shapes = []
        applied = 0
        _open_chunk(cmds, 'GT Apply Blendshape Deltas')
        try:
            for target_name, delta_map in all_deltas.items():
                # 复制网格作为形变目标
                duplicate = cmds.duplicate(
                    target_mesh, name='{}_shape#'.format(target_name),
                    returnRootsOnly=True,
                )[0]
                for vertex_str, delta in delta_map.items():
                    cmds.xform(
                        '{}.vtx[{}]'.format(duplicate, vertex_str),
                        relative=True, worldSpace=True, translation=delta,
                    )
                if as_blendshape:
                    blend_node = cmds.blendShape(
                        duplicate, target_mesh,
                        name='{}_bs#'.format(target_name),
                        focus=[0], weight=[0],
                    )[0]
                    created_shapes.append(blend_node)
                applied += 1
        finally:
            _close_chunk(cmds, 'GT Apply Blendshape Deltas')
        return {'ok': True, 'applied': applied, 'shapes': created_shapes}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 7. 相机快照
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='抓取相机全套属性为字典（GT Tools get_camera_data 移植）。'
                'transform + shape 40 余项属性一次抽取，用于镜头迁移。',
    category='animation',
    examples=[
        {
            'summary': '快照 shotCam 属性',
            'args': {'camera': 'shotCam'},
        },
    ],
    notes=[
        'camera 传 transform 名，自动找 shape。',
        'transform 侧取 translate/rotate/scale/visibility/rotateOrder。',
        'shape 侧取焦距/胶片/裁剪面/DOF/输出等常规属性。',
    ],
    returns_desc='dict {"ok": True, "camera": 相机名, "data": {"transform": {...}, "shape": {...}}}',
    prerequisites=['camera 必须存在'],
)
def snapshot_gt_camera(camera):
    # type: (str) -> Dict[str, Any]
    """相机属性快照。

    :param camera: 相机 transform 名
    :returns: dict {"ok": True, "camera": ..., "data": {...}}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(camera):
            raise ValueError('相机不存在: {}'.format(camera))
        shapes = cmds.listRelatives(camera, shapes=True, fullPath=True) or []
        if not shapes:
            raise ValueError('相机下没有 shape: {}'.format(camera))
        cam_shape = shapes[0]
        transform_data = {
            'translate': list(cmds.getAttr('{}.translate'.format(camera))[0]),
            'rotate': list(cmds.getAttr('{}.rotate'.format(camera))[0]),
            'scale': list(cmds.getAttr('{}.scale'.format(camera))[0]),
            'visibility': bool(cmds.getAttr('{}.visibility'.format(camera))),
            'rotateOrder': int(cmds.getAttr('{}.rotateOrder'.format(camera))),
        }
        shape_data = {}
        for attr in _CAMERA_SHAPE_ATTRS:
            if not cmds.attributeQuery(attr, node=cam_shape, exists=True):
                continue
            try:
                value = cmds.getAttr('{}.{}'.format(cam_shape, attr))
                if isinstance(value, (list, tuple)):
                    value = list(value[0]) if value else None
                shape_data[attr] = value
            except Exception:  # pylint: disable=broad-except
                continue
        return {
            'ok': True,
            'camera': camera,
            'data': {'transform': transform_data, 'shape': shape_data},
        }

    return run_on_main(_do)


@tool(
    dcc=['maya'],
    description='把相机快照数据应用回目标相机（GT Tools apply_camera_data 移植）。'
                '与 snapshot_gt_camera 配对使用。',
    category='animation',
    examples=[
        {
            'summary': '把快照恢复到新相机',
            'args': {
                'camera': 'shotCam_new',
                'data': '{"transform": {"translate": [0, 0, 0]}, "shape": {"focalLength": 35.0}}',
            },
        },
    ],
    notes=[
        'data 结构与 snapshot_gt_camera 的返回一致（{"transform": {...}, "shape": {...}}）。',
        'shape 属性逐项应用，不存在的属性自动跳过。',
        '缺失键不覆盖对应属性（部分应用安全）。',
    ],
    returns_desc='dict {"ok": True, "applied": 应用属性数}',
    prerequisites=['camera 必须存在'],
)
def apply_gt_camera(camera, data):
    # type: (str, Dict[str, Any]) -> Dict[str, Any]
    """相机属性应用。

    :param camera: 目标相机 transform 名
    :param data: 快照数据字典
    :returns: dict {"ok": True, "applied": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(camera):
            raise ValueError('相机不存在: {}'.format(camera))
        shapes = cmds.listRelatives(camera, shapes=True, fullPath=True) or []
        if not shapes:
            raise ValueError('相机下没有 shape: {}'.format(camera))
        cam_shape = shapes[0]
        applied = 0
        _open_chunk(cmds, 'GT Apply Camera')
        try:
            transform_data = data.get('transform', {}) if data else {}
            if 'translate' in transform_data:
                cmds.setAttr('{}.translate'.format(camera), *transform_data['translate'])
                applied += 1
            if 'rotate' in transform_data:
                cmds.setAttr('{}.rotate'.format(camera), *transform_data['rotate'])
                applied += 1
            if 'scale' in transform_data:
                cmds.setAttr('{}.scale'.format(camera), *transform_data['scale'])
                applied += 1
            if 'visibility' in transform_data:
                cmds.setAttr('{}.visibility'.format(camera), transform_data['visibility'])
                applied += 1
            if 'rotateOrder' in transform_data:
                cmds.setAttr('{}.rotateOrder'.format(camera), transform_data['rotateOrder'])
                applied += 1
            shape_data = data.get('shape', {}) if data else {}
            for attr, value in shape_data.items():
                if not cmds.attributeQuery(attr, node=cam_shape, exists=True):
                    continue
                try:
                    cmds.setAttr('{}.{}'.format(cam_shape, attr), value)
                    applied += 1
                except Exception:  # pylint: disable=broad-except
                    continue
        finally:
            _close_chunk(cmds, 'GT Apply Camera')
        return {'ok': True, 'applied': applied}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 8. Offset 变换
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='在目标对象与父级间插入 offset 变换并保世界变换不变'
                '（GT Tools add_offset_transform 移植）。',
    category='rigging',
    examples=[
        {
            'summary': '给控件插一个 offset 组（轴心与控件对齐）',
            'args': {'targets': 'ctrl_arm', 'transform_type': 'group'},
        },
        {
            'summary': '给关节插 offset locator（轴心取父级）',
            'args': {'targets': 'jnt_arm', 'transform_type': 'locator', 'pivot_source': 'parent'},
        },
    ],
    notes=[
        'transform_type: group / joint / locator。',
        'pivot_source: target（新变换轴心取目标自身）/ parent（取目标父级）。',
        '新变换命名为 <目标名>_offset。',
    ],
    returns_desc='dict {"ok": True, "offsets": 创建的 offset 列表}',
    prerequisites=['targets 必须存在'],
)
def add_gt_offset_transform(
    targets,
    transform_type='group',
    pivot_source='target',
    suffix='offset',
):
    # type: (Any, str, str, str) -> Dict[str, Any]
    """插入 offset 变换。

    :param targets: 目标列表
    :param transform_type: group / joint / locator
    :param pivot_source: target / parent
    :param suffix: 新变换命名后缀
    :returns: dict {"ok": True, "offsets": [...]}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        type_norm = str(transform_type or 'group').lower()
        if type_norm not in ('group', 'joint', 'locator'):
            raise ValueError('transform_type 仅支持 group/joint/locator: {}'.format(type_norm))
        pivot_norm = str(pivot_source or 'target').lower()
        if pivot_norm not in ('target', 'parent'):
            raise ValueError('pivot_source 仅支持 target/parent: {}'.format(pivot_norm))
        target_list = [n for n in _normalize_list(targets) if cmds.objExists(n)]
        if not target_list:
            raise ValueError('必须指定至少一个有效目标')
        offsets = []
        _open_chunk(cmds, 'GT Add Offset Transform')
        try:
            for target in target_list:
                short_name = _get_short_name(target)
                offset_name = '{}_{}'.format(short_name, suffix)
                if type_norm == 'group':
                    offset = cmds.group(empty=True, name=offset_name, world=True)
                elif type_norm == 'joint':
                    offset = cmds.joint(name=offset_name)
                else:
                    offset = cmds.spaceLocator(name=offset_name)[0]
                parent = cmds.listRelatives(target, parent=True, fullPath=True) or []
                # 轴心对齐
                match_source = target if pivot_norm == 'target' else (
                    parent[0] if parent else target
                )
                _match_world_transform(cmds, match_source, offset)
                # 挂接父子
                if parent:
                    cmds.parent(offset, parent[0])
                    cmds.parent(target, offset, absolute=True)
                else:
                    cmds.parent(target, offset, absolute=True)
                offsets.append(offset)
        finally:
            _close_chunk(cmds, 'GT Add Offset Transform')
        return {'ok': True, 'offsets': offsets}

    return run_on_main(_do)


def _match_world_transform(cmds, source, target):
    # type: (object, str, str) -> None
    """把 target 世界矩阵对齐到 source。"""
    matrix = cmds.getAttr('{}.worldMatrix'.format(source))
    cmds.xform(target, worldSpace=True, matrix=matrix)


# ---------------------------------------------------------------------- #
# 9. 四元数扭转网络
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='创建纯 Maya 原生节点的四元数扭转提取网络（GT Tools create_twist_network 移植）。'
                '驱动关节局部四元数投影到指定轴，经 blendMatrix 加权后写入 driven 的 offsetParentMatrix。',
    category='rigging',
    examples=[
        {
            'summary': '给前臂建扭转传递（X 轴）',
            'args': {'driver': 'elbow_jnt', 'driven': 'wrist_jnt', 'twist_axis': 'x'},
        },
    ],
    notes=[
        'twist_axis: x / y / z（局部旋转投影轴）。',
        'twist_weight -1~1，1 为全额传递。',
        '网络节点链：multMatrix -> decomposeMatrix -> condition -> composeMatrix -> blendMatrix -> multMatrix。',
        'driven 的 offsetParentMatrix 必须无已有输入。',
    ],
    returns_desc='dict {"ok": True, "network": network 节点名, "nodes": 创建节点列表}',
    prerequisites=['driver/driven 必须存在'],
)
def create_gt_twist_network(driver, driven, twist_axis='x', twist_weight=1.0):
    # type: (str, str, str, float) -> Dict[str, Any]
    """四元数扭转网络。

    :param driver: 驱动 transform
    :param driven: 被驱动 transform（接收 offsetParentMatrix）
    :param twist_axis: 投影轴 x/y/z
    :param twist_weight: 扭转权重 -1~1
    :returns: dict {"ok": True, "network": ..., "nodes": [...]}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(driver):
            raise ValueError('驱动不存在: {}'.format(driver))
        if not cmds.objExists(driven):
            raise ValueError('被驱动不存在: {}'.format(driven))
        axis = str(twist_axis).upper()
        if axis not in ('X', 'Y', 'Z'):
            raise ValueError('twist_axis 仅支持 x/y/z: {}'.format(twist_axis))
        weight = float(twist_weight)
        if not -1.0 <= weight <= 1.0:
            raise ValueError('twist_weight 必须在 -1 到 1 之间')
        offset_attr = '{}.offsetParentMatrix'.format(driven)
        existing = cmds.listConnections(
            offset_attr, source=True, destination=False, plugs=True,
        ) or []
        if existing:
            raise RuntimeError(
                '被驱动的 offsetParentMatrix 已有输入: {}'.format(existing[0]),
            )
        short = _get_short_name(driven)
        created = []
        _open_chunk(cmds, 'GT Twist Network')
        try:
            # 设置存储节点
            network = cmds.createNode('network', name='{}_twistNode'.format(short))
            cmds.addAttr(
                network, longName='twist', attributeType='double',
                defaultValue=weight, minValue=-1.0, maxValue=1.0, keyable=True,
            )
            cmds.addAttr(network, longName='targetRestMatrix', dataType='matrix')
            created.append(network)
            # 节点链
            local_matrix = cmds.createNode('multMatrix', name='{}_twistLocal'.format(short))
            decompose = cmds.createNode('decomposeMatrix', name='{}_twistDecompose'.format(short))
            negate = cmds.createNode('multiplyDivide', name='{}_twistNegate'.format(short))
            sign_cond = cmds.createNode('condition', name='{}_twistSign'.format(short))
            compose = cmds.createNode('composeMatrix', name='{}_twistCompose'.format(short))
            blend = cmds.createNode('blendMatrix', name='{}_twistBlend'.format(short))
            output = cmds.createNode('multMatrix', name='{}_twistOutput'.format(short))
            created.extend([local_matrix, decompose, negate, sign_cond, compose, blend, output])
            # 驱动局部矩阵 = worldMatrix * parentInverseMatrix
            cmds.connectAttr('{}.worldMatrix[0]'.format(driver), '{}.matrixIn[0]'.format(local_matrix))
            cmds.connectAttr(
                '{}.parentInverseMatrix[0]'.format(driver),
                '{}.matrixIn[1]'.format(local_matrix),
            )
            cmds.connectAttr('{}.matrixSum'.format(local_matrix), '{}.inputMatrix'.format(decompose))
            # 四元数分量投影与符号处理
            quat_attr = '{}.outputQuat{}'.format(decompose, axis)
            cmds.connectAttr(quat_attr, '{}.input1X'.format(negate))
            cmds.connectAttr('{}.twist'.format(network), '{}.input1Y'.format(negate))
            cmds.setAttr('{}.input2'.format(negate), -1, -1, 1, type='double3')
            cmds.setAttr('{}.operation'.format(sign_cond), 4)
            cmds.connectAttr('{}.twist'.format(network), '{}.firstTerm'.format(sign_cond))
            cmds.connectAttr('{}.outputX'.format(negate), '{}.colorIfTrueR'.format(sign_cond))
            cmds.connectAttr(quat_attr, '{}.colorIfFalseR'.format(sign_cond))
            cmds.connectAttr('{}.outputY'.format(negate), '{}.colorIfTrueG'.format(sign_cond))
            cmds.connectAttr('{}.twist'.format(network), '{}.colorIfFalseG'.format(sign_cond))
            # 四元数重组 + blendMatrix 加权
            cmds.setAttr('{}.useEulerRotation'.format(compose), False)
            cmds.connectAttr('{}.outColorR'.format(sign_cond), '{}.inputQuat{}'.format(compose, axis))
            cmds.connectAttr('{}.outputQuatW'.format(decompose), '{}.inputQuatW'.format(compose))
            cmds.connectAttr('{}.outputMatrix'.format(compose), '{}.target[0].targetMatrix'.format(blend))
            cmds.connectAttr('{}.outColorG'.format(sign_cond), '{}.target[0].weight'.format(blend))
            cmds.setAttr('{}.target[0].translateWeight'.format(blend), 0)
            cmds.setAttr('{}.target[0].scaleWeight'.format(blend), 0)
            cmds.setAttr('{}.target[0].shearWeight'.format(blend), 0)
            # 输出矩阵 -> offsetParentMatrix
            cmds.connectAttr('{}.outputMatrix'.format(blend), '{}.matrixIn[0]'.format(output))
            cmds.connectAttr('{}.targetRestMatrix'.format(network), '{}.matrixIn[1]'.format(output))
            cmds.connectAttr('{}.matrixSum'.format(output), offset_attr)
        finally:
            _close_chunk(cmds, 'GT Twist Network')
        return {'ok': True, 'network': network, 'nodes': created}

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 10. 世界钉住
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='在世界空间创建钉住 locator 并按 Follow 开关键帧烘焙回目标'
                '（ML Tools snapBake 世界钉移植）。Follow 关闭的帧保持目标原动画。',
    category='animation',
    examples=[
        {
            'summary': '把手控件钉在世界空间 1-24 帧（10-20 帧跟随钉子，其余保持原动画）',
            'args': {
                'node': 'ctrl_hand', 'pin_frames': [10, 20],
                'follow_frames': [[10, 20]],
            },
        },
    ],
    notes=[
        '流程：创建 locator 抓取当前世界矩阵 -> 在 pin_frames 区间烘焙 node 到 locator -> '
        'follow_frames 指定哪些帧实际生效。',
        'follow_frames 传 [[start, end], ...] 区间列表，区间外帧不打键（保留原动画）。',
        'locator 命名 snapPin_<node>_#，烘焙后自动删除。',
    ],
    returns_desc='dict {"ok": True, "locator": 钉子名, "baked_frames": 实际打键帧数}',
    prerequisites=['node 必须存在'],
)
def pin_ml_world(node, pin_frames, follow_frames=None):
    # type: (str, List[float], List[List[float]]) -> Dict[str, Any]
    """世界钉住烘焙。

    :param node: 目标 transform
    :param pin_frames: [start, end] 钉住区间
    :param follow_frames: [[start, end], ...] 生效区间列表；None 表示全区间生效
    :returns: dict {"ok": True, "locator": ..., "baked_frames": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(node):
            raise ValueError('节点不存在: {}'.format(node))
        if not isinstance(pin_frames, (list, tuple)) or len(pin_frames) < 2:
            raise ValueError('pin_frames 必须是 [start, end]')
        pin_start, pin_end = float(pin_frames[0]), float(pin_frames[1])
        if pin_start >= pin_end:
            raise ValueError('pin_frames start 必须小于 end')
        # 解析生效帧集合
        if follow_frames:
            follow_set = set()
            for segment in follow_frames:
                if not isinstance(segment, (list, tuple)) or len(segment) < 2:
                    raise ValueError('follow_frames 必须是 [[start, end], ...]')
                seg_start, seg_end = int(math.ceil(float(segment[0]))), int(math.floor(float(segment[1])))
                follow_set.update(range(seg_start, seg_end + 1))
        else:
            follow_set = set(range(int(math.ceil(pin_start)), int(math.floor(pin_end)) + 1))
        short = _get_short_name(node)
        locator = cmds.spaceLocator(name='snapPin_{}_#'.format(short))[0]
        cmds.setAttr('{}.rotateOrder'.format(locator), 3)
        current_time = cmds.currentTime(query=True)
        baked_frames = 0
        _open_chunk(cmds, 'ML World Pin')
        try:
            # locator 抓当前世界矩阵（在区间起点）
            cmds.currentTime(pin_start, edit=True)
            world_matrix = cmds.getAttr('{}.worldMatrix'.format(node))
            cmds.xform(locator, worldSpace=True, matrix=world_matrix)
            # 烘焙目标到钉子
            constraint = cmds.parentConstraint(
                locator, node, maintainOffset=True,
            )[0]
            attrs = ['translateX', 'translateY', 'translateZ', 'rotateX', 'rotateY', 'rotateZ']
            try:
                for frame in range(int(math.ceil(pin_start)), int(math.floor(pin_end)) + 1):
                    if frame not in follow_set:
                        continue
                    cmds.currentTime(frame, edit=True)
                    for attr in attrs:
                        value = cmds.getAttr('{}.{}'.format(node, attr))
                        cmds.setKeyframe(node, attribute=attr, time=frame, value=value)
                    baked_frames += 1
            finally:
                cmds.delete(constraint)
        finally:
            cmds.currentTime(current_time, edit=True)
            cmds.delete(locator)
            _close_chunk(cmds, 'ML World Pin')
        return {'ok': True, 'locator': locator, 'baked_frames': baked_frames}

    return run_on_main(_do)


__all__ = [
    'export_gt_skin_weights',
    'import_gt_skin_weights',
    'export_gt_driven_keys',
    'import_gt_driven_keys',
    'export_gt_blendshape_deltas',
    'apply_gt_blendshape_deltas',
    'snapshot_gt_camera',
    'apply_gt_camera',
    'add_gt_offset_transform',
    'create_gt_twist_network',
    'pin_ml_world',
]
