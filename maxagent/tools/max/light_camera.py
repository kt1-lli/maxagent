#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""灯光与相机类工具。

灯光类型：omni / spot / direct / skylight / target_spot / target_direct
相机类型：free / target / physical

**关于 position 的处理**：与 geometry.py 一致，优先在构造器里传 ``pos=Point3(...)``，
_apply_transform（复用 geometry._apply_common）只做后置兜底 + 硬校验。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import List
from typing import Optional

from ...runtime_helpers import has_runtime_attr
from ...runtime_helpers import IN_MAX
from ...runtime_helpers import rt
from .geometry import _apply_common as _apply_transform
from ...tools.registry import tool


def _ensure_in_max():
    if not IN_MAX:
        raise RuntimeError('非 3ds Max 环境')


def _get_node(name):
    node = rt.getNodeByName(name, exact=True, all=False)
    if node is None:
        raise ValueError('对象不存在: {}'.format(name))
    return node


@tool(
    dcc=['3dsmax'],
    description=(
        '创建一个灯光。type 可选: omni（点光） / spot（聚光） / '
        'direct（平行光） / skylight（天光） / target_spot / target_direct。'
    ),
    category='light_camera',
    examples=[
        {
            'summary': '在原点创建一盏默认点光源',
            'args': {'type': 'omni'},
        },
        {
            'summary': '在指定位置创建红色聚光灯',
            'args': {
                'type': 'spot',
                'name': 'KeyLight',
                'position': '[100, 200, 100]',
                'multiplier': 1.5,
                'color': '[255, 200, 150]',
            },
        },
    ],
    notes=[
        'position 和 target_position 支持 JSON 字符串 "[x,y,z]" 或 Python list/tuple。',
        'target_spot / target_direct 必须提供 target_position，否则目标点默认在世界原点。',
        'color 接受 0-255 或 0-1 的 RGB 值，内部会统一归一化处理。',
    ],
    returns_desc='dict {"name": 灯光实际对象名, "type": Max 类名}',
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

    # 构造器不再传 pos，统一走后置 setter（_apply_transform 三级兜底）
    kwargs = {}

    if cls_name in ('targetSpot', 'targetDirect'):
        # target 灯光需要一个 target helper；target 位置在世界坐标
        target = rt.Point3(0.0, 0.0, 0.0)
        if target_position and len(target_position) == 3:
            target = rt.Point3(
                float(target_position[0]),
                float(target_position[1]),
                float(target_position[2]),
            )
        kwargs['target'] = rt.Targetobject(target=target)

    node = cls(**kwargs)

    if name:
        node.name = name
    # 后置校验 + 兜底（构造器已传 pos 时通常直接通过校验）
    _apply_transform(node, '', position, None)
    try:
        rt.setProperty(node, 'multiplier', float(multiplier))
    except Exception:  # pylint: disable=broad-except
        try:
            node.multiplier = float(multiplier)
        except Exception:  # pylint: disable=broad-except
            pass
    if color:
        from .material import _to_color  # pylint: disable=import-outside-toplevel
        try:
            rt.setProperty(node, 'rgb', _to_color(color))
        except Exception:  # pylint: disable=broad-except
            try:
                node.rgb = _to_color(color)
            except Exception:  # pylint: disable=broad-except
                pass
    return {
        'name': str(node.name),
        'type': str(rt.classOf(node)),
    }


@tool(
    dcc=['3dsmax'],
    description='创建一个相机。type: free / target / physical（Max 2016+）。',
    category='light_camera',
    examples=[
        {
            'summary': '在原点创建自由相机',
            'args': {'type': 'free'},
        },
        {
            'summary': '创建一架对准目标点的目标相机',
            'args': {
                'type': 'target',
                'name': 'RenderCam',
                'position': '[0, -300, 150]',
                'target_position': '[0, 0, 80]',
                'fov': 60.0,
            },
        },
    ],
    notes=[
        'position 和 target_position 支持 JSON 字符串 "[x,y,z]" 或 Python list/tuple。',
        'physical 相机需要 Max 2016 及以上版本并加载对应插件，否则可能回退为 free 相机。',
        'target 相机必须设置 target_position 以确定目标点，否则目标默认在世界原点。',
    ],
    returns_desc='dict {"name": 相机实际对象名, "type": Max 类名}',
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

    kwargs = {}

    if cls is rt.targetCamera:
        target = rt.Point3(0.0, 0.0, 0.0)
        if target_position and len(target_position) == 3:
            target = rt.Point3(
                float(target_position[0]),
                float(target_position[1]),
                float(target_position[2]),
            )
        kwargs['target'] = rt.Targetobject(target=target)

    node = cls(**kwargs)

    if name:
        node.name = name
    _apply_transform(node, '', position, None)
    try:
        rt.setProperty(node, 'fov', float(fov))
    except Exception:  # pylint: disable=broad-except
        try:
            node.fov = float(fov)
        except Exception:  # pylint: disable=broad-except
            pass
    return {
        'name': str(node.name),
        'type': str(rt.classOf(node)),
    }


@tool(
    dcc=['3dsmax'],
    description='把活动视口切换为指定相机视角。',
    category='light_camera',
    examples=[
        {
            'summary': '将活动视口切换到名为 RenderCam 的相机',
            'args': {'camera_name': 'RenderCam'},
        },
    ],
    notes=[
        'camera_name 必须精确匹配场景中已存在的相机对象名。',
        '切换后活动视口会立即变为该相机的视角，可用于渲染预览。',
    ],
    returns_desc='dict {"camera": 相机名, "ok": True}',
    prerequisites=['场景中必须存在名为 camera_name 的相机对象'],
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
    dcc=['3dsmax'],
    description='把活动视口切换为标准视图（top / front / left / perspective 等）。',
    category='light_camera',
    examples=[
        {
            'summary': '切换到透视图',
            'args': {'view_type': 'perspective'},
        },
        {
            'summary': '切换到顶视图',
            'args': {'view_type': 'top'},
        },
    ],
    notes=[
        'view_type 仅支持 top / bottom / front / back / left / right / perspective。',
        '传入值不区分大小写，但建议使用小写。',
    ],
    returns_desc='dict {"view_type": 标准化后的视图名, "ok": True}',
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

