#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""材质类工具。

支持：创建标准/物理材质、赋予材质给对象、加贴图、查询材质库。
注：PhysicalMaterial 在 Max 2017+ 才有；旧版本会自动降级到 StandardMaterial。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
from typing import List
from typing import Optional

from ..runtime_helpers import has_runtime_attr
from ..runtime_helpers import IN_MAX
from ..runtime_helpers import rt
from .registry import tool


def _ensure_in_max():
    if not IN_MAX:
        raise RuntimeError('非 3ds Max 环境')


def _get_node(name):
    node = rt.getNodeByName(name, exact=True, all=False)
    if node is None:
        raise ValueError('对象不存在: {}'.format(name))
    return node


def _to_color(rgb):
    """RGB 输入 -> rt.Color。支持多种输入格式：

    - [r, g, b] / (r, g, b) list/tuple
    - "255,0,0" / "255 0 0" 字符串
    - "[255,0,0]" / "(255,0,0)" 带括号字符串（LLM 常传这种）
    - 0-1 浮点 或 0-255 整数（自动判断）

    None 或非法输入返回白色。
    """
    if rgb is None:
        return rt.Color(255, 255, 255)
    # 字符串：兼容 "[255,0,0]" / "255,0,0" / "255 0 0"
    if isinstance(rgb, str):
        s = rgb.strip()
        # 剥掉常见包裹符
        for lb, rb in (('[', ']'), ('(', ')'), ('{', '}')):
            if s.startswith(lb) and s.endswith(rb):
                s = s[1:-1].strip()
                break
        # 支持逗号或空白分隔
        parts = [p for p in s.replace(',', ' ').split() if p]
        try:
            rgb = [float(p) for p in parts]
        except (ValueError, TypeError):
            return rt.Color(255, 255, 255)
    try:
        if len(rgb) < 3:
            return rt.Color(255, 255, 255)
        r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
    except (TypeError, ValueError):
        return rt.Color(255, 255, 255)
    # 自动判断是 0-1 还是 0-255
    if max(r, g, b) <= 1.0:
        r, g, b = r * 255.0, g * 255.0, b * 255.0
    return rt.Color(r, g, b)


# 模块级缓存：保存 agent 创建过的材质，确保未被对象引用时也能再次通过名字找到
# 注意：不会跨 Max 重启保留；仅是当前会话内的弱引用簿
_MATERIAL_REGISTRY = {}


def _register_material_to_medit(mat):
    """把刚创建的材质放进材质编辑器空槽 + 内存簿。

    Max 设计上：未被任何对象引用的材质既不在 ``sceneMaterials`` 也不在
    ``getMeditMaterial`` 列表里。这会导致 ``create_*`` 之后立刻
    ``assign_material`` 找不到。这里做两件事：
    1. 找到第一个空 medit 槽放进去（会显示在材质编辑器面板）
    2. 在模块内存里登记一份（最直接的找回路径）
    """
    try:
        _MATERIAL_REGISTRY[str(mat.name)] = mat
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        # 查找一个空槽（默认 24 个槽位，从 1 开始）
        for i in range(1, 25):
            slot = rt.getMeditMaterial(i)
            cls = str(rt.classOf(slot))
            # 默认空槽是 Standardmaterial 且名字以 #map 开头
            if cls in ('Standardmaterial', 'PhysicalMaterial'):
                slot_name = str(getattr(slot, 'name', '') or '')
                if (not slot_name) or slot_name.startswith('Map ') \
                        or slot_name.startswith('#') \
                        or slot_name == cls:
                    rt.setMeditMaterial(i, mat)
                    break
    except Exception:  # pylint: disable=broad-except
        pass


def _find_material_by_name(material_name):
    """按名字在所有可能位置查找材质。"""
    # 1. agent 内存簿（最快、最可靠）
    if material_name in _MATERIAL_REGISTRY:
        try:
            mat = _MATERIAL_REGISTRY[material_name]
            # 验证仍有效
            _ = str(rt.classOf(mat))
            return mat
        except Exception:  # pylint: disable=broad-except
            _MATERIAL_REGISTRY.pop(material_name, None)
    # 2. sceneMaterials（已被对象引用过的）
    try:
        for i in range(int(rt.sceneMaterials.count)):
            m = rt.sceneMaterials[i]
            if str(m.name) == material_name:
                return m
    except Exception:  # pylint: disable=broad-except
        pass
    # 3. medit 槽
    for i in range(1, 25):
        try:
            m = rt.getMeditMaterial(i)
            if str(m.name) == material_name:
                return m
        except Exception:  # pylint: disable=broad-except
            continue
    return None


@tool(
    description=(
        '创建一个标准材质（Standard Material）。返回材质名供后续 assign_material 使用。'
    ),
    category='material',
)
def create_standard_material(
    name='AgentStandard',
    diffuse=None,
    specular=None,
    glossiness=40.0,
    opacity=100.0,
    self_illumination=0.0,
):
    """创建标准材质。

    :param name: 材质名
    :param diffuse: 漫反射颜色 [r, g, b]，分量可以是 0-255 或 0-1
    :param specular: 高光颜色 [r, g, b]
    :param glossiness: 光泽度（0-100）
    :param opacity: 不透明度（0-100，100 完全不透明）
    :param self_illumination: 自发光（0-100）
    :returns: dict {"name": 材质名, "type": "Standardmaterial"}
    """
    _ensure_in_max()
    mat = rt.Standardmaterial()
    mat.name = name
    mat.diffuse = _to_color(diffuse) if diffuse else _to_color([200, 200, 200])
    if specular is not None:
        mat.specular = _to_color(specular)
    try:
        mat.glossiness = float(glossiness)
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        mat.opacity = float(opacity)
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        mat.selfIllumAmount = float(self_illumination)
    except Exception:  # pylint: disable=broad-except
        pass
    # 把材质注册到材质编辑器槽，确保后续 assign_material 能通过 name 找到
    # 不放槽的话，未被对象引用前材质既不在 sceneMaterials 也不在 medit
    _register_material_to_medit(mat)
    return {'name': str(mat.name), 'type': str(rt.classOf(mat))}


@tool(
    description=(
        '创建一个物理材质（PhysicalMaterial）。Max 2017+ 推荐使用。'
        '若当前版本不支持，会自动降级为 Standardmaterial。'
    ),
    category='material',
)
def create_physical_material(
    name='AgentPhysical',
    base_color=None,
    roughness=0.4,
    metalness=0.0,
    transparency=0.0,
    ior=1.5,
):
    """创建物理材质。

    :param name: 材质名
    :param base_color: 基础色 [r, g, b]
    :param roughness: 粗糙度（0-1）
    :param metalness: 金属度（0-1）
    :param transparency: 透明度（0-1）
    :param ior: 折射率（典型 1.0~2.5，玻璃 1.5）
    :returns: dict {"name": ..., "type": ..., "downgraded": bool}
    """
    _ensure_in_max()
    if has_runtime_attr('PhysicalMaterial'):
        mat = rt.PhysicalMaterial()
        mat.name = name
        try:
            mat.base_color = _to_color(base_color or [200, 200, 200])
            mat.roughness = float(roughness)
            mat.metalness = float(metalness)
            mat.transparency = float(transparency)
            mat.trans_ior = float(ior)
        except Exception:  # pylint: disable=broad-except
            pass
        _register_material_to_medit(mat)
        return {
            'name': str(mat.name),
            'type': 'PhysicalMaterial',
            'downgraded': False,
        }
    # 降级
    mat = rt.Standardmaterial()
    mat.name = name
    mat.diffuse = _to_color(base_color or [200, 200, 200])
    _register_material_to_medit(mat)
    return {
        'name': str(mat.name),
        'type': 'Standardmaterial',
        'downgraded': True,
    }


@tool(
    description=(
        '把已存在的材质赋给对象。材质需先通过 create_standard_material / '
        'create_physical_material 创建，或者从场景中已有材质里查找。'
    ),
    category='material',
)
def assign_material(object_name, material_name):
    """把材质赋给对象。

    :param object_name: 对象名
    :param material_name: 材质名
    :returns: dict {"object": ..., "material": ..., "ok": True}
    """
    _ensure_in_max()
    node = _get_node(object_name)
    mat = _find_material_by_name(material_name)
    if mat is None:
        raise ValueError('材质未找到: {}'.format(material_name))
    try:
        rt.setProperty(node, 'material', mat)
    except Exception:  # pylint: disable=broad-except
        node.material = mat
    # 赋给对象后材质会自动进入 sceneMaterials；把它也补进内存簿避免后续重复查找
    _MATERIAL_REGISTRY[str(mat.name)] = mat
    return {
        'object': str(node.name),
        'material': str(mat.name),
        'ok': True,
    }


@tool(
    description=(
        '给材质添加漫反射贴图（Bitmap Texture）。'
        '会按文件路径加载图片并连接到材质的 diffuse 通道。'
    ),
    category='material',
)
def add_diffuse_map(material_name, image_path):
    """加漫反射贴图。

    :param material_name: 材质名
    :param image_path: 图片绝对路径（jpg/png/exr/tx 等 Max 支持的格式）
    :returns: dict {"material": ..., "image": ..., "ok": True}
    """
    _ensure_in_max()
    if not os.path.isfile(image_path):
        raise ValueError('图片文件不存在: {}'.format(image_path))
    mat = _find_material_by_name(material_name)
    if mat is None:
        raise ValueError('材质未找到: {}'.format(material_name))
    bmap = rt.Bitmaptexture(filename=image_path)
    # PhysicalMaterial 用 base_color_map，Standardmaterial 用 diffuseMap
    cls = str(rt.classOf(mat))
    try:
        if cls == 'PhysicalMaterial':
            mat.base_color_map = bmap
        else:
            mat.diffuseMap = bmap
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError('无法连接贴图: {}'.format(exc))
    return {
        'material': str(mat.name),
        'image': image_path,
        'ok': True,
    }


@tool(
    description='列出场景中所有已使用的材质（含名字与类型）。',
    category='material',
    wrap_undo=False,
)
def list_scene_materials(limit=50):
    """列出场景材质。

    :param limit: 最多返回数
    :returns: dict {"count": N, "items": [{"name": ..., "type": ...}, ...]}
    """
    _ensure_in_max()
    items = []
    count = int(rt.sceneMaterials.count)
    for i in range(count):
        m = rt.sceneMaterials[i]
        items.append({
            'name': str(m.name),
            'type': str(rt.classOf(m)),
        })
        if 0 < limit <= len(items):
            break
    return {'count': len(items), 'items': items}
