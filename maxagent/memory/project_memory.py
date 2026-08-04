#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目级记忆（Project Memory）——按 .max 文件路径挂钩的长期上下文。

**动机**：一个 .max 场景往往会被用户反复打开、多次会话迭代。传统 LLM
Agent 每次开新会话都要用户重新解释「这个场景的命名约定/单位/主要对象/
上次做到哪里」。项目级记忆把这些"跟场景走"的信息按场景文件路径存到
本地磁盘（``~/.maxagent/projects/<hash>.json``），下次打开同一场景时
自动读回来注入 system prompt。

**存储**：JSON 文件，key = 场景路径的 SHA1；内容包含：

- ``scene_path``：完整路径（便于人肉排查）
- ``scene_name``：文件名
- ``updated_at``：ISO 时间戳
- ``notes``：``List[str]``——LLM 或用户显式沉淀的项目上下文条目
- ``naming_conventions``：命名约定（如 "环境物体一律以 env_ 开头"）
- ``unit_system``：单位系统提示
- ``open_count``：打开次数（用于活跃度排序）

**写入触发**：worker 检测到用户显式意图（含"这个项目/这个场景/记住…"）
或调用 ``add_note`` API 时。**读取**：worker 每轮启动时 best-effort。
"""

from __future__ import absolute_import
from __future__ import print_function

import hashlib
import json
import os
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional


# ---------------------------------------------------------------------- #
# 路径 / 序列化
# ---------------------------------------------------------------------- #

def _project_dir():
    # type: () -> str
    """返回项目级记忆存放目录，按需创建。"""
    root = os.path.join(
        os.path.expanduser('~'), '.maxagent', 'projects',
    )
    try:
        os.makedirs(root, exist_ok=True)
    except Exception:  # pylint: disable=broad-except
        pass
    return root


def _key_for_path(scene_path):
    # type: (str) -> str
    """把场景路径转成稳定的文件名 key（大小写归一化）。"""
    if not scene_path:
        return ''
    norm = os.path.normcase(os.path.abspath(scene_path))
    return hashlib.sha1(norm.encode('utf-8')).hexdigest()[:16]


def _file_for_path(scene_path):
    # type: (str) -> str
    key = _key_for_path(scene_path)
    if not key:
        return ''
    return os.path.join(_project_dir(), '{}.json'.format(key))


# ---------------------------------------------------------------------- #
# 读写 API
# ---------------------------------------------------------------------- #

def load_project_memory(scene_path):
    # type: (str) -> Optional[Dict[str, Any]]
    """按场景路径读出项目记忆。不存在返回 None，读失败返回 None。"""
    fp = _file_for_path(scene_path)
    if not fp or not os.path.isfile(fp):
        return None
    try:
        with open(fp, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:  # pylint: disable=broad-except
        pass
    return None


def save_project_memory(scene_path, mem):
    # type: (str, Dict[str, Any]) -> bool
    """原子写回项目记忆。scene_path 为空时返回 False。"""
    fp = _file_for_path(scene_path)
    if not fp:
        return False
    try:
        mem = dict(mem or {})
        mem['scene_path'] = scene_path
        mem['scene_name'] = os.path.basename(scene_path) or ''
        mem['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        tmp = fp + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(mem, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, fp)
        return True
    except Exception:  # pylint: disable=broad-except
        return False


def add_note(scene_path, note, max_notes=50):
    # type: (str, str, int) -> bool
    """向指定场景的项目记忆追加一条自由文本 note。

    自动去重（完全相同的条目不重复加），超出 ``max_notes`` 时丢弃最旧。
    """
    note = (note or '').strip()
    if not scene_path or not note:
        return False
    mem = load_project_memory(scene_path) or {}
    notes = list(mem.get('notes') or [])
    # 去重：完全相同不重复添加，只把它移到末尾表示"最近强调过"
    notes = [n for n in notes if n != note]
    notes.append(note)
    if len(notes) > max_notes:
        notes = notes[-max_notes:]
    mem['notes'] = notes
    mem['open_count'] = int(mem.get('open_count') or 0)
    return save_project_memory(scene_path, mem)


def bump_open_count(scene_path):
    # type: (str) -> None
    """打开场景时 +1，纯计数无副作用，失败静默。"""
    if not scene_path:
        return
    mem = load_project_memory(scene_path) or {}
    mem['open_count'] = int(mem.get('open_count') or 0) + 1
    save_project_memory(scene_path, mem)


def to_prompt_text(mem, max_notes=8):
    # type: (Optional[Dict[str, Any]], int) -> str
    """把项目记忆转成 system prompt 文本片段。

    None / 空记忆返回空串，避免污染上下文。
    """
    if not mem:
        return ''
    name = mem.get('scene_name') or '当前场景'
    notes = list(mem.get('notes') or [])[-max_notes:]
    naming = (mem.get('naming_conventions') or '').strip()
    unit = (mem.get('unit_system') or '').strip()
    open_count = int(mem.get('open_count') or 0)

    lines = ['## 项目记忆（{}）'.format(name)]
    if open_count > 1:
        lines.append('- 本场景已打开 {} 次，用户可能延续之前的工作。'
                     .format(open_count))
    if naming:
        lines.append('- 命名约定：{}'.format(naming))
    if unit:
        lines.append('- 单位系统：{}'.format(unit))
    if notes:
        lines.append('- 项目上下文条目：')
        for i, n in enumerate(notes, 1):
            lines.append('  {}. {}'.format(i, n))
    if len(lines) == 1:  # 只有标题，说明没有实质内容
        return ''
    lines.append(
        '（以上信息按场景文件路径持久化保存，跨会话可用。'
        '如与当前请求冲突，以用户当前请求为准。）',
    )
    return '\n'.join(lines)


# ---------------------------------------------------------------------- #
# 获取当前打开的 .max 场景路径
# ---------------------------------------------------------------------- #

def current_scene_path():
    # type: () -> str
    """尝试通过 pymxs 拿到当前打开的 .max 文件绝对路径。

    未开 Max 环境（如单测）或没打开场景（Untitled）返回空串。
    """
    try:
        import pymxs  # type: ignore
        rt = pymxs.runtime
    except Exception:  # pylint: disable=broad-except
        return ''
    try:
        fpath = getattr(rt, 'maxFilePath', '') or ''
        fname = getattr(rt, 'maxFileName', '') or ''
        if fpath and fname:
            return os.path.join(str(fpath), str(fname))
    except Exception:  # pylint: disable=broad-except
        pass
    return ''


# ---------------------------------------------------------------------- #
# 列表（供 UI 展示）
# ---------------------------------------------------------------------- #

def list_all_projects():
    # type: () -> List[Dict[str, Any]]
    """列出所有已记录的项目记忆概览，按最近更新时间倒序。"""
    root = _project_dir()
    result = []
    try:
        for fn in os.listdir(root):
            if not fn.endswith('.json'):
                continue
            try:
                with open(os.path.join(root, fn), 'r',
                          encoding='utf-8') as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    result.append({
                        'scene_path': data.get('scene_path', ''),
                        'scene_name': data.get('scene_name', ''),
                        'updated_at': data.get('updated_at', ''),
                        'open_count': int(data.get('open_count') or 0),
                        'note_count': len(data.get('notes') or []),
                    })
            except Exception:  # pylint: disable=broad-except
                continue
    except Exception:  # pylint: disable=broad-except
        return []
    result.sort(key=lambda x: x.get('updated_at') or '', reverse=True)
    return result


def delete_project_memory(scene_path):
    # type: (str) -> bool
    """按场景路径删除项目记忆文件。不存在或删除失败返回 False。"""
    fp = _file_for_path(scene_path)
    if not fp or not os.path.isfile(fp):
        return False
    try:
        os.remove(fp)
        return True
    except Exception:  # pylint: disable=broad-except
        return False
