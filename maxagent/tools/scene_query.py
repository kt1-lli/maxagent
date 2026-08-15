#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""场景查询类工具。

提供给 agent 的"读"能力：列举对象、查询选中、统计信息、查找等。
全部为只读操作，wrap_undo=False（无需 undo）。
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


# ---------------------------------------------------------------------- #
# 内部辅助：把 Max 节点转成 LLM 友好的 dict
# ---------------------------------------------------------------------- #

def _node_to_dict(node, detail=False):
    """节点 -> dict。

    :param detail: True 时附加更详细的字段（变换、材质、修改器列表等）
    """
    info = {
        "name": str(node.name),
        "class": str(rt.classOf(node)),
        "super_class": str(rt.superClassOf(node)),
        "is_hidden": bool(node.isHidden),
        "is_frozen": bool(node.isFrozen),
    }
    if detail:
        try:
            pos = node.position
            info["position"] = [float(pos.x), float(pos.y), float(pos.z)]
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            rot = node.rotation
            info["rotation_euler"] = [
                float(rt.quatToEuler(rot).x),
                float(rt.quatToEuler(rot).y),
                float(rt.quatToEuler(rot).z),
            ]
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            scl = node.scale
            info["scale"] = [float(scl.x), float(scl.y), float(scl.z)]
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            mat = node.material
            info["material"] = str(mat.name) if mat is not None else None
        except Exception:  # pylint: disable=broad-except
            info["material"] = None
        try:
            mods = []
            for i in range(1, int(node.modifiers.count) + 1):
                m = node.modifiers[i - 1]
                mods.append({"name": str(m.name), "class": str(rt.classOf(m))})
            info["modifiers"] = mods
        except Exception:  # pylint: disable=broad-except
            info["modifiers"] = []
        # 几何统计
        try:
            info["face_count"] = int(rt.getPolygonCount(node)[0])
        except Exception:  # pylint: disable=broad-except
            pass
    return info


def _ensure_in_max():
    if not IN_MAX:
        raise RuntimeError("非 3ds Max 环境")


# ---------------------------------------------------------------------- #
# 工具实现
# ---------------------------------------------------------------------- #

@tool(
    description="获取 3ds Max 当前版本与基本信息（版本号、产品名、当前打开的文件名等）。",
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '查询当前 Max 版本与文件路径',
            'args': {},
        },
    ],
    notes=[
        '无需任何参数，返回结果可能包含 version_year / product / current_file / current_dir 等字段。',
        '未保存场景时 current_file 通常为空白字符串或 "<未保存>"。',
    ],
    returns_desc='dict {"version_year": int | null, "product": str, "current_file": str, ...}',
)
def get_max_info():
    """获取 Max 基本信息。

    :returns: 包含 version_year / product / current_file 等字段的 dict
    """
    _ensure_in_max()
    info = {}
    try:
        ver = rt.maxVersion()
        for v in list(ver):
            try:
                iv = int(v)
                if 2000 <= iv <= 2100:
                    info["version_year"] = iv
                    break
            except (TypeError, ValueError):
                continue
    except Exception:  # pylint: disable=broad-except
        info["version_year"] = None
    try:
        info["product"] = str(rt.maxOps.productAppID)
    except Exception:  # pylint: disable=broad-except
        info["product"] = "3ds Max"
    try:
        info["current_file"] = str(rt.maxFileName) or "<未保存>"
        info["current_dir"] = str(rt.maxFilePath)
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        info["unit_type"] = str(rt.units.SystemType)
        info["unit_scale"] = float(rt.units.SystemScale)
    except Exception:  # pylint: disable=broad-except
        pass
    return info


@tool(
    description="统计当前场景的对象总数、灯光数、相机数、面数等。",
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '获取当前场景的对象与面数统计',
            'args': {},
        },
    ],
    notes=[
        '该工具为只读操作，不会修改场景。',
        'total_faces 仅统计可获取 polygon 数量的几何体，部分对象可能未被计入。',
    ],
    returns_desc=(
        'dict {"total": 总数, "geometry": 几何体数, "lights": 灯光数, '
        '"cameras": 相机数, "shapes": 形数, "helpers": 辅助体数, '
        '"others": 其他, "total_faces": 总面数}'
    ),
)
def get_scene_stats():
    """统计场景规模。

    :returns: dict 包含 total / lights / cameras / geometry / shapes / helpers / total_faces
    """
    _ensure_in_max()
    objs = list(rt.objects)
    counters = {
        "total": len(objs),
        "geometry": 0,
        "lights": 0,
        "cameras": 0,
        "shapes": 0,
        "helpers": 0,
        "others": 0,
        "total_faces": 0,
    }
    for obj in objs:
        try:
            sc = str(rt.superClassOf(obj))
        except Exception:  # pylint: disable=broad-except
            sc = ""
        if sc == "GeometryClass":
            counters["geometry"] += 1
            try:
                counters["total_faces"] += int(rt.getPolygonCount(obj)[0])
            except Exception:  # pylint: disable=broad-except
                pass
        elif sc == "light":
            counters["lights"] += 1
        elif sc == "camera":
            counters["cameras"] += 1
        elif sc == "shape":
            counters["shapes"] += 1
        elif sc == "helper":
            counters["helpers"] += 1
        else:
            counters["others"] += 1
    return counters


@tool(
    description=(
        "列出场景中的对象。可按 super_class（geometry/light/camera/shape/helper）过滤，"
        "可限制返回数量，避免上下文过载。"
    ),
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '列出前 20 个几何体',
            'args': {'super_class': 'geometry', 'limit': 20, 'detail': False},
        },
        {
            'summary': '列出所有灯光并返回详细信息',
            'args': {'super_class': 'light', 'limit': -1, 'detail': True},
        },
    ],
    notes=[
        'super_class 为空字符串 "" 时返回全部类型对象。',
        'limit <= 0 表示不限制返回数量，请谨慎在大型场景中使用。',
        'detail=True 会返回对象的变换、材质、修改器栈与面数等详细信息。',
    ],
    returns_desc='dict {"count": 返回数, "total": 场景总数, "items": [对象信息 dict, ...]}',
)
def list_objects(super_class="", limit=50, detail=False):
    """列出场景对象。

    :param super_class: 过滤超类，可选值: ``""`` (全部), ``"geometry"``,
                        ``"light"``, ``"camera"``, ``"shape"``, ``"helper"``
    :param limit: 最多返回的对象数（防止 token 爆炸），<=0 表示不限
    :param detail: 是否返回详细信息（变换、材质、修改器栈），默认 False
    :returns: dict: {"count": 实际返回数, "total": 场景总数, "items": [...]}
    """
    _ensure_in_max()
    sc_map = {
        "": None,
        "geometry": "GeometryClass",
        "light": "light",
        "camera": "camera",
        "shape": "shape",
        "helper": "helper",
    }
    target_sc = sc_map.get((super_class or "").lower(), None)

    objs = list(rt.objects)
    items = []  # type: List[Dict[str, Any]]
    for obj in objs:
        if target_sc is not None:
            try:
                if str(rt.superClassOf(obj)) != target_sc:
                    continue
            except Exception:  # pylint: disable=broad-except
                continue
        items.append(_node_to_dict(obj, detail=detail))
        if 0 < limit <= len(items):
            break
    return {"count": len(items), "total": len(objs), "items": items}


@tool(
    description="获取当前选中的对象列表。",
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '获取当前选中对象的详细信息',
            'args': {'detail': True},
        },
        {
            'summary': '仅获取选中对象的基本名称与类名',
            'args': {'detail': False},
        },
    ],
    notes=[
        '若当前未选中任何对象，返回 {"count": 0, "items": []}。',
        'detail=True 会包含位置、旋转、缩放、材质、修改器等信息。',
    ],
    returns_desc='dict {"count": 选中数量, "items": [对象信息 dict, ...]}',
)
def get_selection(detail=True):
    """获取当前选中的对象。

    :param detail: 是否返回详细信息
    :returns: dict: {"count": N, "items": [...]}
    """
    _ensure_in_max()
    sel = list(rt.selection)
    items = [_node_to_dict(o, detail=detail) for o in sel]
    return {"count": len(items), "items": items}


@tool(
    description="按名字精确或模糊查找对象。",
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '模糊查找名称包含 Box 的对象',
            'args': {'pattern': 'Box', 'exact': False, 'limit': 10},
        },
        {
            'summary': '精确查找名为 Camera001 的相机',
            'args': {'pattern': 'Camera001', 'exact': True, 'detail': True},
        },
    ],
    notes=[
        'exact=False 时进行不区分大小写的子串匹配。',
        'pattern 为空字符串时直接返回空列表。',
        'limit 控制最多返回的对象数，避免上下文过载。',
    ],
    returns_desc='dict {"count": 命中数, "items": [对象信息 dict, ...]}',
)
def find_objects_by_name(pattern, exact=False, limit=50, detail=False):
    """按名字查找对象。

    :param pattern: 名字或子串
    :param exact: True 表示完全匹配；False 表示子串匹配（不区分大小写）
    :param limit: 最多返回数
    :param detail: 是否返回详细信息
    :returns: dict: {"count": N, "items": [...]}
    """
    _ensure_in_max()
    if not pattern:
        return {"count": 0, "items": []}
    needle = pattern if exact else pattern.lower()
    items = []
    for obj in rt.objects:
        try:
            name = str(obj.name)
        except Exception:  # pylint: disable=broad-except
            continue
        if exact:
            if name != needle:
                continue
        else:
            if needle not in name.lower():
                continue
        items.append(_node_to_dict(obj, detail=detail))
        if 0 < limit <= len(items):
            break
    return {"count": len(items), "items": items}


@tool(
    description="按名字获取单个对象的详细信息（变换、材质、修改器栈、面数等）。",
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '查询名为 MyBox 的对象详情',
            'args': {'name': 'MyBox'},
        },
    ],
    notes=[
        'name 必须精确匹配场景中已存在的对象名。',
        '找不到对象时返回 {"found": False, "name": 输入名称}。',
    ],
    returns_desc='dict {"found": True, "name": str, "class": str, "position": [...], ...}',
    prerequisites=['场景中必须存在名为 name 的对象'],
)
def get_object_info(name):
    """按名字获取对象详细信息。

    :param name: 对象名（必须精确）
    :returns: dict 详细信息；找不到时返回 {"found": False}
    """
    _ensure_in_max()
    obj = rt.getNodeByName(name, exact=True, all=False)
    if obj is None:
        return {"found": False, "name": name}
    info = _node_to_dict(obj, detail=True)
    info["found"] = True
    return info


@tool(
    description="获取活动视口的相机/视角信息（视图类型、焦距、视点位置等）。",
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '获取当前活动视口信息',
            'args': {},
        },
    ],
    notes=[
        '无需任何参数，返回结果可能包含 view_type / camera_name / fov / transform_row4 等字段。',
        '当活动视口不是相机视角时，camera_name 为 null。',
    ],
    returns_desc='dict {"view_type": str, "camera_name": str | null, "fov": float | null, ...}',
)
def get_active_viewport():
    """获取活动视口信息。

    :returns: dict 包含 view_type / fov / camera_name 等字段
    """
    _ensure_in_max()
    info = {}
    try:
        info["view_type"] = str(rt.viewport.getType())
    except Exception:  # pylint: disable=broad-except
        info["view_type"] = ""
    try:
        cam = rt.viewport.getCamera()
        info["camera_name"] = str(cam.name) if cam is not None else None
    except Exception:  # pylint: disable=broad-except
        info["camera_name"] = None
    try:
        info["fov"] = float(rt.viewport.getFOV())
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        tm = rt.viewport.getTM()
        info["transform_row4"] = [
            float(tm.row4.x), float(tm.row4.y), float(tm.row4.z),
        ]
    except Exception:  # pylint: disable=broad-except
        pass
    return info


@tool(
    description=(
        "获取动画时间范围与当前帧。返回 start / end / current / fps 等信息。"
    ),
    category="scene_query",
    wrap_undo=False,
    examples=[
        {
            'summary': '获取当前时间范围与帧率',
            'args': {},
        },
    ],
    notes=[
        '无需任何参数，返回结果可能包含 start / end / current / fps 字段。',
        '若场景未设置动画范围或无法读取，对应字段可能缺失。',
    ],
    returns_desc='dict {"start": int, "end": int, "current": int, "fps": int}',
)
def get_time_info():
    """获取时间/动画信息。

    :returns: dict: {"start": int, "end": int, "current": int, "fps": int}
    """
    _ensure_in_max()
    info = {}
    try:
        info["start"] = int(rt.animationRange.start.frame)
        info["end"] = int(rt.animationRange.end.frame)
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        info["current"] = int(rt.currentTime.frame)
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        info["fps"] = int(rt.frameRate)
    except Exception:  # pylint: disable=broad-except
        pass
    return info
