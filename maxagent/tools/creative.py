#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""创作辅助类工具。

面向美术/TA 工作流：参数变体探索、程序化生成意图解析、场景智能批量替换。
所有写操作都会自动包进 undo group，便于回滚。
"""

from __future__ import absolute_import
from __future__ import print_function

import colorsys
import re
from typing import Any
from typing import Dict
from typing import List

from ..runtime_helpers import IN_MAX
from ..runtime_helpers import rt
from .material import _find_material_by_name
from .material import _register_material_to_medit
from .material import _to_color
from .modifier import _get_node
from .modifier import _MODIFIER_MAP
from .registry import tool


def _ensure_in_max():
    if not IN_MAX:
        raise RuntimeError('非 3ds Max 环境')


def _clamp(value, low, high):
    return max(low, min(high, value))


def _adjust_color(rgb, factor):
    """对 [r,g,b] 做亮度/饱和度调整，factor > 1 变亮，0~1 变暗。"""
    if rgb is None or len(rgb) < 3:
        rgb = [200, 200, 200]
    # 归一化到 0-1
    norm = []
    for v in rgb:
        try:
            norm.append(float(v) / 255.0 if float(v) <= 1.0 else float(v) / 255.0)
        except (TypeError, ValueError):
            norm.append(0.5)
    # 用 HLS 空间调整亮度
    h, l, s = colorsys.rgb_to_hls(norm[0], norm[1], norm[2])
    l = _clamp(l * factor, 0.0, 1.0)
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return [int(r * 255), int(g * 255), int(b * 255)]


def _parse_material_description(desc):
    # type: (str) -> Dict[str, Any]
    """把自然语言描述解析为材质参数调整因子。

    返回 dict：{
        "metalness_delta": float,    # 金属度偏移
        "roughness_delta": float,    # 粗糙度偏移
        "brightness_factor": float,  # 亮度倍率
        "opacity_delta": float,      # 不透明度偏移
    }
    """
    desc = (desc or '').lower()
    result = {
        'metalness_delta': 0.0,
        'roughness_delta': 0.0,
        'brightness_factor': 1.0,
        'opacity_delta': 0.0,
    }
    # 金属度
    if any(k in desc for k in ('金属', 'metal', 'metallic', '更钢', '更铁')):
        result['metalness_delta'] = 0.3
    if any(k in desc for k in ('非金属', 'less metal', '更塑料')):
        result['metalness_delta'] = -0.3
    # 粗糙度
    if any(k in desc for k in ('粗糙', 'rough', '哑光', '磨砂')):
        result['roughness_delta'] = 0.3
    if any(k in desc for k in ('光滑', '光滑', 'glossy', '更光', '抛光')):
        result['roughness_delta'] = -0.3
    # 亮度
    if any(k in desc for k in ('更亮', '亮', 'bright', 'lighter')):
        result['brightness_factor'] = 1.25
    if any(k in desc for k in ('更暗', '暗', 'dark', 'darker', '更深')):
        result['brightness_factor'] = 0.8
    # 透明度
    if any(k in desc for k in ('透明', '玻璃', 'glass', '更透')):
        result['opacity_delta'] = -0.25
    if any(k in desc for k in ('不透明', '更实', 'solid')):
        result['opacity_delta'] = 0.25
    return result


@tool(
    description=(
        '基于已有材质生成多个参数变体，用于美术探索。description 支持自然语言，'
        '如："更金属一点"、"更粗糙"、"更亮"、"更透明"。'
        '返回创建的新材质名列表（不会自动赋给对象）。'
    ),
    category='creative',
)
def generate_material_variants(material_name, description, count=3):
    """生成材质参数变体。

    :param material_name: 基准材质名
    :param description: 目标风格的自然语言描述
    :param count: 生成变体数量（1~5）
    :returns: dict {"base": ..., "variants": [{"name": ..., "params": ...}, ...]}
    """
    _ensure_in_max()
    count = _clamp(int(count), 1, 5)
    base_mat = _find_material_by_name(material_name)
    if base_mat is None:
        raise ValueError('找不到材质: {}'.format(material_name))
    base_cls = str(rt.classOf(base_mat))
    adjustments = _parse_material_description(description)
    variants = []
    for i in range(1, count + 1):
        new_name = '{}_variant_{}'.format(material_name, i)
        # 避免重名：如果已存在则加序号
        counter = 1
        unique_name = new_name
        while _find_material_by_name(unique_name) is not None:
            unique_name = '{}_variant_{}_{}'.format(material_name, i, counter)
            counter += 1
        if base_cls == 'PhysicalMaterial':
            mat = rt.PhysicalMaterial()
            mat.name = unique_name
            # 复制基础属性
            try:
                base_color = list(base_mat.base_color)[:3]
            except Exception:  # pylint: disable=broad-except
                base_color = [200, 200, 200]
            bright = adjustments['brightness_factor']
            # 不同变体在基础调整上再引入小幅随机阶梯
            step = (i - (count + 1) / 2.0) / count
            mat.base_color = _to_color(_adjust_color(base_color, bright + step * 0.15))
            mat.metalness = _clamp(
                float(base_mat.metalness) + adjustments['metalness_delta'] + step * 0.1,
                0.0, 1.0,
            )
            mat.roughness = _clamp(
                float(base_mat.roughness) + adjustments['roughness_delta'] + step * 0.1,
                0.0, 1.0,
            )
            mat.transparency = _clamp(
                float(base_mat.transparency) + adjustments['opacity_delta'] + step * 0.05,
                0.0, 1.0,
            )
            params = {
                'base_color': [int(c) for c in _to_color(list(mat.base_color)[:3])],
                'metalness': float(mat.metalness),
                'roughness': float(mat.roughness),
                'transparency': float(mat.transparency),
            }
        else:
            # Standardmaterial
            mat = rt.Standardmaterial()
            mat.name = unique_name
            try:
                base_color = list(base_mat.diffuse)[:3]
            except Exception:  # pylint: disable=broad-except
                base_color = [200, 200, 200]
            bright = adjustments['brightness_factor']
            step = (i - (count + 1) / 2.0) / count
            mat.diffuse = _to_color(_adjust_color(base_color, bright + step * 0.15))
            try:
                mat.glossiness = _clamp(
                    float(base_mat.glossiness) - adjustments['roughness_delta'] * 50.0,
                    0.0, 100.0,
                )
            except Exception:  # pylint: disable=broad-except
                pass
            try:
                mat.opacity = _clamp(
                    float(base_mat.opacity) + adjustments['opacity_delta'] * 100.0,
                    0.0, 100.0,
                )
            except Exception:  # pylint: disable=broad-except
                pass
            params = {
                'diffuse': [int(c) for c in _to_color(list(mat.diffuse)[:3])],
                'glossiness': float(mat.glossiness),
                'opacity': float(mat.opacity),
            }
        _register_material_to_medit(mat)
        variants.append({'name': unique_name, 'params': params})
    return {'base': material_name, 'variants': variants}


def _match_nodes(criteria):
    # type: (Dict[str, Any]) -> List[Any]
    """根据 criteria 筛选场景节点。

    criteria 支持：
    - name_contains: str / List[str]
    - name_regex: str
    - super_class: str（geometry/light/camera/shape/helper）
    - class_equals: str / List[str]
    - has_modifier: str / List[str]
    - material_contains: str
    - selected_only: bool
    """
    _ensure_in_max()
    # LLM 有概率把 dict 传成 JSON 字符串（tool_call arguments 里嵌套 dict
    # 序列化不彻底），这里做自动兼容。
    if isinstance(criteria, str):
        try:
            import json
            criteria = json.loads(criteria)
        except (ValueError, TypeError):
            criteria = {}
    if not isinstance(criteria, dict):
        criteria = {}
    source = list(rt.selection) if criteria.get('selected_only') else list(rt.objects)
    matched = []
    for node in source:
        try:
            node_name = str(node.name)
        except Exception:  # pylint: disable=broad-except
            continue
        # 名字包含
        name_contains = criteria.get('name_contains')
        if name_contains:
            if isinstance(name_contains, str):
                name_contains = [name_contains]
            if not any(k.lower() in node_name.lower() for k in name_contains):
                continue
        # 名字正则
        name_regex = criteria.get('name_regex')
        if name_regex:
            try:
                if not re.search(name_regex, node_name):
                    continue
            except re.error:
                continue
        # 超类
        super_class = criteria.get('super_class')
        if super_class:
            sc_map = {
                'geometry': 'GeometryClass',
                'light': 'light',
                'camera': 'camera',
                'shape': 'shape',
                'helper': 'helper',
            }
            target = sc_map.get(super_class.lower(), super_class)
            try:
                if str(rt.superClassOf(node)) != target:
                    continue
            except Exception:  # pylint: disable=broad-except
                continue
        # 类精确匹配
        class_equals = criteria.get('class_equals')
        if class_equals:
            if isinstance(class_equals, str):
                class_equals = [class_equals]
            try:
                node_cls = str(rt.classOf(node))
            except Exception:  # pylint: disable=broad-except
                continue
            if node_cls not in class_equals:
                continue
        # 是否有某修改器
        has_modifier = criteria.get('has_modifier')
        if has_modifier:
            if isinstance(has_modifier, str):
                has_modifier = [has_modifier]
            found = False
            try:
                for i in range(int(node.modifiers.count)):
                    mod_cls = str(rt.classOf(node.modifiers[i]))
                    if mod_cls in has_modifier:
                        found = True
                        break
            except Exception:  # pylint: disable=broad-except
                pass
            if not found:
                continue
        # 材质名包含
        material_contains = criteria.get('material_contains')
        if material_contains:
            try:
                mat = node.material
                mat_name = str(mat.name) if mat is not None else ''
            except Exception:  # pylint: disable=broad-except
                mat_name = ''
            if material_contains.lower() not in mat_name.lower():
                continue
        matched.append(node)
    return matched


@tool(
    description=(
        '按条件批量查找场景对象并添加/替换修改器。'
        'criteria 支持 name_contains / name_regex / super_class / class_equals / '
        'has_modifier / material_contains / selected_only。'
        '如果 replace_existing=True，会先删除同类型修改器再添加新修改器。'
    ),
    category='creative',
)
def smart_replace_modifier(
    criteria,
    modifier_type,
    params=None,
    replace_existing=False,
):
    """智能批量替换/添加修改器。

    :param criteria: dict，筛选条件
    :param modifier_type: 修改器类型，同 add_modifier
    :param params: dict，修改器参数
    :param replace_existing: 是否先删除同类型已有修改器
    :returns: dict {"matched": N, "modified": [...]}
    """
    _ensure_in_max()
    nodes = _match_nodes(criteria)
    if not nodes:
        return {'matched': 0, 'modified': []}
    cls_name = _MODIFIER_MAP.get(modifier_type.lower(), modifier_type)
    cls = getattr(rt, cls_name, None)
    if cls is None:
        raise ValueError('未知修改器类型: {} (尝试 {})'.format(modifier_type, cls_name))
    modified = []
    for node in nodes:
        node_name = str(node.name)
        # 如需要替换，先删除同类型修改器
        if replace_existing:
            try:
                count = int(node.modifiers.count)
                for idx in range(count, 0, -1):
                    if str(rt.classOf(node.modifiers[idx - 1])) == cls_name:
                        rt.deleteModifier(node, idx)
            except Exception:  # pylint: disable=broad-except
                pass
        mod = cls()
        norm_params = params or {}
        if isinstance(norm_params, str):
            try:
                import json
                norm_params = json.loads(norm_params)
            except (ValueError, TypeError):
                norm_params = {}
        if isinstance(norm_params, dict):
            for key, val in norm_params.items():
                try:
                    setattr(mod, key, val)
                except Exception:  # pylint: disable=broad-except
                    pass
        rt.addModifier(node, mod)
        modified.append({
            'object': node_name,
            'modifier': str(rt.classOf(mod)),
            'stack_size': int(node.modifiers.count),
        })
    return {'matched': len(nodes), 'modified': modified}


@tool(
    description=(
        '把自然语言程序化生成意图解析为可执行脚本模板。'
        '不会自动执行代码，而是返回建议的 Python 脚本和需要调用的工具序列，'
        '由用户/专家确认后再运行。'
    ),
    category='creative',
    wrap_undo=False,
)
def generate_script_from_description(description, language='python'):
    """解析生成意图并返回代码模板。

    :param description: 自然语言描述，例如："创建 10 个随机位置的球体"
    :param language: 生成语言，目前仅支持 python
    :returns: dict {"description": ..., "language": ..., "script": ..., "notes": ...}
    """
    desc = (description or '').lower()
    script_lines = []
    notes = []
    # 简单规则解析：数量 + 对象类型 + 随机/阵列/分布
    count_match = re.search(r'(\d+)\s*个', desc)
    count = int(count_match.group(1)) if count_match else 1
    count = _clamp(count, 1, 100)

    obj_type = 'sphere'
    if any(k in desc for k in ('立方体', '方块', 'box', 'cube')):
        obj_type = 'box'
    elif any(k in desc for k in ('圆柱', 'cylinder', '柱体')):
        obj_type = 'cylinder'
    elif any(k in desc for k in ('圆锥', 'cone', '锥体')):
        obj_type = 'cone'
    elif any(k in desc for k in ('茶壶', 'teapot')):
        obj_type = 'teapot'
    elif any(k in desc for k in ('平面', 'plane', '板')):
        obj_type = 'plane'

    if any(k in desc for k in ('随机', 'random')):
        notes.append('检测到随机分布意图，脚本使用 random 生成位置。')
        script_lines.extend([
            'import random',
            'from pymxs import runtime as rt',
            '',
            'count = {}'.format(count),
            'for i in range(count):',
            '    x = random.uniform(-50, 50)',
            '    y = random.uniform(-50, 50)',
            '    z = random.uniform(0, 20)',
        ])
        if obj_type == 'sphere':
            script_lines.append('    obj = rt.sphere(radius=random.uniform(5, 15))')
        elif obj_type == 'box':
            script_lines.append(
                '    obj = rt.box(length=random.uniform(5, 15), '
                'width=random.uniform(5, 15), height=random.uniform(5, 15))'
            )
        elif obj_type == 'cylinder':
            script_lines.append(
                '    obj = rt.cylinder(radius=random.uniform(3, 8), '
                'height=random.uniform(5, 20))'
            )
        elif obj_type == 'cone':
            script_lines.append(
                '    obj = rt.cone(radius1=random.uniform(3, 8), '
                'radius2=0, height=random.uniform(5, 20))'
            )
        elif obj_type == 'teapot':
            script_lines.append('    obj = rt.teapot(radius=random.uniform(5, 15))')
        elif obj_type == 'plane':
            script_lines.append(
                '    obj = rt.plane(length=random.uniform(10, 30), '
                'width=random.uniform(10, 30))'
            )
        script_lines.extend([
            '    obj.pos = rt.Point3(x, y, z)',
            '    obj.name = "{}_{{:03d}}".format(i + 1)'.format(obj_type),
        ])
    elif any(k in desc for k in ('阵列', '阵列', 'array', '排成', '排成一行', 'grid')):
        notes.append('检测到阵列排布意图。')
        cols = min(count, 10)
        script_lines.extend([
            'from pymxs import runtime as rt',
            '',
            'count = {}'.format(count),
            'cols = {}'.format(cols),
            'spacing = 20',
            'for i in range(count):',
            '    row = i // cols',
            '    col = i % cols',
            '    x = col * spacing',
            '    y = row * spacing',
            '    z = 0',
        ])
        if obj_type == 'sphere':
            script_lines.append('    obj = rt.sphere(radius=5)')
        elif obj_type == 'box':
            script_lines.append('    obj = rt.box(length=10, width=10, height=10)')
        elif obj_type == 'cylinder':
            script_lines.append('    obj = rt.cylinder(radius=3, height=10)')
        elif obj_type == 'cone':
            script_lines.append('    obj = rt.cone(radius1=3, radius2=0, height=10)')
        elif obj_type == 'teapot':
            script_lines.append('    obj = rt.teapot(radius=5)')
        elif obj_type == 'plane':
            script_lines.append('    obj = rt.plane(length=10, width=10)')
        script_lines.extend([
            '    obj.pos = rt.Point3(x, y, z)',
            '    obj.name = "{}_{{:03d}}".format(i + 1)'.format(obj_type),
        ])
    else:
        notes.append('未识别到明确分布模式，按单个对象生成。')
        script_lines.extend([
            'from pymxs import runtime as rt',
            '',
        ])
        if obj_type == 'sphere':
            script_lines.append('obj = rt.sphere(radius=10)')
        elif obj_type == 'box':
            script_lines.append('obj = rt.box(length=10, width=10, height=10)')
        elif obj_type == 'cylinder':
            script_lines.append('obj = rt.cylinder(radius=5, height=10)')
        elif obj_type == 'cone':
            script_lines.append('obj = rt.cone(radius1=5, radius2=0, height=10)')
        elif obj_type == 'teapot':
            script_lines.append('obj = rt.teapot(radius=10)')
        elif obj_type == 'plane':
            script_lines.append('obj = rt.plane(length=20, width=20)')
        script_lines.append('obj.name = "{}_001"'.format(obj_type))

    script = '\n'.join(script_lines)
    return {
        'description': description,
        'language': language,
        'script': script,
        'notes': notes,
    }


@tool(
    description=(
        '按条件批量替换对象材质。criteria 支持 name_contains / name_regex / '
        'super_class / class_equals / has_modifier / material_contains / selected_only。'
    ),
    category='creative',
)
def smart_replace_material(criteria, material_name):
    """智能批量替换材质。

    :param criteria: dict，筛选条件
    :param material_name: 目标材质名
    :returns: dict {"matched": N, "assigned": [...]}
    """
    _ensure_in_max()
    mat = _find_material_by_name(material_name)
    if mat is None:
        raise ValueError('找不到材质: {}'.format(material_name))
    nodes = _match_nodes(criteria)
    assigned = []
    for node in nodes:
        try:
            node.material = mat
            assigned.append(str(node.name))
        except Exception:  # pylint: disable=broad-except
            pass
    return {'matched': len(nodes), 'assigned': assigned}
