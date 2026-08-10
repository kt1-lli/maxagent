#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识库数据源抽象。

设计：``DocSource`` 是所有数据源的基类，负责"提供 chunks"。
上层的 ``KnowledgeIndex`` 负责把多个 source 的 chunks 灌进同一个
BM25 索引，并管理增量重建。

内置数据源：
- ``MaxHelpSource``：本地打包的 Max Python Help（A 场景）
- ``MarkdownFileSource``：用户导入的单个 md/txt 文件（D 场景）
- ``DirectorySource``：递归扫描目录下所有 md/txt（D 场景批量）

Skills / Sessions 的语义召回（C 场景）在第二批加，本文件先留基类扩展位。
"""

from __future__ import absolute_import
from __future__ import print_function

import hashlib
import os
from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional

from .chunker import chunk_markdown
from .chunker import chunk_plaintext


# 项目根目录下的 A 场景默认文档位置
_MAXHELP_DEFAULT_SUBPATH = 'knowledge/data/max_python_help.md'

# 支持的文本扩展名 → chunker
_TEXT_EXT_CHUNKERS = {
    '.md': chunk_markdown,
    '.markdown': chunk_markdown,
    '.txt': chunk_plaintext,
    '.text': chunk_plaintext,
}


def _sha1(text):
    # type: (str) -> str
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]


class DocSource(object):
    """数据源基类。

    子类需实现 ``iter_chunks()``，返回 chunk dict 列表（结构见 chunker）。
    子类可选实现 ``get_fingerprint()``（用于变更检测和增量重建）。
    """

    # 数据源唯一标识（如 'maxhelp' / 'user:my_notes.md'）
    source_id = 'base'
    # 数据源来源分类，供上层筛选和展示
    source_tag = 'unknown'  # 'maxhelp' / 'user' / 'skill' / 'session' ...
    # 人类可读的显示名
    display_name = 'unnamed'

    def iter_chunks(self):
        # type: () -> Iterable[Dict[str, Any]]
        """产出 chunk dict，每项至少含 'text' / 'meta'。"""
        raise NotImplementedError

    def get_fingerprint(self):
        # type: () -> str
        """内容指纹，用于快速检测是否需要重建索引。默认空串。"""
        return ''


# ------------------------------------------------------------------- #
# A 场景：本地打包 Max 官方文档
# ------------------------------------------------------------------- #
def _default_maxhelp_path():
    # type: () -> str
    """定位打包时随包发布的 Max Python Help md 文件。"""
    # 本文件位于 maxagent/knowledge/sources.py
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, 'data', 'max_python_help.md')


class MaxHelpSource(DocSource):
    """3ds Max Python Help 官方文档（本地打包）。

    文件位置：``maxagent/knowledge/data/max_python_help.md``
    这是一份**占位文件**，用户可以随后替换为真实的 Max-Python-Help_YYYY.md。
    """

    source_id = 'maxhelp'
    source_tag = 'maxhelp'
    display_name = '3ds Max Python Help（本地）'

    def __init__(self, path=None):
        self.path = path or _default_maxhelp_path()

    def exists(self):
        # type: () -> bool
        return os.path.isfile(self.path)

    def iter_chunks(self):
        if not self.exists():
            return []
        with open(self.path, 'r', encoding='utf-8') as f:
            text = f.read()
        chunks = chunk_markdown(text, source_name='maxhelp')
        # 加入 meta：source_id / display_name / path
        for c in chunks:
            c.setdefault('meta', {})
            c['meta'].update({
                'source_id': self.source_id,
                'source_tag': self.source_tag,
                'display_name': self.display_name,
                'file_path': self.path,
                'heading_path': c.get('heading_path', ''),
                'line_start': c.get('line_start'),
                'line_end': c.get('line_end'),
            })
        return chunks

    def get_fingerprint(self):
        if not self.exists():
            return ''
        try:
            st = os.stat(self.path)
            # mtime + size 组合足够区分内容变化
            return '{}:{}'.format(int(st.st_mtime), int(st.st_size))
        except OSError:
            return ''


# ------------------------------------------------------------------- #
# D 场景：用户导入的单个文件
# ------------------------------------------------------------------- #
class MarkdownFileSource(DocSource):
    """用户导入的单个 md / txt 文件。"""

    source_tag = 'user'

    def __init__(self, path, display_name=None, tags=None, source_id=None):
        # type: (str, Optional[str], Optional[List[str]], Optional[str]) -> None
        self.path = os.path.abspath(path)
        self.display_name = display_name or os.path.basename(path)
        self.tags = list(tags or [])
        self.source_id = source_id or 'user:' + _sha1(self.path)

    def exists(self):
        return os.path.isfile(self.path)

    def _pick_chunker(self):
        ext = os.path.splitext(self.path)[1].lower()
        return _TEXT_EXT_CHUNKERS.get(ext, chunk_plaintext)

    def iter_chunks(self):
        if not self.exists():
            return []
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                text = f.read()
        except UnicodeDecodeError:
            # 尝试 gbk 兜底
            with open(self.path, 'r', encoding='gbk', errors='replace') as f:
                text = f.read()
        chunker = self._pick_chunker()
        chunks = chunker(text, source_name=self.source_id)
        for c in chunks:
            c.setdefault('meta', {})
            c['meta'].update({
                'source_id': self.source_id,
                'source_tag': self.source_tag,
                'display_name': self.display_name,
                'file_path': self.path,
                'tags': list(self.tags),
                'heading_path': c.get('heading_path', ''),
                'line_start': c.get('line_start'),
                'line_end': c.get('line_end'),
            })
        return chunks

    def get_fingerprint(self):
        if not self.exists():
            return ''
        try:
            st = os.stat(self.path)
            return '{}:{}'.format(int(st.st_mtime), int(st.st_size))
        except OSError:
            return ''


# ------------------------------------------------------------------- #
# D 场景：目录批量导入
# ------------------------------------------------------------------- #
class DirectorySource(DocSource):
    """递归扫描目录下所有 md / txt 文件。"""

    source_tag = 'user'

    def __init__(self, dir_path, display_name=None, tags=None,
                 max_file_size=2 * 1024 * 1024, source_id=None):
        self.dir_path = os.path.abspath(dir_path)
        self.display_name = display_name or os.path.basename(dir_path) or 'root'
        self.tags = list(tags or [])
        self.source_id = source_id or 'userdir:' + _sha1(self.dir_path)
        self.max_file_size = int(max_file_size)

    def exists(self):
        return os.path.isdir(self.dir_path)

    def _iter_files(self):
        for root, _dirs, files in os.walk(self.dir_path):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in _TEXT_EXT_CHUNKERS:
                    continue
                full = os.path.join(root, fn)
                try:
                    if os.path.getsize(full) > self.max_file_size:
                        continue
                except OSError:
                    continue
                yield full

    def iter_chunks(self):
        if not self.exists():
            return []
        all_chunks = []
        for full in self._iter_files():
            sub = MarkdownFileSource(
                full,
                display_name=os.path.relpath(full, self.dir_path),
                tags=self.tags,
            )
            sub.source_id = self.source_id + ':' + _sha1(full)
            all_chunks.extend(sub.iter_chunks())
        return all_chunks

    def get_fingerprint(self):
        if not self.exists():
            return ''
        # 目录指纹 = 所有文件 mtime + size 拼串再 hash
        parts = []
        for full in sorted(self._iter_files()):
            try:
                st = os.stat(full)
                parts.append('{}:{}:{}'.format(
                    full, int(st.st_mtime), int(st.st_size),
                ))
            except OSError:
                continue
        return _sha1('\n'.join(parts))


__all__ = [
    'DocSource',
    'MaxHelpSource',
    'MarkdownFileSource',
    'DirectorySource',
]
