#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Max 类树与反射查询工具。

让 Agent 不用盲猜 API：直接查当前 Max 环境里有哪些类、
某个类有什么属性/方法、构造参数签名如何。

基于 pymxs 的反射能力：
- rt.getClassNames() / rt.getInterfaces()
- rt.classOf(obj)
- rt.classHierarchy / rt.superClassOf
- MAXScript 的 class 元数据（通过执行小片段 MAXScript 读取）

限制：只在 3ds Max 进程内有意义，非 Max 环境返回空结果。
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..logger import get_logger
from ..runtime_helpers import IN_MAX
from ..runtime_helpers import run_on_main
from ..runtime_helpers import rt
from .registry import tool


logger = get_logger(__name__)


def _ensure_in_max():
    if not IN_MAX:
        raise RuntimeError("非 3ds Max 环境")


# ---------------------------------------------------------------------- #
# 主线程内部：真正和 Max 反射接口打交道
# ---------------------------------------------------------------------- #

def _get_class_names_main(super_class: Optional[str], pattern: Optional[str], limit: int) -> List[Dict[str, str]]:
    """在主线程执行：获取类名列表。"""
    if not IN_MAX or rt is None:
        return []
    try:
        # getClassNames() 返回所有可构造类的 MAXClass 值数组
        names = rt.getClassNames()
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("getClassNames() 失败: %s", exc)
        return []

    items = []
    pat_lower = (pattern or "").lower()
    for cls in names:
        try:
            name = str(cls)
            if pat_lower and pat_lower not in name.lower():
                continue
            if super_class:
                try:
                    sc = str(rt.superClassOf(cls))
                    if super_class.lower() not in sc.lower():
                        continue
                except Exception:  # pylint: disable=broad-except
                    continue
            items.append({
                "name": name,
                "super_class": str(rt.superClassOf(cls)) if hasattr(rt, "superClassOf") else "",
            })
            if 0 < limit <= len(items):
                break
        except Exception:  # pylint: disable=broad-except
            continue
    return items


def _get_class_info_main(class_name: str) -> Dict[str, Any]:
    """在主线程执行：查询某个类的属性/方法/构造参数。"""
    if not IN_MAX or rt is None:
        return {"found": False, "name": class_name}

    try:
        # 尝试直接通过属性拿到 MAXClass，例如 rt.Box
        cls = getattr(rt, class_name, None)
        if cls is None:
            return {"found": False, "name": class_name, "error": "未找到该 MAXScript 类"}

        info = {"name": class_name, "found": True}
        try:
            info["super_class"] = str(rt.superClassOf(cls))
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            info["class_id"] = str(rt.classID(cls))
        except Exception:  # pylint: disable=broad-except
            pass

        # 通过 MAXScript 读取类的 properties / methods
        # 使用 execute 时要小心，class_name 必须做基本过滤
        safe_name = class_name.replace("'", "\"").replace(";", "")
        ms = '''
        cls = (execute "{0}")
        result = #()
        props = try(getPropNames cls) catch(#())
        meths = try(showMethods cls) catch("?")
        result = #(props, meths)
        '''.format(safe_name)
        try:
            out = rt.execute(ms)
            props = out[0] if out and len(out) > 0 else []
            meths = out[1] if out and len(out) > 1 else ""
            info["properties"] = [str(p) for p in list(props)[:50]]
            info["methods_preview"] = str(meths)[:500]
        except Exception as exc:  # pylint: disable=broad-except
            info["reflection_error"] = str(exc)

        # 可构造参数探测：试建一个实例读其属性（只对无参或带默认构造函数安全）
        try:
            instance = cls()
            info["constructible"] = True
            info["instance_class"] = str(rt.classOf(instance))
            try:
                info["instance_properties"] = [
                    str(p) for p in list(rt.getPropNames(instance))[:30]
                ]
            except Exception:  # pylint: disable=broad-except
                pass
        except Exception as exc:  # pylint: disable=broad-expect
            info["constructible"] = False
            info["construct_error"] = str(exc)

        return info
    except Exception as exc:  # pylint: disable=broad-except
        return {"found": False, "name": class_name, "error": str(exc)}


def _list_constructible_classes_main(super_class: Optional[str], pattern: Optional[str], limit: int) -> List[Dict[str, str]]:
    """只返回能成功 new 出来的类。"""
    all_items = _get_class_names_main(super_class, pattern, 0)
    results = []
    for item in all_items:
        name = item["name"]
        try:
            cls = getattr(rt, name, None)
            if cls is None:
                continue
            _ = cls()
            results.append({"name": name, "super_class": item["super_class"]})
            if 0 < limit <= len(results):
                break
        except Exception:  # pylint: disable=broad-except
            continue
    return results


# ---------------------------------------------------------------------- #
# LLM 可调工具
# ---------------------------------------------------------------------- #

@tool(
    description=(
        "列出 3ds Max 当前环境中的 MAXScript 类树。"
        "可指定 super_class（如 'GeometryClass', 'light', 'camera'）"
        "或 pattern 进行过滤。用于帮 Agent 发现某个功能对应的 MAXScript 类名。"
    ),
    category="reflection",
    wrap_undo=False,
    run_on_main_thread=True,
)
def list_class_tree(super_class: Optional[str] = None, pattern: Optional[str] = None, limit: int = 100):
    """列出 Max 类树。

    :param super_class: 超类过滤，例如 "GeometryClass" / "light" / "camera"
    :param pattern: 类名包含的子串，例如 "Box" / "Constraint"
    :param limit: 最大返回数量，避免 token 爆炸
    :returns: dict {"count": N, "items": [{name, super_class}]}
    """
    _ensure_in_max()
    items = run_on_main(
        _get_class_names_main, super_class, pattern, limit, _timeout=30.0,
    )
    return {"count": len(items), "items": items}


@tool(
    description=(
        "查询某个 MAXScript 类的反射信息：超类、属性、方法预览、是否可构造。"
        "配合 list_class_tree 使用，帮助 Agent 写出正确的 MAXScript/Pymxs 调用。"
    ),
    category="reflection",
    wrap_undo=False,
    run_on_main_thread=True,
)
def get_class_info(class_name: str):
    """查询单个类的反射信息。

    :param class_name: MAXScript 类名，例如 "Box" / "FFDBox" / "Position_XYZ"
    :returns: dict 包含 found / super_class / properties / methods_preview / constructible 等
    """
    _ensure_in_max()
    if not class_name or not str(class_name).strip():
        return {"found": False, "error": "class_name 不能为空"}
    return run_on_main(_get_class_info_main, class_name, _timeout=30.0)


@tool(
    description=(
        "列出当前 Max 环境中可以成功实例化的类。"
        "等价于 list_class_tree 但只返回能 'new' 出来的类。"
    ),
    category="reflection",
    wrap_undo=False,
    run_on_main_thread=True,
)
def list_constructible_classes(super_class: Optional[str] = None, pattern: Optional[str] = None, limit: int = 100):
    """列出可构造类。

    :param super_class: 超类过滤
    :param pattern: 类名子串过滤
    :param limit: 最大返回数
    :returns: dict {"count": N, "items": [{name, super_class}]}
    """
    _ensure_in_max()
    items = run_on_main(
        _list_constructible_classes_main, super_class, pattern, limit, _timeout=30.0,
    )
    return {"count": len(items), "items": items}


__all__ = [
    "list_class_tree",
    "get_class_info",
    "list_constructible_classes",
]
