#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变换类工具：移动、旋转、缩放、对齐、重置轴心。

设计要点：
1. 所有工具用对象名定位节点（agent 友好），找不到则结构化报错。
2. 移动/旋转/缩放支持绝对模式（set）和相对模式（add）。
3. 默认在世界坐标系操作；如需局部坐标，可在后续扩展加 space 参数。

**关于属性 setter**：Autodesk pymxs 官方文档明确警告 ``node.xxx = value``
这种点号赋值在部分场景下会静默失败（Max 返回 copy 而非 reference）。
本模块统一走 ``rt.setProperty(node, 'xxx', value)``（官方推荐方案 1），
避免陷阱。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import List
from typing import Optional

from ...runtime_helpers import IN_MAX
from ...runtime_helpers import rt
from ...tools.registry import tool


def _ensure_in_max():
    if not IN_MAX:
        raise RuntimeError('非 3ds Max 环境')


def _get_node(name):
    """按名查找节点，未找到时抛 ValueError。"""
    node = rt.getNodeByName(name, exact=True, all=False)
    if node is None:
        raise ValueError('对象不存在: {}'.format(name))
    return node


def _set_prop_safe(node, prop_name, value):
    """走官方 setProperty 路径写属性，兜底再走 setmxsprop / 属性赋值。

    避免 pymxs "attribute setter 返回 copy 静默失败" 陷阱。
    """
    try:
        rt.setProperty(node, prop_name, value)
        return
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        node.setmxsprop(prop_name, value)
        return
    except Exception:  # pylint: disable=broad-except
        pass
    # 最后兜底：直接属性赋值（对已知稳定的属性如 rotation 通常也 OK）
    setattr(node, prop_name, value)


@tool(
    dcc=['3dsmax'],
    description='移动对象到指定位置或按偏移量相对移动。',
    category='transform',
    examples=[
        {
            'summary': '把对象移到世界坐标 (100, 0, 50)',
            'args': {'name': 'Box001', 'x': 100.0, 'y': 0.0, 'z': 50.0, 'mode': 'set'},
        },
        {
            'summary': '把对象沿 X 轴相对移动 10 个单位',
            'args': {'name': 'Box001', 'x': 10.0, 'y': 0.0, 'z': 0.0, 'mode': 'add'},
        },
    ],
    notes=[
        'mode="set" 表示设置绝对世界坐标位置；mode="add" 表示在当前位置上叠加偏移量。',
        'x/y/z 为世界空间坐标值，单位与当前场景一致。',
        '函数返回最终世界空间位置 [x, y, z]。',
    ],
    returns_desc='dict {"name": 对象名, "position": [x, y, z]}',
    prerequisites=['场景中必须存在名为 name 的对象'],
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
    _set_prop_safe(node, 'pos', target)
    pos = node.position
    return {
        'name': str(node.name),
        'position': [float(pos.x), float(pos.y), float(pos.z)],
    }


@tool(
    dcc=['3dsmax'],
    description='旋转对象到指定欧拉角或按偏移量相对旋转。',
    category='transform',
    examples=[
        {
            'summary': '把对象设置为绕 Z 轴旋转 45 度',
            'args': {'name': 'Box001', 'x': 0.0, 'y': 0.0, 'z': 45.0, 'mode': 'set'},
        },
        {
            'summary': '把对象在当前旋转基础上再绕 X 轴转 15 度',
            'args': {'name': 'Box001', 'x': 15.0, 'y': 0.0, 'z': 0.0, 'mode': 'add'},
        },
    ],
    notes=[
        'x/y/z 为欧拉角，单位为度，按 X->Y->Z 顺序应用。',
        'mode="set" 表示设置绝对旋转；mode="add" 表示在当前旋转上叠加。',
        '内部使用四元数存储，返回值为转换后的欧拉角 [x, y, z]。',
    ],
    returns_desc='dict {"name": 对象名, "rotation_euler": [x, y, z]}',
    prerequisites=['场景中必须存在名为 name 的对象'],
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
    _set_prop_safe(node, 'rotation', rt.eulerToQuat(new_euler))
    out = rt.quatToEuler(node.rotation)
    return {
        'name': str(node.name),
        'rotation_euler': [float(out.x), float(out.y), float(out.z)],
    }


@tool(
    dcc=['3dsmax'],
    description='缩放对象，支持各轴独立设置、相加或相乘。',
    category='transform',
    examples=[
        {
            'summary': '把对象在 X 轴缩放到 2 倍',
            'args': {'name': 'Box001', 'x': 2.0, 'y': 1.0, 'z': 1.0, 'mode': 'set'},
        },
        {
            'summary': '把对象整体再放大 1.5 倍',
            'args': {'name': 'Box001', 'x': 1.5, 'y': 1.5, 'z': 1.5, 'mode': 'multiply'},
        },
    ],
    notes=[
        'mode="set" 表示直接设置缩放值；mode="add" 表示在当前缩放上相加；'
        'mode="multiply" 表示在当前缩放上相乘。',
        '默认缩放基准为 1.0，设置为 2.0 表示该轴放大一倍。',
        '返回最终缩放值 [x, y, z]。',
    ],
    returns_desc='dict {"name": 对象名, "scale": [x, y, z]}',
    prerequisites=['场景中必须存在名为 name 的对象'],
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
    _set_prop_safe(node, 'scale', target)
    out = node.scale
    return {
        'name': str(node.name),
        'scale': [float(out.x), float(out.y), float(out.z)],
    }


@tool(
    dcc=['3dsmax'],
    description='把一个对象对齐到另一个对象，可独立选择位置、旋转或缩放。',
    category='transform',
    examples=[
        {
            'summary': '把 Box001 的位置对齐到 Box002',
            'args': {
                'source_name': 'Box001',
                'target_name': 'Box002',
                'align_position': True,
                'align_rotation': False,
                'align_scale': False,
            },
        },
        {
            'summary': '把 Box001 的位置和旋转都对齐到 Box002',
            'args': {
                'source_name': 'Box001',
                'target_name': 'Box002',
                'align_position': True,
                'align_rotation': True,
                'align_scale': False,
            },
        },
    ],
    notes=[
        'source_name 是被修改的对象，target_name 是参考对象且不会被修改。',
        '在世界空间下进行对齐，勾选对应布尔开关即可分别对齐位置、旋转、缩放。',
        '三个布尔参数可以同时为 True，实现完整对齐。',
    ],
    returns_desc='dict {"source": 源对象名, "target": 目标对象名, "aligned": {...}}',
    prerequisites=[
        '场景中必须同时存在 source_name 和 target_name 对应的对象',
    ],
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
        _set_prop_safe(src, 'pos', tgt.position)
    if align_rotation:
        _set_prop_safe(src, 'rotation', tgt.rotation)
    if align_scale:
        _set_prop_safe(src, 'scale', tgt.scale)
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
    dcc=['3dsmax'],
    description='把对象的轴心重置到几何中心、世界原点或自定义位置。',
    category='transform',
    examples=[
        {
            'summary': '把对象轴心重置到几何中心',
            'args': {'name': 'Box001', 'mode': 'object_center'},
        },
        {
            'summary': '把对象轴心移动到世界原点',
            'args': {'name': 'Box001', 'mode': 'world_origin'},
        },
        {
            'summary': '把对象轴心设置到自定义位置 (10, 20, 30)',
            'args': {'name': 'Box001', 'mode': 'custom', 'x': 10.0, 'y': 20.0, 'z': 30.0},
        },
    ],
    notes=[
        'mode="object_center" 调用 CenterPivot 将轴心重置到对象几何中心。',
        'mode="world_origin" 把轴心移动到世界原点 (0, 0, 0)。',
        'mode="custom" 时必须提供 x/y/z 参数，作为目标轴心位置。',
        '轴心位置修改后，对象的视觉位置可能不变，但后续变换会围绕新轴心进行。',
    ],
    returns_desc='dict {"name": 对象名, "pivot": [x, y, z]}',
    prerequisites=['场景中必须存在名为 name 的对象'],
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
        _set_prop_safe(node, 'pivot', rt.Point3(0.0, 0.0, 0.0))
    elif mode == 'custom':
        _set_prop_safe(
            node, 'pivot', rt.Point3(float(x), float(y), float(z)),
        )
    else:
        raise ValueError('未知 mode: {}'.format(mode))
    pv = node.pivot
    return {
        'name': str(node.name),
        'pivot': [float(pv.x), float(pv.y), float(pv.z)],
    }

