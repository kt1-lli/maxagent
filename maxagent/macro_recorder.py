#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会话级操作回放（Macro Recorder）。

职责：
1. 监听 AgentWorker 的工具调用流，无损记录所有"修改场景状态"的操作。
2. 在会话结束时，将记录的操作链导出为可复用的 Python 脚本（pymxs）
   或 MaxScript。
3. 提供"复制最后一步"、"生成回放脚本"等 UI 快捷操作。

记录策略：
- 只记录会**修改**场景的工具调用（跳过所有 list_/get_/query_）。
- 对于 create_*/modify_*/set_*/run_python/run_maxscript 等，保留
  原始参数和调用顺序。
- 纯 LLM 推理文本（无 tool_calls）不记录，因为它不含确定性操作。

导出的脚本可直接在 Max Listener 或 Scripting 窗口中执行，
实现"零 AI 干预"的确定性重放。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional


# ---------------------------------------------------------------------- #
# 数据模型
# ---------------------------------------------------------------------- #

@dataclass
class RecordedAction(object):
    """单条记录的操作。"""

    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    success: bool = True
    order: int = 0  # 在同一会话内的全局序号


@dataclass
class MacroSession(object):
    """一次会话的完整操作记录。"""

    session_id: str = ''
    created_at: float = field(default_factory=time.time)
    actions: List[RecordedAction] = field(default_factory=list)
    title: str = ''  # 会话标题/用户首条消息摘要

    def is_empty(self):
        return not self.actions

    def to_dict(self):
        return {
            'session_id': self.session_id,
            'title': self.title,
            'created_at': self.created_at,
            'actions': [
                {
                    'tool': a.tool_name,
                    'args': a.arguments,
                    'ts': a.timestamp,
                    'ok': a.success,
                    'order': a.order,
                }
                for a in self.actions
            ],
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls(
            session_id=d.get('session_id', ''),
            title=d.get('title', ''),
            created_at=d.get('created_at', 0.0),
        )
        for ad in d.get('actions', []):
            obj.actions.append(RecordedAction(
                tool_name=ad.get('tool', ''),
                arguments=ad.get('args', {}),
                timestamp=ad.get('ts', 0.0),
                success=ad.get('ok', True),
                order=ad.get('order', 0),
            ))
        return obj


# ---------------------------------------------------------------------- #
# 工具 → Python / MaxScript 映射表
# ---------------------------------------------------------------------- #

# 常量：参数占位符映射（避免 pymxs 运行时问题）
_COLOR_DEFAULT = '(128,128,128)'

_PYMXS_TOOL_MAP = {
    # 几何创建
    'create_sphere': lambda a: _build_create_sphere(a),
    'create_box': lambda a: _build_create_box(a),
    'create_cylinder': lambda a: _build_create_cylinder(a),
    'create_teapot': lambda a: _build_create_teapot(a),
    'create_plane': lambda a: _build_create_plane(a),
    'create_cone': lambda a: _build_create_cone(a),
    'create_torus': lambda a: _build_create_torus(a),
    'create_tube': lambda a: _build_create_tube(a),
    'create_text': lambda a: _build_create_text(a),
    'create_circle': lambda a: _build_create_circle(a),
    'create_rectangle': lambda a: _build_create_rectangle(a),
    'create_ellipse': lambda a: _build_create_ellipse(a),
    'create_star': lambda a: _build_create_star(a),
    'create_arc': lambda a: _build_create_arc(a),
    'create_helix': lambda a: _build_create_helix(a),

    # 变换
    'move_object': lambda a: _build_move_object(a),
    'rotate_object': lambda a: _build_rotate_object(a),
    'scale_object': lambda a: _build_scale_object(a),
    'delete_object': lambda a: _build_delete_object(a),
    'clone_object': lambda a: _build_clone_object(a),

    # 修改器
    'add_modifier': lambda a: _build_add_modifier(a),
    'remove_modifier': lambda a: _build_remove_modifier(a),
    'set_modifier_param': lambda a: _build_set_modifier_param(a),

    # 材质
    'assign_material': lambda a: _build_assign_material(a),
    'create_standard_material': lambda a: _build_create_standard_material(a),
    'set_material_color': lambda a: _build_set_material_color(a),

    # 灯光
    'create_omni_light': lambda a: _build_create_omni_light(a),
    'create_target_spot': lambda a: _build_create_target_spot(a),
    'create_target_direct': lambda a: _build_create_target_direct(a),
    'create_area_light': lambda a: _build_create_area_light(a),
    'create_sun_light': lambda a: _build_create_sun_light(a),

    # 场景管理
    'set_object_property': lambda a: _build_set_object_property(a),
    'rename_object': lambda a: _build_rename_object(a),

    # 代码执行（直接保留）
    'run_python': lambda a: _build_run_python(a),
    'run_maxscript': lambda a: _build_run_maxscript(a),
}

_MAXSCRIPT_TOOL_MAP = {
    # MaxScript 映射——大部分 pymxs 调用写法一致，
    # 但因为 MaxScript 语法不同，某些工具需要变体。
    'run_maxscript': lambda a: a.get('code', ''),
    'run_python': lambda a: _build_run_python_maxscript(a),
}


# ---------------------------------------------------------------------- #
# 参数构建辅助
# ---------------------------------------------------------------------- #

def _point3_str(lst):
    if not isinstance(lst, (list, tuple)) or len(lst) < 3:
        lst = [0.0, 0.0, 0.0]
    return 'Point3({:.3f},{:.3f},{:.3f})'.format(
        float(lst[0]), float(lst[1]), float(lst[2]),
    )


def _color_str(lst):
    if not isinstance(lst, (list, tuple)) or len(lst) < 3:
        lst = [128, 128, 128]
    return 'Color({},{},{})'.format(
        int(lst[0]), int(lst[1]), int(lst[2]),
    )


def _name_or_tmp(a):
    return a.get('name', 'Unnamed')


# -- 几何创建 ----------------------------------------------------------- #

def _build_create_sphere(a):
    name = _name_or_tmp(a)
    radius = a.get('radius', 25.0)
    segments = a.get('segments', 16)
    pos = a.get('pos', [0.0, 0.0, 0.0])
    lines = [
        's = rt.Sphere(name={!r}, radius={:.3f}, segs={})'.format(
            name, float(radius), int(segments),
        ),
        's.pos = {}'.format(_point3_str(pos)),
    ]
    return '\n'.join(lines)


def _build_create_box(a):
    name = _name_or_tmp(a)
    length = a.get('length', 50.0)
    width = a.get('width', 50.0)
    height = a.get('height', 50.0)
    pos = a.get('pos', [0.0, 0.0, 0.0])
    lines = [
        'b = rt.Box(name={!r}, length={:.3f}, width={:.3f}, height={:.3f})'.format(
            name, float(length), float(width), float(height),
        ),
        'b.pos = {}'.format(_point3_str(pos)),
    ]
    return '\n'.join(lines)


def _build_create_cylinder(a):
    name = _name_or_tmp(a)
    radius = a.get('radius', 15.0)
    height = a.get('height', 50.0)
    pos = a.get('pos', [0.0, 0.0, 0.0])
    lines = [
        'c = rt.Cylinder(name={!r}, radius={:.3f}, height={:.3f})'.format(
            name, float(radius), float(height),
        ),
        'c.pos = {}'.format(_point3_str(pos)),
    ]
    return '\n'.join(lines)


def _build_create_teapot(a):
    name = _name_or_tmp(a)
    radius = a.get('radius', 25.0)
    pos = a.get('pos', [0.0, 0.0, 0.0])
    lines = [
        't = rt.Teapot(name={!r}, radius={:.3f})'.format(name, float(radius)),
        't.pos = {}'.format(_point3_str(pos)),
    ]
    return '\n'.join(lines)


def _build_create_plane(a):
    name = _name_or_tmp(a)
    length = a.get('length', 100.0)
    width = a.get('width', 100.0)
    pos = a.get('pos', [0.0, 0.0, 0.0])
    lines = [
        'p = rt.Plane(name={!r}, length={:.3f}, width={:.3f})'.format(
            name, float(length), float(width),
        ),
        'p.pos = {}'.format(_point3_str(pos)),
    ]
    return '\n'.join(lines)


def _build_create_cone(a):
    name = _name_or_tmp(a)
    radius1 = a.get('radius1', 15.0)
    radius2 = a.get('radius2', 0.0)
    height = a.get('height', 50.0)
    pos = a.get('pos', [0.0, 0.0, 0.0])
    lines = [
        'c = rt.Cone(name={!r}, radius1={:.3f}, radius2={:.3f}, height={:.3f})'.format(
            name, float(radius1), float(radius2), float(height),
        ),
        'c.pos = {}'.format(_point3_str(pos)),
    ]
    return '\n'.join(lines)


def _build_create_torus(a):
    name = _name_or_tmp(a)
    radius1 = a.get('radius1', 30.0)
    radius2 = a.get('radius2', 10.0)
    pos = a.get('pos', [0.0, 0.0, 0.0])
    lines = [
        't = rt.Torus(name={!r}, radius1={:.3f}, radius2={:.3f})'.format(
            name, float(radius1), float(radius2),
        ),
        't.pos = {}'.format(_point3_str(pos)),
    ]
    return '\n'.join(lines)


def _build_create_tube(a):
    name = _name_or_tmp(a)
    radius1 = a.get('radius1', 20.0)
    radius2 = a.get('radius2', 15.0)
    height = a.get('height', 50.0)
    pos = a.get('pos', [0.0, 0.0, 0.0])
    lines = [
        't = rt.Tube(name={!r}, radius1={:.3f}, radius2={:.3f}, height={:.3f})'.format(
            name, float(radius1), float(radius2), float(height),
        ),
        't.pos = {}'.format(_point3_str(pos)),
    ]
    return '\n'.join(lines)


def _build_create_text(a):
    name = _name_or_tmp(a)
    text = a.get('text', 'MaxAgent')
    size = a.get('size', 10.0)
    pos = a.get('pos', [0.0, 0.0, 0.0])
    lines = [
        'txt = rt.Text(name={!r}, text={!r}, size={:.3f})'.format(
            name, str(text), float(size),
        ),
        'txt.pos = {}'.format(_point3_str(pos)),
    ]
    return '\n'.join(lines)


def _build_create_circle(a):
    name = _name_or_tmp(a)
    radius = a.get('radius', 20.0)
    lines = [
        'c = rt.Circle(name={!r}, radius={:.3f})'.format(name, float(radius)),
    ]
    return '\n'.join(lines)


def _build_create_rectangle(a):
    name = _name_or_tmp(a)
    length = a.get('length', 50.0)
    width = a.get('width', 50.0)
    lines = [
        'r = rt.Rectangle(name={!r}, length={:.3f}, width={:.3f})'.format(
            name, float(length), float(width),
        ),
    ]
    return '\n'.join(lines)


def _build_create_ellipse(a):
    name = _name_or_tmp(a)
    length = a.get('length', 50.0)
    width = a.get('width', 30.0)
    lines = [
        'e = rt.Ellipse(name={!r}, length={:.3f}, width={:.3f})'.format(
            name, float(length), float(width),
        ),
    ]
    return '\n'.join(lines)


def _build_create_star(a):
    name = _name_or_tmp(a)
    radius1 = a.get('radius1', 25.0)
    radius2 = a.get('radius2', 10.0)
    points = a.get('points', 6)
    lines = [
        's = rt.Star(name={!r}, radius1={:.3f}, radius2={:.3f}, points={})'.format(
            name, float(radius1), float(radius2), int(points),
        ),
    ]
    return '\n'.join(lines)


def _build_create_arc(a):
    name = _name_or_tmp(a)
    radius = a.get('radius', 25.0)
    from_angle = a.get('from', 0)
    to_angle = a.get('to', 90)
    lines = [
        'a = rt.Arc(name={!r}, radius={:.3f}, from={!r}, to={!r})'.format(
            name, float(radius), int(from_angle), int(to_angle),
        ),
    ]
    return '\n'.join(lines)


def _build_create_helix(a):
    name = _name_or_tmp(a)
    radius1 = a.get('radius1', 10.0)
    radius2 = a.get('radius2', 10.0)
    height = a.get('height', 100.0)
    turns = a.get('turns', 3.0)
    lines = [
        'h = rt.Helix(name={!r}, radius1={:.3f}, radius2={:.3f}, height={:.3f}, turns={:.3f})'.format(
            name, float(radius1), float(radius2), float(height), float(turns),
        ),
    ]
    return '\n'.join(lines)


# -- 变换 --------------------------------------------------------------- #

def _build_move_object(a):
    name = a.get('name', '')
    pos = a.get('pos', [0.0, 0.0, 0.0])
    return "rt.getNodeByName({!r}).pos = {}".format(name, _point3_str(pos))


def _build_rotate_object(a):
    name = a.get('name', '')
    axis = a.get('axis', 'z')
    angle = a.get('angle', 0.0)
    return (
        "rt.getNodeByName({!r}).rotation = "
        "rt.EulerAngles(x=0,y=0,z={})")
    # axis 简化：目前只处理绕 Z 轴


def _build_scale_object(a):
    name = a.get('name', '')
    scale = a.get('scale', [1.0, 1.0, 1.0])
    return "rt.getNodeByName({!r}).scale = {}".format(
        name, _point3_str(scale),
    )


def _build_delete_object(a):
    name = a.get('name', '')
    return "rt.delete(rt.getNodeByName({!r}))".format(name)


def _build_clone_object(a):
    name = a.get('name', '')
    new_name = a.get('new_name', '')
    copy = a.get('copy', False)
    lines = [
        "o = rt.getNodeByName({!r})".format(name),
    ]
    if copy:
        lines.append("c = rt.copy(o)")
    else:
        lines.append("c = rt.instance(o)")
    if new_name:
        lines.append("c.name = {!r}".format(new_name))
    return '\n'.join(lines)


# -- 修改器 ------------------------------------------------------------- #

def _build_add_modifier(a):
    name = a.get('name', '')
    modifier = a.get('modifier', '')
    return (
        "rt.addModifier(rt.getNodeByName({!r}), rt.{})"
    ).format(name, modifier)


def _build_remove_modifier(a):
    name = a.get('name', '')
    index = a.get('index', 0)
    return "rt.deleteModifier(rt.getNodeByName({!r}), {})".format(name, int(index) + 1)


def _build_set_modifier_param(a):
    name = a.get('name', '')
    index = a.get('index', 0)
    param = a.get('param', '')
    value = a.get('value', 0)
    lines = [
        "obj = rt.getNodeByName({!r})".format(name),
        "modif = obj.modifiers[{}]".format(int(index) + 1),
        "modif.{} = {}".format(param, json.dumps(value)),
    ]
    return '\n'.join(lines)


# -- 材质 --------------------------------------------------------------- #

def _build_assign_material(a):
    name = a.get('name', '')
    material_name = a.get('material_name', '')
    lines = [
        "obj = rt.getNodeByName({!r})".format(name),
        "mat = rt.getNodeByName({!r})".format(material_name),
        "obj.material = mat",
    ]
    return '\n'.join(lines)


def _build_create_standard_material(a):
    name = a.get('name', '')
    diffuse = a.get('diffuse_color', [128, 128, 128])
    lines = [
        "mat = rt.StandardMaterial(name={!r})".format(name),
        "mat.diffuse = {}".format(_color_str(diffuse)),
        "rt.meditMaterials[1] = mat",
    ]
    return '\n'.join(lines)


def _build_set_material_color(a):
    name = a.get('name', '')
    color = a.get('color', [128, 128, 128])
    channel = a.get('channel', 'diffuse')
    lines = [
        "mat = rt.getNodeByName({!r})".format(name),
        "mat.{} = {}".format(channel, _color_str(color)),
    ]
    return '\n'.join(lines)


# -- 灯光 --------------------------------------------------------------- #

def _build_create_omni_light(a):
    name = _name_or_tmp(a)
    pos = a.get('pos', [0.0, 0.0, 100.0])
    color = a.get('color', [255, 255, 255])
    lines = [
        "l = rt.OmniLight(name={!r})".format(name),
        "l.pos = {}".format(_point3_str(pos)),
        "l.color = {}".format(_color_str(color)),
    ]
    return '\n'.join(lines)


def _build_create_target_spot(a):
    name = _name_or_tmp(a)
    pos = a.get('pos', [0.0, 0.0, 100.0])
    target_pos = a.get('target_pos', [0.0, 0.0, 0.0])
    lines = [
        "l = rt.TargetSpot(name={!r})".format(name),
        "l.pos = {}".format(_point3_str(pos)),
        "l.target.pos = {}".format(_point3_str(target_pos)),
    ]
    return '\n'.join(lines)


def _build_create_target_direct(a):
    name = _name_or_tmp(a)
    pos = a.get('pos', [50.0, 50.0, 100.0])
    target_pos = a.get('target_pos', [0.0, 0.0, 0.0])
    lines = [
        "l = rt.TargetDirect(name={!r})".format(name),
        "l.pos = {}".format(_point3_str(pos)),
        "l.target.pos = {}".format(_point3_str(target_pos)),
    ]
    return '\n'.join(lines)


def _build_create_area_light(a):
    name = _name_or_tmp(a)
    pos = a.get('pos', [0.0, 0.0, 100.0])
    lines = [
        "l = rt.AreaLight(name={!r})".format(name),
        "l.pos = {}".format(_point3_str(pos)),
    ]
    return '\n'.join(lines)


def _build_create_sun_light(a):
    name = _name_or_tmp(a)
    pos = a.get('pos', [100.0, 100.0, 200.0])
    lines = [
        "l = rt.SunLight(name={!r})".format(name),
        "l.pos = {}".format(_point3_str(pos)),
    ]
    return '\n'.join(lines)


# -- 属性 / 场景 -------------------------------------------------------- #

def _build_set_object_property(a):
    name = a.get('name', '')
    prop = a.get('property', '')
    value = a.get('value', '')
    return "rt.getNodeByName({!r}).{} = {}".format(
        name, prop, json.dumps(value),
    )


def _build_rename_object(a):
    name = a.get('name', '')
    new_name = a.get('new_name', '')
    return "rt.getNodeByName({!r}).name = {!r}".format(name, new_name)


# -- 代码执行 ----------------------------------------------------------- #

def _build_run_python(a):
    code = a.get('code', '')
    if not code:
        return '# (empty python code)'
    lines = ['# ---- run_python ----']
    lines.extend(code.split('\n'))
    return '\n'.join(lines)


def _build_run_maxscript(a):
    code = a.get('code', '')
    if not code:
        return '# (empty maxscript code)'
    lines = ['# ---- run_maxscript ----']
    lines.append("rt.execute({!r})".format(code))
    return '\n'.join(lines)


def _build_run_python_maxscript(a):
    # MaxScript 里调 Python
    code = a.get('code', '')
    if not code:
        return ''
    return "python.Execute({!r})".format(code)


# ---------------------------------------------------------------------- #
# MacroRecorder 主类
# ---------------------------------------------------------------------- #

_QUERY_TOOL_PREFIXES = (
    'list_', 'get_', 'query_', 'count_', 'find_',
    'is_', 'has_', 'check_', 'describe_',
)


# ---------------------------------------------------------------------- #
# 语义化描述器：把 (tool_name, args) 翻译成一句中文自然语言
# ---------------------------------------------------------------------- #

_GEOM_KIND_ZH = {
    'create_box': '立方体',
    'create_sphere': '球体',
    'create_cylinder': '圆柱',
    'create_cone': '圆锥',
    'create_torus': '圆环',
    'create_plane': '平面',
    'create_teapot': '茶壶',
    'create_tube': '管道',
    'create_pyramid': '金字塔',
    'create_text': '文本',
    'create_circle': '圆形样条',
    'create_rectangle': '矩形样条',
    'create_ellipse': '椭圆样条',
    'create_star': '星形样条',
    'create_arc': '弧形样条',
    'create_helix': '螺旋线',
}

_LIGHT_KIND_ZH = {
    'create_omni_light': '泛光灯',
    'create_target_spot': '目标聚光灯',
    'create_target_direct': '目标平行光',
    'create_area_light': '面光源',
    'create_sun_light': '日光',
    'create_light': '灯光',
}


def _pos_txt(args):
    """把 [x,y,z] 参数格式化为紧凑的中文位置描述，参数缺失返回空串。"""
    pos = args.get('position') or args.get('pos') or args.get('location')
    if not pos:
        return ''
    try:
        x, y, z = pos[0], pos[1], pos[2]
        return '(位于 {:g},{:g},{:g})'.format(float(x), float(y), float(z))
    except Exception:  # pylint: disable=broad-except
        return ''


def _name_txt(args, key='name'):
    n = args.get(key)
    if not n:
        return ''
    return '「{}」'.format(str(n))


def describe_action(tool_name, args):
    # type: (str, Dict[str, Any]) -> str
    """把一次工具调用翻译成一句中文自然语言描述。

    覆盖不到的工具会返回泛化描述，永远不抛异常，永远不返回空串。
    这是导出脚本注释、Skill 描述、UI 步骤列表的公共语义层。
    """
    if not tool_name:
        return '（未知操作）'
    args = args or {}

    # 几何创建
    if tool_name in _GEOM_KIND_ZH:
        kind = _GEOM_KIND_ZH[tool_name]
        name = _name_txt(args)
        pos = _pos_txt(args)
        size_parts = []
        for k, zh in (
            ('length', '长'),
            ('width', '宽'),
            ('height', '高'),
            ('radius', '半径'),
            ('radius1', '外径'),
            ('radius2', '内径'),
        ):
            v = args.get(k)
            if v not in (None, ''):
                try:
                    size_parts.append('{}={:g}'.format(zh, float(v)))
                except Exception:  # pylint: disable=broad-except
                    pass
        size = ('（{}）'.format('，'.join(size_parts))) if size_parts else ''
        return '创建{}{}{}{}'.format(kind, name, size, pos).strip()

    # 灯光
    if tool_name in _LIGHT_KIND_ZH:
        return '放置{}{}{}'.format(
            _LIGHT_KIND_ZH[tool_name], _name_txt(args), _pos_txt(args),
        ).strip()

    # 相机
    if tool_name in ('create_camera', 'create_target_camera',
                     'create_free_camera'):
        return '添加相机{}{}'.format(_name_txt(args), _pos_txt(args)).strip()

    # 变换
    if tool_name == 'move_object':
        mode = args.get('mode') or 'set'
        prefix = '平移' if mode == 'add' else '移动到'
        return '{}{}{}'.format(
            prefix, _name_txt(args), _pos_txt(args),
        ).strip()
    if tool_name == 'rotate_object':
        return '旋转{}'.format(_name_txt(args)).strip()
    if tool_name == 'scale_object':
        return '缩放{}'.format(_name_txt(args)).strip()
    if tool_name == 'align_to':
        target = args.get('target') or args.get('to')
        return '对齐{} → {}'.format(
            _name_txt(args), '「{}」'.format(target) if target else '目标',
        )
    if tool_name == 'reset_pivot':
        return '重置{}的轴心'.format(_name_txt(args)).strip()

    # 修改器
    if tool_name == 'add_modifier':
        mod = args.get('modifier') or args.get('type') or '修改器'
        return '给{}添加 {} 修改器'.format(
            _name_txt(args) or '对象', mod,
        )
    if tool_name == 'remove_modifier':
        return '移除{}上的修改器'.format(_name_txt(args) or '对象')
    if tool_name == 'set_modifier_param':
        p = args.get('param') or args.get('name') or ''
        v = args.get('value')
        return '设置{}的修改器参数 {}={}'.format(
            _name_txt(args) or '对象', p, v,
        )
    if tool_name == 'collapse_stack':
        return '塌陷{}的修改器堆栈'.format(_name_txt(args) or '对象')

    # 材质
    if tool_name == 'create_standard_material':
        return '创建标准材质{}'.format(_name_txt(args))
    if tool_name == 'create_physical_material':
        return '创建物理材质{}'.format(_name_txt(args))
    if tool_name == 'set_material_color':
        return '设置材质{}颜色'.format(_name_txt(args))
    if tool_name == 'assign_material':
        return '把材质赋给{}'.format(_name_txt(args) or '对象')
    if tool_name == 'add_diffuse_map':
        return '给材质{}添加漫反射贴图'.format(_name_txt(args))
    if tool_name == 'generate_material_variants':
        return '生成材质变体'

    # 选择 / 显示
    if tool_name == 'select_objects':
        names = args.get('names') or []
        return '选中 {} 个对象'.format(len(names)) if names else '选中对象'
    if tool_name == 'clear_selection':
        return '清空选择'
    if tool_name == 'set_object_visibility':
        vis = args.get('visible')
        return '{}对象{}'.format(
            '显示' if vis else '隐藏', _name_txt(args) or '',
        )
    if tool_name == 'set_object_frozen':
        return '{}对象{}'.format(
            '冻结' if args.get('frozen') else '解冻',
            _name_txt(args) or '',
        )

    # 组织 / 命名
    if tool_name == 'group_objects':
        return '编组 {}'.format(args.get('group_name') or '对象')
    if tool_name == 'rename_object':
        return '重命名{} → {}'.format(
            _name_txt(args, 'old_name') or '对象',
            args.get('new_name') or args.get('name') or '',
        )

    # 视口 / 渲染
    if tool_name == 'set_viewport_camera':
        return '视口切换到相机 {}'.format(args.get('camera') or '')
    if tool_name == 'set_viewport_view':
        return '切换视图 {}'.format(args.get('view') or '')
    if tool_name == 'set_render_resolution':
        return '设置渲染分辨率 {}×{}'.format(
            args.get('width') or '?', args.get('height') or '?',
        )
    if tool_name == 'render_current_frame':
        return '渲染当前帧'
    if tool_name == 'render_animation':
        return '渲染动画序列'

    # 场景 IO
    if tool_name == 'save_max_file':
        return '保存 max 文件'
    if tool_name == 'load_max_file':
        return '打开 max 文件'
    if tool_name == 'merge_max_file':
        return '合并 max 文件'
    if tool_name == 'import_file':
        return '导入外部文件'
    if tool_name == 'export_file':
        return '导出场景'
    if tool_name == 'delete_objects':
        names = args.get('names') or []
        return '删除 {} 个对象'.format(len(names)) if names else '删除对象'

    # 自由脚本
    if tool_name == 'run_python':
        code = str(args.get('code') or '')
        head = code.strip().splitlines()[0] if code.strip() else ''
        head = head[:40] + ('…' if len(head) > 40 else '')
        return '执行 Python 片段：{}'.format(head) if head else '执行 Python 片段'
    if tool_name == 'run_maxscript':
        code = str(args.get('code') or '')
        head = code.strip().splitlines()[0] if code.strip() else ''
        head = head[:40] + ('…' if len(head) > 40 else '')
        return '执行 MaxScript 片段：{}'.format(head) if head else '执行 MaxScript 片段'

    # 泛化兜底：把 tool_name 前缀翻成动词
    if tool_name.startswith('create_'):
        return '创建 {}'.format(tool_name[7:].replace('_', ' '))
    if tool_name.startswith('set_'):
        return '设置 {}'.format(tool_name[4:].replace('_', ' '))
    if tool_name.startswith('add_'):
        return '添加 {}'.format(tool_name[4:].replace('_', ' '))
    if tool_name.startswith('delete_') or tool_name.startswith('remove_'):
        return '删除 {}'.format(
            tool_name.split('_', 1)[1].replace('_', ' '),
        )
    return '执行 {}'.format(tool_name)


def summarize_actions(actions):
    # type: (List[RecordedAction]) -> str
    """把整段动作序列聚合成一句中文摘要，用于 Skill 描述 / UI 标题。

    策略：按语义类别（创建 / 变换 / 材质 / 修改器 / 其他）分组计数，
    产生形如「创建 3 个几何体 + 添加 2 个修改器 + 赋 1 个材质」的
    紧凑描述。空列表返回空串。
    """
    if not actions:
        return ''
    buckets = {
        '创建几何体': 0,
        '放置灯光': 0,
        '添加相机': 0,
        '变换对象': 0,
        '添加修改器': 0,
        '处理材质': 0,
        '视口/渲染': 0,
        '场景 IO': 0,
        '脚本片段': 0,
        '其他': 0,
    }
    for a in actions:
        n = a.tool_name
        if n in _GEOM_KIND_ZH:
            buckets['创建几何体'] += 1
        elif n in _LIGHT_KIND_ZH:
            buckets['放置灯光'] += 1
        elif n in ('create_camera', 'create_target_camera',
                   'create_free_camera'):
            buckets['添加相机'] += 1
        elif n in ('move_object', 'rotate_object', 'scale_object',
                   'align_to', 'reset_pivot'):
            buckets['变换对象'] += 1
        elif n in ('add_modifier', 'remove_modifier',
                   'set_modifier_param', 'collapse_stack'):
            buckets['添加修改器'] += 1
        elif n in ('assign_material', 'create_standard_material',
                   'create_physical_material', 'set_material_color',
                   'add_diffuse_map', 'generate_material_variants'):
            buckets['处理材质'] += 1
        elif n in ('set_viewport_camera', 'set_viewport_view',
                   'set_render_resolution', 'render_current_frame',
                   'render_animation'):
            buckets['视口/渲染'] += 1
        elif n in ('save_max_file', 'load_max_file', 'merge_max_file',
                   'import_file', 'export_file'):
            buckets['场景 IO'] += 1
        elif n in ('run_python', 'run_maxscript'):
            buckets['脚本片段'] += 1
        else:
            buckets['其他'] += 1

    parts = []
    for label, cnt in buckets.items():
        if cnt > 0:
            parts.append('{} {}'.format(label, cnt))
    if not parts:
        return ''
    return ' + '.join(parts)


class MacroRecorder(object):
    """会话级操作记录器。

    用法（由 AgentWorker 每轮调用）：
        recorder = MacroRecorder()
        recorder.record(tool_name, arguments, success=True)
        ...
        script = recorder.to_python_script()
    """

    def __init__(self, session_id='', title=''):
        self._session = MacroSession(
            session_id=session_id,
            title=title,
        )
        self._order_counter = 0

    # ------------------------------------------------------------------ #
    # 记录接口
    # ------------------------------------------------------------------ #

    def record(self, tool_name, arguments, success=True):
        # type: (str, Dict[str, Any], bool) -> None
        """记录一次工具调用。

        只记录"修改场景"的操作，跳过查询类工具。
        """
        if tool_name.startswith(_QUERY_TOOL_PREFIXES):
            return
        self._order_counter += 1
        self._session.actions.append(RecordedAction(
            tool_name=tool_name,
            arguments=dict(arguments) if arguments else {},
            success=success,
            order=self._order_counter,
        ))

    def record_batch(self, tool_calls):
        # type: (List[Dict[str, Any]]) -> None
        """从 LLM 返回的 tool_calls 列表批量记录。"""
        for tc in tool_calls:
            fn = tc.get('function', {})
            self.record(
                tool_name=fn.get('name', ''),
                arguments=fn.get('arguments', {}),
                success=True,  # 实际结果由调用方更新
            )

    def update_last_success(self, ok):
        """更新最后一条记录的执行结果状态。"""
        if self._session.actions:
            self._session.actions[-1].success = bool(ok)

    # ------------------------------------------------------------------ #
    # 导出接口
    # ------------------------------------------------------------------ #

    def to_python_script(self):
        # type: () -> str
        """导出为 pymxs Python 脚本。

        :returns: 可直接在 Max 的 Python 环境中执行的脚本字符串。
        """
        summary = summarize_actions(self._session.actions)
        lines = [
            '# -*- coding: utf-8 -*-',
            '# Auto-generated by MaxAgent Macro Recorder',
            '# Timestamp: {}'.format(
                time.strftime('%Y-%m-%d %H:%M:%S'),
            ),
            '# Session: {}'.format(self._session.session_id),
        ]
        if self._session.title:
            lines.append('# Title  : {}'.format(self._session.title))
        if summary:
            lines.append('# 摘要   : {}'.format(summary))
        # 语义化步骤大纲：让用户一眼看清脚本做了什么，无需读代码
        if self._session.actions:
            lines.append('#')
            lines.append('# 步骤大纲：')
            for act in self._session.actions:
                mark = '✓' if act.success else '✗'
                try:
                    desc = describe_action(act.tool_name, act.arguments)
                except Exception:  # pylint: disable=broad-except
                    desc = act.tool_name
                lines.append('#  {} {}. {}'.format(mark, act.order, desc))
        lines.extend([
            '',
            'import pymxs',
            'rt = pymxs.runtime',
            '',
        ])
        for act in self._session.actions:
            try:
                desc = describe_action(act.tool_name, act.arguments)
            except Exception:  # pylint: disable=broad-except
                desc = ''
            step_header = '# ---- step {}: {}{} ----'.format(
                act.order, desc or act.tool_name,
                (' [{}]'.format(act.tool_name)) if desc else '',
            )
            if not act.success:
                lines.append(
                    '# ---- [FAIL] step {}: {}{} ----'.format(
                        act.order, desc or act.tool_name,
                        (' [{}]'.format(act.tool_name)) if desc else '',
                    ),
                )
                lines.append(
                    '# args = {}'.format(
                        json.dumps(act.arguments, ensure_ascii=False),
                    ),
                )
                lines.append('')
                continue

            builder = _PYMXS_TOOL_MAP.get(act.tool_name)
            if builder is None:
                lines.append(
                    '# ---- [UNMAPPED] step {}: {}{} ----'.format(
                        act.order, desc or act.tool_name,
                        (' [{}]'.format(act.tool_name)) if desc else '',
                    ),
                )
                lines.append(
                    '# args = {}'.format(
                        json.dumps(act.arguments, ensure_ascii=False),
                    ),
                )
                lines.append('')
                continue

            lines.append(step_header)
            try:
                snippet = builder(act.arguments)
                lines.append(snippet)
            except Exception as exc:  # pylint: disable=broad-except
                lines.append(
                    '# !! build failed: {}'.format(exc),
                )
            lines.append('')

        return '\n'.join(lines)

    def to_maxscript(self):
        # type: () -> str
        """导出为 MaxScript 脚本。"""
        summary = summarize_actions(self._session.actions)
        lines = [
            '/* Auto-generated by MaxAgent Macro Recorder */',
            '/* Timestamp: {} */'.format(
                time.strftime('%Y-%m-%d %H:%M:%S'),
            ),
            '/* Session: {} */'.format(self._session.session_id),
        ]
        if self._session.title:
            lines.append('/* Title  : {} */'.format(self._session.title))
        if summary:
            lines.append('/* 摘要   : {} */'.format(summary))
        if self._session.actions:
            lines.append('/*')
            lines.append(' * 步骤大纲：')
            for act in self._session.actions:
                mark = '✓' if act.success else '✗'
                try:
                    desc = describe_action(act.tool_name, act.arguments)
                except Exception:  # pylint: disable=broad-except
                    desc = act.tool_name
                lines.append(' *  {} {}. {}'.format(mark, act.order, desc))
            lines.append(' */')
        lines.append('')
        for act in self._session.actions:
            try:
                desc = describe_action(act.tool_name, act.arguments)
            except Exception:  # pylint: disable=broad-except
                desc = ''
            step_header = '/* step {}: {}{} */'.format(
                act.order, desc or act.tool_name,
                (' [{}]'.format(act.tool_name)) if desc else '',
            )
            if not act.success:
                lines.append(
                    '/* [FAIL] step {}: {}{} */'.format(
                        act.order, desc or act.tool_name,
                        (' [{}]'.format(act.tool_name)) if desc else '',
                    ),
                )
                lines.append('')
                continue

            builder = _MAXSCRIPT_TOOL_MAP.get(act.tool_name)
            if builder is not None:
                snippet = builder(act.arguments)
            else:
                # 通用 pymxs 调用在 MaxScript 中不存在，
                # 尝试用 python.Execute 包装
                snippet = (
                    'python.Execute("'
                    'import pymxs; rt = pymxs.runtime; '
                    "rt.getNodeByName('{}')"
                    '")'
                ).format(act.arguments.get('name', ''))

            if snippet:
                lines.append(step_header)
                lines.append(snippet)
                lines.append('')

        return '\n'.join(lines)

    def to_json(self):
        # type: () -> str
        """导出为 JSON 记录（完整无损）。"""
        return json.dumps(
            self._session.to_dict(),
            ensure_ascii=False,
            indent=2,
        )

    def to_semantic_summary(self):
        # type: () -> str
        """返回整段宏的一句中文摘要，用作 Skill 描述兜底。

        形如「创建几何体 3 + 添加修改器 2 + 处理材质 1」，无操作返回空串。
        """
        return summarize_actions(self._session.actions)

    def to_semantic_outline(self):
        # type: () -> List[str]
        """返回每步的中文语义描述列表，供 UI 步骤面板直接渲染。

        每项形如 "3. 创建立方体「Box01」（长=50，宽=30）(位于 0,0,0)"。
        """
        outline = []
        for act in self._session.actions:
            try:
                desc = describe_action(act.tool_name, act.arguments)
            except Exception:  # pylint: disable=broad-except
                desc = act.tool_name
            mark = '' if act.success else '（失败）'
            outline.append('{}. {}{}'.format(act.order, desc, mark))
        return outline

    # ------------------------------------------------------------------ #
    # 元数据 / 状态
    # ------------------------------------------------------------------ #

    def is_empty(self):
        # type: () -> bool
        return self._session.is_empty()

    def action_count(self):
        # type: () -> int
        return len(self._session.actions)

    def set_title(self, title):
        # type: (str) -> None
        self._session.title = title

    @property
    def session(self):
        return self._session


# ---------------------------------------------------------------------- #
# 保存 / 加载 持久化
# ---------------------------------------------------------------------- #

_MACRO_DIR = os.path.join(os.path.expanduser('~'), '.maxagent', 'macros')


def _ensure_macro_dir():
    try:
        os.makedirs(_MACRO_DIR, exist_ok=True)
    except Exception:  # pylint: disable=broad-except
        pass


def save_macro(session_id, recorder_or_session, fmt='py'):
    # type: (str, Any, str) -> Optional[str]
    """把 MacroRecorder 或 MacroSession 保存到磁盘。

    :param session_id: 会话 ID，用作文件名前缀
    :param recorder_or_session: MacroRecorder 或 MacroSession 实例
    :param fmt: 'py' | 'ms' | 'json'
    :returns: 保存的文件路径，失败返回 None
    """
    if recorder_or_session is None:
        return None
    _ensure_macro_dir()

    if isinstance(recorder_or_session, MacroRecorder):
        session = recorder_or_session.session
    else:
        session = recorder_or_session

    ts = time.strftime('%Y%m%d_%H%M%S')
    fname = 'macro_{}_{}.{}'.format(session_id[-8:], ts, fmt)
    fpath = os.path.join(_MACRO_DIR, fname)

    try:
        if fmt == 'py':
            content = recorder_or_session.to_python_script()
        elif fmt == 'ms':
            content = recorder_or_session.to_maxscript()
        else:
            content = recorder_or_session.to_json()

        with open(fpath, 'w', encoding='utf-8') as fh:
            fh.write(content)
        return fpath
    except Exception:  # pylint: disable=broad-except
        return None


def list_saved_macros():
    # type: () -> List[str]
    """列出所有已保存的宏脚本文件名。"""
    _ensure_macro_dir()
    try:
        return sorted(
            f for f in os.listdir(_MACRO_DIR)
            if f.startswith('macro_')
        )
    except Exception:  # pylint: disable=broad-except
        return []
