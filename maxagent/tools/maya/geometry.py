#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 几何体创建类工具。

提供 box / sphere / cylinder 等基础图元创建，并统一处理位置与旋转。
所有工具都会返回创建出来的 transform 节点名，方便 agent 后续操作。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
from typing import Any
from typing import List
from typing import Optional
from typing import Tuple

from ...dcc.runtime import current_dcc
from ...dcc.runtime import run_on_main
from ...tools.registry import tool


_POSITION_TOLERANCE = 0.01


def _ensure_in_maya():
    # type: () -> None
    """确保当前运行在 Maya 环境，否则抛出 RuntimeError。"""
    if current_dcc() != 'maya':
        raise RuntimeError('非 Maya 环境')


def _to_xyz_list(value, name='position'):
    # type: (Any, str) -> Optional[Tuple[float, float, float]]
    """把 [x, y, z] 列表/元组/JSON字符串转为三元组，非法输入抛 ValueError。"""
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
        if len(coords) != 3:
            raise ValueError(
                '{} 必须是包含 3 个数值的列表/元组: {}'.format(name, value),
            )
        return (float(coords[0]), float(coords[1]), float(coords[2]))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            '{} 参数解析失败: {} ({})'.format(name, value, exc),
        ) from exc


def _apply_transform(name: str, position: Any = None, rotation_euler: Any = None):
    # type: (str, Any, Any) -> str
    """统一处理对象创建后的世界空间位置与旋转。"""
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    pos = _to_xyz_list(position, name='position')
    rot = _to_xyz_list(rotation_euler, name='rotation_euler')
    if pos is not None:
        cmds.xform(name, translation=list(pos), worldSpace=True)
        # 硬校验：写入后读回坐标，偏差过大抛错
        actual = cmds.xform(name, query=True, translation=True, worldSpace=True) or [0, 0, 0]
        if (
            abs(float(actual[0]) - pos[0]) > _POSITION_TOLERANCE
            or abs(float(actual[1]) - pos[1]) > _POSITION_TOLERANCE
            or abs(float(actual[2]) - pos[2]) > _POSITION_TOLERANCE
        ):
            raise RuntimeError(
                'position 未生效: 期望 [{},{},{}], 实际 [{},{},{}]'.format(
                    pos[0], pos[1], pos[2],
                    actual[0], actual[1], actual[2],
                ),
            )
    if rot is not None:
        cmds.xform(name, rotation=list(rot), worldSpace=True)
    return name


# ---------------------------------------------------------------------- #
# 工具实现
# ---------------------------------------------------------------------- #

@tool(
    dcc=['maya'],
    description="在 Maya 中创建一个多边形立方体。",
    category="geometry",
    examples=[
        {
            'summary': '在 (10,0,10) 创建一个立方体',
            'args': {'name': 'myCube', 'width': 1, 'height': 1, 'depth': 1, 'position': '[10,0,10]'},
        },
    ],
    notes=[
        'position 为 JSON 字符串如 "[10,0,10]"。',
        '未指定 name 时 Maya 会自动命名为 pCube1, pCube2 等。',
    ],
    returns_desc='dict {"name": transform 节点名, "type": "polyCube"}',
)
def create_maya_box(
    name: str = "",
    width: float = 1.0,
    height: float = 1.0,
    depth: float = 1.0,
    position: str = "",
    rotation_euler: str = "",
):
    # type: (...) -> Dict[str, Any]
    """创建 Maya 立方体。"""
    _ensure_in_maya()
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _make():
        kwargs = {
            'width': float(width),
            'height': float(height),
            'depth': float(depth),
        }
        if name:
            kwargs['name'] = name
        transform, _ = cmds.polyCube(**kwargs)
        _apply_transform(transform, position=position, rotation_euler=rotation_euler)
        return transform

    transform = run_on_main(_make)
    return {'name': transform, 'type': 'polyCube'}


@tool(
    dcc=['maya'],
    description="在 Maya 中创建一个多边形球体。",
    category="geometry",
    examples=[
        {
            'summary': '在中心创建一个半径为 3 的球体',
            'args': {'radius': 3.0, 'subdivisions_axis': 20, 'subdivisions_height': 20},
        },
    ],
    notes=[
        'position 为 JSON 字符串如 "[0,5,0]"。',
    ],
    returns_desc='dict {"name": transform 节点名, "type": "polySphere"}',
)
def create_maya_sphere(
    name: str = "",
    radius: float = 1.0,
    subdivisions_axis: int = 20,
    subdivisions_height: int = 20,
    position: str = "",
    rotation_euler: str = "",
):
    # type: (...) -> Dict[str, Any]
    """创建 Maya 球体。"""
    _ensure_in_maya()

    def _make():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        kwargs = {
            'radius': float(radius),
            'subdivisionsX': int(subdivisions_axis),
            'subdivisionsY': int(subdivisions_height),
        }
        if name:
            kwargs['name'] = name
        transform, _ = cmds.polySphere(**kwargs)
        _apply_transform(transform, position=position, rotation_euler=rotation_euler)
        return transform

    transform = run_on_main(_make)
    return {'name': transform, 'type': 'polySphere'}


@tool(
    dcc=['maya'],
    description="在 Maya 中创建一个多边形圆柱体。",
    category="geometry",
    examples=[
        {
            'summary': '在中心创建一个圆柱',
            'args': {'radius': 1.0, 'height': 2.0},
        },
    ],
    notes=[
        'position 为 JSON 字符串如 "[0,1,0]"。',
    ],
    returns_desc='dict {"name": transform 节点名, "type": "polyCylinder"}',
)
def create_maya_cylinder(
    name: str = "",
    radius: float = 1.0,
    height: float = 2.0,
    subdivisions_axis: int = 20,
    position: str = "",
    rotation_euler: str = "",
):
    # type: (...) -> Dict[str, Any]
    """创建 Maya 圆柱体。"""
    _ensure_in_maya()

    def _make():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        kwargs = {
            'radius': float(radius),
            'height': float(height),
            'subdivisionsX': int(subdivisions_axis),
        }
        if name:
            kwargs['name'] = name
        transform, _ = cmds.polyCylinder(**kwargs)
        _apply_transform(transform, position=position, rotation_euler=rotation_euler)
        return transform

    transform = run_on_main(_make)
    return {'name': transform, 'type': 'polyCylinder'}


__all__ = [
    'create_maya_box',
    'create_maya_sphere',
    'create_maya_cylinder',
]