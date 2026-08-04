#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""灯光与相机类工具。

灯光类型：omni / spot / direct / skylight / target_spot / target_direct
相机类型：free / target / physical
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import List
from typing import Optional

from ..runtime_helpers import has_runtime_attr
from ..runtime_helpers import IN_MAX
from ..runtime_helpers import rt
from .geometry import _apply_common as _apply_transform
from .registry import tool


def _ensure_in_max():
    if not IN_MAX:
        raise RuntimeError('非 3ds Max 环境')


def _get_node(name):
    node = rt.getNodeByName(name, exact=True, all=False)
    if node is None:
        raise ValueError('对象不存在: {}'.format(name))
    return node


@tool(
    description=(
        '创建一个灯光。type 可选: omni（点光） / spot（聚光） / '
        'direct（平行光） / skylight（天光） / target_spot / target_direct。'
    ),
    category='light_camera',
)
def create_light(
    type='omni',  # pylint: disable=redefined-builtin
    name='',
    position=None,
    target_position=None,
    multiplier=1.0,
    color=None,
):
    """创建灯光。

    :param type: 灯光类型
    :param name: 灯光名
    :param position: [x, y, z] 灯光位置
    :param target_position: [x, y, z] 目标点（仅 target_spot/target_direct 使用）
    :param multiplier: 强度倍增（典型 0.5~2.0）
    :param color: 颜色 [r, g, b]（0-255 或 0-1）
    :returns: dict {"name": ..., "type": ...}
    """
    _ensure_in_max()
    type_map = {
        'omni': 'Omnilight',
        'spot': 'freeSpot',
        'direct': 'freeDirect',
        'skylight': 'Skylight',
        'target_spot': 'targetSpot',
        'target_direct': 'targetDirect',
    }
    cls_name = type_map.get(type.lower())
    if cls_name is None:
        raise ValueError('未知灯光类型: {}'.format(type))
    cls = getattr(rt, cls_name, None)
    if cls is None:
        raise ValueError('当前 Max 版本不支持: {}'.format(cls_name))

    if cls_name in ('targetSpot', 'targetDirect'):
        target = rt.Point3(0.0, 0.0, 0.0)
        if target_position and len(target_position) == 3:
            target = rt.Point3(
                float(target_position[0]),
                float(target_position[1]),
                float(target_position[2]),
            )
        node = cls(target=rt.Targetobject(target=target))
    else:
        node = cls()

    if name:
        node.name = name
    _apply_transform(node, '', position, None)
    try:
        node.multiplier = float(multiplier)
    except Exception:  # pylint: disable=broad-except
        pass
    if color:
        from .material import _to_color  # pylint: disable=import-outside-toplevel
        try:
            node.rgb = _to_color(color)
        except Exception:  # pylint: disable=broad-except
            pass
    return {
        'name': str(node.name),
        'type': str(rt.classOf(node)),
    }


@tool(
    description='创建一个相机。type: free / target / physical（Max 2016+）。',
    category='light_camera',
)
def create_camera(
    type='free',  # pylint: disable=redefined-builtin
    name='',
    position=None,
    target_position=None,
    fov=45.0,
):
    """创建相机。

    :param type: 'free' / 'target' / 'physical'
    :param name: 相机名
    :param position: 相机位置 [x, y, z]
    :param target_position: 目标位置（target / physical 用）
    :param fov: 视场角（度）
    :returns: dict {"name": ..., "type": ...}
    """
    _ensure_in_max()
    if type == 'physical' and has_runtime_attr('Physical'):
        cls = rt.Physical
    elif type == 'target':
        cls = rt.targetCamera
    else:
        cls = rt.freeCamera

    if cls is rt.targetCamera:
        target = rt.Point3(0.0, 0.0, 0.0)
        if target_position and len(target_position) == 3:
            target = rt.Point3(
                float(target_position[0]),
                float(target_position[1]),
                float(target_position[2]),
            )
        node = cls(target=rt.Targetobject(target=target))
    else:
        node = cls()

    if name:
        node.name = name
    _apply_transform(node, '', position, None)
    try:
        node.fov = float(fov)
    except Exception:  # pylint: disable=broad-except
        pass
    return {
        'name': str(node.name),
        'type': str(rt.classOf(node)),
    }


@tool(
    description='把活动视口切换为指定相机视角。',
    category='light_camera',
)
def set_viewport_camera(camera_name):
    """设置视口相机。

    :param camera_name: 相机对象名
    :returns: dict {"camera": ..., "ok": True}
    """
    _ensure_in_max()
    node = _get_node(camera_name)
    rt.viewport.setCamera(node)
    return {'camera': str(node.name), 'ok': True}


@tool(
    description='把活动视口切换为标准视图（top / front / left / perspective 等）。',
    category='light_camera',
)
def set_viewport_view(view_type='perspective'):
    """切换标准视图。

    :param view_type: 'top' / 'bottom' / 'front' / 'back' / 'left' /
                      'right' / 'perspective'
    :returns: dict {"view_type": ..., "ok": True}
    """
    _ensure_in_max()
    type_map = {
        'top': rt.Name('view_top'),
        'bottom': rt.Name('view_bottom'),
        'front': rt.Name('view_front'),
        'back': rt.Name('view_back'),
        'left': rt.Name('view_left'),
        'right': rt.Name('view_right'),
        'perspective': rt.Name('view_persp_user'),
    }
    key = view_type.lower()
    if key not in type_map:
        raise ValueError('未知视图: {}'.format(view_type))
    rt.viewport.setType(type_map[key])
    return {'view_type': key, 'ok': True}