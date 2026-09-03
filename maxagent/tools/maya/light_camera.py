#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 灯光与相机创建工具（最小集）。"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict

from ...dcc.runtime import current_dcc
from ...dcc.runtime import run_on_main
from ._common import _ensure_in_maya, rollback_on_error
from ...tools.registry import tool


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
        # Maya 没有通用 cmds.light；每种灯光有专用命令，且都返回 shape 节点名。
        # 使用命令而非 shadingNode 是为了让 Maya 自动创建对应 transform 并挂上 shape。
        creator = {
            'ambient': cmds.ambientLight,
            'directional': cmds.directionalLight,
            'point': cmds.pointLight,
            'spot': cmds.spotLight,
        }.get(light_type)
        if creator is not None:
            kwargs = {}
            if name:
                kwargs['name'] = name
            shape = creator(**kwargs)
        else:
            # areaLight 需要用 shadingNode + asLight
            shade_kwargs = {'asLight': True}
            if name:
                shade_kwargs['name'] = name
            shape = cmds.shadingNode('areaLight', **shade_kwargs)
        # 灯光命令返回的通常是 shape 节点；transform 是它的父节点
        parents = cmds.listRelatives(shape, parent=True, fullPath=False) or []
        transform = parents[0] if parents else shape
        with rollback_on_error([transform, shape]):
            if position:
                s = position.strip() if isinstance(position, str) else position
                if s:
                    coords = json.loads(s) if isinstance(s, str) else s
                    cmds.xform(
                        transform,
                        translation=[float(coords[0]), float(coords[1]), float(coords[2])],
                        worldSpace=True,
                    )
            if color:
                s = color.strip() if isinstance(color, str) else color
                if s:
                    coords = json.loads(s) if isinstance(s, str) else s
                    cmds.setAttr(
                        shape + '.color',
                        float(coords[0]) / 255.0,
                        float(coords[1]) / 255.0,
                        float(coords[2]) / 255.0,
                        type='double3',
                    )
            cmds.setAttr(shape + '.intensity', float(intensity))
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
        with rollback_on_error([transform]):
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
