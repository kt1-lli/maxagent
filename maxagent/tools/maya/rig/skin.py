#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 蒙皮相关工具：绑定、权重设置。

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
    description='对网格执行平滑蒙皮（skinCluster），绑定到指定关节。',
    category='rigging',
    examples=[
        {
            'summary': '把 body_mesh 绑定到根关节链',
            'args': {'mesh': 'body_mesh', 'joints': ['root_joint', 'spine_joint'], 'name': 'body_skinCluster'},
        },
    ],
    returns_desc='str: skinCluster 节点名',
    notes=['bind_method: 0=classicLinear, 1=geodesicVoxel（Maya 2015+）。', 'skinning_method: 0=classicLinear, 1=dualQuaternion, 2=weightBlended。'],
)
def bind_skin(mesh, joints, name='skinCluster1', max_influences=4):
    # type: (str, Any, str, int) -> str
    """创建 smooth bind skinCluster。

    :param mesh: 网格 transform 名或 shape 名
    :param joints: 关节名列表或逗号分隔字符串
    :param name: skinCluster 节点名
    :param max_influences: 最大影响数
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    joint_list = _normalize_names(joints)

    def _impl():
        if not cmds.objExists(mesh):
            raise ValueError('网格不存在: {}'.format(mesh))
        missing = [j for j in joint_list if not cmds.objExists(j)]
        if missing:
            raise ValueError('关节不存在: {}'.format(', '.join(missing)))

        shapes = cmds.listRelatives(mesh, shapes=True, type='mesh') or []
        if not shapes:
            raise ValueError('{} 不是有效网格'.format(mesh))
        skin = cmds.skinCluster(
            joint_list,
            shapes[0],
            name=name,
            toSelectedBones=True,
            maximumInfluences=max_influences,
        )
        return skin[0]

    return run_on_main(_impl)

@tool(
    dcc=['maya'],
    description='对选定顶点/边/面设置蒙皮权重。',
    category='rigging',
    examples=[
        {
            'summary': '把选中的点权重全部给 wrist_joint',
            'args': {'mesh': 'body_mesh', 'joint': 'wrist_joint', 'value': 1.0},
        },
    ],
    returns_desc='dict: {"ok": True}',
    notes=['influence 为对该顶点该关节的权重（0-1）。', '设置后其它关节权重会按 Maya 默认策略重新归一化。'],
)
def set_skin_weight(mesh, joint, value=1.0):
    # type: (str, str, float) -> Dict[str, Any]
    """设置当前选中组件对某关节的权重。

    :param mesh: 网格 transform 名
    :param joint: 关节名
    :param value: 权重值 0.0-1.0
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        selection = cmds.ls(selection=True, flatten=True)
        if not selection:
            raise ValueError('请先选择要设置权重的顶点/边/面')
        if not cmds.objExists(mesh):
            raise ValueError('网格不存在: {}'.format(mesh))
        if not cmds.objExists(joint):
            raise ValueError('关节不存在: {}'.format(joint))

        history_nodes = cmds.listHistory(mesh) or []
        skin_cluster = None
        for node in history_nodes:
            if cmds.nodeType(node) == 'skinCluster':
                skin_cluster = node
                break
        if skin_cluster is None:
            raise ValueError('网格 {} 没有 skinCluster'.format(mesh))

        cmds.skinPercent(skin_cluster, selection, transformValue=(joint, value))
        return {'ok': True}

    return run_on_main(_impl)
