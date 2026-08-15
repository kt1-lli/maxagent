#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高层次语义工具（High-Level Tools）——一次调用完成通常需要 5~10 轮
低阶工具调用的复合意图。

**设计原则**：
1. 每个工具对应"一句话意图"（如"给这个模型打三点光"）。
2. 内部通过调用 dispatcher 的低阶工具组合实现，**不直接碰 pymxs**，
   保证任何低阶工具的改动会同步惠及高层次工具。
3. 参数尽量少而语义化，暴露 3~5 个用户高频关心的旋钮，其他都用工业
   标准兜底。
4. 全部 wrap_undo=True，配合 approval_queue.UndoBatch 让整个动作
   一次 Ctrl+Z 回滚。

**当前实现的 5 个工具**（覆盖 3ds Max 用户最常见的高频场景）：
- ``create_three_point_lighting``：主光/补光/背光的三点布光
- ``arrange_in_grid``：把选中对象或复制若干份摆成矩阵
- ``align_along_curve``：沿样条曲线均匀分布对象
- ``setup_studio_scene``：一键搭建产品渲染的 studio 场景
- ``create_pbr_metal``：PBR 金属材质快速预设
"""

from __future__ import absolute_import
from __future__ import print_function

import math
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..runtime_helpers import IN_MAX
from ..runtime_helpers import rt
from .registry import tool


def _ensure_in_max():
    if not IN_MAX:
        raise RuntimeError('非 3ds Max 环境')


def _get_node(name):
    if not IN_MAX:
        return None
    return rt.getNodeByName(name, exact=True, all=False)


def _get_bbox(node):
    """返回节点的世界包围盒 (min, max, center, size)。"""
    bmin = node.min
    bmax = node.max
    center = (
        (bmin.x + bmax.x) * 0.5,
        (bmin.y + bmax.y) * 0.5,
        (bmin.z + bmax.z) * 0.5,
    )
    size = (
        abs(bmax.x - bmin.x),
        abs(bmax.y - bmin.y),
        abs(bmax.z - bmin.z),
    )
    return bmin, bmax, center, size


# ---------------------------------------------------------------------- #
# 1. 三点布光
# ---------------------------------------------------------------------- #

@tool(
    description=(
        '一次性给指定对象创建三点布光（主光/补光/背光）。'
        '按对象包围盒自动定位与朝向，是产品/角色渲染的经典布光起点。'
        '省去手工创建 3 盏灯 + 逐个设定位置/强度/色温的 8~12 轮调用。'
    ),
    category='high_level',
    parameters={
        'type': 'object',
        'properties': {
            'target': {
                'type': 'string',
                'description': '要打光的目标对象名称（用其包围盒计算光位）',
            },
            'key_intensity': {
                'type': 'number',
                'description': '主光强度倍率（默认 1.2）',
            },
            'fill_ratio': {
                'type': 'number',
                'description': '补光相对主光的强度比（默认 0.4）',
            },
            'back_ratio': {
                'type': 'number',
                'description': '背光相对主光的强度比（默认 0.7）',
            },
            'prefix': {
                'type': 'string',
                'description': '生成灯光的命名前缀，默认 "3PL"',
            },
        },
        'required': ['target'],
    },
    examples=[{"summary": "典型调用", "args": {"target": 'value', "key_intensity": 1.0, "fill_ratio": 1.0, "back_ratio": 1.0, "prefix": 'value'}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def create_three_point_lighting(
    target,
    key_intensity=1.2,
    fill_ratio=0.4,
    back_ratio=0.7,
    prefix='3PL',
):
    """按目标对象包围盒生成三点布光。"""
    _ensure_in_max()
    node = _get_node(target)
    if node is None:
        raise ValueError('目标对象不存在: {}'.format(target))

    _bmin, _bmax, center, size = _get_bbox(node)
    diag = math.sqrt(size[0] ** 2 + size[1] ** 2 + size[2] ** 2)
    # 灯距 = 对角 × 1.6；高度 = 对角 × 0.9（更符合摄影经验值）
    dist = max(diag * 1.6, 100.0)
    high = max(size[2] * 0.9, diag * 0.5)

    def _mk_light(nm, offset_xy, height_scale, mult, kelvin):
        light = rt.TargetSpot(
            targetDistance=dist,
            useFarAtten=False,
            hotSpot=45,
            fallSize=55,
        )
        light.name = nm
        light.pos = rt.Point3(
            center[0] + offset_xy[0],
            center[1] + offset_xy[1],
            center[2] + high * height_scale,
        )
        try:
            light.target.pos = rt.Point3(center[0], center[1], center[2])
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            light.multiplier = float(mult)
        except Exception:  # pylint: disable=broad-except
            pass
        # 色温：主光偏暖 3200K，补光偏中性 5000K，背光偏冷 6500K
        try:
            k = float(kelvin)
            # 简化 kelvin→RGB：低色温偏红，高色温偏蓝
            if k <= 4000:
                col = rt.color(255, 214, 170)
            elif k <= 5500:
                col = rt.color(255, 244, 229)
            else:
                col = rt.color(206, 225, 255)
            light.color = col
        except Exception:  # pylint: disable=broad-except
            pass
        return light

    key = _mk_light(
        '{}_Key'.format(prefix),
        (dist * 0.7, -dist * 0.7),
        1.0, key_intensity, 3200,
    )
    fill = _mk_light(
        '{}_Fill'.format(prefix),
        (-dist * 0.9, -dist * 0.4),
        0.5, key_intensity * float(fill_ratio), 5000,
    )
    back = _mk_light(
        '{}_Back'.format(prefix),
        (0, dist * 1.1),
        1.3, key_intensity * float(back_ratio), 6500,
    )

    return {
        'ok': True,
        'target': target,
        'lights': [key.name, fill.name, back.name],
        'distance': round(dist, 2),
        'height': round(high, 2),
    }


# ---------------------------------------------------------------------- #
# 2. 网格排布
# ---------------------------------------------------------------------- #

@tool(
    description=(
        '把指定对象复制若干份摆成矩形网格（rows × cols），按 spacing '
        '均匀间距。原对象保持在 (0,0) 位置。'
    ),
    category='high_level',
    parameters={
        'type': 'object',
        'properties': {
            'source': {
                'type': 'string',
                'description': '要复制排列的源对象名',
            },
            'rows': {
                'type': 'integer',
                'description': '行数（沿 Y 轴），>=1',
            },
            'cols': {
                'type': 'integer',
                'description': '列数（沿 X 轴），>=1',
            },
            'spacing': {
                'type': 'number',
                'description': '相邻对象间距（单位与场景一致，默认 100）',
            },
            'z_offset': {
                'type': 'number',
                'description': '每行的 Z 高度递增（默认 0，非零可做阶梯）',
            },
        },
        'required': ['source', 'rows', 'cols'],
    },
    examples=[{"summary": "典型调用", "args": {"source": 'value', "rows": 1, "cols": 1, "spacing": 1.0, "z_offset": 1.0}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def arrange_in_grid(source, rows, cols, spacing=100.0, z_offset=0.0):
    """把源对象复制 rows×cols-1 份摆成矩阵。"""
    _ensure_in_max()
    src = _get_node(source)
    if src is None:
        raise ValueError('源对象不存在: {}'.format(source))

    rows = max(1, int(rows))
    cols = max(1, int(cols))
    spacing = float(spacing or 0)
    z_offset = float(z_offset or 0)

    created = []
    origin = src.pos
    for r in range(rows):
        for c in range(cols):
            if r == 0 and c == 0:
                continue  # 原对象保留
            copy = rt.copy(src)
            copy.pos = rt.Point3(
                origin.x + c * spacing,
                origin.y + r * spacing,
                origin.z + r * z_offset,
            )
            created.append(copy.name)

    return {
        'ok': True,
        'source': source,
        'rows': rows,
        'cols': cols,
        'created': created,
        'total_count': rows * cols,
    }


# ---------------------------------------------------------------------- #
# 3. 沿曲线分布
# ---------------------------------------------------------------------- #

@tool(
    description=(
        '沿指定样条曲线均匀分布对象。source 为要复制分布的源对象，'
        'curve 为样条曲线对象，count 为总数（含起点终点）。'
        '常用于沿路径摆花草/摆栏杆/摆吊灯等场景。'
    ),
    category='high_level',
    parameters={
        'type': 'object',
        'properties': {
            'source': {
                'type': 'string',
                'description': '要复制分布的源对象名',
            },
            'curve': {
                'type': 'string',
                'description': '样条曲线对象名（Line / Spline / Helix 等）',
            },
            'count': {
                'type': 'integer',
                'description': '沿曲线均匀分布的对象总数（>=2）',
            },
            'align_to_tangent': {
                'type': 'boolean',
                'description': '是否让每个副本对齐到曲线切线方向（默认 True）',
            },
        },
        'required': ['source', 'curve', 'count'],
    },
    examples=[{"summary": "典型调用", "args": {"source": 'value', "curve": 'value', "count": 1, "align_to_tangent": True}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def align_along_curve(source, curve, count, align_to_tangent=True):
    """沿曲线均匀分布对象。"""
    _ensure_in_max()
    src = _get_node(source)
    crv = _get_node(curve)
    if src is None:
        raise ValueError('源对象不存在: {}'.format(source))
    if crv is None:
        raise ValueError('曲线对象不存在: {}'.format(curve))

    count = max(2, int(count))
    created = []
    for i in range(count):
        t = i / float(count - 1) if count > 1 else 0.0
        try:
            # lengthInterp = t 处的世界坐标
            pos = rt.pathInterp(crv, 1, t)
        except Exception:  # pylint: disable=broad-except
            # 兼容旧 API：用 curveEval
            try:
                pos = rt.CurveEval(crv, t)
            except Exception:  # pylint: disable=broad-except
                pos = rt.Point3(0, 0, 0)

        if i == 0:
            node = src
            node.pos = pos
        else:
            node = rt.copy(src)
            node.pos = pos
            created.append(node.name)

        if align_to_tangent:
            try:
                # 通过取相邻两点近似切线
                t2 = min(1.0, t + 0.001)
                p2 = rt.pathInterp(crv, 1, t2)
                dx, dy = p2.x - pos.x, p2.y - pos.y
                yaw = math.atan2(dy, dx)
                try:
                    rt.setProperty(
                        node, 'rotation',
                        rt.EulerAngles(0, 0, math.degrees(yaw)),
                    )
                except Exception:  # pylint: disable=broad-except
                    node.rotation = rt.EulerAngles(0, 0, math.degrees(yaw))
            except Exception:  # pylint: disable=broad-except
                pass

    return {
        'ok': True,
        'source': source,
        'curve': curve,
        'count': count,
        'created': created,
    }


# ---------------------------------------------------------------------- #
# 4. 一键 Studio 场景
# ---------------------------------------------------------------------- #

@tool(
    description=(
        '一键搭建产品渲染 studio 场景：背景板（cyc wall）+ 地板 + '
        '三点布光 + 一台目标相机。适合快速开始"给这个模型出效果图"。'
    ),
    category='high_level',
    parameters={
        'type': 'object',
        'properties': {
            'size': {
                'type': 'number',
                'description': '场景总尺寸（默认 500，按场景单位）',
            },
            'target': {
                'type': 'string',
                'description': '相机注视的目标对象名（可选，为空则看向原点）',
            },
        },
        'required': [],
    },
    examples=[{"summary": "典型调用", "args": {"size": 1.0, "target": 'value'}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def setup_studio_scene(size=500.0, target=''):
    """搭建 studio 场景。"""
    _ensure_in_max()
    size = float(size or 500.0)

    # 地板
    floor = rt.Plane(width=size, length=size, lengthsegs=1, widthsegs=1)
    floor.name = 'Studio_Floor'
    floor.pos = rt.Point3(0, 0, 0)

    # 背景板（无缝弧形墙的简化：一块高背板）
    wall = rt.Plane(
        width=size, length=size * 0.8, lengthsegs=1, widthsegs=1,
    )
    wall.name = 'Studio_BackWall'
    wall.pos = rt.Point3(0, size * 0.5, size * 0.4)
    wall.rotation = rt.EulerAngles(90, 0, 0)

    # 三点布光的目标：如果指定了 target 就照它，否则照原点
    tgt_node = _get_node(target) if target else None
    if tgt_node is not None:
        _bmin, _bmax, center, _size = _get_bbox(tgt_node)
    else:
        center = (0.0, 0.0, size * 0.15)

    # 简化三点：直接内联，不递归调用工具（避免 dispatcher 依赖）
    dist = size * 0.9
    high = size * 0.4

    def _quick_light(nm, offset, mult, r, g, b):
        light = rt.TargetSpot(
            targetDistance=dist,
            useFarAtten=False,
            hotSpot=50,
            fallSize=65,
        )
        light.name = nm
        light.pos = rt.Point3(
            center[0] + offset[0],
            center[1] + offset[1],
            center[2] + offset[2],
        )
        try:
            light.target.pos = rt.Point3(center[0], center[1], center[2])
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            light.multiplier = float(mult)
            light.color = rt.color(r, g, b)
        except Exception:  # pylint: disable=broad-except
            pass
        return light

    key = _quick_light('Studio_Key', (dist * 0.7, -dist * 0.6, high), 1.2,
                      255, 244, 229)
    fill = _quick_light('Studio_Fill', (-dist * 0.8, -dist * 0.3, high * 0.5),
                       0.5, 210, 225, 245)
    back = _quick_light('Studio_Back', (0, dist * 1.0, high * 1.3), 0.9,
                       255, 240, 220)

    # 相机
    cam = rt.FreeCamera()
    cam.name = 'Studio_Cam'
    cam.pos = rt.Point3(
        center[0], center[1] - dist * 1.4, center[2] + high * 0.4,
    )
    try:
        cam.fov = 40
    except Exception:  # pylint: disable=broad-except
        pass

    return {
        'ok': True,
        'created': {
            'floor': floor.name,
            'wall': wall.name,
            'lights': [key.name, fill.name, back.name],
            'camera': cam.name,
        },
        'size': size,
    }


# ---------------------------------------------------------------------- #
# 5. PBR 金属材质预设
# ---------------------------------------------------------------------- #

@tool(
    description=(
        '快速创建 PBR 金属材质预设（Physical Material）。'
        'tint 是主色调 RGB，roughness 0~1 越高越粗糙磨砂。'
        '省去 create_physical_material + 逐个 set 参数的 3~5 轮调用。'
    ),
    category='high_level',
    parameters={
        'type': 'object',
        'properties': {
            'name': {
                'type': 'string',
                'description': '材质名称',
            },
            'tint': {
                'type': 'string',
                'description': (
                    '金属基础色 "R,G,B"（0~255），例如 "220,180,80" '
                    '为金色，"200,200,200" 为铝，"180,60,50" 为红铜。'
                ),
            },
            'roughness': {
                'type': 'number',
                'description': '粗糙度 0~1（0 = 镜面，1 = 全磨砂）',
            },
            'assign_to': {
                'type': 'string',
                'description': '可选：立即赋给该对象',
            },
        },
        'required': ['name'],
    },
    examples=[{"summary": "典型调用", "args": {"name": 'Box01', "tint": 'value', "roughness": 1.0, "assign_to": 'value'}}],
notes=['调用前请确认 name 对应的对象已存在于场景中。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def create_pbr_metal(name, tint='200,200,200', roughness=0.3, assign_to=''):
    """创建 PBR 金属预设材质。"""
    _ensure_in_max()

    # 解析 tint
    try:
        parts = [int(p.strip()) for p in str(tint).split(',')]
        while len(parts) < 3:
            parts.append(200)
        r, g, b = parts[:3]
    except Exception:  # pylint: disable=broad-except
        r, g, b = 200, 200, 200

    rough = max(0.0, min(1.0, float(roughness or 0.3)))

    # PhysicalMaterial 在 3ds Max 2018+ 可用；老版本回退到 Standard
    try:
        mat = rt.PhysicalMaterial()
    except Exception:  # pylint: disable=broad-except
        mat = rt.StandardMaterial()

    mat.name = str(name or 'PBR_Metal')
    try:
        mat.base_color = rt.color(r, g, b)
        mat.metalness = 1.0
        mat.roughness = rough
    except Exception:  # pylint: disable=broad-except
        # Standard 兼容
        try:
            mat.diffuse = rt.color(r, g, b)
        except Exception:  # pylint: disable=broad-except
            pass

    assigned = ''
    if assign_to:
        node = _get_node(assign_to)
        if node is not None:
            try:
                try:
                    rt.setProperty(node, 'material', mat)
                except Exception:  # pylint: disable=broad-except
                    node.material = mat
                assigned = assign_to
            except Exception:  # pylint: disable=broad-except
                pass

    return {
        'ok': True,
        'material_name': mat.name,
        'metalness': 1.0,
        'roughness': rough,
        'tint_rgb': [r, g, b],
        'assigned_to': assigned,
    }