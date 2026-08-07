#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""几何体创建类工具。

提供给 agent 的"创建"能力：box / sphere / cylinder / cone / torus / plane / teapot。
所有工具都会返回创建出来的对象名（如果有重名 Max 会自动加后缀），方便 agent 后续操作。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import List
from typing import Optional

from ..runtime_helpers import IN_MAX
from ..runtime_helpers import rt
from .registry import tool


def _ensure_in_max():
    if not IN_MAX:
        raise RuntimeError('非 3ds Max 环境')


def _apply_common(node, name, position, rotation_euler):
    """统一处理对象创建后的命名与变换。

    坐标系陷阱（已修）：pymxs 里 ``node.position = rt.Point3(...)`` 在
    Max 2022 的部分 primitive（Box/Sphere/targetSpot/targetCamera）返回
    的 node 上会被静默吞掉——setter 无异常但值不写入。根因是这些属性
    setter 依赖当前 coordsys 上下文，且 pymxs 层无法可靠注入
    ``in coordsys world``。

    修复策略：优先直接走 MaxScript ``in coordsys world`` 一次性定位——
    这是 3ds Max 官方唯一稳定的坐标写入路径；写入后**主动读回校验**，
    偏差 > 0.01 直接抛异常（不再静默通过）。
    """
    if name:
        try:
            node.name = name
        except Exception:  # pylint: disable=broad-except
            pass
    if position is not None and len(position) == 3:
        px, py, pz = (
            float(position[0]), float(position[1]), float(position[2]),
        )
        # 优先走 MaxScript：语义稳定，不受 pymxs setter bug 影响。
        # 用 getAnimByHandle 而不是 $'name' —— 前者不依赖名字里的特殊字符转义。
        try:
            handle = int(rt.getHandleByAnim(node))
        except Exception:  # pylint: disable=broad-except
            handle = 0
        _applied = False
        if handle > 0:
            script = (
                'in coordsys world (getAnimByHandle {h}).pos = [{x},{y},{z}]'
            ).format(h=handle, x=px, y=py, z=pz)
            try:
                rt.execute(script)
                _applied = True
            except Exception:  # pylint: disable=broad-except
                _applied = False
        # 兜底 1：pymxs .pos setter
        if not _applied:
            try:
                node.pos = rt.Point3(px, py, pz)
                _applied = True
            except Exception:  # pylint: disable=broad-except
                pass
        # 兜底 2：pymxs .position setter
        if not _applied:
            try:
                node.position = rt.Point3(px, py, pz)
                _applied = True
            except Exception:  # pylint: disable=broad-except
                pass
        # 硬校验：读回实际坐标，偏差过大直接抛，绝不静默失败。
        try:
            actual = node.pos
            dx = abs(float(actual.x) - px)
            dy = abs(float(actual.y) - py)
            dz = abs(float(actual.z) - pz)
            if dx > 0.01 or dy > 0.01 or dz > 0.01:
                raise RuntimeError(
                    'position 未生效: 期望 [{},{},{}], 实际 [{},{},{}]'.format(
                        px, py, pz,
                        float(actual.x), float(actual.y), float(actual.z),
                    ),
                )
        except RuntimeError:
            raise
        except Exception:  # pylint: disable=broad-except
            # 读回失败（罕见），不阻塞——上层还能通过 get_object_info 复核
            pass
    if rotation_euler is not None and len(rotation_euler) == 3:
        euler = rt.eulerAngles(
            float(rotation_euler[0]),
            float(rotation_euler[1]),
            float(rotation_euler[2]),
        )
        try:
            node.rotation = rt.eulerToQuat(euler)
        except Exception:  # pylint: disable=broad-except
            pass
    return node


@tool(
    description='在场景中创建一个长方体（Box）。',
    category='geometry',
)
def create_box(
    length=10.0,
    width=10.0,
    height=10.0,
    name='',
    position=None,
    rotation_euler=None,
):
    """创建长方体。

    :param length: 长（Y 方向，单位 Max system unit）
    :param width: 宽（X 方向）
    :param height: 高（Z 方向）
    :param name: 对象名（空字符串时由 Max 自动命名）
    :param position: [x, y, z] 世界坐标，None 表示放在原点
    :param rotation_euler: [x, y, z] 欧拉角（度），None 表示无旋转
    :returns: dict {"name": 实际名称, "class": "Box"}
    """
    _ensure_in_max()
    node = rt.Box(
        length=float(length), width=float(width), height=float(height),
    )
    _apply_common(node, name, position, rotation_euler)
    return {'name': str(node.name), 'class': 'Box'}


@tool(
    description='在场景中创建一个球体（Sphere）。',
    category='geometry',
)
def create_sphere(
    radius=10.0,
    segments=32,
    name='',
    position=None,
):
    """创建球体。

    :param radius: 半径
    :param segments: 分段数（决定圆滑度）
    :param name: 对象名
    :param position: [x, y, z] 位置
    :returns: dict {"name": ..., "class": "Sphere"}
    """
    _ensure_in_max()
    node = rt.Sphere(radius=float(radius), segs=int(segments))
    _apply_common(node, name, position, None)
    return {'name': str(node.name), 'class': 'Sphere'}


@tool(
    description='在场景中创建一个圆柱体（Cylinder）。',
    category='geometry',
)
def create_cylinder(
    radius=10.0,
    height=20.0,
    sides=18,
    name='',
    position=None,
    rotation_euler=None,
):
    """创建圆柱体。

    :param radius: 半径
    :param height: 高度
    :param sides: 边数（侧面分段）
    :param name: 对象名
    :param position: [x, y, z]
    :param rotation_euler: 欧拉角（度）
    """
    _ensure_in_max()
    node = rt.Cylinder(
        radius=float(radius), height=float(height), sides=int(sides),
    )
    _apply_common(node, name, position, rotation_euler)
    return {'name': str(node.name), 'class': 'Cylinder'}


@tool(
    description='在场景中创建一个圆锥体（Cone）。',
    category='geometry',
)
def create_cone(
    radius1=10.0,
    radius2=0.0,
    height=20.0,
    sides=18,
    name='',
    position=None,
):
    """创建圆锥体。

    :param radius1: 底面半径
    :param radius2: 顶面半径（0 表示尖锥）
    :param height: 高度
    :param sides: 边数
    :param name: 对象名
    :param position: [x, y, z]
    """
    _ensure_in_max()
    node = rt.Cone(
        radius1=float(radius1),
        radius2=float(radius2),
        height=float(height),
        sides=int(sides),
    )
    _apply_common(node, name, position, None)
    return {'name': str(node.name), 'class': 'Cone'}


@tool(
    description='在场景中创建一个圆环（Torus）。',
    category='geometry',
)
def create_torus(
    radius1=15.0,
    radius2=3.0,
    segments=24,
    sides=12,
    name='',
    position=None,
):
    """创建圆环（甜甜圈）。

    :param radius1: 主半径（中心到管中心）
    :param radius2: 副半径（管半径）
    :param segments: 主分段
    :param sides: 副分段
    :param name: 对象名
    :param position: [x, y, z]
    """
    _ensure_in_max()
    node = rt.Torus(
        radius1=float(radius1),
        radius2=float(radius2),
        segs=int(segments),
        sides=int(sides),
    )
    _apply_common(node, name, position, None)
    return {'name': str(node.name), 'class': 'Torus'}


@tool(
    description='在场景中创建一个平面（Plane）。常用作地面。',
    category='geometry',
)
def create_plane(
    length=100.0,
    width=100.0,
    length_segs=4,
    width_segs=4,
    name='',
    position=None,
):
    """创建平面。

    :param length: 长（Y 方向）
    :param width: 宽（X 方向）
    :param length_segs: 长方向分段
    :param width_segs: 宽方向分段
    :param name: 对象名
    :param position: [x, y, z]
    """
    _ensure_in_max()
    node = rt.Plane(
        length=float(length),
        width=float(width),
        lengthsegs=int(length_segs),
        widthsegs=int(width_segs),
    )
    _apply_common(node, name, position, None)
    return {'name': str(node.name), 'class': 'Plane'}


@tool(
    description='在场景中创建一个茶壶（Teapot，Max 的标志性测试模型）。',
    category='geometry',
)
def create_teapot(
    radius=10.0,
    segments=4,
    name='',
    position=None,
):
    """创建茶壶。

    :param radius: 半径
    :param segments: 分段数
    :param name: 对象名
    :param position: [x, y, z]
    """
    _ensure_in_max()
    node = rt.Teapot(radius=float(radius), segs=int(segments))
    _apply_common(node, name, position, None)
    return {'name': str(node.name), 'class': 'Teapot'}
