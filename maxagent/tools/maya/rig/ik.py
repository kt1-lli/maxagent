#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya IK/FK 相关工具：IK 手柄、极向量、IK/FK 切换、链联动。

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
    description='用 IK 手柄在起始关节和末端关节之间创建 RP 或 SC 解算器。',
    category='rigging',
    examples=[
        {
            'summary': '为腿骨创建 RP IK',
            'args': {
                'start_joint': 'hip_joint',
                'end_effector': 'ankle_joint',
                'name': 'leg_ikHandle',
                'solver': 'ikRPsolver',
            },
        },
    ],
    returns_desc='dict: {"ik_handle": str, "effector": str}',
    notes=['solver 支持 ikRPsolver / ikSCsolver / ikSplineSolver。', 'start_joint 与 end_joint 必须在同一链上且 end 是 start 的后代。'],
)
def create_ik_handle(start_joint, end_effector, name='ikHandle1', solver='ikRPsolver'):
    # type: (str, str, str, str) -> Dict[str, Any]
    """创建 IK 手柄。

    :param start_joint: 起始关节名
    :param end_effector: 末端关节名
    :param name: IK 手柄名
    :param solver: ikRPsolver 或 ikSCsolver
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not cmds.objExists(start_joint):
            raise ValueError('起始关节不存在: {}'.format(start_joint))
        if not cmds.objExists(end_effector):
            raise ValueError('末端关节不存在: {}'.format(end_effector))
        handle, effector = cmds.ikHandle(
            startJoint=start_joint,
            endEffector=end_effector,
            name=name,
            solver=solver,
        )
        return {'ik_handle': handle, 'effector': effector}

    return run_on_main(_impl)

@tool(
    dcc=['maya'],
    description='把 IK/FK 关节链通过 orientConstraint 连接到同一套控制器，实现 FK/IK 切换的预备结构。',
    category='rigging',
    examples=[
        {
            'summary': '把 FK 前臂驱动到 IK 前臂',
            'args': {'driver_joints': ['fk_shoulder', 'fk_elbow', 'fk_wrist'], 'driven_joints': ['ik_shoulder', 'ik_elbow', 'ik_wrist']},
        },
    ],
    returns_desc='List[str]: 创建的约束节点名列表',
    notes=['fk/ik/bind 三条链的关节数必须相同。', '返回创建出来的 blendColors / plusMinusAverage 节点。'],
)
def connect_fk_ik_chains(driver_joints, driven_joints, maintain_offset=False):
    # type: (Any, Any, bool) -> List[str]
    """把驱动关节链和受驱动关节链逐节点创建 orientConstraint。

    :param driver_joints: 驱动关节名列表
    :param driven_joints: 受驱动关节名列表
    :param maintain_offset: 是否保持偏移
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    drivers = _normalize_names(driver_joints)
    drivens = _normalize_names(driven_joints)

    def _impl():
        if len(drivers) != len(drivens):
            raise ValueError('驱动链与受驱动链长度不一致')
        created = []
        for drv, dvn in zip(drivers, drivens):
            if not cmds.objExists(drv):
                raise ValueError('驱动关节不存在: {}'.format(drv))
            if not cmds.objExists(dvn):
                raise ValueError('受驱动关节不存在: {}'.format(dvn))
            node = cmds.orientConstraint(
                drv, dvn,
                maintainOffset=maintain_offset,
                name='{}_fkik_orientConstraint'.format(dvn),
            )
            created.append(node[0])
        return created

    return run_on_main(_impl)

@tool(
    dcc=['maya'],
    description=(
        '给指定 IK 手柄创建极向量约束（poleVectorConstraint），控制 IK 弯曲方向。'
        '内部会自动在合适位置生成一个 locator 作为极向量控制器。'
    ),
    category='rigging',
    examples=[
        {
            'summary': '为腿部 IK 创建极向量',
            'args': {'ik_handle': 'leg_ikHandle', 'name': 'leg_pv_loc', 'offset': [0.0, 0.0, 10.0]},
        },
    ],
    notes=[
        '会在关节链中间关节（elbow / knee）附近生成一个 locator 作为 pole vector。',
        'offset 控制 locator 从中间关节沿哪个方向偏移，值越大离得越远。',
    ],
    returns_desc='dict: {"locator": str, "constraint": str}',
    prerequisites=['ik_handle 必须已存在'],
)
def create_pole_vector(ik_handle, name='pv_loc', offset=None):
    # type: (str, str, Any) -> Dict[str, Any]
    """创建极向量约束。

    :param ik_handle: 已存在的 IK 手柄名
    :param name: pole vector locator 名
    :param offset: 从中间关节的偏移向量 [x, y, z]，默认 [0, 0, 5]
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    off = _to_xyz_list(offset, 'offset') or (0.0, 0.0, 5.0)

    def _impl():
        if not cmds.objExists(ik_handle):
            raise ValueError('IK 手柄不存在: {}'.format(ik_handle))
        if cmds.nodeType(ik_handle) != 'ikHandle':
            raise ValueError('{} 不是 ikHandle 节点'.format(ik_handle))

        # 找到 IK 链中的中间关节
        start_joint = cmds.ikHandle(ik_handle, query=True, startJoint=True)
        end_effector = cmds.ikHandle(ik_handle, query=True, endEffector=True)
        # end effector 的父是末端关节
        end_joint = cmds.listRelatives(end_effector, parent=True)
        if not end_joint:
            raise ValueError('无法定位 IK 末端关节')
        end_joint = end_joint[0]

        # 从末端关节向上找到中间关节（parent 的 parent 是 start）
        mid_joint = cmds.listRelatives(end_joint, parent=True)
        if not mid_joint:
            raise ValueError('IK 链至少需要 3 个关节')
        mid_joint = mid_joint[0]

        mid_pos = cmds.xform(mid_joint, query=True, worldSpace=True, translation=True)

        loc = cmds.spaceLocator(name=name)[0]
        cmds.xform(
            loc, worldSpace=True,
            translation=(mid_pos[0] + off[0], mid_pos[1] + off[1], mid_pos[2] + off[2]),
        )
        constraint = cmds.poleVectorConstraint(loc, ik_handle)[0]

        return {
            'locator': loc,
            'constraint': constraint,
            'start_joint': start_joint,
            'mid_joint': mid_joint,
            'end_joint': end_joint,
        }

    return run_on_main(_impl)

@tool(
    dcc=['maya'],
    description=(
        '一键搭建 IKFK 切换：给控制器添加 ikfk 开关属性，并用 reverse 节点驱动 IK/FK 链的可见性和约束权重。'
    ),
    category='rigging',
    examples=[
        {
            'summary': '为手臂创建 IKFK 切换',
            'args': {
                'switch_ctrl': 'arm_settings_ctrl',
                'ik_ctrls': ['ik_wrist_ctrl'],
                'fk_ctrls': ['fk_shoulder_ctrl', 'fk_elbow_ctrl', 'fk_wrist_ctrl'],
                'orient_constraints': ['shoulder_orientConstraint', 'elbow_orientConstraint', 'wrist_orientConstraint'],
            },
        },
    ],
    notes=[
        'switch_ctrl 上会新增 ikfk 属性（0=FK, 1=IK）。',
        '会创建一个 reverse 节点自动生成 FK 权重（1 - ikfk）。',
        'orient_constraints 中每个约束节点应至少有 IK 和 FK 两个 target；'
        '本工具默认 target[0]=FK, target[1]=IK，如果顺序不同请手动调整。',
    ],
    returns_desc='dict: {"ikfk_attr": str, "reverse_node": str}',
)
def create_ikfk_switch(switch_ctrl, ik_ctrls, fk_ctrls, orient_constraints):
    # type: (str, Any, Any, Any) -> Dict[str, Any]
    """一键搭建 IKFK 切换。

    :param switch_ctrl: 承载 ikfk 开关属性的控制器
    :param ik_ctrls: IK 控制器名列表（用于可见性驱动）
    :param fk_ctrls: FK 控制器名列表
    :param orient_constraints: 需要根据 ikfk 值切换权重的约束节点列表
    """
    _ensure_in_maya()

    ik_list = _normalize_names(ik_ctrls)
    fk_list = _normalize_names(fk_ctrls)
    con_list = _normalize_names(orient_constraints)

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(switch_ctrl):
            raise ValueError('切换控制器不存在: {}'.format(switch_ctrl))

        # 添加 ikfk 属性
        if not cmds.attributeQuery('ikfk', node=switch_ctrl, exists=True):
            cmds.addAttr(
                switch_ctrl,
                longName='ikfk',
                attributeType='double',
                minValue=0.0,
                maxValue=1.0,
                defaultValue=0.0,
                keyable=True,
            )

        ikfk_attr = '{}.ikfk'.format(switch_ctrl)

        # 创建 reverse 节点：1 - ikfk = fk 权重
        rev_node = cmds.createNode('reverse', name='{}_ikfk_reverse'.format(switch_ctrl))
        cmds.connectAttr(ikfk_attr, rev_node + '.inputX', force=True)

        # IK 控制器可见性
        for c in ik_list:
            if cmds.objExists(c):
                cmds.connectAttr(ikfk_attr, c + '.visibility', force=True)
        # FK 控制器可见性
        for c in fk_list:
            if cmds.objExists(c):
                cmds.connectAttr(rev_node + '.outputX', c + '.visibility', force=True)

        # 约束权重：target[0]=FK, target[1]=IK
        for con in con_list:
            if not cmds.objExists(con):
                continue
            weight_attrs = cmds.listAttr(con, string='target[*].targetWeight') or []
            weight_attrs = cmds.listConnections(
                con + '.target', destination=False, source=True, plugs=False,
            ) or []
            # 直接用 constraint weight attrs
            attrs = cmds.listAttr(con, userDefined=True) or []
            weight_plugs = [
                '{}.{}'.format(con, a) for a in attrs
                if a.endswith('W0') or a.endswith('W1')
            ]
            weight_plugs.sort()
            if len(weight_plugs) >= 2:
                # W0 = FK 权重 = reverse.outputX
                cmds.connectAttr(rev_node + '.outputX', weight_plugs[0], force=True)
                # W1 = IK 权重 = ikfk
                cmds.connectAttr(ikfk_attr, weight_plugs[1], force=True)

        return {
            'ikfk_attr': ikfk_attr,
            'reverse_node': rev_node,
        }

    return run_on_main(_impl)
