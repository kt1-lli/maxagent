#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 工具共享辅助函数。

集中定义所有 maxagent.tools.maya.* 模块共用的:
- _ensure_in_maya: DCC 环境校验
- _normalize_names: 名称字符串/列表归一化
- _to_xyz_list: JSON/列表 -> (x, y, z) 元组
- _to_color: JSON/列表 -> (r, g, b) 元组
- _rollback_on_error: 失败自动 cmds.delete 兜底
- _parse_json_scalar: 字符串数字/bool/向量的宽松解析

原则:
- 只依赖 maxagent.dcc.runtime.current_dcc, 不主动 import maya.cmds,
  让 CI 与非 Maya 环境也能 import 该模块。
- 保持函数纯函数化, 错误统一抛 ValueError / RuntimeError。
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, Iterable, Iterator, List, Optional, Tuple

from ...dcc.runtime import current_dcc


# =========================================================================== #
# 环境校验
# =========================================================================== #
def ensure_in_maya():
    # type: () -> None
    """确保当前运行在 Maya 环境, 否则抛 RuntimeError。"""
    if current_dcc() != 'maya':
        raise RuntimeError('非 Maya 环境')


# 兼容旧下划线开头调用点
_ensure_in_maya = ensure_in_maya


# =========================================================================== #
# 名称归一化
# =========================================================================== #
_NAME_SEPARATORS = (',', ';', '\uff0c', '\uff1b')


def normalize_names(names):
    # type: (Any) -> List[str]
    """把 names 归一化为 list[str]。

    支持:
    - None -> []
    - str: 按 , ; ， ； 切分, 每个片段 strip
    - list/tuple: 保序去空
    - 其他: [str(names)]
    """
    if names is None:
        return []
    if isinstance(names, (list, tuple)):
        return [str(x).strip() for x in names if str(x).strip()]
    if isinstance(names, str):
        s = names.strip()
        if not s:
            return []
        for sep in _NAME_SEPARATORS:
            if sep in s:
                return [p.strip() for p in s.split(sep) if p.strip()]
        return [s]
    return [str(names)]


_normalize_names = normalize_names


# =========================================================================== #
# 数值/向量解析
# =========================================================================== #
def to_xyz_list(value, name='position'):
    # type: (Any, str) -> Optional[Tuple[float, float, float]]
    """把 [x, y, z] 列表/元组/JSON字符串转为三元组; 空字符串/None 返回 None。"""
    if value is None:
        return None
    coords = value
    if isinstance(coords, str):
        s = coords.strip()
        if not s:
            return None
        try:
            coords = json.loads(s)
        except json.JSONDecodeError as exc:
            raise ValueError(
                '{} 字符串不是合法 JSON: {} ({})'.format(name, value, exc),
            ) from exc
    try:
        if len(coords) != 3:
            raise ValueError(
                '{} 必须是包含 3 个数值的列表/元组: {}'.format(name, value),
            )
        return (float(coords[0]), float(coords[1]), float(coords[2]))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            '{} 参数解析失败: {} ({})'.format(name, value, exc),
        ) from exc


_to_xyz_list = to_xyz_list


def to_color(value):
    # type: (Any) -> Tuple[float, float, float]
    """把 [r, g, b] 转为三元组。支持 list/tuple 与 JSON 字符串。"""
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise ValueError('color 参数为空字符串')
        try:
            value = json.loads(s)
        except json.JSONDecodeError as exc:
            raise ValueError('color 字符串不是合法 JSON: {} ({})'.format(s, exc)) from exc
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise ValueError('color 必须是包含 3 个数值的列表: {}'.format(value))
    return (float(value[0]), float(value[1]), float(value[2]))


_to_color = to_color


def parse_scalar(value):
    # type: (Any) -> Any
    """宽松解析 JSON 标量字符串。

    - "2.0" -> 2.0
    - "1" -> 1
    - "true"/"false" -> bool
    - "[1,2,3]" -> [1, 2, 3]
    - 非字符串或解析失败 -> 原样返回
    """
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not s:
        return value
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return value


# =========================================================================== #
# 失败回滚
# =========================================================================== #
@contextlib.contextmanager
def rollback_on_error(names):
    # type: (Iterable[str]) -> Iterator[None]
    """代码块抛异常时, 尝试删除 names 中已存在的节点, 避免残留。

    使用示例::

        with rollback_on_error([name]):
            cmds.polyCube(name=name)
            cmds.xform(name, t=(1, 2, 3))
            _apply_transform(name, ...)

    注意: 仅在 Maya 环境有效; 非 Maya 环境静默 pass。
    """
    try:
        yield
    except Exception:
        if current_dcc() == 'maya':
            try:
                import maya.cmds as cmds  # type: ignore  # pylint: disable=import-error,import-outside-toplevel
                to_delete = [n for n in names if n and cmds.objExists(n)]
                if to_delete:
                    cmds.delete(to_delete)
            except Exception:  # pylint: disable=broad-except
                # 兜底路径本身失败时不要遮蔽原异常
                pass
        raise
