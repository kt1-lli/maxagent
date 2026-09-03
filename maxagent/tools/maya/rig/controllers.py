#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 控制器/组层级相关工具：曲线控制器、offset 组、层级、locator 采样。

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
    description='创建 NURBS 曲线作为控制器，可指定形状（圆、方块、十字、锥体箭头）。',
    category='rigging',
    examples=[
        {
            'summary': '在手腕创建圆圈控制器',
            'args': {'name': 'wrist_ctrl', 'shape': 'circle', 'position': [0.0, 10.0, 0.0]},
        },
    ],
    returns_desc='str: 控制器 transform 节点名',
    notes=['position 用于把控制器 CV 归零后再整体挪到目标位置，冻结不会吸回原点。', 'shape 支持 circle / square / cross / arrow / star。'],
)
def create_controller(name, shape='circle', position=None, radius=2.0, color=None):
    # type: (str, str, Any, float, Any) -> str
    """创建控制器曲线。

    :param name: 控制器名
    :param shape: circle / square / cross / arrow / star
    :param position: [x, y, z]
    :param radius: 控制器大小
    :param color: 0-31 的 Maya 绘制覆盖颜色索引，None 表示不设置
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    xyz = _to_xyz_list(position, 'position')

    def _impl():
        cv_points = _controller_shape_points(shape, radius)
        curve = cmds.curve(degree=1, point=cv_points, knot=list(range(len(cv_points))), name=name)
        # 先冻结 scale/rotate（保持形状 CV 归零），再移动到目标位置。
        # 注意：不能冻结 translate，否则位置会被吸回原点。
        cmds.makeIdentity(curve, apply=True, translate=False, rotate=True, scale=True)
        if xyz is not None:
            cmds.xform(curve, translation=list(xyz), worldSpace=True)
        if color is not None:
            cmds.setAttr(curve + '.overrideEnabled', 1)
            cmds.setAttr(curve + '.overrideColor', int(color))
        return curve

    return run_on_main(_impl)

def _controller_shape_points(shape, radius):
    # type: (str, float) -> List[tuple]
    """返回控制器形状的点列表（局部坐标）。"""
    r = float(radius)
    segments = 64
    import math  # pylint: disable=import-outside-toplevel

    if shape == 'circle':
        pts = []
        for i in range(segments):
            ang = 2.0 * math.pi * i / segments
            pts.append((math.cos(ang) * r, math.sin(ang) * r, 0.0))
        pts.append(pts[0])
        return pts

    if shape == 'square':
        return [
            (-r, -r, 0.0), (r, -r, 0.0), (r, r, 0.0),
            (-r, r, 0.0), (-r, -r, 0.0),
        ]

    if shape == 'cross':
        return [
            (-r, 0.0, 0.0), (-0.2 * r, 0.0, 0.0), (-0.2 * r, 0.8 * r, 0.0),
            (0.2 * r, 0.8 * r, 0.0), (0.2 * r, 0.0, 0.0), (r, 0.0, 0.0),
            (r, -0.2 * r, 0.0), (0.2 * r, -0.2 * r, 0.0),
            (0.2 * r, -0.8 * r, 0.0), (-0.2 * r, -0.8 * r, 0.0),
            (-0.2 * r, -0.2 * r, 0.0), (-r, -0.2 * r, 0.0),
            (-r, 0.0, 0.0),
        ]

    if shape == 'arrow':
        return [
            (-0.3 * r, 0.0, 0.0), (-0.3 * r, 0.4 * r, 0.0),
            (-0.6 * r, 0.4 * r, 0.0), (0.0, r, 0.0),
            (0.6 * r, 0.4 * r, 0.0), (0.3 * r, 0.4 * r, 0.0),
            (0.3 * r, 0.0, 0.0), (-0.3 * r, 0.0, 0.0),
        ]

    if shape == 'star':
        pts = []
        for i in range(10):
            rr = r if i % 2 == 0 else 0.4 * r
            ang = 0.5 * math.pi - 2.0 * math.pi * i / 10
            pts.append((math.cos(ang) * rr, math.sin(ang) * rr, 0.0))
        pts.append(pts[0])
        return pts

    raise ValueError('未知控制器形状: {}'.format(shape))

@tool(
    dcc=['maya'],
    description='把控制器按层级关系建立父子关系，形成控制层级（例如 root → hip → knee → ankle）。',
    category='rigging',
    examples=[
        {
            'summary': '建立角色控制器层级',
            'args': {'controllers': ['root_ctrl', 'hip_ctrl', 'knee_ctrl', 'ankle_ctrl']},
        },
    ],
    returns_desc='dict: {"ok": True}',
    notes=['按传入顺序建立父子层级，第一个为最上层。'],
)
def parent_controller_hierarchy(controllers):
    # type: (Any) -> Dict[str, Any]
    """按顺序建立控制器父子层级。

    :param controllers: 控制器名列表，顺序为从根到子
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    names = _normalize_names(controllers)

    def _impl():
        if len(names) < 2:
            raise ValueError('至少需要 2 个控制器')
        for n in names:
            if not cmds.objExists(n):
                raise ValueError('控制器不存在: {}'.format(n))
        for i in range(len(names) - 1):
            cmds.parent(names[i + 1], names[i])
        return {'ok': True}

    return run_on_main(_impl)


# ---------------------------------------------------------------------------
# 绑定进阶工具

@tool(
    dcc=['maya'],
    description=(
        '给控制器套一层 offset group（空 transform 作为父级），'
        '把控制器的初始 transform 归零。绑定中的标准做法，避免直接操作控制器时通道变脏。'
    ),
    category='rigging',
    examples=[
        {
            'summary': '给 wrist_ctrl 套 offset group',
            'args': {'controller': 'wrist_ctrl'},
        },
    ],
    notes=[
        '会生成 "{controller}_offset" 命名的组。',
        '控制器的初始位置会被 offset group 吸收，控制器的 translate/rotate 归零。',
    ],
    returns_desc='dict: {"offset_group": str, "controller": str}',
    prerequisites=['controller 必须是已存在的 transform'],
)
def create_offset_group(controller, suffix='_offset'):
    # type: (str, str) -> Dict[str, str]
    """给控制器套 offset group。

    :param controller: 控制器 transform 名
    :param suffix: 组名后缀
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not cmds.objExists(controller):
            raise ValueError('控制器不存在: {}'.format(controller))

        group_name = controller + suffix
        grp = cmds.group(empty=True, name=group_name)

        # 拷贝控制器的世界 transform 到 group
        matrix = cmds.xform(controller, query=True, worldSpace=True, matrix=True)
        cmds.xform(grp, worldSpace=True, matrix=matrix)

        # 如果控制器有父，把 group 塞到同一级
        parents = cmds.listRelatives(controller, parent=True) or []
        if parents:
            cmds.parent(grp, parents[0])

        # 控制器变成 group 的子
        cmds.parent(controller, grp)

        return {'offset_group': grp, 'controller': controller}

    return run_on_main(_impl)

@tool(
    dcc=['maya'],
    description='在关节链上均匀复制一组定位器（locator），常用于放置控制器或 Twist 关节。',
    category='rigging',
    examples=[
        {
            'summary': '沿 spine 链创建 5 个定位器',
            'args': {'joint_chain': ['root_joint', 'spine_joint', 'chest_joint'], 'count': 5, 'prefix': 'spineTwist'},
        },
    ],
    returns_desc='List[str]: 创建的定位器名列表',
    notes=['沿关节链每关节位置放置一个 locator，可用于打点/取样。'],
)
def create_locators_along_chain(joint_chain, count=3, prefix='loc'):
    # type: (Any, int, str) -> List[str]
    """沿关节链均匀创建定位器。

    :param joint_chain: 关节链名列表
    :param count: 要创建的定位器数量
    :param prefix: 定位器名前缀
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    joints = _normalize_names(joint_chain)

    def _impl():
        if len(joints) < 2:
            raise ValueError('关节链至少需要 2 个关节')
        for j in joints:
            if not cmds.objExists(j):
                raise ValueError('关节不存在: {}'.format(j))

        positions = []
        for j in joints:
            pos = cmds.xform(j, query=True, worldSpace=True, translation=True)
            positions.append(pos)

        locators = []
        for i in range(count):
            t = i / max(count - 1, 1)
            idx = t * (len(positions) - 1)
            i0 = int(idx)
            i1 = min(i0 + 1, len(positions) - 1)
            frac = idx - i0
            p0 = positions[i0]
            p1 = positions[i1]
            pos = tuple(p0[k] + (p1[k] - p0[k]) * frac for k in range(3))
            loc = cmds.spaceLocator(name='{}_{}_loc'.format(prefix, i + 1))[0]
            cmds.xform(loc, worldSpace=True, translation=pos)
            locators.append(loc)
        return locators

    return run_on_main(_impl)

@tool(
    dcc=['maya'],
    description=(
        '给关节链批量创建 offset group 层级（用于 FK 控制器）。'
        '为每个关节生成一个空组并对齐关节位置和方向。'
    ),
    category='rigging',
    examples=[
        {
            'summary': '为手臂 FK 关节链生成对齐 offset 组',
            'args': {
                'joints': ['fk_shoulder', 'fk_elbow', 'fk_wrist'],
                'suffix': '_ctrlGrp',
            },
        },
    ],
    notes=['返回的 offset group 已按关节顺序建立父子层级。'],
    returns_desc='List[str]: 对齐的 offset 组名列表',
)
def create_aligned_groups(joints, suffix='_grp'):
    # type: (Any, str) -> List[str]
    """为每个关节创建对齐的空组，并建立父子层级。

    :param joints: 关节名列表
    :param suffix: 组名后缀
    """
    _ensure_in_maya()

    joint_list = _normalize_names(joints)
    if not joint_list:
        raise ValueError('joints 不能为空')

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        groups = []
        for j in joint_list:
            if not cmds.objExists(j):
                raise ValueError('关节不存在: {}'.format(j))
            grp = cmds.group(empty=True, name=j + suffix)
            matrix = cmds.xform(j, query=True, worldSpace=True, matrix=True)
            cmds.xform(grp, worldSpace=True, matrix=matrix)
            groups.append(grp)

        # 建立父子层级
        for i in range(len(groups) - 1):
            cmds.parent(groups[i + 1], groups[i])

        return groups

    return run_on_main(_impl)
