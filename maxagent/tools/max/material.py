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

from ...runtime_helpers import has_runtime_attr
from ...runtime_helpers import IN_MAX
from ...runtime_helpers import rt
from ...runtime_helpers import run_on_main
from ...tools.registry import tool


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
    ``assign_material`` 找不到。这里做三件事：
    1. 在模块内存里登记一份（最直接的找回路径）
    2. 优先找第一个真正空的 medit 槽放进去
    3. 如果没空槽，覆盖第一个默认未命名槽，确保材质一定被注册
    """
    try:
        _MATERIAL_REGISTRY[str(mat.name)] = mat
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        # 查找一个空槽（默认 24 个槽位，从 1 开始）
        for i in range(1, 25):
            try:
                slot = rt.getMeditMaterial(i)
            except Exception:  # pylint: disable=broad-except
                continue
            try:
                cls = str(rt.classOf(slot))
            except Exception:  # pylint: disable=broad-except
                cls = ''
            try:
                slot_name = str(getattr(slot, 'name', '') or '')
            except Exception:  # pylint: disable=broad-except
                slot_name = ''
            is_empty = (
                (not slot_name)
                or slot_name.startswith('Map ')
                or slot_name.startswith('#')
                or slot_name == cls
            )
            if is_empty:
                rt.setMeditMaterial(i, mat)
                return
        # 没有空槽：回退到槽 1，确保材质一定可见
        rt.setMeditMaterial(1, mat)
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
    dcc=['3dsmax'],
    description=(
        '创建一个标准材质（Standard Material）。返回材质名供后续 assign_material 使用。'
    ),
    category='material',
    examples=[
        {
            'summary': '创建默认灰色标准材质',
            'args': {'name': 'MyStandard'},
        },
        {
            'summary': '创建红色半透明自发光材质',
            'args': {
                'name': 'RedGlow',
                'diffuse': '[255, 0, 0]',
                'glossiness': 60.0,
                'opacity': 80.0,
                'self_illumination': 30.0,
            },
        },
    ],
    notes=[
        'diffuse / specular 支持 JSON 字符串 "[r,g,b]"、"r,g,b" 或 Python 列表。',
        '颜色分量可传 0-255 整数或 0-1 浮点，工具会自动判断。',
        '创建后材质会自动注册到材质编辑器槽，确保后续 assign_material 可通过名字查找。',
    ],
    returns_desc='dict {"name": 实际材质名, "type": "Standardmaterial"}',
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
    dcc=['3dsmax'],
    description=(
        '创建一个物理材质（PhysicalMaterial）。Max 2017+ 推荐使用。'
        '若当前版本不支持，会自动降级为 Standardmaterial。'
    ),
    category='material',
    examples=[
        {
            'summary': '创建默认粗糙塑料材质',
            'args': {'name': 'Plastic'},
        },
        {
            'summary': '创建金色金属材质',
            'args': {
                'name': 'GoldMetal',
                'base_color': '[255, 215, 0]',
                'roughness': 0.2,
                'metalness': 1.0,
            },
        },
    ],
    notes=[
        'base_color 支持 JSON 字符串 "[r,g,b]" 或 Python 列表；分量 0-255 或 0-1 均可。',
        'roughness / metalness / transparency 为 0-1 的浮点值。',
        '当 Max 版本低于 2017 或不存在 PhysicalMaterial 类时，返回 downgraded=true。',
    ],
    returns_desc='dict {"name": 材质名, "type": "PhysicalMaterial" | "Standardmaterial", "downgraded": bool}',
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
    dcc=['3dsmax'],
    description=(
        '把已存在的材质赋给对象。材质需先通过 create_standard_material / '
        'create_physical_material 创建，或者从场景中已有材质里查找。'
    ),
    category='material',
    examples=[
        {
            'summary': '把名为 MyMaterial 的材质赋给 Box001',
            'args': {'object_name': 'Box001', 'material_name': 'MyMaterial'},
        },
    ],
    notes=[
        'object_name 必须是场景中已存在的对象名。',
        'material_name 必须已存在于材质簿、sceneMaterials 或材质编辑器槽位中。',
        '材质被赋予后会自动进入 sceneMaterials，后续可直接复用。',
    ],
    returns_desc='dict {"object": 对象名, "material": 材质名, "ok": true}',
    prerequisites=['对象 object_name 必须已存在于场景中'],
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
    dcc=['3dsmax'],
    description=(
        '给材质添加漫反射贴图（Bitmap Texture）。'
        '会按文件路径加载图片并连接到材质的 diffuse 通道。'
    ),
    category='material',
    examples=[
        {
            'summary': '给 MyMaterial 添加漫反射贴图',
            'args': {
                'material_name': 'MyMaterial',
                'image_path': 'C:/Textures/diffuse.jpg',
            },
        },
    ],
    notes=[
        'image_path 必须是 Max 可读取的本地绝对路径（jpg/png/exr/tx 等）。',
        'PhysicalMaterial 会连到 base_color_map，Standardmaterial 会连到 diffuseMap。',
        '贴图加载成功后会再次读取该属性，确认连接已生效。',
    ],
    returns_desc='dict {"material": 材质名, "image": 图片路径, "ok": true}',
    prerequisites=['材质 material_name 必须已存在'],
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
            rt.setProperty(mat, 'base_color_map', bmap)
        else:
            rt.setProperty(mat, 'diffuseMap', bmap)
    except Exception as exc:  # pylint: disable=broad-except
        try:
            if cls == 'PhysicalMaterial':
                mat.base_color_map = bmap
            else:
                mat.diffuseMap = bmap
        except Exception as fallback_exc:  # pylint: disable=broad-except
            raise RuntimeError(
                '无法连接贴图: {}; 兜底失败: {}'.format(exc, fallback_exc)
            )
    # 确认贴图确实连上
    connected_map = None
    try:
        connected_map = (
            rt.getProperty(mat, 'base_color_map')
            if cls == 'PhysicalMaterial'
            else rt.getProperty(mat, 'diffuseMap')
        )
    except Exception:  # pylint: disable=broad-except
        pass
    if connected_map is None:
        raise RuntimeError('贴图连接后未能读取到，可能未生效')
    return {
        'material': str(mat.name),
        'image': image_path,
        'ok': True,
    }


@tool(
    dcc=['3dsmax'],
    description='列出场景中所有已使用的材质（含名字与类型）。',
    category='material',
    wrap_undo=False,
    examples=[
        {
            'summary': '列出全部场景材质',
            'args': {},
        },
        {
            'summary': '最多返回 10 条场景材质',
            'args': {'limit': 10},
        },
    ],
    notes=[
        '仅返回已被对象引用的材质（sceneMaterials），未赋给对象的材质可能不在列表中。',
        'limit <= 0 时返回所有材质。',
    ],
    returns_desc='dict {"count": 实际数量, "items": [{"name": ..., "type": ...}, ...]}',
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


def _is_texture_map(value):
    """判断一个值是否是贴图/纹理对象。"""
    if value is None:
        return False
    try:
        cls = str(rt.classOf(value))
    except Exception:  # pylint: disable=broad-except
        return False
    # 常见贴图基类名；不同版本可能略有差异
    texture_super = ('TextureMap', 'Map', 'Bitmaptexture')
    try:
        super_cls = str(rt.superClassOf(value))
    except Exception:  # pylint: disable=broad-except
        super_cls = ''
    return cls.endswith('Map') or cls.endswith('Texture') or super_cls in texture_super


def _is_sub_material(value):
    """判断一个值是否是子材质。"""
    if value is None:
        return False
    try:
        super_cls = str(rt.superClassOf(value))
    except Exception:  # pylint: disable=broad-except
        return False
    return super_cls == 'Material'


def _serialize_value(value, depth=0):
    """把 MAXScript 属性值序列化为可 JSON 的 Python 对象。

    对贴图/材质只做摘要，避免递归过深和循环引用。
    """
    if depth > 4:
        return '<nested>'
    if value is None:
        return None
    try:
        cls = str(rt.classOf(value))
    except Exception:  # pylint: disable=broad-except
        cls = type(value).__name__
    if _is_texture_map(value) or _is_sub_material(value):
        name = ''
        try:
            name = str(getattr(value, 'name', '') or '')
        except Exception:  # pylint: disable=broad-except
            pass
        return {
            'type': cls,
            'name': name,
            'is_map': _is_texture_map(value),
            'is_material': _is_sub_material(value),
        }
    # 颜色
    if cls == 'Color':
        try:
            return {
                'r': int(value.r),
                'g': int(value.g),
                'b': int(value.b),
            }
        except Exception:  # pylint: disable=broad-except
            return str(value)
    # 数组/列表
    if cls in ('Array', 'MAXScriptArray') or isinstance(value, (list, tuple)):
        out = []
        try:
            for item in value:
                out.append(_serialize_value(item, depth + 1))
                if len(out) >= 50:
                    out.append('<truncated>')
                    break
        except Exception:  # pylint: disable=broad-except
            pass
        return out
    # 简单标量
    if isinstance(value, (int, float, bool, str)):
        return value
    # 其他复杂类型统一字符串化
    try:
        return str(value)
    except Exception:  # pylint: disable=broad-except
        return '<unserializable>'


def _get_material_properties(mat):
    """读取材质的主要可读写属性。"""
    props = []
    try:
        raw = rt.getPropNames(mat)
        for p in raw:
            props.append(str(p))
    except Exception:  # pylint: disable=broad-except
        pass
    return props


def _inspect_material_main(material_name):
    """在主线程执行材质自省。"""
    mat = _find_material_by_name(material_name)
    if mat is None:
        raise ValueError('材质未找到: {}'.format(material_name))

    info = {
        'name': str(mat.name),
        'class': str(rt.classOf(mat)),
        'super_class': str(rt.superClassOf(mat)),
    }

    properties = _get_material_properties(mat)
    channels = []
    sub_materials = []
    basic_values = {}

    for prop in properties[:64]:
        try:
            value = rt.getProperty(mat, prop)
        except Exception:  # pylint: disable=broad-except
            continue
        serialized = _serialize_value(value)
        entry = {
            'property': prop,
            'value': serialized,
        }
        if _is_texture_map(value):
            entry['kind'] = 'map'
            channels.append(entry)
        elif _is_sub_material(value):
            entry['kind'] = 'sub_material'
            sub_materials.append(entry)
        else:
            entry['kind'] = 'value'
            basic_values[prop] = serialized

    info['channels'] = channels
    info['sub_materials'] = sub_materials
    info['basic_values'] = basic_values
    info['properties_count'] = len(properties)
    return info


def _list_texture_maps_main(material_name):
    """递归收集材质及其子材质/通道上的所有 Bitmaptexture。"""
    mat = _find_material_by_name(material_name)
    if mat is None:
        raise ValueError('材质未找到: {}'.format(material_name))

    results = []
    visited = set()

    def _scan(obj, path=''):
        if obj in visited:
            return
        visited.add(obj)
        try:
            cls = str(rt.classOf(obj))
        except Exception:  # pylint: disable=broad-except
            return
        if cls == 'Bitmaptexture':
            filename = ''
            try:
                filename = str(getattr(obj, 'filename', '') or '')
            except Exception:  # pylint: disable=broad-except
                pass
            name = ''
            try:
                name = str(getattr(obj, 'name', '') or '')
            except Exception:  # pylint: disable=broad-except
                pass
            results.append({
                'name': name,
                'path': filename,
                'channel': path,
            })
            return
        if _is_sub_material(obj) or _is_texture_map(obj):
            props = _get_material_properties(obj)
            for prop in props[:32]:
                try:
                    value = rt.getProperty(obj, prop)
                except Exception:  # pylint: disable=broad-except
                    continue
                if _is_texture_map(value) or _is_sub_material(value):
                    new_path = '{}.{}'.format(path, prop) if path else prop
                    _scan(value, new_path)

    _scan(mat, material_name)
    return {'count': len(results), 'maps': results}


def _replace_texture_map_main(material_name, slot_name, new_image_path):
    """替换材质指定属性上的 Bitmaptexture。"""
    if not os.path.isfile(new_image_path):
        raise ValueError('图片文件不存在: {}'.format(new_image_path))
    mat = _find_material_by_name(material_name)
    if mat is None:
        raise ValueError('材质未找到: {}'.format(material_name))

    # 精确匹配属性名
    try:
        current = rt.getProperty(mat, slot_name)
    except Exception as exc:  # pylint: disable=broad-except
        raise ValueError('材质没有属性 {}: {}'.format(slot_name, exc))

    new_map = rt.Bitmaptexture(filename=new_image_path)
    rt.setProperty(mat, slot_name, new_map)
    return {
        'material': material_name,
        'slot': slot_name,
        'old_map': str(rt.classOf(current)) if current else None,
        'new_image': new_image_path,
        'ok': True,
    }


@tool(
    dcc=['3dsmax'],
    description='列出材质编辑器 24 个槽位中当前存放的材质名与类型。',
    category='material',
    wrap_undo=False,
    examples=[
        {
            'summary': '查看所有材质编辑器槽位',
            'args': {},
        },
    ],
    notes=[
        '槽位编号 1-24，空槽通常显示为空名或默认 # 名称。',
        '此工具只读，不会修改场景或材质。',
    ],
    returns_desc='dict {"count": 24, "items": [{"slot": 1, "name": ..., "type": ...}, ...]}',
)
def inspect_material_slots():
    """列出材质编辑器槽位。

    :returns: dict {"count": N, "items": [{"slot": 1, "name": ..., "type": ...}, ...]}
    """
    _ensure_in_max()
    items = []
    for i in range(1, 25):
        try:
            mat = rt.getMeditMaterial(i)
            items.append({
                'slot': i,
                'name': str(getattr(mat, 'name', '') or ''),
                'type': str(rt.classOf(mat)),
            })
        except Exception:  # pylint: disable=broad-except
            items.append({'slot': i, 'name': '', 'type': '<error>'})
    return {'count': len(items), 'items': items}


@tool(
    dcc=['3dsmax'],
    description=(
        '深入自省一个材质的内部结构：属性、贴图通道、子材质。'
        '用于 Agent 理解现有材质网络，而不是盲目修改。'
    ),
    category='material',
    wrap_undo=False,
    run_on_main_thread=True,
    examples=[
        {
            'summary': '查看名为 MyMaterial 的材质结构',
            'args': {'material_name': 'MyMaterial'},
        },
    ],
    notes=[
        '会递归读取材质的属性、贴图通道（map）和子材质（sub_material）。',
        '贴图和子材质只做摘要序列化，避免循环引用导致递归过深。',
    ],
    returns_desc=(
        'dict {"name": 材质名, "class": 类名, "channels": [...], '
        '"sub_materials": [...], "basic_values": {...}, "properties_count": N}'
    ),
    prerequisites=['材质 material_name 必须已存在'],
)
def inspect_material(material_name: str):
    """自省材质节点网络。

    :param material_name: 材质名
    :returns: dict 包含 name, class, channels, sub_materials, basic_values
    """
    _ensure_in_max()
    return run_on_main(
        _inspect_material_main, material_name, _timeout=30.0,
    )


@tool(
    dcc=['3dsmax'],
    description=(
        '递归列出一个材质及其子材质/通道上所有 Bitmaptexture 贴图文件路径。'
        '用于检查贴图引用、批量重链等场景。'
    ),
    category='material',
    wrap_undo=False,
    run_on_main_thread=True,
    examples=[
        {
            'summary': '列出 MyMaterial 上的所有 Bitmaptexture',
            'args': {'material_name': 'MyMaterial'},
        },
    ],
    notes=[
        '会递归扫描子材质和贴图通道，已访问对象会被去重。',
        '仅收集 classOf 为 Bitmaptexture 的贴图， procedural map 不在此列。',
    ],
    returns_desc='dict {"count": N, "maps": [{"name": ..., "path": ..., "channel": ...}, ...]}',
    prerequisites=['材质 material_name 必须已存在'],
)
def list_texture_maps(material_name: str):
    """列出材质上的贴图。

    :param material_name: 材质名
    :returns: dict {"count": N, "maps": [{"name", "path", "channel"}, ...]}
    """
    _ensure_in_max()
    return run_on_main(
        _list_texture_maps_main, material_name, _timeout=30.0,
    )


@tool(
    dcc=['3dsmax'],
    description=(
        '把材质某个贴图通道（如 diffuseMap、base_color_map、bumpMap）'
        '替换为新的图片文件。'
    ),
    category='material',
    run_on_main_thread=True,
    examples=[
        {
            'summary': '替换 MyMaterial 的漫反射贴图',
            'args': {
                'material_name': 'MyMaterial',
                'slot_name': 'diffuseMap',
                'new_image_path': 'C:/Textures/new_diffuse.png',
            },
        },
        {
            'summary': '替换物理材质的基础色贴图',
            'args': {
                'material_name': 'MyPhysical',
                'slot_name': 'base_color_map',
                'new_image_path': 'C:/Textures/basecolor.exr',
            },
        },
    ],
    notes=[
        'slot_name 必须是材质上实际存在的贴图属性名，如 diffuseMap / base_color_map / bumpMap。',
        'new_image_path 必须是本地存在的文件路径。',
        '当前属性值若不是 Bitmaptexture 也会被替换为新的 Bitmaptexture。',
    ],
    returns_desc='dict {"material": 材质名, "slot": 属性名, "old_map": 旧类型, "new_image": 新路径, "ok": true}',
    prerequisites=['材质 material_name 必须已存在'],
)
def replace_texture_map(material_name: str, slot_name: str, new_image_path: str):
    """替换材质贴图。

    :param material_name: 材质名
    :param slot_name: 贴图属性名，如 diffuseMap / base_color_map / bumpMap
    :param new_image_path: 新的图片绝对路径
    :returns: dict {"material", "slot", "old_map", "new_image", "ok"}
    """
    _ensure_in_max()
    return run_on_main(
        _replace_texture_map_main,
        material_name,
        slot_name,
        new_image_path,
        _timeout=30.0,
    )
