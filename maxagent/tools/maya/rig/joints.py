#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 关节相关工具：创建、半径、方向、镜像、首选角。

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
    description='在指定位置创建 Maya 关节。',
    category='rigging',
    examples=[
        {
            'summary': '在原点创建根关节',
            'args': {'name': 'root_joint', 'position': [0.0, 0.0, 0.0]},
        },
    ],
    returns_desc='str: 创建的关节名',
    notes=['position 为 JSON 字符串如 "[0,0,0]"；未指定会在原点创建。'],
)
def create_joint(name='joint1', position=None):
    # type: (str, Any) -> str
    """在指定位置创建 Maya 关节。

    :param name: 关节名
    :param position: [x, y, z] 世界坐标
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    xyz = _to_xyz_list(position, 'position') or (0.0, 0.0, 0.0)

    def _impl():
        cmds.select(clear=True)
        jnt = cmds.joint(name=name, position=xyz)
        return jnt

    return run_on_main(_impl)

@tool(
    dcc=['maya'],
    description='在指定父关节下创建子关节，形成骨骼链。',
    category='rigging',
    examples=[
        {
            'summary': '在 root 下创建 leg 子关节',
            'args': {'parent': 'root_joint', 'name': 'leg_joint', 'position': [0.0, -10.0, 0.0]},
        },
    ],
    returns_desc='str: 创建的子关节名',
    notes=['会把新关节以 parent 为父，且相对 parent 的偏移由 offset 指定。'],
)
def create_child_joint(parent, name, position):
    # type: (str, str, Any) -> str
    """在父关节下创建子关节。

    :param parent: 父关节名
    :param name: 新关节名
    :param position: [x, y, z] 关节位置（相对于父关节的局部坐标或世界坐标）
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    xyz = _to_xyz_list(position, 'position')

    def _impl():
        if not cmds.objExists(parent):
            raise ValueError('父关节不存在: {}'.format(parent))
        cmds.select(parent)
        jnt = cmds.joint(name=name, position=xyz)
        return jnt

    return run_on_main(_impl)

@tool(
    dcc=['maya'],
    description='设置关节的显示半径，便于在视口中观察。',
    category='rigging',
    examples=[
        {'summary': '把关节半径设为 2', 'args': {'joint_name': 'root_joint', 'radius': 2.0}},
    ],
    returns_desc='dict: {"ok": True}',
    notes=['radius 只影响视口显示大小，不影响计算。'],
)
def set_joint_radius(joint_name, radius=1.0):
    # type: (str, float) -> Dict[str, Any]
    """设置关节半径。

    :param joint_name: 关节名
    :param radius: 半径值
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not cmds.objExists(joint_name):
            raise ValueError('关节不存在: {}'.format(joint_name))
        cmds.setAttr(joint_name + '.radius', radius)
        return {'ok': True}

    return run_on_main(_impl)

@tool(
    dcc=['maya'],
    description=(
        '设置关节的 preferredAngle（首选角度），控制 IK 弯曲方向。'
        '常用于腿膝盖、手肘等 IK 铰链关节。'
    ),
    category='rigging',
    examples=[
        {
            'summary': '设置膝盖首选角度为 Z 轴 +45 度',
            'args': {'joint': 'knee_joint', 'axis': 'z', 'angle': 45.0},
        },
    ],
    notes=[
        'axis: x / y / z，对应 preferredAngleX/Y/Z。',
        '正负号决定弯曲方向，通常膝盖是 +Z，手肘是 -Z（取决于骨骼朝向）。',
    ],
    returns_desc='dict: {"ok": True}',
)
def set_preferred_angle(joint, axis='z', angle=45.0):
    # type: (str, str, float) -> Dict[str, Any]
    """设置关节首选角度。

    :param joint: 关节名
    :param axis: x / y / z
    :param angle: 度数
    """
    _ensure_in_maya()

    if axis not in ('x', 'y', 'z'):
        raise ValueError("axis 必须是 'x' / 'y' / 'z'")

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(joint):
            raise ValueError('关节不存在: {}'.format(joint))
        if cmds.nodeType(joint) != 'joint':
            raise ValueError('{} 不是 joint 节点'.format(joint))

        attr = '{}.preferredAngle{}'.format(joint, axis.upper())
        cmds.setAttr(attr, float(angle))
        return {'ok': True}

    return run_on_main(_impl)

@tool(
    dcc=['maya'],
    description=(
        '对齐关节朝向（joint orient）。让关节的 X 轴沿骨骼方向，Y/Z 定义次轴。'
        '绑定第一步的必备操作，未对齐的关节 IK/FK 都会出问题。'
    ),
    category='rigging',
    examples=[
        {
            'summary': '标准 XYZ 对齐，Y 朝上',
            'args': {'joint': 'root_joint', 'aim_axis': 'xyz', 'up_axis': 'yup', 'children': True},
        },
    ],
    notes=[
        'aim_axis: "xyz" (X 主轴) / "yzx" / "zxy" 等 6 种排列。',
        'up_axis: "xup" / "xdown" / "yup" / "ydown" / "zup" / "zdown" / "none"。',
        'children=True 会递归对齐所有子关节。',
        '末端关节（无子关节）会被自动置为世界对齐，避免不确定朝向。',
    ],
    returns_desc='dict: {"ok": True, "processed": int}',
)
def orient_joint(joint, aim_axis='xyz', up_axis='yup', children=True):
    # type: (str, str, str, bool) -> Dict[str, Any]
    """对齐关节朝向。

    :param joint: 关节名
    :param aim_axis: 主轴/次轴/第三轴排列，如 "xyz"
    :param up_axis: 世界向上方向
    :param children: 是否递归处理子关节
    """
    _ensure_in_maya()

    valid_aim = {'xyz', 'yzx', 'zxy', 'xzy', 'yxz', 'zyx', 'none'}
    valid_up = {'xup', 'xdown', 'yup', 'ydown', 'zup', 'zdown', 'none'}
    if aim_axis not in valid_aim:
        raise ValueError('aim_axis 必须是 {}'.format(valid_aim))
    if up_axis not in valid_up:
        raise ValueError('up_axis 必须是 {}'.format(valid_up))

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(joint):
            raise ValueError('关节不存在: {}'.format(joint))
        if cmds.nodeType(joint) != 'joint':
            raise ValueError('{} 不是 joint 节点'.format(joint))

        cmds.joint(
            joint,
            edit=True,
            orientJoint=aim_axis,
            secondaryAxisOrient=up_axis,
            children=children,
            zeroScaleOrient=True,
        )

        # 处理末端关节：置零 jointOrient
        end_joints = []
        if children:
            all_joints = cmds.listRelatives(joint, allDescendents=True, type='joint') or []
            all_joints.append(joint)
        else:
            all_joints = [joint]
        for j in all_joints:
            has_child = cmds.listRelatives(j, children=True, type='joint')
            if not has_child:
                cmds.setAttr(j + '.jointOrientX', 0)
                cmds.setAttr(j + '.jointOrientY', 0)
                cmds.setAttr(j + '.jointOrientZ', 0)
                end_joints.append(j)

        return {
            'ok': True,
            'processed': len(all_joints),
            'end_joints_zeroed': end_joints,
        }

    return run_on_main(_impl)

@tool(
    dcc=['maya'],
    description=(
        '镜像关节链。绑定角色左右对称部件的标准操作。'
        '常见做法：先做完 L_ 一侧，再镜像出 R_ 一侧。'
    ),
    category='rigging',
    examples=[
        {
            'summary': '沿 YZ 平面镜像左侧腿部到右侧，改名 L_ → R_',
            'args': {
                'joint': 'L_hip_joint',
                'mirror_axis': 'YZ',
                'search': 'L_',
                'replace': 'R_',
            },
        },
    ],
    notes=[
        'mirror_axis: "XY" / "YZ" / "XZ"，人形左右对称通常是 "YZ"。',
        'search/replace 用于替换关节名（L_ → R_）；不传则关节以 "_mirror" 结尾。',
        'mirror_behavior=True (默认) 让镜像后关节旋转值保持对称行为，'
        '如果要用 mirror_animation 建议保持 True。',
    ],
    returns_desc='List[str]: 新创建的镜像关节名列表',
)
def mirror_joint_chain(
    joint,
    mirror_axis='YZ',
    search='L_',
    replace='R_',
    mirror_behavior=True,
):
    # type: (str, str, str, str, bool) -> List[str]
    """镜像关节链。

    :param joint: 源关节链的根
    :param mirror_axis: XY / YZ / XZ
    :param search: 关节名中要被替换的字串
    :param replace: 替换成什么
    :param mirror_behavior: 是否镜像行为（True）还是仅镜像 orientation（False）
    """
    _ensure_in_maya()

    if mirror_axis not in ('XY', 'YZ', 'XZ'):
        raise ValueError("mirror_axis 必须是 'XY' / 'YZ' / 'XZ'")

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        if not cmds.objExists(joint):
            raise ValueError('关节不存在: {}'.format(joint))

        kwargs = {
            'mirrorBehavior': mirror_behavior,
        }
        if mirror_axis == 'XY':
            kwargs['mirrorXY'] = True
        elif mirror_axis == 'YZ':
            kwargs['mirrorYZ'] = True
        elif mirror_axis == 'XZ':
            kwargs['mirrorXZ'] = True

        if search and replace:
            kwargs['searchReplace'] = (search, replace)

        result = cmds.mirrorJoint(joint, **kwargs)
        return list(result) if result else []

    return run_on_main(_impl)
