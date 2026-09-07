#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 几何体创建类工具。

提供 box / sphere / cylinder 等基础图元创建，并统一处理位置与旋转。
所有工具都会返回创建出来的 transform 节点名，方便 agent 后续操作。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import List
from typing import Optional
from typing import Tuple

from ...dcc.runtime import current_dcc
from ...dcc.runtime import run_on_main
from ._common import _ensure_in_maya, _to_xyz_list, rollback_on_error
from ...tools.registry import tool


_POSITION_TOLERANCE = 0.01


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
        with rollback_on_error([transform]):
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
        with rollback_on_error([transform]):
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
        with rollback_on_error([transform]):
            _apply_transform(transform, position=position, rotation_euler=rotation_euler)
        return transform

    transform = run_on_main(_make)
    return {'name': transform, 'type': 'polyCylinder'}


@tool(
    dcc=['maya'],
    description="在 Maya 中创建一个多边形圆环。",
    category="geometry",
    examples=[
        {
            'summary': '创建一个半径 3、截面半径 0.5 的圆环',
            'args': {'radius': 3.0, 'section_radius': 0.5},
        },
    ],
    notes=[
        'position 为 JSON 字符串如 "[0,0,0]"。',
        'section_radius 是管子截面半径，需小于 radius。',
    ],
    returns_desc='dict {"name": transform 节点名, "type": "polyTorus"}',
)
def create_maya_torus(
    name: str = "",
    radius: float = 1.0,
    section_radius: float = 0.5,
    subdivisions_axis: int = 20,
    subdivisions_height: int = 20,
    position: str = "",
    rotation_euler: str = "",
):
    # type: (...) -> Dict[str, Any]
    """创建 Maya 圆环体。"""
    _ensure_in_maya()

    def _make():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        kwargs = {
            'radius': float(radius),
            'sectionRadius': float(section_radius),
            'subdivisionsX': int(subdivisions_axis),
            'subdivisionsY': int(subdivisions_height),
        }
        if name:
            kwargs['name'] = name
        transform, _ = cmds.polyTorus(**kwargs)
        with rollback_on_error([transform]):
            _apply_transform(transform, position=position, rotation_euler=rotation_euler)
        return transform

    transform = run_on_main(_make)
    return {'name': transform, 'type': 'polyTorus'}


@tool(
    dcc=['maya'],
    description="在 Maya 中创建一个多边形平面。",
    category="geometry",
    examples=[
        {
            'summary': '创建一块 10x10 的地面',
            'args': {'width': 10.0, 'height': 10.0, 'subdivisions_x': 1, 'subdivisions_y': 1},
        },
    ],
    notes=[
        '默认沿 XZ 平面创建（法线朝 +Y），适合做地面。',
        'position 为 JSON 字符串如 "[0,0,0]"。',
    ],
    returns_desc='dict {"name": transform 节点名, "type": "polyPlane"}',
)
def create_maya_plane(
    name: str = "",
    width: float = 1.0,
    height: float = 1.0,
    subdivisions_x: int = 1,
    subdivisions_y: int = 1,
    axis: str = 'y',
    position: str = "",
    rotation_euler: str = "",
):
    # type: (...) -> Dict[str, Any]
    """创建 Maya 平面。"""
    _ensure_in_maya()

    def _make():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        axis_map = {'x': 0, 'y': 1, 'z': 2}
        axis_key = str(axis).lower()
        if axis_key not in axis_map:
            raise ValueError('axis 必须是 x/y/z: {}'.format(axis))
        kwargs = {
            'width': float(width),
            'height': float(height),
            'subdivisionsX': int(subdivisions_x),
            'subdivisionsY': int(subdivisions_y),
            'axis': axis_map[axis_key],
        }
        if name:
            kwargs['name'] = name
        transform, _ = cmds.polyPlane(**kwargs)
        with rollback_on_error([transform]):
            _apply_transform(transform, position=position, rotation_euler=rotation_euler)
        return transform

    transform = run_on_main(_make)
    return {'name': transform, 'type': 'polyPlane'}


@tool(
    dcc=['maya'],
    description='镜像 Maya 几何体（polyMirrorFace：跨轴翻转生成镜像副本，支持顶点合并）。',
    category="geometry",
    examples=[
        {
            'summary': '把模型沿 -X 方向镜像并与原几何合并（做左右对称）',
            'args': {'object': 'pCube1', 'direction': '-x', 'merge_mode': 'merge'},
        },
        {
            'summary': '镜像成独立副本不合并',
            'args': {'object': 'pCube1', 'direction': '-x', 'merge_mode': 'none'},
        },
    ],
    notes=[
        'direction 取值 +x/-x/+y/-y/+z/-z，对应 polyMirrorFace 的 direction 0-5。',
        'merge_mode: none=不合并（border 有重合顶点）；merge=合并阈值内顶点；bridge=桥接边。',
        'merge_threshold 仅 merge 模式生效，默认 0.001。',
        '调用前确保对象有开口边界（如删掉一半的面），镜像作用于整个 mesh。',
    ],
    returns_desc='dict {"object": 对象名, "direction": 方向, "merge_mode": 合并模式}',
)
def mirror_maya_geometry(
    object: str,
    direction: str = '-x',
    merge_mode: str = 'merge',
    merge_threshold: float = 0.001,
):
    # type: (str, str, str, float) -> Dict[str, Any]
    """镜像 Maya 几何体。

    :param object: 网格对象名
    :param direction: 镜像方向 +x/-x/+y/-y/+z/-z
    :param merge_mode: none / merge / bridge
    :param merge_threshold: 合并阈值
    :returns: dict {"object": ..., "direction": ..., "merge_mode": ...}
    """
    _ensure_in_maya()

    def _do():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(object):
            raise ValueError('对象不存在: {}'.format(object))
        dir_map = {'+x': 0, '-x': 1, '+y': 2, '-y': 3, '+z': 4, '-z': 5}
        mode_map = {'none': 0, 'merge': 1, 'bridge': 2}
        dir_key = str(direction).lower()
        mode_key = str(merge_mode).lower()
        if dir_key not in dir_map:
            raise ValueError(
                'direction 必须是 +x/-x/+y/-y/+z/-z: {}'.format(direction),
            )
        if mode_key not in mode_map:
            raise ValueError(
                'merge_mode 必须是 none/merge/bridge: {}'.format(merge_mode),
            )
        cmds.polyMirrorFace(
            object,
            worldSpace=True,
            direction=dir_map[dir_key],
            mergeMode=mode_map[mode_key],
            mergeThreshold=float(merge_threshold),
            mergeThresholdType=1,
            constructionHistory=True,
        )
        return {
            'object': object,
            'direction': dir_key,
            'merge_mode': mode_key,
        }

    return run_on_main(_do)


__all__ = [
    'create_maya_box',
    'create_maya_sphere',
    'create_maya_cylinder',
    'create_maya_torus',
    'create_maya_plane',
    'mirror_maya_geometry',
]