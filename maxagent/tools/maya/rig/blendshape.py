#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya BlendShape 相关工具：创建、追加目标。

本模块由 rigging.py 拆分而来，rigging.py 保留为兼容门面。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ....dcc.runtime import run_on_main
from ....tools.registry import tool
from .._common import _ensure_in_maya, _normalize_names, _to_xyz_list


@tool(
    dcc=['maya'],
    description=(
        '在基础网格上创建 blendShape 变形器，可绑定多个目标形状。'
        '角色表情、面部绑定的核心工具。'
    ),
    category='rigging',
    examples=[
        {
            'summary': '把 smile / frown 两个目标绑到 head_geo',
            'args': {
                'base_mesh': 'head_geo',
                'target_meshes': ['smile_geo', 'frown_geo'],
                'name': 'face_bs',
            },
        },
    ],
    notes=[
        'target_meshes 中每个网格必须与 base_mesh 有相同的拓扑（点数一致、顺序一致）。',
        '创建后每个目标的权重属性是 blendShape 节点的自定义属性，'
        '名字与目标 transform 名一致，可用 set_maya_attr 驱动。',
    ],
    returns_desc='dict: {"blendshape": str, "target_names": List[str]}',
)
def create_maya_blendshape(base_mesh, target_meshes, name='blendShape1'):
    # type: (str, Any, str) -> Dict[str, Any]
    """创建 blendShape 变形器。

    :param base_mesh: 基础网格 transform 名
    :param target_meshes: 目标网格列表
    :param name: blendShape 节点名
    """
    _ensure_in_maya()

    targets = _normalize_names(target_meshes)
    if not targets:
        raise ValueError('target_meshes 不能为空')

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(base_mesh):
            raise ValueError('基础网格不存在: {}'.format(base_mesh))
        for t in targets:
            if not cmds.objExists(t):
                raise ValueError('目标网格不存在: {}'.format(t))

        bs_node = cmds.blendShape(targets + [base_mesh], name=name)[0]
        # 收集目标属性名
        alias_list = cmds.listAttr(bs_node + '.w', multi=True) or []
        return {
            'blendshape': bs_node,
            'target_names': alias_list,
        }

    return run_on_main(_impl)

@tool(
    dcc=['maya'],
    description='向已有 blendShape 节点追加一个目标形状。',
    category='rigging',
    examples=[
        {
            'summary': '给 face_bs 添加一个新的表情',
            'args': {
                'blendshape': 'face_bs',
                'base_mesh': 'head_geo',
                'target_mesh': 'kiss_geo',
                'weight_index': 3,
            },
        },
    ],
    notes=[
        'weight_index 必须是当前 blendShape 中未使用的索引（从 0 开始）。',
        '追加后需要通过 set_maya_attr 设置 "{blendshape}.{target_mesh}" 的权重值。',
    ],
    returns_desc='dict: {"ok": True, "target_alias": str}',
)
def add_blendshape_target(blendshape, base_mesh, target_mesh, weight_index):
    # type: (str, str, str, int) -> Dict[str, Any]
    """向 blendShape 追加目标。

    :param blendshape: 已存在的 blendShape 节点
    :param base_mesh: 基础网格
    :param target_mesh: 新目标网格
    :param weight_index: 权重索引
    """
    _ensure_in_maya()

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(blendshape):
            raise ValueError('blendShape 不存在: {}'.format(blendshape))
        if cmds.nodeType(blendshape) != 'blendShape':
            raise ValueError('{} 不是 blendShape 节点'.format(blendshape))
        if not cmds.objExists(base_mesh):
            raise ValueError('基础网格不存在: {}'.format(base_mesh))
        if not cmds.objExists(target_mesh):
            raise ValueError('目标网格不存在: {}'.format(target_mesh))

        cmds.blendShape(
            blendshape,
            edit=True,
            target=(base_mesh, int(weight_index), target_mesh, 1.0),
        )
        alias = cmds.aliasAttr('{}.weight[{}]'.format(blendshape, weight_index), query=True)
        return {'ok': True, 'target_alias': alias}

    return run_on_main(_impl)
