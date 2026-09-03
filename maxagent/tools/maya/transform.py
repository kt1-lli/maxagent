#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 变换类工具：移动、旋转、缩放。"""

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


# ---------------------------------------------------------------------- #
# 内部辅助
# ---------------------------------------------------------------------- #


def _to_float_list(value: Any, name: str = 'value') -> Optional[List[float]]:
    """把 JSON 字符串或列表转为 float 列表。"""
    import json  # pylint: disable=import-outside-toplevel
    if value is None:
        return None
    coords = value
    if isinstance(coords, str):
        try:
            coords = json.loads(coords)
        except json.JSONDecodeError as exc:
            raise ValueError(
                '{} 字符串不是合法 JSON: {} ({})'.format(name, value, exc),
            ) from exc
    try:
        return [float(v) for v in coords]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            '{} 参数解析失败: {} ({})'.format(name, value, exc),
        ) from exc


# ---------------------------------------------------------------------- #
# 工具实现
# ---------------------------------------------------------------------- #

@tool(
    dcc=['maya'],
    description="移动 Maya 对象到指定位置或按偏移量相对移动。",
    category="transform",
    examples=[
        {
            'summary': '把对象移到世界坐标 (100, 0, 50)',
            'args': {'name': 'pCube1', 'x': 100.0, 'y': 0.0, 'z': 50.0, 'mode': 'set'},
        },
    ],
    notes=[
        'mode="set" 表示设置绝对世界坐标位置；mode="add" 表示在当前位置上叠加偏移量。',
        'x/y/z 为世界空间坐标值，单位与当前场景一致。',
    ],
    returns_desc='dict {"name": 对象名, "position": [x, y, z]}',
    prerequisites=['场景中必须存在名为 name 的对象'],
)
def move_maya_object(name: str, x: float = 0.0, y: float = 0.0, z: float = 0.0, mode: str = 'set'):
    # type: (...) -> Dict[str, Any]
    """移动 Maya 对象。"""
    _ensure_in_maya()

    def _do():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(name):
            raise ValueError('对象不存在: {}'.format(name))
        if mode == 'set':
            cmds.xform(name, translation=[float(x), float(y), float(z)], worldSpace=True)
        elif mode == 'add':
            cmds.move(float(x), float(y), float(z), name, relative=True, worldSpace=True)
        else:
            raise ValueError('mode 只能是 set 或 add: {}'.format(mode))
        pos = cmds.xform(name, query=True, translation=True, worldSpace=True) or [0, 0, 0]
        return [float(pos[0]), float(pos[1]), float(pos[2])]

    position = run_on_main(_do)
    return {'name': name, 'position': position}


@tool(
    dcc=['maya'],
    description="旋转 Maya 对象到指定欧拉角或按偏移量相对旋转。",
    category="transform",
    examples=[
        {
            'summary': '把对象设置为绕 Z 轴旋转 45 度',
            'args': {'name': 'pCube1', 'x': 0.0, 'y': 0.0, 'z': 45.0, 'mode': 'set'},
        },
    ],
    notes=[
        'mode="set" 表示设置绝对旋转；mode="add" 表示相对旋转。',
        'x/y/z 为欧拉角，单位为度。',
    ],
    returns_desc='dict {"name": 对象名, "rotation_euler": [x, y, z]}',
    prerequisites=['场景中必须存在名为 name 的对象'],
)
def rotate_maya_object(name: str, x: float = 0.0, y: float = 0.0, z: float = 0.0, mode: str = 'set'):
    # type: (...) -> Dict[str, Any]
    """旋转 Maya 对象。"""
    _ensure_in_maya()

    def _do():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(name):
            raise ValueError('对象不存在: {}'.format(name))
        if mode == 'set':
            cmds.xform(name, rotation=[float(x), float(y), float(z)], worldSpace=True)
        elif mode == 'add':
            cmds.rotate(float(x), float(y), float(z), name, relative=True, objectSpace=True)
        else:
            raise ValueError('mode 只能是 set 或 add: {}'.format(mode))
        rot = cmds.xform(name, query=True, rotation=True, worldSpace=True) or [0, 0, 0]
        return [float(rot[0]), float(rot[1]), float(rot[2])]

    rotation = run_on_main(_do)
    return {'name': name, 'rotation_euler': rotation}


@tool(
    dcc=['maya'],
    description="缩放 Maya 对象。",
    category="transform",
    examples=[
        {
            'summary': '把对象各轴缩放设为 2',
            'args': {'name': 'pCube1', 'x': 2.0, 'y': 2.0, 'z': 2.0},
        },
    ],
    notes=[
        'mode="set" 表示设置绝对缩放；mode="add" 表示相对缩放。',
    ],
    returns_desc='dict {"name": 对象名, "scale": [x, y, z]}',
    prerequisites=['场景中必须存在名为 name 的对象'],
)
def scale_maya_object(name: str, x: float = 1.0, y: float = 1.0, z: float = 1.0, mode: str = 'set'):
    # type: (...) -> Dict[str, Any]
    """缩放 Maya 对象。"""
    _ensure_in_maya()

    def _do():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(name):
            raise ValueError('对象不存在: {}'.format(name))
        if mode == 'set':
            cmds.xform(name, scale=[float(x), float(y), float(z)], worldSpace=True)
        elif mode == 'add':
            cmds.scale(float(x), float(y), float(z), name, relative=True)
        else:
            raise ValueError('mode 只能是 set 或 add: {}'.format(mode))
        scl = cmds.xform(name, query=True, scale=True, worldSpace=True) or [1, 1, 1]
        return [float(scl[0]), float(scl[1]), float(scl[2])]

    scale = run_on_main(_do)
    return {'name': name, 'scale': scale}


__all__ = [
    'move_maya_object',
    'rotate_maya_object',
    'scale_maya_object',
]
