#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 场景查询类工具。

提供给 agent 的"读"能力：列举对象、查询选中、统计信息等。
全部为只读操作，wrap_undo=False（无需 undo）。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ...dcc.runtime import current_dcc
from ...dcc.runtime import run_on_main
from ._common import _ensure_in_maya
from ...tools.registry import tool


# Maya 时间单位名 -> FPS 映射（currentUnit -q -time 返回值）
_TIME_UNIT_FPS = {
    'game': 15,
    'film': 24,
    'fps': 24,
    'show': 48,
    'pal': 25,
    'ntsc': 30,
    'ntscf': 60,
    'palf': 50,
    'hour': 3600,
    'minute': 60,
    'sec': 1,
}


# ---------------------------------------------------------------------- #
# 内部辅助
# ---------------------------------------------------------------------- #


def _node_to_dict(name: str, detail: bool = False) -> Dict[str, Any]:
    """把 Maya DAG 对象名转成 LLM 友好的 dict。"""
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    info: Dict[str, Any] = {
        'name': name,
        'type': cmds.objectType(name),
    }
    # visibility 只在 DAG 节点上存在，DG 节点（如 time1、defaultRenderGlobals）没有此属性
    if cmds.attributeQuery('visibility', node=name, exists=True):
        try:
            info['visible'] = bool(cmds.getAttr(name + '.visibility'))
        except Exception:  # pylint: disable=broad-except
            pass
    if detail:
        try:
            tx, ty, tz = cmds.xform(name, query=True, translation=True, worldSpace=True) or (0, 0, 0)
            info['position'] = [float(tx), float(ty), float(tz)]
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            rx, ry, rz = cmds.xform(name, query=True, rotation=True, worldSpace=True) or (0, 0, 0)
            info['rotation_euler'] = [float(rx), float(ry), float(rz)]
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            sx, sy, sz = cmds.xform(name, query=True, scale=True, worldSpace=True) or (1, 1, 1)
            info['scale'] = [float(sx), float(sy), float(sz)]
        except Exception:  # pylint: disable=broad-except
            pass
    return info


# ---------------------------------------------------------------------- #
# 工具实现
# ---------------------------------------------------------------------- #

@tool(
    dcc=['maya'],
    description="获取 Maya 当前版本与基本信息（版本号、产品名、当前打开的文件名等）。",
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '查询当前 Maya 版本与文件路径',
            'args': {},
        },
    ],
    notes=[
        '无需任何参数，返回结果可能包含 version_year / product / current_file / current_dir 等字段。',
        '未保存场景时 current_file 通常为空白字符串或 "untitled"。',
    ],
    returns_desc='dict {"version_year": str | null, "product": str, "current_file": str, ...}',
)
def get_maya_info():
    # type: () -> Dict[str, Any]
    """获取 Maya 基本信息。"""
    _ensure_in_maya()
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    info: Dict[str, Any] = {
        'product': 'Autodesk Maya',
        'version_year': None,
        'current_file': cmds.file(query=True, sceneName=True) or '',
    }
    try:
        info['version_year'] = cmds.about(version=True)
    except Exception:  # pylint: disable=broad-except
        pass
    return info


@tool(
    dcc=['maya'],
    description="列出 Maya 当前场景中的对象。",
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '列出场景中的所有 transform',
            'args': {'object_type': 'transform', 'detail': False},
        },
        {
            'summary': '只取前 30 个对象（LLM 场景快照场景）',
            'args': {'object_type': '', 'limit': 30, 'detail': False},
        },
    ],
    notes=[
        'object_type 为空时返回所有 DAG 对象（transform）。',
        'detail=True 会附加每个对象的位置、旋转、缩放。',
        'limit>0 时返回 dict {"items": [...], "total": N}；limit<=0 或省略时直接返回 list。',
        '默认 object_type 过滤只在 DAG 节点上有效；如需查 time1 等 DG 节点，请指定 object_type。',
    ],
    returns_desc=(
        'list[dict] 或 dict {"items": list[dict], "total": int}（当 limit>0 时）'
    ),
)
def list_maya_objects(object_type: str = "", detail: bool = False, limit: int = 0):
    # type: (str, bool, int) -> Any
    """列出 Maya 场景对象。

    :param object_type: 对象类型过滤，如 "transform" / "mesh" / "joint"，空串表示不过滤
    :param detail: 是否附加 transform 位置/旋转/缩放信息
    :param limit: >0 时截断结果并返回带 total 的 dict；<=0 时返回 list
    """
    _ensure_in_maya()
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    kwargs: Dict[str, Any] = {'long': True}
    if object_type:
        kwargs['type'] = object_type
    else:
        # 不指定 type 时默认只取 DAG 对象，避免把 time1 等 DG 节点也拉进来
        kwargs['dag'] = True
    nodes = cmds.ls(**kwargs) or []
    total = len(nodes)
    if limit and limit > 0:
        nodes = nodes[:int(limit)]
        return {
            'items': [_node_to_dict(n, detail=detail) for n in nodes],
            'total': total,
        }
    return [_node_to_dict(n, detail=detail) for n in nodes]


@tool(
    dcc=['maya'],
    description="获取 Maya 当前选中的对象列表。",
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '查询当前选中对象',
            'args': {'detail': False},
        },
    ],
    notes=[
        '未选中任何对象时返回空列表。',
    ],
    returns_desc='list[dict {"name": str, "type": str, ...}]',
)
def get_maya_selection(detail: bool = False):
    # type: (bool) -> List[Dict[str, Any]]
    """获取当前选中的 Maya 对象。"""
    _ensure_in_maya()
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    nodes = cmds.ls(selection=True, long=True) or []
    return [_node_to_dict(n, detail=detail) for n in nodes]


@tool(
    dcc=['maya'],
    description="按名称查找 Maya 对象并返回其信息。",
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '查询 pCube1 的信息',
            'args': {'name': 'pCube1', 'detail': True},
        },
    ],
    notes=[
        '对象不存在时返回 {"exists": False}。',
    ],
    returns_desc='dict {"exists": True, ...} 或 {"exists": False}',
)
def get_maya_object_info(name: str, detail: bool = False):
    # type: (str, bool) -> Dict[str, Any]
    """按名称查询 Maya 对象信息。"""
    _ensure_in_maya()
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    if not cmds.objExists(name):
        return {'exists': False}
    info = _node_to_dict(name, detail=detail)
    info['exists'] = True
    return info


@tool(
    dcc=['maya'],
    description="获取 Maya 场景统计信息（对象数、材质数、灯光数、相机数、帧范围等）。",
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '获取场景整体统计',
            'args': {},
        },
    ],
    notes=[
        '各计数字段为估计值：objects 指所有 DAG transform，meshes 指 mesh 形节点。',
        'frame_range 取自 playbackSlider 的 playback 范围（-inf/-inf 时回退到 1-24）。',
    ],
    returns_desc=(
        'dict {"objects": int, "meshes": int, "materials": int, "lights": int, '
        '"cameras": int, "joints": int, "references": int, '
        '"frame_range": [start, end], "current_frame": float}'
    ),
)
def get_maya_scene_stats():
    # type: () -> Dict[str, Any]
    """获取 Maya 场景统计信息。"""
    _ensure_in_maya()
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        # 各类型节点计数（mesh 数的是 shape 节点）
        stats: Dict[str, Any] = {
            'objects': len(cmds.ls(dag=True, transforms=True) or []),
            'meshes': len(cmds.ls(type='mesh') or []),
            'materials': len(cmds.ls(mat=True) or []),
            'lights': len(cmds.ls(lights=True) or []),
            'cameras': len(cmds.ls(cameras=True) or []),
            'joints': len(cmds.ls(type='joint') or []),
            'references': len(cmds.ls(type='reference') or []) - 1,
        }
        # 帧范围（playbackOptions 记录的是 UI 播放范围）
        stats['frame_range'] = [
            float(cmds.playbackOptions(query=True, minTime=True)),
            float(cmds.playbackOptions(query=True, maxTime=True)),
        ]
        try:
            stats['current_frame'] = float(cmds.currentTime(query=True))
        except Exception:  # pylint: disable=broad-except
            pass
        return stats

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description="获取 Maya 当前播放范围与时间相关设置（FPS、当前帧、播放范围、动画范围）。",
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '查询时间与播放设置',
            'args': {},
        },
    ],
    notes=[
        'fps 单位是帧每秒；无法识别的帧率返回 None。',
        'playback_range 来自 playbackOptions；animation_range 取时间轴上下限。',
    ],
    returns_desc=(
        'dict {"current_frame": float, "fps": int | None, "playback_range": [start, end], '
        '"animation_range": [start, end]}'
    ),
)
def get_maya_time_info():
    # type: () -> Dict[str, Any]
    """获取 Maya 时间与播放设置信息。"""
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        info: Dict[str, Any] = {
            'current_frame': float(cmds.currentTime(query=True)),
            'fps': None,
            'playback_range': [
                float(cmds.playbackOptions(query=True, minTime=True)),
                float(cmds.playbackOptions(query=True, maxTime=True)),
            ],
            'animation_range': [
                float(cmds.playbackOptions(query=True, animationStartTime=True)),
                float(cmds.playbackOptions(query=True, animationEndTime=True)),
            ],
        }
        # 帧率识别失败不阻断主流程
        try:
            unit = cmds.currentUnit(query=True, time=True)
            info['fps'] = _TIME_UNIT_FPS.get(unit)
        except Exception:  # pylint: disable=broad-except
            pass
        return info

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description="按名称模式（通配符）查找 Maya 对象，返回匹配的节点名列表。",
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '查找所有名字含 cube 的对象',
            'args': {'pattern': '*cube*'},
        },
        {
            'summary': '查找所有 transform 顶层节点',
            'args': {'pattern': '*', 'object_type': 'transform'},
        },
    ],
    notes=[
        'pattern 支持 Maya 通配符语法：* 任意长度、? 单字符、[abc] 字符集。',
        'object_type 可选过滤，如 mesh / joint / camera；为空表示不过滤。',
        '返回带路径的长名，重名对象（父子同名）可以据此区分。',
    ],
    returns_desc='list[str]: 匹配的节点长名列表',
)
def find_maya_objects_by_name(pattern: str, object_type: str = ""):
    # type: (str, str) -> List[str]
    """按名称模式查找 Maya 对象。

    :param pattern: 通配符模式，如 "*cube*"、"*Ctrl"
    :param object_type: 可选类型过滤，如 "mesh" / "joint"；空串不过滤
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not pattern:
            raise ValueError('pattern 不能为空')
        kwargs: Dict[str, Any] = {'long': True}
        if object_type:
            kwargs['type'] = object_type
        else:
            # 与 list_maya_objects 一致：默认只搜 DAG 对象
            kwargs['dag'] = True
        return list(cmds.ls(pattern, **kwargs) or [])

    return run_on_main(_impl)


__all__ = [
    'get_maya_info',
    'list_maya_objects',
    'get_maya_selection',
    'get_maya_object_info',
    'get_maya_scene_stats',
    'get_maya_time_info',
    'find_maya_objects_by_name',
]
