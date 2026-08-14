#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""几何体创建类工具。

提供给 agent 的"创建"能力：box / sphere / cylinder / cone / torus / plane / teapot。
所有工具都会返回创建出来的对象名（如果有重名 Max 会自动加后缀），方便 agent 后续操作。

**关于 position 参数的官方正确用法（源自 Autodesk pymxs 官方文档）：**

Autodesk 官方文档 "Accessing Object Properties and Controllers" 指出：

    Python and MAXScript treat properties indicated with dot notation differently:
    Python always returns a reference to the indicated property, while MAXScript
    returns a copy of the value. Attempting to assign a value may not work as
    intended in pymxs.

    There are three solutions to this problem:
      1. Use the MAXScript getProperty() and setProperty() functions
      2. Use the pymxs MXSWrapperBase getmxsprop() and setmxsprop() functions
      3. Work on a copy of the target property, and assign the object back

因此本模块采用：

- **首选**：构造器直接传 ``pos=rt.Point3(x, y, z)``（官方 teapot 示例的写法）——
  这是最简单最稳的路径，创建瞬间就定位好，避免任何后续 setter 陷阱。
- **兜底**：``rt.setProperty(node, 'pos', p3)``（官方 3 大方案之首）。
- **硬校验**：写入后读回坐标，偏差 > 0.01 抛异常，绝不静默通过。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import List
from typing import Optional

from ..runtime_helpers import IN_MAX
from ..runtime_helpers import rt
from .registry import tool


_POSITION_TOLERANCE = 0.01


def _ensure_in_max():
    if not IN_MAX:
        raise RuntimeError('非 3ds Max 环境')


def _to_point3(position):
    """把 [x, y, z] 列表/元组转为 rt.Point3，非法输入返回 None。"""
    if position is None:
        return None
    try:
        if len(position) != 3:
            return None
        return rt.Point3(
            float(position[0]), float(position[1]), float(position[2]),
        )
    except (TypeError, ValueError):
        return None


def _verify_position(node, expected):
    """读回 node.pos 并硬校验：偏差 > _POSITION_TOLERANCE 抛 RuntimeError。

    读回失败（罕见，如 node 已被 undo 销毁）不阻塞——上层还能通过
    get_object_info 复核，不应破坏正常流程。
    """
    if expected is None:
        return
    try:
        actual = node.pos
        dx = abs(float(actual.x) - float(expected[0]))
        dy = abs(float(actual.y) - float(expected[1]))
        dz = abs(float(actual.z) - float(expected[2]))
    except Exception:  # pylint: disable=broad-except
        return
    if (
        dx > _POSITION_TOLERANCE
        or dy > _POSITION_TOLERANCE
        or dz > _POSITION_TOLERANCE
    ):
        raise RuntimeError(
            'position 未生效: 期望 [{},{},{}], 实际 [{},{},{}]'.format(
                float(expected[0]), float(expected[1]), float(expected[2]),
                float(actual.x), float(actual.y), float(actual.z),
            ),
        )


def _apply_common(node, name, position, rotation_euler):
    """统一处理对象创建后的命名、后置定位与旋转。

    pymxs 对点号属性访问的赋值行为与 MAXScript 不同，直接 ``node.pos = p3``
    在某些版本/对象上不会真正生效。Autodesk 官方文档推荐三种方案之一即
    使用 MAXScript ``setProperty()`` 函数，本模块统一采用它作为标准写法。

    :param node: rt 创建返回的节点
    :param name: 要设置的名字（空字符串跳过）
    :param position: 期望位置，用于硬校验；None 表示不校验
    :param rotation_euler: 期望旋转（欧拉角度），None 表示不设置
    """
    if name:
        try:
            node.name = name
        except Exception:  # pylint: disable=broad-except
            pass

    # position：使用官方标准方法 rt.setProperty(node, 'pos', p3) 设置。
    # 部分 Max 版本（如 2022）构造器 pos 不生效，因此创建后统一再设置一次，
    # 并硬校验读回坐标与期望值偏差不超过 _POSITION_TOLERANCE。
    if position is not None:
        p3 = _to_point3(position)
        if p3 is not None:
            rt.setProperty(node, 'pos', p3)
            _verify_position(node, position)

    if rotation_euler is not None and len(rotation_euler) == 3:
        try:
            euler = rt.eulerAngles(
                float(rotation_euler[0]),
                float(rotation_euler[1]),
                float(rotation_euler[2]),
            )
            rt.setProperty(node, 'rotation', rt.eulerToQuat(euler))
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
    kwargs = {
        'length': float(length),
        'width': float(width),
        'height': float(height),
    }
    p3 = _to_point3(position)
    if p3 is not None:
        kwargs['pos'] = p3
    node = rt.Box(**kwargs)
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
    kwargs = {'radius': float(radius), 'segs': int(segments)}
    p3 = _to_point3(position)
    if p3 is not None:
        kwargs['pos'] = p3
    node = rt.Sphere(**kwargs)
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
    kwargs = {
        'radius': float(radius),
        'height': float(height),
        'sides': int(sides),
    }
    p3 = _to_point3(position)
    if p3 is not None:
        kwargs['pos'] = p3
    node = rt.Cylinder(**kwargs)
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
    kwargs = {
        'radius1': float(radius1),
        'radius2': float(radius2),
        'height': float(height),
        'sides': int(sides),
    }
    p3 = _to_point3(position)
    if p3 is not None:
        kwargs['pos'] = p3
    node = rt.Cone(**kwargs)
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
    kwargs = {
        'radius1': float(radius1),
        'radius2': float(radius2),
        'segs': int(segments),
        'sides': int(sides),
    }
    p3 = _to_point3(position)
    if p3 is not None:
        kwargs['pos'] = p3
    node = rt.Torus(**kwargs)
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
    kwargs = {
        'length': float(length),
        'width': float(width),
        'lengthsegs': int(length_segs),
        'widthsegs': int(width_segs),
    }
    p3 = _to_point3(position)
    if p3 is not None:
        kwargs['pos'] = p3
    node = rt.Plane(**kwargs)
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
    kwargs = {'radius': float(radius), 'segments': int(segments)}
    p3 = _to_point3(position)
    if p3 is not None:
        kwargs['pos'] = p3
    node = rt.Teapot(**kwargs)
    _apply_common(node, name, position, None)
    return {'name': str(node.name), 'class': 'Teapot'}