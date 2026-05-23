#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""禁用名单（工具 / 技能）管理。

设计要点：
1. **非侵入**：被禁用的工具 / 技能源文件原封不动，仅在
   ``{config_dir}/disabled.json`` 维护一份黑名单。删除该文件即可恢复。
2. **LLM 完全感知不到禁用项**：
   - 工具：``build_openai_tools_schema`` 过滤掉禁用项 → LLM 看不到 schema；
     ``dispatcher.dispatch`` 同样拦截，防止历史会话里残留的 tool_call_id
     再次调用已禁用工具。
   - 技能：``SkillManager._scan()`` / 系统提示注入器都跳过禁用项，
     LLM 既看不到技能简介，也无法被触发关键词命中。
3. **规则不在这里**：规则有自己的 ``enabled`` 字段（见
   ``user_rules_loader.update_rule(enabled=...)``），直接复用即可。

为什么不内联到 user_tools_loader / skills.py？
- 把"是否启用"这一**用户偏好**与"工具/技能定义"这一**资源**解耦：
  导入资源包时不需要带启用状态（避免对方机器上一上来全是禁用的）；
  本机迁移配置目录时禁用状态自动跟随。
- 单元测试时可以用 ``set_disabled_path_override`` 把存档指到临时目录，
  彻底隔离测试环境。

文件结构::

    {config_dir}/disabled.json
    {
      "schema_version": 1,
      "tools":  ["foo_tool", "bar_tool"],
      "skills": ["my_export_flow"]
    }
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os
import threading
from typing import List
from typing import Optional
from typing import Set

from .config import get_config_dir
from .logger import get_logger


logger = get_logger(__name__)


DISABLED_FILENAME = 'disabled.json'
SCHEMA_VERSION = 1

# 测试期可临时改写存档路径
_OVERRIDE_PATH = None  # type: Optional[str]

# 进程内缓存：避免每次调用 ``is_disabled`` 都打开磁盘文件
# 调用 ``add`` / ``remove`` / ``set_disabled`` 后会失效重读
_CACHE_LOCK = threading.RLock()
_CACHE = None  # type: Optional[dict]


def set_disabled_path_override(path):
    # type: (Optional[str]) -> None
    """单元测试用：把存档路径改到任意位置。传 None 恢复默认。"""
    global _OVERRIDE_PATH  # pylint: disable=global-statement
    _OVERRIDE_PATH = path
    invalidate_cache()


def get_disabled_path():
    # type: () -> str
    """返回当前 disabled.json 的绝对路径。"""
    if _OVERRIDE_PATH:
        return _OVERRIDE_PATH
    return os.path.join(get_config_dir(), DISABLED_FILENAME)


def invalidate_cache():
    """强制下次访问重新读盘（add/remove 后内部会自动调用）。"""
    global _CACHE  # pylint: disable=global-statement
    with _CACHE_LOCK:
        _CACHE = None


def _load():
    # type: () -> dict
    """带缓存读盘。返回值是只读视图，禁止外部修改。"""
    global _CACHE  # pylint: disable=global-statement
    with _CACHE_LOCK:
        if _CACHE is not None:
            return _CACHE
        path = get_disabled_path()
        data = {
            'schema_version': SCHEMA_VERSION,
            'tools': [],
            'skills': [],
        }
        if os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    # 容错：未知字段忽略；列表项强制成字符串
                    data['tools'] = [
                        str(x) for x in (raw.get('tools') or [])
                        if isinstance(x, (str, bytes))
                    ]
                    data['skills'] = [
                        str(x) for x in (raw.get('skills') or [])
                        if isinstance(x, (str, bytes))
                    ]
            except (OSError, ValueError) as exc:
                logger.warning(
                    'disabled.json 读取失败，按"无禁用项"处理: %s', exc,
                )
        _CACHE = data
        return data


def _save(data):
    # type: (dict) -> None
    path = get_disabled_path()
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    payload = {
        'schema_version': SCHEMA_VERSION,
        'tools': sorted(set(data.get('tools') or [])),
        'skills': sorted(set(data.get('skills') or [])),
    }
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    if os.path.exists(path):
        os.replace(tmp, path)
    else:
        os.rename(tmp, path)
    invalidate_cache()


# ---------------------------------------------------------------------- #
# 公共 API
# ---------------------------------------------------------------------- #

def list_disabled_tools():
    # type: () -> List[str]
    """返回当前所有被禁用的工具名（已排序去重）。"""
    return sorted(set(_load().get('tools') or []))


def list_disabled_skills():
    # type: () -> List[str]
    """返回当前所有被禁用的技能名（已排序去重）。"""
    return sorted(set(_load().get('skills') or []))


def get_disabled_tools_set():
    # type: () -> Set[str]
    """O(1) 查询用：返回禁用工具集合。"""
    return set(_load().get('tools') or [])


def get_disabled_skills_set():
    # type: () -> Set[str]
    """O(1) 查询用：返回禁用技能集合。"""
    return set(_load().get('skills') or [])


def is_tool_disabled(name):
    # type: (str) -> bool
    if not name:
        return False
    return name in get_disabled_tools_set()


def is_skill_disabled(name):
    # type: (str) -> bool
    if not name:
        return False
    return name in get_disabled_skills_set()


def set_tool_disabled(name, disabled):
    # type: (str, bool) -> None
    """切换某工具的禁用状态。无变化时不写盘。"""
    name = (name or '').strip()
    if not name:
        return
    data = dict(_load())
    cur = set(data.get('tools') or [])
    if disabled:
        if name in cur:
            return
        cur.add(name)
        logger.info('disable tool: %s', name)
    else:
        if name not in cur:
            return
        cur.discard(name)
        logger.info('enable tool: %s', name)
    data['tools'] = list(cur)
    data['skills'] = list(data.get('skills') or [])
    _save(data)


def set_skill_disabled(name, disabled):
    # type: (str, bool) -> None
    """切换某技能的禁用状态。无变化时不写盘。"""
    name = (name or '').strip()
    if not name:
        return
    data = dict(_load())
    cur = set(data.get('skills') or [])
    if disabled:
        if name in cur:
            return
        cur.add(name)
        logger.info('disable skill: %s', name)
    else:
        if name not in cur:
            return
        cur.discard(name)
        logger.info('enable skill: %s', name)
    data['skills'] = list(cur)
    data['tools'] = list(data.get('tools') or [])
    _save(data)


def clear_all():
    """清空全部禁用项（仅测试 / 用户手动恢复时使用）。"""
    _save({'tools': [], 'skills': []})


__all__ = [
    'DISABLED_FILENAME',
    'SCHEMA_VERSION',
    'set_disabled_path_override',
    'get_disabled_path',
    'invalidate_cache',
    'list_disabled_tools',
    'list_disabled_skills',
    'get_disabled_tools_set',
    'get_disabled_skills_set',
    'is_tool_disabled',
    'is_skill_disabled',
    'set_tool_disabled',
    'set_skill_disabled',
    'clear_all',
]
