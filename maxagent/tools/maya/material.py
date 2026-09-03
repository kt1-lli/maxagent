#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 材质类工具。

提供 Lambert、Blinn、aiStandardSurface 创建，以及文件贴图连接。
所有会修改场景的操作都默认包在 undo 块内。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ...dcc.runtime import current_dcc
from ...dcc.runtime import run_on_main
from ...tools.registry import tool


def _ensure_in_maya():
    # type: () -> None
    """确保当前运行在 Maya 环境，否则抛出 RuntimeError。"""
    if current_dcc() != 'maya':
        raise RuntimeError('非 Maya 环境')


def _normalize_names(names):
    # type: (Any) -> List[str]
    """把 names 归一化为 list[str]。"""
    if names is None:
        return []
    if isinstance(names, (list, tuple)):
        return [str(x).strip() for x in names if str(x).strip()]
    if isinstance(names, str):
        s = names.strip()
        if not s:
            return []
        for sep in (',', ';', '\uff0c', '\uff1b'):
            if sep in s:
                return [p.strip() for p in s.split(sep) if p.strip()]
        return [s]
    return [str(names)]


def _to_color(value):
    # type: (Any) -> tuple
    """把 [r,g,b] 转为三元组。支持 list/tuple 与 JSON 字符串。"""
    if isinstance(value, str):
        import json  # pylint: disable=import-outside-toplevel
        s = value.strip()
        if not s:
            raise ValueError('color 参数为空字符串')
        value = json.loads(s)
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise ValueError('color 必须是包含 3 个数值的列表: {}'.format(value))
    return (float(value[0]), float(value[1]), float(value[2]))


def _color_attribute(material):
    # type: (str) -> str
    """根据材质类型返回颜色属性名。"""
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    node_type = cmds.nodeType(material)
    mapping = {
        'lambert': 'color',
        'blinn': 'color',
        'phong': 'color',
        'aiStandardSurface': 'baseColor',
    }
    return mapping.get(node_type, 'color')


def _texture_attribute(material, attribute):
    # type: (str, str) -> str
    """把通用属性名映射到 aiStandardSurface 的属性名。"""
    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    node_type = cmds.nodeType(material)
    if node_type == 'aiStandardSurface':
        mapping = {
            'color': 'baseColor',
            'diffuse': 'baseColor',
        }
        return mapping.get(attribute, attribute)
    return attribute


@tool(
    dcc=['maya'],
    description='创建 Maya 材质节点（lambert/blinn/phong/aiStandardSurface），并返回节点名。',
    category='material',
    examples=[
        {'summary': '创建红色 Lambert', 'args': {'name': 'red_lambert', 'material_type': 'lambert'}},
    ],
    returns_desc='str: 创建的材质节点名',
    notes=['支持类型: lambert / blinn / phong / aiStandardSurface。', 'aiStandardSurface 需要已加载 Arnold 插件（mtoa）。'],
)
def create_material(name, material_type='lambert'):
    # type: (str, str) -> str
    """创建材质节点。

    :param name: 材质名
    :param material_type: lambert / blinn / phong / aiStandardSurface
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if material_type == 'aiStandardSurface':
            shader = cmds.shadingNode('aiStandardSurface', asShader=True, name=name)
        else:
            shader = cmds.shadingNode(material_type, asShader=True, name=name)
        return shader

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='设置材质颜色。',
    category='material',
    examples=[
        {'summary': '把 red_lambert 设为红色', 'args': {'material': 'red_lambert', 'color': [1.0, 0.0, 0.0]}},
    ],
    returns_desc='dict: {"ok": True}',
    notes=['color 为 0-1 范围的 [r, g, b] 或形如 "[1,0,0]" 的 JSON。', 'lambert/blinn/phong 写入 color 属性，aiStandardSurface 写入 baseColor。'],
)
def set_material_color(material, color):
    # type: (str, Any) -> Dict[str, Any]
    """设置材质颜色。

    :param material: 材质节点名
    :param color: [r, g, b]，0-1 范围
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not cmds.objExists(material):
            raise ValueError('材质不存在: {}'.format(material))
        rgb = _to_color(color)
        attr = _color_attribute(material)
        cmds.setAttr('{}.{}'.format(material, attr), *rgb, type='double3')
        return {'ok': True}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='把指定材质赋给网格对象。',
    category='material',
    examples=[
        {'summary': '把 red_lambert 赋给 pCube1', 'args': {'material': 'red_lambert', 'objects': 'pCube1'}},
    ],
    returns_desc='dict: {"ok": True}',
    notes=['objects 支持列表或逗号分隔的字符串。', '会复用已连接的 shadingEngine；否则自动创建 <material>SG。'],
)
def assign_material(material, objects):
    # type: (str, Any) -> Dict[str, Any]
    """把材质赋给对象。

    :param material: 材质节点名
    :param objects: 对象名列表或逗号分隔字符串
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    targets = _normalize_names(objects)

    def _impl():
        if not cmds.objExists(material):
            raise ValueError('材质不存在: {}'.format(material))
        missing = [n for n in targets if not cmds.objExists(n)]
        if missing:
            raise ValueError('对象不存在: {}'.format(', '.join(missing)))

        shading_groups = cmds.listConnections(material, type='shadingEngine') or []
        if shading_groups:
            sg = shading_groups[0]
        else:
            sg = cmds.sets(
                renderable=True,
                noSurfaceShader=True,
                empty=True,
                name='{}SG'.format(material),
            )
            cmds.connectAttr('{}.outColor'.format(material), '{}.surfaceShader'.format(sg))

        cmds.sets(targets, forceElement=sg)
        return {'ok': True}

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='创建文件纹理节点并连接到指定材质的 color 属性。',
    category='material',
    examples=[
        {
            'summary': '给 red_lambert 连接漫反射贴图',
            'args': {'material': 'red_lambert', 'file_path': 'C:/textures/diffuse.png', 'attribute': 'color'},
        },
    ],
    returns_desc='str: 文件纹理节点名',
    notes=['attribute 支持 color / diffuse 等常见语义名，工具自动映射到实际属性。', 'file_path 必须是已存在的绝对路径。'],
)
def connect_file_texture(material, file_path, attribute='color', texture_name=None):
    # type: (str, str, str, Optional[str]) -> str
    """连接文件贴图。

    :param material: 材质节点名
    :param file_path: 贴图绝对路径
    :param attribute: 要连接的材质属性名，如 color / diffuseColor / baseColor
    :param texture_name: 文件节点名，None 则自动生成
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not cmds.objExists(material):
            raise ValueError('材质不存在: {}'.format(material))
        path = os.path.normpath(file_path)
        if not os.path.exists(path):
            raise ValueError('贴图文件不存在: {}'.format(path))

        tex = cmds.shadingNode('file', asTexture=True, name=texture_name or '{}_{}_file'.format(material, attribute))
        cmds.setAttr('{}.fileTextureName'.format(tex), path, type='string')

        target_attr = '{}.{}'.format(material, _texture_attribute(material, attribute))
        cmds.connectAttr('{}.outColor'.format(tex), target_attr, force=True)
        return tex

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='列出场景中的所有材质节点。',
    category='material',
    wrap_undo=False,
    examples=[{'summary': '列出材质', 'args': {}}],
    returns_desc='List[dict]: 材质名与类型列表',
    notes=['返回场景全部材质节点及其类型。'],
)
def list_materials():
    # type: () -> List[Dict[str, Any]]
    """列出场景材质。"""
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        mats = cmds.ls(materials=True) or []
        return [{'name': m, 'type': cmds.nodeType(m)} for m in mats]

    return run_on_main(_impl)


@tool(
    dcc=['maya'],
    description='获取对象当前使用的材质名。',
    category='material',
    wrap_undo=False,
    examples=[{'summary': '查询 pCube1 的材质', 'args': {'object_name': 'pCube1'}}],
    returns_desc='List[str]: 材质名列表',
    notes=['通过 shadingEngine 反查该对象使用的所有材质。'],
)
def get_object_materials(object_name):
    # type: (str) -> List[str]
    """获取对象使用的材质。

    :param object_name: 对象名
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not cmds.objExists(object_name):
            raise ValueError('对象不存在: {}'.format(object_name))
        # shadingEngine 通常连在 shape 节点上（如 pCubeShape1），
        # 传入 transform 时需要先展开成 shape，否则 listConnections 返回空。
        node_type = cmds.nodeType(object_name)
        shapes = []  # type: List[str]
        if node_type == 'transform':
            shapes = cmds.listRelatives(
                object_name, shapes=True, noIntermediate=True, fullPath=False,
            ) or []
        elif node_type in ('mesh', 'nurbsSurface', 'subdiv'):
            shapes = [object_name]
        else:
            # 其它节点类型（如 face 组件、光源 shape 等）：直接查它自己
            shapes = [object_name]

        if not shapes:
            return []

        materials = []  # type: List[str]
        seen = set()  # type: set
        for shape in shapes:
            sgs = cmds.listConnections(shape, type='shadingEngine') or []
            # 组件级材质（面赋材质）也会连到 shadingEngine，去重
            for sg in set(sgs):
                mats = cmds.ls(
                    cmds.listConnections('{}.surfaceShader'.format(sg)) or [],
                    materials=True,
                ) or []
                for m in mats:
                    if m not in seen:
                        seen.add(m)
                        materials.append(m)
        return materials

    return run_on_main(_impl)
