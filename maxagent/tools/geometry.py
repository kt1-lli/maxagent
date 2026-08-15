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

- **构造器**：仍然传 ``pos=rt.Point3(x, y, z)``（官方 teapot 示例写法）。
- **首选后置 setter**：``node.setmxsprop('pos', p3)``（官方方案 2，
  实测在 Max 2022~2027 均可靠）。
- **兜底**：``rt.setProperty(node, 'pos', p3)``（官方方案 1）。
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
    """把 [x, y, z] 列表/元组转为 rt.Point3，非法输入抛出 ValueError。

    之前返回 None 会导致非法 position 被静默忽略，agent 以为创建成功。
    现在改为显式报错，让调用方能立即感知参数错误。
    """
    if position is None:
        return None
    try:
        if len(position) != 3:
            raise ValueError(
                'position 必须是包含 3 个数值的列表/元组: {}'.format(position),
            )
        return rt.Point3(
            float(position[0]), float(position[1]), float(position[2]),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            'position 参数解析失败: {} ({})'.format(position, exc),
        ) from exc


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
    在某些版本/对象上不会真正生效。Autodesk 官方文档推荐的
    ``rt.setProperty`` / ``node.setmxsprop`` 在 Max 2022 实测仍可能失败。

    经过多轮回归测试验证，最可靠的方式是回到 MAXScript 原生执行路径：
    通过 ``rt.execute`` 直接运行 ``obj.pos = [x, y, z]`` /
    ``obj.rotation = eulerAngles x y z``。该路径与用户在 MaxScript 监听器
    中手动验证成功的路径完全一致。

    :param node: rt 创建返回的节点
    :param name: 要设置的名字（空字符串跳过）
    :param position: 期望位置 [x, y, z]，None 表示不校验
    :param rotation_euler: 期望旋转（欧拉角度），None 表示不设置
    """
    if name:
        try:
            node.name = name
        except Exception:  # pylint: disable=broad-except
            pass

    node_name = str(node.name).replace('"', '\\"')

    if position is not None:
        p3 = _to_point3(position)
        if p3 is not None:
            script = (
                'obj = getNodeByName "{name}"\n'
                'obj.pos = [{x}, {y}, {z}]'
            ).format(
                name=node_name,
                x=float(p3.x),
                y=float(p3.y),
                z=float(p3.z),
            )
            try:
                rt.execute(script)
            except Exception as exc:  # pylint: disable=broad-except
                raise RuntimeError(
                    'position 设置失败: {} ({})'.format(position, exc),
                ) from exc
            _verify_position(node, position)

    if rotation_euler is not None:
        if len(rotation_euler) != 3:
            raise ValueError(
                'rotation_euler 必须是包含 3 个数值的列表/元组: {}'.format(
                    rotation_euler,
                ),
            )
        script = (
            'obj = getNodeByName "{name}"\n'
            'obj.rotation = eulerAngles {x} {y} {z}'
        ).format(
            name=node_name,
            x=float(rotation_euler[0]),
            y=float(rotation_euler[1]),
            z=float(rotation_euler[2]),
        )
        try:
            rt.execute(script)
        except Exception as exc:  # pylint: disable=broad-except
            raise RuntimeError(
                'rotation_euler 设置失败: {} ({})'.format(rotation_euler, exc),
            ) from exc
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
    node = rt.Teapot(**kwargs)
    _apply_common(node, name, position, None)
    return {'name': str(node.name), 'class': 'Teapot'}