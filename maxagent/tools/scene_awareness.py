#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构化场景感知工具。

不依赖多模态 LLM，只从 3ds Max 场景中抽取结构化语义信息：
- 几何质量检查（ngon、非流形边、UV 重叠等）
- 场景语义图（层级、角色推断）
- 场景快照 Diff（语义化变化描述）

全部为只读操作，wrap_undo=False。
"""

from __future__ import absolute_import
from __future__ import print_function

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
    node = rt.getNodeByName(name, exact=True, all=False)
    if node is None:
        raise ValueError('对象不存在: {}'.format(name))
    return node


def _node_to_light_dict(node):
    # type: (Any) -> Dict[str, Any]
    """把节点转成轻量 dict。"""
    info = {
        'name': str(node.name),
        'class': str(rt.classOf(node)),
        'super_class': str(rt.superClassOf(node)),
    }
    try:
        pos = node.position
        info['position'] = [float(pos.x), float(pos.y), float(pos.z)]
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        info['parent'] = str(node.parent.name) if node.parent is not None else None
    except Exception:  # pylint: disable=broad-except
        pass
    return info


@tool(
    description=(
        '检查指定对象或当前选择集的几何质量。返回 ngon 面数、三角面数、'
        '顶点/边/面统计、是否含非流形边等信息。'
    ),
    category='scene_awareness',
    wrap_undo=False,
    examples=[{"summary": "典型调用", "args": {"object_names": 'value', "selected_only": False}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def check_mesh_quality(object_names=None, selected_only=False):
    """检查网格质量。

    :param object_names: 要检查的对象名列表；None 且 selected_only=False 时检查所有几何体
    :param selected_only: 是否只检查当前选择集
    :returns: dict {"checked": N, "items": [...]}
    """
    _ensure_in_max()
    nodes = []
    if selected_only:
        nodes = list(rt.selection)
    elif object_names:
        # 兼容 LLM 传 "Box01,Box02" 字符串或 ["Box01","Box02"] 数组
        if isinstance(object_names, str):
            object_names = [
                s.strip()
                for s in object_names.replace('，', ',').split(',')
                if s.strip()
            ]
        for name in object_names:
            node = rt.getNodeByName(name, exact=True, all=False)
            if node is not None:
                nodes.append(node)
    else:
        for obj in rt.objects:
            try:
                if str(rt.superClassOf(obj)) == 'GeometryClass':
                    nodes.append(obj)
            except Exception:  # pylint: disable=broad-except
                continue

    items = []
    for node in nodes:
        try:
            sc = str(rt.superClassOf(node))
        except Exception:  # pylint: disable=broad-except
            continue
        if sc != 'GeometryClass':
            continue
        item = {'name': str(node.name), 'class': str(rt.classOf(node))}

        # primitive（Box/Sphere/...）没有 mesh 数据，需要先取 mesh 快照
        # rt.snapshotAsMesh 返回一份只读的 TriMesh 副本，不修改场景
        mesh_obj = node
        try:
            snap = rt.snapshotAsMesh(node)
            if snap is not None:
                mesh_obj = snap
        except Exception:  # pylint: disable=broad-except
            pass

        try:
            face_info = rt.getPolygonCount(mesh_obj)
            item['face_count'] = int(face_info[0])
            item['triangle_count'] = int(face_info[1])
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            item['vertex_count'] = int(rt.getNumVerts(mesh_obj))
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            item['edge_count'] = int(rt.getNumEdges(mesh_obj))
        except Exception:  # pylint: disable=broad-except
            pass

        # ngon 检查：只对 Editable Poly 有效，primitive 快照转成 TriMesh
        # 后天然都是三角形；这里对 primitive 直接标记 ngon_count=0
        ngon_count = 0
        try:
            n_faces = int(rt.polyop.getNumFaces(node))
            for i in range(1, n_faces + 1):
                try:
                    deg = len(list(rt.polyop.getFaceVerts(node, i)))
                    if deg > 4:
                        ngon_count += 1
                except Exception:  # pylint: disable=broad-except
                    continue
        except Exception:  # pylint: disable=broad-except
            # primitive 类型没有 polyop 接口，视为 0 ngon
            pass
        item['ngon_count'] = ngon_count

        # 非流形边：仅对 Editable Poly 检查
        non_manifold_edges = 0
        try:
            edge_count = int(rt.polyop.getNumEdges(node))
            for i in range(1, edge_count + 1):
                try:
                    faces = list(rt.polyop.getEdgeFaces(node, i))
                    if len(faces) not in (1, 2):
                        non_manifold_edges += 1
                except Exception:  # pylint: disable=broad-except
                    continue
            item['non_manifold_edges'] = non_manifold_edges
        except Exception:  # pylint: disable=broad-except
            item['non_manifold_edges'] = 0

        # UV 统计
        try:
            uv_count = int(rt.polyop.getNumMapVerts(node, 1))
            face_count = int(rt.polyop.getNumFaces(node))
            item['uv_vertex_count'] = uv_count
            item['uv_face_count'] = face_count
            # 保守提示：uv 顶点远少于面顶点时可能存在重叠/未展开
            if 'vertex_count' in item and uv_count > 0:
                ratio = float(uv_count) / max(item['vertex_count'], 1)
                item['uv_to_vertex_ratio'] = round(ratio, 3)
        except Exception:  # pylint: disable=broad-except
            pass
        items.append(item)
    return {'checked': len(items), 'items': items}


@tool(
    description=(
        '构建当前场景的语义图。返回根对象、层级关系、按超类分组的摘要，'
        '以及基于命名和修改器推断的对象角色标签。'
    ),
    category='scene_awareness',
    wrap_undo=False,
    examples=[{'summary': '构建场景语义图', 'args': {}}],
    notes=[
        '构建场景语义图，用于理解对象之间的关系。',
        '返回结果通常包含对象名、类型、父子关系、材质等。',
    ],
    returns_desc='dict {"roots": [...], "groups": {...}, "objects": [...]}',
)
def build_scene_semantic_graph():
    """构建场景语义图。

    :returns: dict {"roots": [...], "groups": {...}, "objects": [...]}
    """
    _ensure_in_max()
    objs = list(rt.objects)
    objects = []
    children_map = {}  # parent_name -> [child_names]
    root_names = []

    for obj in objs:
        info = _node_to_light_dict(obj)
        # 修改器列表
        try:
            mods = []
            for i in range(int(obj.modifiers.count)):
                mod = obj.modifiers[i]
                mods.append(str(rt.classOf(mod)))
            info['modifiers'] = mods
        except Exception:  # pylint: disable=broad-except
            info['modifiers'] = []
        # 材质
        try:
            mat = obj.material
            info['material'] = str(mat.name) if mat is not None else None
        except Exception:  # pylint: disable=broad-except
            info['material'] = None
        # 角色推断
        info['tags'] = _infer_object_tags(info)
        objects.append(info)
        # 层级
        parent_name = info.get('parent')
        if parent_name is None:
            root_names.append(info['name'])
        else:
            children_map.setdefault(parent_name, []).append(info['name'])

    # 按超类分组统计
    groups = {}
    for info in objects:
        sc = info.get('super_class', 'other')
        groups.setdefault(sc, []).append(info['name'])

    # 层级树（仅根节点及其直接子级，避免过深）
    roots = []
    for name in root_names:
        roots.append({
            'name': name,
            'children': children_map.get(name, []),
        })

    return {
        'roots': roots,
        'groups': {k: len(v) for k, v in groups.items()},
        'object_count': len(objects),
        'objects': objects,
    }


def _infer_object_tags(info):
    # type: (Dict[str, Any]) -> List[str]
    """基于名字、类、修改器推断对象角色标签。"""
    tags = []
    name = info.get('name', '').lower()
    klass = info.get('class', '').lower()
    mods = [m.lower() for m in info.get('modifiers', [])]
    sc = info.get('super_class', '').lower()

    if sc == 'light':
        tags.append('lighting')
        if 'spot' in klass:
            tags.append('spot_light')
        elif 'omni' in klass or 'point' in klass:
            tags.append('point_light')
        elif 'dir' in klass:
            tags.append('directional_light')
    elif sc == 'camera':
        tags.append('camera')
    elif sc == 'geometryclass':
        tags.append('geometry')
        if any(k in name for k in ('lod', 'proxy', 'low', 'high')):
            tags.append('lod_mesh')
        if 'turbosmooth' in mods or 'meshsmooth' in mods:
            tags.append('subdivision')
        if 'skin' in mods:
            tags.append('rigged')
        if 'bend' in mods or 'twist' in mods or 'taper' in mods:
            tags.append('deformed')
        if any(k in name for k in ('tree', 'rock', 'building', 'car', 'weapon')):
            tags.append('prop')
        if any(k in name for k in ('hero', 'character', 'char_', 'chr_')):
            tags.append('character')
    elif sc == 'helper':
        tags.append('helper')
        if 'dummy' in klass:
            tags.append('dummy')
    elif sc == 'shape':
        tags.append('shape')

    return tags


@tool(
    description=(
        '对比两个场景快照（build_scene_snapshot 的输出），生成语义化 Diff 描述。'
        '返回新增、删除、位置变化的对象列表，以及一句人可读总结。'
    ),
    category='scene_awareness',
    wrap_undo=False,
    examples=[{"summary": "典型调用", "args": {"before": 'value', "after": 'value'}}],
notes=['参数必须严格符合 JSON Schema 声明的类型。', '调用失败时应先检查对象/文件是否存在。'],
returns_desc="dict {\"ok\": True, ...}"
)
def diff_scene_snapshots(before, after):
    """语义化对比两个场景快照。

    :param before: 之前快照 dict
    :param after: 之后快照 dict
    :returns: dict {"added": [...], "removed": [...], "moved": [...], "summary": str}
    """
    before_objs = {
        o.get('name'): o
        for o in (before or {}).get('objects', [])
        if o.get('name')
    }
    after_objs = {
        o.get('name'): o
        for o in (after or {}).get('objects', [])
        if o.get('name')
    }
    before_names = set(before_objs.keys())
    after_names = set(after_objs.keys())

    added_names = after_names - before_names
    removed_names = before_names - after_names
    common_names = before_names & after_names

    added = [after_objs[n] for n in sorted(added_names)]
    removed = [before_objs[n] for n in sorted(removed_names)]

    moved = []
    for name in sorted(common_names):
        b_pos = before_objs[name].get('position')
        a_pos = after_objs[name].get('position')
        if (
            isinstance(b_pos, (list, tuple))
            and isinstance(a_pos, (list, tuple))
            and len(b_pos) == 3
            and len(a_pos) == 3
        ):
            dx = a_pos[0] - b_pos[0]
            dy = a_pos[1] - b_pos[1]
            dz = a_pos[2] - b_pos[2]
            dist = (dx * dx + dy * dy + dz * dz) ** 0.5
            if dist > 0.01:
                moved.append({
                    'name': name,
                    'distance': round(dist, 3),
                    'from': list(b_pos),
                    'to': list(a_pos),
                })

    # 生成一句语义化总结
    parts = []
    if added:
        parts.append('新增了 {} 个对象'.format(len(added)))
    if removed:
        parts.append('删除了 {} 个对象'.format(len(removed)))
    if moved:
        parts.append('{} 个对象位置发生变化'.format(len(moved)))
    if not parts:
        summary = '场景没有明显变化。'
    else:
        summary = '，'.join(parts) + '。'
        # 尝试识别新增对象类型
        if added:
            class_counts = {}
            for obj in added:
                klass = obj.get('class', '未知')
                class_counts[klass] = class_counts.get(klass, 0) + 1
            top_class = max(class_counts, key=class_counts.get)
            summary += ' 新增对象以 {} 为主（{} 个）。'.format(
                top_class, class_counts[top_class],
            )

    return {
        'added': added,
        'removed': removed,
        'moved': moved,
        'summary': summary,
    }