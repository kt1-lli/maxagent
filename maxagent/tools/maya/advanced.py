#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 高级场景工具（P2 批次）。

从 Max 端 high_level.py 移植的高层语义工具 + Maya 特有的相机控制工具：
- create_maya_three_point_lighting：主光/补光/背光三点布光
- setup_maya_studio_scene：一键搭建产品渲染 studio 场景
- arrange_maya_in_grid：把对象复制摆成矩形网格
- look_through_maya_camera：激活视口"透过相机看"
- set_maya_camera_dof：相机景深设置

依赖：只依赖 maxagent.tools.registry 与 _common，不主动 import maya.cmds，
保证 CI / 非 Maya 环境也能 import 本模块。
"""

from __future__ import absolute_import
from __future__ import print_function

import math
from typing import Any
from typing import Dict

from ...tools.registry import tool
from ._common import _ensure_in_maya
from ...dcc.runtime import run_on_main


def _cmds():
    """延迟导入 maya.cmds。"""
    import maya.cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
    return maya.cmds


def _camera_shape(transform):
    """把相机 transform 名解析成 shape 名；输入已是 shape 则原样返回。"""
    cmds = _cmds()
    shapes = cmds.listRelatives(
        transform, shapes=True, fullPath=False,
    ) or [transform]
    for shape in shapes:
        if cmds.nodeType(shape) == 'camera':
            return shape
    raise ValueError('不是相机节点: {}'.format(transform))


def _ensure_camera(node_name):
    """校验节点是相机（transform 或 shape 均可），返回 shape 名。"""
    cmds = _cmds()
    if not cmds.objExists(node_name):
        raise ValueError('节点不存在: {}'.format(node_name))
    if cmds.nodeType(node_name) == 'camera':
        return node_name
    return _camera_shape(node_name)


def _aim_xform(transform, target_xyz):
    """用 aimConstraint 把 transform 瞄准目标点后立即删除约束。

    Maya 中让对象"看向"某点最稳的做法：约束在命令流中同步求值，
    随后立刻删除即可保留朝向。
    """
    cmds = _cmds()
    loc = cmds.spaceLocator(name='__maxagent_aim_tmp__')[0]
    try:
        cmds.xform(loc, translation=list(target_xyz), worldSpace=True)
        constraint = cmds.aimConstraint(
            loc, transform,
            aimVector=(0, 0, -1), upVector=(0, 1, 0),
            worldUpType='vector', worldUpVector=(0, 1, 0),
            maintainOffset=False,
        )
        cmds.delete(constraint)
    finally:
        if cmds.objExists(loc):
            cmds.delete(loc)
    return transform


def _place_dir_light(name, pos_xyz, intensity, rgb):
    """创建方向光并放到 pos_xyz，返回 transform 名。"""
    cmds = _cmds()
    shape = cmds.directionalLight(
        name=name, intensity=float(intensity),
        rgb=(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0),
    )
    transform = (
        cmds.listRelatives(shape, parent=True, fullPath=False) or [shape]
    )[0]
    cmds.xform(transform, translation=list(pos_xyz), worldSpace=True)
    return transform


# ---------------------------------------------------------------------- #
# 1. 三点布光
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='一次性给指定对象创建三点布光（主光/补光/背光），'
                '按包围盒自动定位与朝向。',
    category='high_level',
    examples=[
        {
            'summary': '给 pCube1 打标准三点光',
            'args': {
                'target': 'pCube1',
                'key_intensity': 1.2,
                'fill_ratio': 0.4,
                'back_ratio': 0.7,
            },
        },
    ],
    notes=[
        '主光暖色调、补光中性、背光偏冷；强度按 ratio 递减。',
        '灯距 = 包围盒对角线 × 1.6，高度 = 高度 × 0.9，符合摄影经验值。',
        '生成灯光名：{prefix}_Key / _Fill / _Back，默认前缀 3PL。',
    ],
    returns_desc='dict {"ok": True, "lights": [3 个灯光名], "distance": 灯距}',
    prerequisites=['场景中必须存在目标对象'],
)
def create_maya_three_point_lighting(
    target,
    key_intensity=1.2,
    fill_ratio=0.4,
    back_ratio=0.7,
    prefix='3PL',
):
    # type: (...) -> Dict[str, Any]
    """按目标包围盒生成三点布光。

    :param target: 目标对象名（用其包围盒算光位）
    :param key_intensity: 主光强度倍率
    :param fill_ratio: 补光相对主光的强度比
    :param back_ratio: 背光相对主光的强度比
    :param prefix: 生成灯光命名前缀
    :returns: dict {"ok": True, "lights": [...], "distance": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(target):
            raise ValueError('目标对象不存在: {}'.format(target))
        bb = cmds.exactWorldBoundingBox(target)
        bmin = bb[0:3]
        bmax = bb[3:6]
        cx = (bmin[0] + bmax[0]) * 0.5
        cy = (bmin[1] + bmax[1]) * 0.5
        cz = (bmin[2] + bmax[2]) * 0.5
        size = (
            abs(bmax[0] - bmin[0]),
            abs(bmax[1] - bmin[1]),
            abs(bmax[2] - bmin[2]),
        )
        diag = math.sqrt(size[0] ** 2 + size[1] ** 2 + size[2] ** 2)
        dist = max(diag * 1.6, 10.0)
        high = max(size[1] * 0.9, diag * 0.5)

        def _mk(nm, offset, mult, rgb):
            shape = cmds.directionalLight(
                name=nm, intensity=float(mult),
                rgb=(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0),
            )
            transform = (
                cmds.listRelatives(shape, parent=True, fullPath=False)
                or [shape]
            )[0]
            # offset 按 (前向, 高度, 侧向) 语义映射到世界 (x, y, z)
            pos = (cx + offset[0], cy + offset[1], cz + offset[2])
            cmds.xform(transform, translation=list(pos), worldSpace=True)
            _aim_xform(transform, (cx, cy, cz))
            return transform

        key = _mk(
            '{}_Key'.format(prefix), (dist * 0.7, high, -dist * 0.7),
            key_intensity, (255, 214, 170),
        )
        fill = _mk(
            '{}_Fill'.format(prefix),
            (-dist * 0.9, high * 0.5, -dist * 0.4),
            key_intensity * float(fill_ratio), (255, 244, 229),
        )
        back = _mk(
            '{}_Back'.format(prefix), (0, high * 1.3, dist * 1.1),
            key_intensity * float(back_ratio), (206, 225, 255),
        )
        return {
            'ok': True,
            'target': target,
            'lights': [key, fill, back],
            'distance': round(dist, 2),
            'height': round(high, 2),
        }

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 2. 一键 Studio 场景
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='一键搭建产品渲染 studio 场景：地面 + 背板 + 三点布光 + '
                '一台注视目标点的相机。',
    category='high_level',
    examples=[
        {
            'summary': '给 pCube1 搭一个 100 单位的影棚',
            'args': {'size': 100.0, 'target': 'pCube1'},
        },
    ],
    notes=[
        '地面/背板统一 lambert 中性灰 0.5。',
        '相机 focalLength=50 标准镜头；指定 target 时相机与灯光都 aim 到目标中心。',
    ],
    returns_desc='dict {"ok": True, "created": {floor/wall/lights/camera}}',
)
def setup_maya_studio_scene(size=100.0, target=''):
    # type: (float, str) -> Dict[str, Any]
    """搭建 studio 场景。

    :param size: 场景总尺寸
    :param target: 相机与布光注视的目标对象名，为空则看向原点上方
    :returns: dict {"ok": True, "created": {...}}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        size_f = float(size)
        center = (0.0, size_f * 0.15, 0.0)
        if target:
            if not cmds.objExists(target):
                raise ValueError('目标对象不存在: {}'.format(target))
            bb = cmds.exactWorldBoundingBox(target)
            center = (
                (bb[0] + bb[3]) * 0.5,
                (bb[1] + bb[4]) * 0.5,
                (bb[2] + bb[5]) * 0.5,
            )

        # 地面（平铺在 XZ 水平面）
        floor_t, _ = cmds.polyPlane(
            name='Studio_Floor', width=size_f, height=size_f,
            subdivisionsX=1, subdivisionsY=1,
        )
        # 背板（plane 默认躺平，立起来放在 -Z 侧）
        wall_t, _ = cmds.polyPlane(
            name='Studio_BackWall', width=size_f, height=size_f * 0.8,
            subdivisionsX=1, subdivisionsY=1,
        )
        cmds.xform(wall_t, rotation=[90, 0, 0], worldSpace=True)
        cmds.xform(
            wall_t, translation=(0, size_f * 0.4, -size_f * 0.5),
            worldSpace=True,
        )

        # 中性灰 lambert 材质
        def _grey(name):
            shading = cmds.shadingNode('lambert', asShader=True, name=name)
            cmds.setAttr(shading + '.color', 0.5, 0.5, 0.5, type='double3')
            return shading

        grey_floor = _grey('Studio_Grey_Floor')
        cmds.select(floor_t, replace=True)
        cmds.hyperShade(assign=grey_floor)
        grey_wall = _grey('Studio_Grey_Wall')
        cmds.select(wall_t, replace=True)
        cmds.hyperShade(assign=grey_wall)
        cmds.select(clear=True)

        # 三点光（对准中心）
        dist = size_f * 0.9
        high = size_f * 0.4
        key = _aim_xform(_place_dir_light(
            'Studio_Key',
            (center[0] + dist * 0.7, center[1] + high, center[2] - dist * 0.6),
            1.2, (255, 244, 229),
        ), center)
        fill = _aim_xform(_place_dir_light(
            'Studio_Fill',
            (center[0] - dist * 0.8, center[1] + high * 0.5, center[2] - dist * 0.3),
            0.5, (210, 225, 245),
        ), center)
        back = _aim_xform(_place_dir_light(
            'Studio_Back',
            (center[0], center[1] + high * 1.3, center[2] + dist * 1.0),
            0.9, (255, 240, 220),
        ), center)

        # 相机
        cam_t, _ = cmds.camera(name='Studio_Cam', focalLength=50.0)
        cmds.xform(
            cam_t,
            translation=(center[0], center[1] + size_f * 0.25,
                         center[2] - dist * 1.4),
            worldSpace=True,
        )
        _aim_xform(cam_t, center)

        return {
            'ok': True,
            'created': {
                'floor': floor_t,
                'wall': wall_t,
                'lights': [key, fill, back],
                'camera': cam_t,
                'shaders': [grey_floor, grey_wall],
            },
            'size': size_f,
        }

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 3. 网格排布
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='把指定对象复制若干份摆成矩形网格（rows × cols），'
                '按 spacing 均匀间距。原对象保留在 (0,0) 格。',
    category='high_level',
    examples=[
        {
            'summary': '把 pCube1 复制成 3 行 4 列网格，间距 5',
            'args': {'source': 'pCube1', 'rows': 3, 'cols': 4, 'spacing': 5.0},
        },
    ],
    notes=[
        '网格平铺在水平面：cols 沿世界 X 展开，rows 沿世界 Z 展开。',
        'y_step 非 0 时每行抬升，可做阶梯效果。',
    ],
    returns_desc='dict {"ok": True, "created": [新对象名列表], "total_count": n}',
    prerequisites=['场景中必须存在源对象'],
)
def arrange_maya_in_grid(source, rows, cols, spacing=5.0, y_step=0.0):
    # type: (str, int, int, float, float) -> Dict[str, Any]
    """把源对象复制 rows×cols-1 份摆成矩阵。

    :param source: 源对象名
    :param rows: 行数（沿 Z 轴），>=1
    :param cols: 列数（沿 X 轴），>=1
    :param spacing: 相邻对象间距
    :param y_step: 每行 Y 高度递增，默认 0
    :returns: dict {"ok": True, "created": [...], "total_count": rows*cols}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not cmds.objExists(source):
            raise ValueError('源对象不存在: {}'.format(source))
        rows_i = max(1, int(rows))
        cols_i = max(1, int(cols))
        spacing_f = float(spacing or 0)
        y_step_f = float(y_step or 0)
        origin = cmds.xform(
            source, translation=True, query=True, worldSpace=True,
        )
        created = []
        for r in range(rows_i):
            for c in range(cols_i):
                if r == 0 and c == 0:
                    # 原对象保留在 (0,0) 格
                    continue
                dup_name = cmds.duplicate(source)[0]
                cmds.xform(
                    dup_name,
                    translation=(
                        origin[0] + c * spacing_f,
                        origin[1] + r * y_step_f,
                        origin[2] + r * spacing_f,
                    ),
                    worldSpace=True,
                )
                created.append(dup_name)
        return {
            'ok': True,
            'source': source,
            'rows': rows_i,
            'cols': cols_i,
            'created': created,
            'total_count': rows_i * cols_i,
        }

    return run_on_main(_do)


# ---------------------------------------------------------------------- #
# 4. 相机工具
# ---------------------------------------------------------------------- #
@tool(
    dcc=['maya'],
    description='让指定模型面板"透过相机看"（look through camera）；'
                '不传 camera 时返回当前激活相机。',
    category='camera',
    examples=[
        {
            'summary': '把当前激活面板切到 cam1 视角',
            'args': {'camera': 'cam1'},
        },
        {
            'summary': '查询当前激活相机',
            'args': {},
        },
    ],
    notes=[
        'camera 可传 transform 或 shape 名；不传 camera 时为查询模式。',
        'panel 为空时取当前焦点面板，非 modelPanel 时兜底取第一个可见面板。',
    ],
    returns_desc='dict {"active_camera": 名字} 或 {"ok": True, "camera": ...}',
)
def look_through_maya_camera(camera='', panel=''):
    # type: (str, str) -> Dict[str, Any]
    """透过相机看 / 查询当前激活相机。

    :param camera: 相机名（transform 或 shape），为空表示查询模式
    :param panel: 目标模型面板名，为空取当前焦点面板
    :returns: dict {"active_camera": ...} 或 {"ok": True, "camera": ..., "panel": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        if not camera:
            # 查询当前激活相机
            active = cmds.lookThru(query=True)
            return {'active_camera': active}
        shape = _ensure_camera(camera)
        panel_name = panel
        if not panel_name:
            panel_name = cmds.getPanel(withFocus=True) or ''
        if not panel_name or not panel_name.startswith('modelPanel'):
            # 兜底：取第一个 modelPanel
            all_panels = cmds.getPanel(type='modelPanel') or []
            panel_name = all_panels[0] if all_panels else 'modelPanel4'
        cmds.lookThru(panel_name, shape)
        return {'ok': True, 'camera': shape, 'panel': panel_name}

    return run_on_main(_do)


@tool(
    dcc=['maya'],
    description='设置相机景深（DOF）：开关、对焦距离、F 光圈值。',
    category='camera',
    examples=[
        {
            'summary': '开启 cam1 景深，对焦 10 单位，F2.8 光圈',
            'args': {
                'camera': 'cam1',
                'enabled': True,
                'focus_distance': 10.0,
                'f_stop': 2.8,
            },
        },
    ],
    notes=[
        'enabled=False 时关闭景深并跳过对焦/光圈写入。',
        'FStop 越小景深越浅；DOF 在渲染时生效，视口需开启 DOF 预览才可见。',
    ],
    returns_desc='dict {"ok": True, "camera": shape名, "enabled": bool, ...}',
    prerequisites=['场景中必须存在相机'],
)
def set_maya_camera_dof(
    camera,
    enabled=True,
    focus_distance=10.0,
    f_stop=5.6,
):
    # type: (str, bool, float, float) -> Dict[str, Any]
    """设置相机景深。

    :param camera: 相机名（transform 或 shape）
    :param enabled: 是否开启景深
    :param focus_distance: 对焦距离
    :param f_stop: F 光圈值
    :returns: dict {"ok": True, "camera": ..., "enabled": ...}
    """
    _ensure_in_maya()

    def _do():
        cmds = _cmds()
        shape = _ensure_camera(camera)
        cmds.setAttr(shape + '.depthOfField', bool(enabled))
        result = {
            'ok': True,
            'camera': shape,
            'enabled': bool(enabled),
        }
        if enabled:
            cmds.setAttr(shape + '.focusDistance', float(focus_distance))
            cmds.setAttr(shape + '.fStop', float(f_stop))
            result['focus_distance'] = float(focus_distance)
            result['f_stop'] = float(f_stop)
        return result

    return run_on_main(_do)


__all__ = [
    'create_maya_three_point_lighting',
    'setup_maya_studio_scene',
    'arrange_maya_in_grid',
    'look_through_maya_camera',
    'set_maya_camera_dof',
]
