#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修改器栈类工具。

支持的操作：添加修改器、删除修改器、列出修改器栈、塌陷栈到可编辑网格/多边形。

注意：MaxScript 中修改器的索引是 1-based，最顶层是 1（最后一个被应用的修改器）。
本模块对外暴露统一的 1-based 索引，避免 agent 混淆。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
from typing import Any
from typing import Dict

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


# 常用修改器的友好名 -> MaxScript class 映射。
# 命名踩坑：3ds Max 内部类名去掉空格后是驼峰/纯词，比如
#   "Noise modifier" -> Noisemodifier（无下划线，Max 内叫 Noisemodifier）
#   "Normal Modifier" -> Normalmodifier
#   "Volume Select" -> Vol__Select（有双下划线）
# 如果这里映射错了，getattr(rt, cls_name, None) 会返回 None，报"未知修改器"。
_MODIFIER_MAP = {
    'bend': 'Bend',
    'twist': 'Twist',
    'taper': 'Taper',
    'noise': 'Noisemodifier',
    'turbosmooth': 'TurboSmooth',
    'meshsmooth': 'MeshSmooth',
    'shell': 'Shell',
    'symmetry': 'Symmetry',
    'fft_uvw_unwrap': 'Unwrap_UVW',
    'unwrap_uvw': 'Unwrap_UVW',
    'uvw_map': 'UVWMap',
    'edit_poly': 'Edit_Poly',
    'edit_mesh': 'Edit_Mesh',
    'subdivide': 'Subdivide',
    'normal': 'Normalmodifier',
    'morpher': 'Morpher',
    'skin': 'Skin',
    'volume_select': 'Vol__Select',
    'extrude': 'Extrude',
    'lathe': 'Lathe',
    'cap_holes': 'Cap_Holes',
    'displace': 'Displace',
}


def _resolve_modifier_class(cls_name):
    """从 rt 找出实际存在的修改器 class。

    LLM 或用户传进来的类名可能有多种变体（Noise / Noise_modifier /
    Noisemodifier / noise），Max 的 rt 里只有一种是真的存在的。
    这里做兜底：先按原名查，再按几种常见变体（去下划线、加/去
    modifier 后缀、驼峰化）逐一尝试。
    """
    cls = getattr(rt, cls_name, None)
    if cls is not None:
        return cls
    # 变体候选：去下划线 / 去 _modifier 后缀 / 加 modifier 后缀 / 首字母大写
    base = cls_name.replace('_', '').lower()
    candidates = set()
    variants = [
        cls_name.replace('_', ''),
        cls_name.replace('_modifier', ''),
        cls_name.replace('_modifier', 'modifier'),
        cls_name.replace('_Modifier', 'modifier'),
        base,
        base.capitalize(),
        base + 'modifier',
        base.capitalize() + 'modifier',
    ]
    for v in variants:
        if not v or v in candidates:
            continue
        candidates.add(v)
        found = getattr(rt, v, None)
        if found is not None:
            return found
    return None


@tool(
    description=(
        '给对象添加一个修改器。type 可选: bend / twist / taper / noise / '
        'turbosmooth / meshsmooth / shell / symmetry / unwrap_uvw / uvw_map / '
        'edit_poly / edit_mesh / subdivide / extrude / lathe / displace 等，'
        '也可以直接写 MaxScript 类名（如 "TurboSmooth"）。'
    ),
    category='modifier',
)
def add_modifier(name, modifier_type, params=None):
    """给对象添加修改器。

    :param name: 对象名
    :param modifier_type: 修改器类型，见上方说明
    :param params: dict，修改器参数（如 turbosmooth 的 ``{"iterations": 2}``）
    :returns: dict {"object": ..., "modifier": "...", "stack_size": N}
    """
    _ensure_in_max()
    node = _get_node(name)
    cls_name = _MODIFIER_MAP.get(modifier_type.lower(), modifier_type)
    cls = _resolve_modifier_class(cls_name)
    if cls is None:
        raise ValueError(
            '未知修改器类型: {} (尝试 {})'.format(modifier_type, cls_name),
        )
    mod = cls()
    # 应用参数。LLM 偶尔会把 dict 序列化成 JSON 字符串传进来，这里做兼容
    norm_params = params
    if isinstance(norm_params, str):
        s = norm_params.strip()
        if not s:
            norm_params = None
        else:
            try:
                norm_params = json.loads(s)
            except (ValueError, TypeError):
                norm_params = None
    if norm_params and isinstance(norm_params, dict):
        for key, val in norm_params.items():
            try:
                setattr(mod, key, val)
            except Exception:  # pylint: disable=broad-except
                # 参数设错不致命，让 agent 看到结果再决定
                pass
    rt.addModifier(node, mod)
    return {
        'object': str(node.name),
        'modifier': str(rt.classOf(mod)),
        'stack_size': int(node.modifiers.count),
    }


@tool(
    description='移除对象修改器栈中的某个修改器（按 1-based 索引，1 是最顶层）。',
    category='modifier',
)
def remove_modifier(name, index=1):
    """删除修改器。

    :param name: 对象名
    :param index: 1-based 索引，1 表示最顶层修改器
    :returns: dict {"object": ..., "removed": "类名", "stack_size_after": N}
    """
    _ensure_in_max()
    node = _get_node(name)
    if node.modifiers.count == 0:
        raise ValueError('对象 {} 修改器栈为空'.format(name))
    if not 1 <= index <= node.modifiers.count:
        raise ValueError(
            '索引越界: {} (栈大小 {})'.format(index, node.modifiers.count),
        )
    mod = node.modifiers[index - 1]
    cls_str = str(rt.classOf(mod))
    rt.deleteModifier(node, index)
    return {
        'object': str(node.name),
        'removed': cls_str,
        'stack_size_after': int(node.modifiers.count),
    }


@tool(
    description='列出对象的修改器栈（从顶到底）。',
    category='modifier',
    wrap_undo=False,
)
def list_modifiers(name):
    """列出修改器栈。

    :param name: 对象名
    :returns: dict {"object": ..., "stack": [{"index": 1, "name": ..., "class": ...}, ...]}
    """
    _ensure_in_max()
    node = _get_node(name)
    stack = []
    count = int(node.modifiers.count)
    for i in range(count):
        mod = node.modifiers[i]
        stack.append({
            'index': i + 1,
            'name': str(mod.name),
            'class': str(rt.classOf(mod)),
            'enabled': bool(mod.enabled),
        })
    return {'object': str(node.name), 'stack': stack}


@tool(
    description=(
        '塌陷对象的修改器栈，把所有修改器烘焙成基础几何。'
        'to 可选: "poly" 转为可编辑多边形（推荐）；"mesh" 转为可编辑网格。'
    ),
    category='modifier',
)
def collapse_stack(name, to='poly'):
    """塌陷修改器栈。

    :param name: 对象名
    :param to: 'poly' 或 'mesh'
    :returns: dict {"object": ..., "result_class": ..., "face_count": N}
    """
    _ensure_in_max()
    node = _get_node(name)
    if to == 'poly':
        rt.convertToPoly(node)
    elif to == 'mesh':
        rt.convertToMesh(node)
    else:
        raise ValueError('to 必须是 poly 或 mesh，收到: {}'.format(to))
    fc = 0
    try:
        fc = int(rt.getPolygonCount(node)[0])
    except Exception:  # pylint: disable=broad-except
        pass
    return {
        'object': str(node.name),
        'result_class': str(rt.classOf(node)),
        'face_count': fc,
    }
