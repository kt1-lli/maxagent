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
from ._common import _ensure_in_maya
from ...tools.registry import tool


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


__all__ = [
    'get_maya_info',
    'list_maya_objects',
    'get_maya_selection',
    'get_maya_object_info',
]
