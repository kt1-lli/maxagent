#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 灯光与相机创建工具（最小集）。"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict

from ...dcc.runtime import current_dcc
from ...dcc.runtime import run_on_main
from ...tools.registry import tool


def _ensure_in_maya():
    # type: () -> None
    if current_dcc() != 'maya':
        raise RuntimeError('非 Maya 环境')


@tool(
    dcc=['maya'],
    description="在 Maya 中创建一盏灯光。",
    category="light_camera",
    examples=[
        {
            'summary': '在 (0,5,0) 创建一盏点光源',
            'args': {'light_type': 'point', 'name': 'myPointLight', 'position': '[0,5,0]'},
        },
    ],
    notes=[
        'light_type 可选: ambient, directional, point, spot, area。',
        'position 为 JSON 字符串如 "[0,5,0]"。',
    ],
    returns_desc='dict {"name": transform 节点名, "light_type": str}',
)
def create_maya_light(
    light_type: str = 'point',
    name: str = '',
    position: str = '',
    intensity: float = 1.0,
    color: str = '[255,255,255]',
):
    # type: (...) -> Dict[str, Any]
    """创建 Maya 灯光。"""
    _ensure_in_maya()

    def _do():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        import json  # pylint: disable=import-outside-toplevel
        valid = {'ambient', 'directional', 'point', 'spot', 'area'}
        if light_type not in valid:
            raise ValueError('不支持的 light_type: {}，可选: {}'.format(
                light_type, ', '.join(sorted(valid)),
            ))
        kwargs = {}
        if name:
            kwargs['name'] = name
        transform = cmds.light(type=light_type, **kwargs)
        if position:
            coords = json.loads(position)
            cmds.xform(transform, translation=[float(coords[0]), float(coords[1]), float(coords[2])], worldSpace=True)
        if color:
            coords = json.loads(color)
            cmds.setAttr(transform + '.color', float(coords[0]) / 255.0, float(coords[1]) / 255.0, float(coords[2]) / 255.0, type='double3')
        cmds.setAttr(transform + '.intensity', float(intensity))
        return transform

    transform = run_on_main(_do)
    return {'name': transform, 'light_type': light_type}


@tool(
    dcc=['maya'],
    description="在 Maya 中创建一个相机。",
    category="light_camera",
    examples=[
        {
            'summary': '在 (10,10,10) 创建一台相机并看向原点',
            'args': {'name': 'myCamera', 'position': '[10,10,10]', 'focal_length': 35.0},
        },
    ],
    notes=[
        'position 为 JSON 字符串如 "[10,10,10]"。',
    ],
    returns_desc='dict {"name": transform 节点名}',
)
def create_maya_camera(
    name: str = '',
    position: str = '',
    focal_length: float = 35.0,
):
    # type: (...) -> Dict[str, Any]
    """创建 Maya 相机。"""
    _ensure_in_maya()

    def _do():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        import json  # pylint: disable=import-outside-toplevel
        kwargs = {}
        if name:
            kwargs['name'] = name
        transform, _ = cmds.camera(**kwargs)
        if position:
            coords = json.loads(position)
            cmds.xform(transform, translation=[float(coords[0]), float(coords[1]), float(coords[2])], worldSpace=True)
        cmds.setAttr(transform + '.focalLength', float(focal_length))
        return transform

    transform = run_on_main(_do)
    return {'name': transform}


__all__ = [
    'create_maya_light',
    'create_maya_camera',
]
