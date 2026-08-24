#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 绑定类工具。

提供关节创建、蒙皮、约束、IK、FK/IK 切换、控制器曲线等常见绑定操作。
所有会修改场景的操作都默认包在 undo 块内。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

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
    # type: (Any, str) -> Optional[tuple]
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
    description='创建 NURBS 曲线作为控制器，可指定形状（圆、方块、十字、锥体箭头）。',
    category='rigging',
    examples=[
        {
            'summary': '在手腕创建圆圈控制器',
            'args': {'name': 'wrist_ctrl', 'shape': 'circle', 'position': [0.0, 10.0, 0.0]},
        },
    ],
    returns_desc='str: 控制器 transform 节点名',
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

    xyz = _to_xyz_list(position, 'position') or (0.0, 0.0, 0.0)

    def _impl():
        cv_points = _controller_shape_points(shape, radius)
        curve = cmds.curve(degree=1, point=cv_points, knot=list(range(len(cv_points))), name=name)
        cmds.xform(curve, translation=xyz, worldSpace=True)
        cmds.makeIdentity(curve, apply=True, translate=True, rotate=True, scale=True)
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
    description='对网格执行平滑蒙皮（skinCluster），绑定到指定关节。',
    category='rigging',
    examples=[
        {
            'summary': '把 body_mesh 绑定到根关节链',
            'args': {'mesh': 'body_mesh', 'joints': ['root_joint', 'spine_joint'], 'name': 'body_skinCluster'},
        },
    ],
    returns_desc='str: skinCluster 节点名',
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


@tool(
    dcc=['maya'],
    description='在控制器和关节/对象之间创建父约束、点约束、方向约束或缩放约束。',
    category='rigging',
    examples=[
        {
            'summary': '用 wrist_ctrl 控制 wrist_joint 的旋转和位置',
            'args': {'driver': 'wrist_ctrl', 'driven': 'wrist_joint', 'constraint_type': 'parent'},
        },
    ],
    returns_desc='str: 约束节点名',
)
def create_constraint(driver, driven, constraint_type='parent', maintain_offset=True, name=None):
    # type: (str, str, str, bool, Optional[str]) -> str
    """创建约束。

    :param driver: 驱动对象名
    :param driven: 被驱动对象名
    :param constraint_type: parent / point / orient / scale
    :param maintain_offset: 是否保持偏移
    :param name: 约束节点名，None 则自动生成
    """
    _ensure_in_maya()

    import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel

    def _impl():
        if not cmds.objExists(driver):
            raise ValueError('驱动对象不存在: {}'.format(driver))
        if not cmds.objExists(driven):
            raise ValueError('被驱动对象不存在: {}'.format(driven))

        fn = getattr(cmds, '{}Constraint'.format(constraint_type), None)
        if fn is None:
            raise ValueError('未知约束类型: {}'.format(constraint_type))

        kwargs = {
            'maintainOffset': maintain_offset,
            'name': name,
        }
        result = fn(driver, driven, **{k: v for k, v in kwargs.items() if v is not None})
        return result[0]

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
    description='在关节链上均匀复制一组定位器（locator），常用于放置控制器或 Twist 关节。',
    category='rigging',
    examples=[
        {
            'summary': '沿 spine 链创建 5 个定位器',
            'args': {'joint_chain': ['root_joint', 'spine_joint', 'chest_joint'], 'count': 5, 'prefix': 'spineTwist'},
        },
    ],
    returns_desc='List[str]: 创建的定位器名列表',
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
    description='把控制器按层级关系建立父子关系，形成控制层级（例如 root → hip → knee → ankle）。',
    category='rigging',
    examples=[
        {
            'summary': '建立角色控制器层级',
            'args': {'controllers': ['root_ctrl', 'hip_ctrl', 'knee_ctrl', 'ankle_ctrl']},
        },
    ],
    returns_desc='dict: {"ok": True}',
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


def _normalize_names(names):
    # type: (Any) -> List[str]
    """把 names 归一化为 list[str]，兼容 LLM 的多种输入形式。"""
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
