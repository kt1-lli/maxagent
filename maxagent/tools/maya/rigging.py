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
        s = coords.strip()
        if not s:
            return None
        try:
            coords = json.loads(s)
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
    notes=['支持类型: parent / point / orient / scale / aim / pole_vector。', 'maintain_offset=True 表示以当前偏移为初始状态。'],
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


# ---------------------------------------------------------------------------
# 绑定进阶工具
# ---------------------------------------------------------------------------


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


@tool(
    dcc=['maya'],
    description=(
        '创建 Set Driven Key（SDK 驱动关键帧）。让一个属性根据另一个属性的值变化。'
        '绑定中常用于校正形态（如手腕转动带动手指弯曲）。'
    ),
    category='rigging',
    examples=[
        {
            'summary': '让 IKFK 开关驱动 IK 链可见性',
            'args': {
                'driver': 'ctrl.ikfk',
                'driven': 'ik_group.visibility',
                'driver_value': 1.0,
                'driven_value': 1.0,
            },
        },
    ],
    notes=[
        '每次调用只设置一个关键点。要形成完整驱动关系，通常需要至少调用 2 次（起点+终点）。',
        'driver 和 driven 都是 "node.attr" 格式。',
    ],
    returns_desc='dict: {"ok": True}',
)
def create_set_driven_key(driver, driven, driver_value, driven_value):
    # type: (str, str, float, float) -> Dict[str, Any]
    """创建 SDK 驱动关键帧。

    :param driver: 驱动属性 "node.attr"
    :param driven: 被驱动属性 "node.attr"
    :param driver_value: 驱动值
    :param driven_value: 被驱动值
    """
    _ensure_in_maya()

    if '.' not in driver or '.' not in driven:
        raise ValueError('driver / driven 都必须是 "node.attr" 格式')

    def _impl():
        import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
        driver_node = driver.split('.', 1)[0]
        driven_node = driven.split('.', 1)[0]
        if not cmds.objExists(driver_node):
            raise ValueError('驱动节点不存在: {}'.format(driver_node))
        if not cmds.objExists(driven_node):
            raise ValueError('被驱动节点不存在: {}'.format(driven_node))

        # 先把 driven 属性设成目标值，再设驱动关键点
        cmds.setAttr(driven, float(driven_value))
        cmds.setDrivenKeyframe(
            driven,
            currentDriver=driver,
            driverValue=float(driver_value),
            value=float(driven_value),
        )
        return {'ok': True}

    return run_on_main(_impl)


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