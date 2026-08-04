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

    坐标系陷阱：pymxs 里 ``node.position = rt.Point3(...)`` 在部分
    Max 版本 / 部分创建函数（rt.Box 等）返回的 node 上会被静默忽略，
    对象仍留在原点。原因是 Max 的 position 属性写入依赖当前
    coordsys 上下文；pymxs 层没有稳定的 coordsys world 上下文注入。

    稳妥做法：优先 ``node.pos``（多数版本可写），失败再兜底
    ``node.position``，最后走 MaxScript 层 ``in coordsys world``。
    """
    if name:
        try:
            node.name = name
        except Exception:  # pylint: disable=broad-except
            pass
    if position is not None and len(position) == 3:
        p3 = rt.Point3(
            float(position[0]), float(position[1]), float(position[2]),
        )
        _applied = False
        # 优先 .pos（Max 里 pos 与 position 同义，但 pos 属性无坐标系依赖）
        try:
            node.pos = p3
            _applied = True
        except Exception:  # pylint: disable=broad-except
            pass
        if not _applied:
            try:
                node.position = p3
                _applied = True
            except Exception:  # pylint: disable=broad-except
                pass
        # 校验实际位置——赋值成功但坐标系错误的兜底
        try:
            actual = node.pos
            if (
                abs(float(actual.x) - float(position[0])) > 0.01
                or abs(float(actual.y) - float(position[1])) > 0.01
                or abs(float(actual.z) - float(position[2])) > 0.01
            ):
                # 最后走 MaxScript 层强制 world coordsys
                script = (
                    'in coordsys world $\'{}\'.pos = [{},{},{}]'
                ).format(
                    str(node.name).replace("'", "\\'"),
                    float(position[0]),
                    float(position[1]),
                    float(position[2]),
                )
                try:
                    rt.execute(script)
                except Exception:  # pylint: disable=broad-except
                    pass
        except Exception:  # pylint: disable=broad-except
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
