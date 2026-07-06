#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长期记忆存储（Layer 2）。

存储布局
========
::

    ~/.maxagent/memory/                      ← 由 get_memory_root() 决定
    ├── INSTRUCTIONS.md      ← 用户显式硬规则（自动注入）
    ├── MEMORY.md            ← 用户画像 + AI 设定 + topic 索引（自动注入）
    ├── topic/
    │   └── <slug>.md        ← 单主题正文，按需读取
    └── events/
        └── YYYY-MM-DD.jsonl ← 事件日志（events.py 管理）

MEMORY.md 结构
==============
frontmatter (YAML) + 三个 heading::

    ---
    name: memory
    description: 用户长期记忆中心文档
    updated_at: 2026-XX-XXTXX:XX:XXZ
    keywords: [...]
    tags: [...]
    ---

    # Memory

    ## User Profile
    - ...

    ## AI Soul
    - ...

    ## Topics

    | Topic | 修改日期 | 标签 | 关键词 | 描述 |
    |...|

topic/<slug>.md
===============
- ``<slug>`` 必须匹配 ``[a-z][a-z0-9-]*``
- 首行为 frontmatter，其后为正文（Markdown）

写入策略
========
- **读宽松**：即使缺 frontmatter / heading 也照读；
- **写保守**：新增 topic 时才补齐结构，不做全量迁移；
- **精准局部替换**：``edit`` 只处理最小片段，禁止大段覆盖。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import re
import threading
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ..logger import get_logger

logger = get_logger(__name__)

# 目录名 & 文件名
_MEMORY_DIRNAME = 'memory'
_INSTRUCTIONS_FILE = 'INSTRUCTIONS.md'
_INDEX_FILE = 'MEMORY.md'
_TOPIC_DIRNAME = 'topic'

# 单文件写入上限（防止误写巨量内容）
_MAX_FILE_BYTES = 512 * 1024

# slug 校验：小写字母开头 + 小写字母/数字/短横线
_SLUG_PATTERN = re.compile(r'^[a-z][a-z0-9-]*$')


# ---------------------------------------------------------------------- #
# root 目录
# ---------------------------------------------------------------------- #

def get_memory_root():
    # type: () -> str
    """获取记忆根目录，不存在则创建。

    优先使用 ``config.get_config_dir()``，失败降级到 ``~/.maxagent``。
    """
    try:
        from ..config import get_config_dir
        base = get_config_dir()
    except Exception:  # pylint: disable=broad-except
        base = os.path.join(os.path.expanduser('~'), '.maxagent')
    root = os.path.join(base, _MEMORY_DIRNAME)
    try:
        os.makedirs(os.path.join(root, _TOPIC_DIRNAME), exist_ok=True)
    except OSError as exc:
        logger.warning('创建记忆目录失败: %s', exc)
    return root


# ---------------------------------------------------------------------- #
# 读写工具
# ---------------------------------------------------------------------- #

def _atomic_write(path, text):
    # type: (str, str) -> None
    """原子写入：先写 tmp 再 rename，防止半写文件。"""
    if len(text.encode('utf-8')) > _MAX_FILE_BYTES:
        raise ValueError(
            '拒绝写入：内容超过单文件上限 {} bytes'.format(_MAX_FILE_BYTES)
        )
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(text)
    if os.path.exists(path):
        os.replace(tmp, path)
    else:
        os.rename(tmp, path)


def _read_text(path):
    # type: (str) -> str
    if not os.path.exists(path):
        return ''
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return fh.read()
    except OSError as exc:
        logger.warning('读取失败 %s: %s', path, exc)
        return ''


# ---------------------------------------------------------------------- #
# 主类
# ---------------------------------------------------------------------- #

class LongTermMemoryStore(object):
    """长期记忆读写：INSTRUCTIONS.md / MEMORY.md / topic/*.md。

    - 所有路径参数用 ``file_path`` 表示（相对路径 + 白名单校验）；
    - 三种合法值：
      * ``'INSTRUCTIONS.md'``
      * ``'MEMORY.md'``
      * ``'topic/<slug>.md'``（``<slug>`` 匹配 ``[a-z][a-z0-9-]*``）
    """

    def __init__(self, root=None):
        # type: (Optional[str]) -> None
        self._root = root or get_memory_root()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # 路径与校验
    # ------------------------------------------------------------------ #
    def _resolve(self, file_path):
        # type: (str) -> Tuple[str, str]
        """把逻辑路径解析为 (kind, absolute_path)。

        kind ∈ {'instructions', 'index', 'topic'}
        非法路径抛 ValueError。
        """
        if not file_path:
            raise ValueError('file_path 不能为空')
        norm = file_path.replace('\\', '/').strip()
        if norm == _INSTRUCTIONS_FILE:
            return 'instructions', os.path.join(self._root, _INSTRUCTIONS_FILE)
        if norm == _INDEX_FILE:
            return 'index', os.path.join(self._root, _INDEX_FILE)
        prefix = _TOPIC_DIRNAME + '/'
        if norm.startswith(prefix) and norm.endswith('.md'):
            slug = norm[len(prefix):-len('.md')]
            if not _SLUG_PATTERN.match(slug):
                raise ValueError(
                    'topic slug 非法（要求 [a-z][a-z0-9-]*）: {}'.format(slug)
                )
            return 'topic', os.path.join(
                self._root, _TOPIC_DIRNAME, slug + '.md',
            )
        raise ValueError('未知记忆路径: {}'.format(file_path))

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    def read(self, file_path='MEMORY.md', offset=None, limit=None):
        # type: (str, Optional[int], Optional[int]) -> str
        """读取记忆文件（可选行范围）。"""
        _kind, abspath = self._resolve(file_path)
        text = _read_text(abspath)
        if offset is None and limit is None:
            return text
        lines = text.splitlines(True)
        start = max(0, (offset or 1) - 1)
        if limit is not None:
            end = start + max(0, int(limit))
            lines = lines[start:end]
        else:
            lines = lines[start:]
        return ''.join(lines)

    def read_instructions(self):
        # type: () -> str
        return self.read(_INSTRUCTIONS_FILE)

    def read_index(self):
        # type: () -> str
        return self.read(_INDEX_FILE)

    # ------------------------------------------------------------------ #
    # 写：create / edit / delete
    # ------------------------------------------------------------------ #
    def create(self, file_path, content):
        # type: (str, str) -> None
        """整文件创建/覆写。"""
        with self._lock:
            _kind, abspath = self._resolve(file_path)
            _atomic_write(abspath, content or '')
        logger.info('memory create: %s (%d chars)', file_path, len(content or ''))

    def edit(self, file_path, old_content, new_content):
        # type: (str, str, str) -> bool
        """严格局部替换。``old_content`` 必须精确出现且唯一。"""
        if not old_content:
            raise ValueError('old_content 不能为空（禁止大段覆写用 create 代替）')
        with self._lock:
            _kind, abspath = self._resolve(file_path)
            text = _read_text(abspath)
            if not text:
                raise ValueError('文件不存在或为空: {}'.format(file_path))
            occ = text.count(old_content)
            if occ == 0:
                raise ValueError(
                    'old_content 未找到（可能已被改动，请先重新 read）'
                )
            if occ > 1:
                raise ValueError(
                    'old_content 匹配到 {} 处，请提供更长/更具区分度的片段'.format(occ)
                )
            new_text = text.replace(old_content, new_content or '', 1)
            _atomic_write(abspath, new_text)
        logger.info('memory edit: %s (Δ=%d)', file_path,
                    len(new_content or '') - len(old_content))
        return True

    def delete(self, file_path):
        # type: (str) -> bool
        """删除单个文件。删除 MEMORY.md 需要手动清空 topic/ 后再调用。"""
        with self._lock:
            _kind, abspath = self._resolve(file_path)
            if not os.path.exists(abspath):
                return False
            try:
                os.remove(abspath)
            except OSError as exc:
                logger.warning('删除失败: %s', exc)
                return False
        logger.info('memory delete: %s', file_path)
        return True

    # ------------------------------------------------------------------ #
    # 追加到 INSTRUCTIONS.md
    # ------------------------------------------------------------------ #
    def append_instruction(self, rule):
        # type: (str) -> str
        """追加一条规则到 INSTRUCTIONS.md 末尾。

        - 已有相同规则则跳过（返回 'skip'）
        - 文件不存在则自动创建
        - 每条独立一行，前缀 ``- ``
        """
        rule = (rule or '').strip()
        if not rule:
            return 'skip'
        with self._lock:
            _kind, abspath = self._resolve(_INSTRUCTIONS_FILE)
            text = _read_text(abspath)
            line = '- ' + rule
            if line in text:
                return 'skip'
            if text and not text.endswith('\n'):
                text += '\n'
            if not text:
                text = '# Instructions\n\n'
            text += line + '\n'
            _atomic_write(abspath, text)
        return 'appended'

    # ------------------------------------------------------------------ #
    # topic 列表 & 索引维护
    # ------------------------------------------------------------------ #
    def list_topics(self):
        # type: () -> List[str]
        """返回所有 topic 的 slug 列表（字典序）。"""
        d = os.path.join(self._root, _TOPIC_DIRNAME)
        if not os.path.isdir(d):
            return []
        slugs = []
        for fname in os.listdir(d):
            if not fname.endswith('.md'):
                continue
            slug = fname[:-3]
            if _SLUG_PATTERN.match(slug):
                slugs.append(slug)
        slugs.sort()
        return slugs

    def upsert_topic(self, slug, content, description=''):
        # type: (str, str, str) -> None
        """创建或整体更新一个 topic 文件，并同步 MEMORY.md 索引。"""
        if not _SLUG_PATTERN.match(slug or ''):
            raise ValueError('slug 非法: {}'.format(slug))
        rel = 'topic/{}.md'.format(slug)
        self.create(rel, content)
        self._sync_topic_pointer(slug, description or '')

    def _sync_topic_pointer(self, slug, description):
        # type: (str, str) -> None
        """确保 MEMORY.md 的 Topics 章节里有指向该 slug 的一行。"""
        idx_path = os.path.join(self._root, _INDEX_FILE)
        text = _read_text(idx_path)
        if not text:
            text = _default_index_template()
        pointer = '[{}](topic/{}.md)'.format(slug, slug)
        if pointer in text:
            return
        # 在末尾追加一行到 Topics 章节
        today = time.strftime('%Y-%m-%d')
        line = '- {} · {} · {}'.format(pointer, today, description or '')
        if '## Topics' not in text:
            text = text.rstrip() + '\n\n## Topics\n\n' + line + '\n'
        else:
            text = text.rstrip() + '\n' + line + '\n'
        _atomic_write(idx_path, text)


def _default_index_template():
    # type: () -> str
    ts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    return (
        '---\n'
        'name: memory\n'
        'description: 用户长期记忆中心文档\n'
        'updated_at: {}\n'
        'keywords: []\n'
        'tags: []\n'
        '---\n\n'
        '# Memory\n\n'
        '## User Profile\n\n'
        '## AI Soul\n\n'
        '## Topics\n\n'
    ).format(ts)


# ---------------------------------------------------------------------- #
# 单例
# ---------------------------------------------------------------------- #

_singleton_lock = threading.Lock()
_singleton = None  # type: Optional[LongTermMemoryStore]


def get_memory_store():
    # type: () -> LongTermMemoryStore
    global _singleton  # pylint: disable=global-statement
    with _singleton_lock:
        if _singleton is None:
            _singleton = LongTermMemoryStore()
        return _singleton


def reset_memory_store():
    # type: () -> None
    global _singleton  # pylint: disable=global-statement
    with _singleton_lock:
        _singleton = None


__all__ = [
    'LongTermMemoryStore',
    'get_memory_store',
    'reset_memory_store',
    'get_memory_root',
]
