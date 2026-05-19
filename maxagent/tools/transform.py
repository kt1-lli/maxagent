#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变换类工具：移动、旋转、缩放、对齐、重置轴心。

设计要点：
1. 所有工具用对象名定位节点（agent 友好），找不到则结构化报错。
2. 移动/旋转/缩放支持绝对模式（set）和相对模式（add）。
3. 默认在世界坐标系操作；如需局部坐标，可在后续扩展加 space 参数。
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


def _get_node(name):
    """按名查找节点，未找到时抛 ValueError。"""
    node = rt.getNodeByName(name, exact=True, all=False)
    if node is None:
        raise ValueError('对象不存在: {}'.format(name))
    return node


@tool(
    description='移动对象。支持绝对位置（set）或相对位移（add）。',
    category='transform',
)
def move_object(name, x=0.0, y=0.0, z=0.0, mode='set'):
    """移动对象。

    :param name: 对象名（精确匹配）
    :param x: X 方向值
    :param y: Y 方向值
    :param z: Z 方向值
    :param mode: 'set' 表示设置为绝对位置；'add' 表示在当前位置上叠加
    :returns: dict {"name": ..., "position": [x, y, z]}
    """
    _ensure_in_max()
    node = _get_node(name)
    target = rt.Point3(float(x), float(y), float(z))
    if mode == 'add':
        cur = node.position
        target = rt.Point3(
            float(cur.x) + float(x),
            float(cur.y) + float(y),
            float(cur.z) + float(z),
        )
    node.position = target
    pos = node.position
    return {
        'name': str(node.name),
        'position': [float(pos.x), float(pos.y), float(pos.z)],
    }


@tool(
    description='旋转对象。欧拉角单位为度。支持绝对（set）或叠加（add）。',
    category='transform',
)
def rotate_object(name, x=0.0, y=0.0, z=0.0, mode='set'):
    """旋转对象。

    :param name: 对象名
    :param x: 绕 X 轴角度（度）
    :param y: 绕 Y 轴角度（度）
    :param z: 绕 Z 轴角度（度）
    :param mode: 'set' 设置欧拉角；'add' 在当前旋转上叠加
    :returns: dict {"name": ..., "rotation_euler": [x, y, z]}
    """
    _ensure_in_max()
    node = _get_node(name)
    new_euler = rt.eulerAngles(float(x), float(y), float(z))
    if mode == 'add':
        # 把当前旋转转成欧拉角并叠加
        cur_euler = rt.quatToEuler(node.rotation)
        new_euler = rt.eulerAngles(
            float(cur_euler.x) + float(x),
            float(cur_euler.y) + float(y),
            float(cur_euler.z) + float(z),
        )
    node.rotation = rt.eulerToQuat(new_euler)
    out = rt.quatToEuler(node.rotation)
    return {
        'name': str(node.name),
        'rotation_euler': [float(out.x), float(out.y), float(out.z)],
    }


@tool(
    description='缩放对象。支持各轴独立缩放，set/add/multiply 三种模式。',
    category='transform',
)
def scale_object(name, x=1.0, y=1.0, z=1.0, mode='set'):
    """缩放对象。

    :param name: 对象名
    :param x: X 轴缩放
    :param y: Y 轴缩放
    :param z: Z 轴缩放
    :param mode: 'set' 设置；'add' 加；'multiply' 在当前缩放上相乘
    :returns: dict {"name": ..., "scale": [x, y, z]}
    """
    _ensure_in_max()
    node = _get_node(name)
    cur = node.scale
    if mode == 'add':
        target = rt.Point3(
            float(cur.x) + float(x),
            float(cur.y) + float(y),
            float(cur.z) + float(z),
        )
    elif mode == 'multiply':
        target = rt.Point3(
            float(cur.x) * float(x),
            float(cur.y) * float(y),
            float(cur.z) * float(z),
        )
    else:
        target = rt.Point3(float(x), float(y), float(z))
    node.scale = target
    out = node.scale
    return {
        'name': str(node.name),
        'scale': [float(out.x), float(out.y), float(out.z)],
    }


@tool(
    description='把一个对象对齐到另一个对象（位置/旋转/缩放可独立选择）。',
    category='transform',
)
def align_to(
    source_name,
    target_name,
    align_position=True,
    align_rotation=False,
    align_scale=False,
):
    """把 source 对齐到 target。

    :param source_name: 被对齐对象（会被修改）
    :param target_name: 参考对象（不被修改）
    :param align_position: 是否对齐位置
    :param align_rotation: 是否对齐旋转
    :param align_scale: 是否对齐缩放
    :returns: dict 描述执行结果
    """
    _ensure_in_max()
    src = _get_node(source_name)
    tgt = _get_node(target_name)
    if align_position:
        src.position = tgt.position
    if align_rotation:
        src.rotation = tgt.rotation
    if align_scale:
        src.scale = tgt.scale
    return {
        'source': str(src.name),
        'target': str(tgt.name),
        'aligned': {
            'position': bool(align_position),
            'rotation': bool(align_rotation),
            'scale': bool(align_scale),
        },
    }


@tool(
    description=(
        '把对象的轴心（pivot）重置到指定位置。'
        'mode="object_center" 重置到对象几何中心；'
        '"world_origin" 重置到世界原点；'
        '"custom" 时使用 x/y/z 参数。'
    ),
    category='transform',
)
def reset_pivot(name, mode='object_center', x=0.0, y=0.0, z=0.0):
    """重置轴心。

    :param name: 对象名
    :param mode: 'object_center' / 'world_origin' / 'custom'
    :param x: 自定义 X
    :param y: 自定义 Y
    :param z: 自定义 Z
    :returns: dict {"name": ..., "pivot": [x, y, z]}
    """
    _ensure_in_max()
    node = _get_node(name)
    if mode == 'object_center':
        # CenterPivot 内置命令
        rt.CenterPivot(node)
    elif mode == 'world_origin':
        # 通过设置 pivot 的 transform 行 4 实现
        node.pivot = rt.Point3(0.0, 0.0, 0.0)
    elif mode == 'custom':
        node.pivot = rt.Point3(float(x), float(y), float(z))
    else:
        raise ValueError('未知 mode: {}'.format(mode))
    pv = node.pivot
    return {
        'name': str(node.name),
        'pivot': [float(pv.x), float(pv.y), float(pv.z)],
    }
