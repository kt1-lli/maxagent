#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识库统一入口。

``KnowledgeIndex`` 负责：
1. 管理若干 ``DocSource``
2. 灌进同一个 BM25 索引
3. 持久化到 ``{config_dir}/knowledge/{name}.idx.json``
4. 提供 search / rebuild 接口
5. 变更检测（对比 source fingerprint）：变了就重建

单例管理（模块级）：
- ``get_maxhelp_index()``：A 场景专用，只挂 MaxHelpSource
- ``get_user_index()``：D 场景用户库，可增删多个 source

**为什么不合并成一个大索引？**
- A 是内置只读，跟随 mzp 发布，不受用户操作影响
- D 是用户可增删的，需要独立管理生命周期
- 分开还便于将来加"只查用户库不查官方"的检索模式
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import os
import threading
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..logger import get_logger
from .bm25 import BM25Index
from .sources import DocSource
from .sources import MarkdownFileSource
from .sources import MaxHelpSource
from .sources import DirectorySource


logger = get_logger(__name__)


def _default_cache_dir():
    # type: () -> str
    """索引持久化目录，默认 ``{HOME}/.maxagent/knowledge``。"""
    # 避免循环导入：延后 import
    try:
        from ..config import get_config_dir  # pylint: disable=import-outside-toplevel
        base = get_config_dir()
    except Exception:  # pylint: disable=broad-except
        base = os.path.expanduser(os.path.join('~', '.maxagent'))
    return os.path.join(base, 'knowledge')


class KnowledgeIndex(object):
    """一组数据源合并成的可检索索引。

    - 通过 ``add_source(src)`` 挂 DocSource
    - ``rebuild()`` 全量重建 BM25 索引
    - ``search(query, topk)`` 检索
    - ``save() / load()`` 持久化
    - ``needs_rebuild()`` 对比 fingerprint 决定是否要重建
    """

    def __init__(self, name, cache_dir=None):
        # type: (str, Optional[str]) -> None
        self.name = name
        self.cache_dir = cache_dir or _default_cache_dir()
        self._sources = []  # type: List[DocSource]
        self._bm25 = BM25Index()
        # source_id -> fingerprint at last build
        self._built_fingerprints = {}  # type: Dict[str, str]
        self._built_at = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------- #
    # 数据源管理
    # ------------------------------------------------------------- #
    def add_source(self, src):
        # type: (DocSource) -> None
        """添加或替换同 ID 的数据源。"""
        with self._lock:
            self._sources = [
                s for s in self._sources if s.source_id != src.source_id
            ]
            self._sources.append(src)

    def remove_source(self, source_id):
        # type: (str) -> bool
        with self._lock:
            before = len(self._sources)
            self._sources = [
                s for s in self._sources if s.source_id != source_id
            ]
            self._built_fingerprints.pop(source_id, None)
            return len(self._sources) < before

    def list_sources(self):
        # type: () -> List[Dict[str, Any]]
        return [
            {
                'source_id': s.source_id,
                'source_tag': s.source_tag,
                'display_name': s.display_name,
                'fingerprint': s.get_fingerprint(),
            }
            for s in self._sources
        ]

    # ------------------------------------------------------------- #
    # 索引重建
    # ------------------------------------------------------------- #
    def needs_rebuild(self):
        # type: () -> bool
        """当前源 fingerprint 与上次构建不一致时返回 True。"""
        if self._bm25.n_docs == 0 and self._sources:
            return True
        for s in self._sources:
            fp = s.get_fingerprint()
            if self._built_fingerprints.get(s.source_id) != fp:
                return True
        # 有 source 被移除的情况
        current_ids = {s.source_id for s in self._sources}
        for sid in list(self._built_fingerprints.keys()):
            if sid not in current_ids:
                return True
        return False

    def rebuild(self):
        # type: () -> Dict[str, Any]
        """全量重建 BM25 索引。返回统计信息。"""
        with self._lock:
            bm = BM25Index()
            fp_map = {}
            total_chunks = 0
            per_source = {}  # type: Dict[str, int]
            for src in self._sources:
                try:
                    chunks = list(src.iter_chunks())
                except Exception as exc:  # pylint: disable=broad-except
                    logger.warning(
                        'source %s iter_chunks 失败: %s',
                        src.source_id, exc,
                    )
                    chunks = []
                per_source[src.source_id] = len(chunks)
                for i, c in enumerate(chunks):
                    doc_id = '{}#{}'.format(src.source_id, i)
                    bm.add_document(
                        doc_id=doc_id,
                        text=c.get('text') or '',
                        meta=c.get('meta') or {},
                    )
                    total_chunks += 1
                fp_map[src.source_id] = src.get_fingerprint()
            bm.finalize()
            self._bm25 = bm
            self._built_fingerprints = fp_map
            self._built_at = time.time()
            logger.info(
                'knowledge index %s rebuilt: sources=%d chunks=%d',
                self.name, len(self._sources), total_chunks,
            )
            return {
                'ok': True,
                'name': self.name,
                'sources': len(self._sources),
                'total_chunks': total_chunks,
                'per_source': per_source,
                'built_at': self._built_at,
            }

    # ------------------------------------------------------------- #
    # 查询
    # ------------------------------------------------------------- #
    def search(self, query, topk=3, auto_rebuild=True):
        # type: (str, int, bool) -> List[Dict[str, Any]]
        """检索。默认在必要时自动重建索引。"""
        if auto_rebuild and self.needs_rebuild():
            self.rebuild()
        return self._bm25.search(query, topk=topk)

    def stats(self):
        # type: () -> Dict[str, Any]
        return {
            'name': self.name,
            'n_docs': self._bm25.n_docs,
            'avg_dl': self._bm25.avg_dl,
            'built_at': self._built_at,
            'sources': self.list_sources(),
        }

    # ------------------------------------------------------------- #
    # 持久化
    # ------------------------------------------------------------- #
    def _index_path(self):
        return os.path.join(self.cache_dir, '{}.idx.json'.format(self.name))

    def _meta_path(self):
        return os.path.join(self.cache_dir, '{}.meta.json'.format(self.name))

    def save(self):
        # type: () -> str
        """保存 BM25 索引 + 元信息。"""
        idx_path = self._index_path()
        meta_path = self._meta_path()
        self._bm25.save(idx_path)
        meta = {
            'version': 1,
            'name': self.name,
            'built_at': self._built_at,
            'fingerprints': self._built_fingerprints,
        }
        parent = os.path.dirname(meta_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        tmp = meta_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp, meta_path)
        return idx_path

    def load(self):
        # type: () -> bool
        """尝试加载持久化的索引。成功返回 True。"""
        idx_path = self._index_path()
        meta_path = self._meta_path()
        if not (os.path.isfile(idx_path) and os.path.isfile(meta_path)):
            return False
        try:
            self._bm25 = BM25Index.load(idx_path)
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            self._built_at = float(meta.get('built_at') or 0.0)
            self._built_fingerprints = dict(meta.get('fingerprints') or {})
            return True
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('加载索引 %s 失败: %s', self.name, exc)
            return False


# ------------------------------------------------------------------- #
# 单例：A 场景（Max 官方文档，只读，跟随 mzp 发布）
# ------------------------------------------------------------------- #
_MAXHELP_INDEX = None  # type: Optional[KnowledgeIndex]
_MAXHELP_LOCK = threading.Lock()


def get_maxhelp_index():
    # type: () -> KnowledgeIndex
    """获取 Max 官方文档索引（懒加载单例）。"""
    global _MAXHELP_INDEX  # pylint: disable=global-statement
    if _MAXHELP_INDEX is not None:
        return _MAXHELP_INDEX
    with _MAXHELP_LOCK:
        if _MAXHELP_INDEX is not None:
            return _MAXHELP_INDEX
        idx = KnowledgeIndex('maxhelp')
        idx.add_source(MaxHelpSource())
        if idx.load():
            # 已有持久化索引；如源指纹发生变化会在 search 时自动重建
            logger.info(
                'knowledge maxhelp: loaded persisted index (docs=%d)',
                idx._bm25.n_docs,  # pylint: disable=protected-access
            )
        _MAXHELP_INDEX = idx
        return idx


# ------------------------------------------------------------------- #
# 单例：D 场景（用户库，可增删）
# ------------------------------------------------------------------- #
_USER_INDEX = None  # type: Optional[KnowledgeIndex]
_USER_LOCK = threading.Lock()

# 用户库的 source 清单持久化文件
def _user_registry_path():
    return os.path.join(_default_cache_dir(), 'user_sources.json')


def _load_user_sources():
    # type: () -> List[DocSource]
    path = _user_registry_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:  # pylint: disable=broad-except
        return []
    out = []
    for item in data.get('sources') or []:
        kind = item.get('kind')
        p = item.get('path')
        if not p:
            continue
        if kind == 'file':
            out.append(MarkdownFileSource(
                p,
                display_name=item.get('display_name'),
                tags=item.get('tags') or [],
            ))
        elif kind == 'dir':
            out.append(DirectorySource(
                p,
                display_name=item.get('display_name'),
                tags=item.get('tags') or [],
            ))
    return out


def _save_user_sources(sources):
    # type: (List[DocSource]) -> None
    path = _user_registry_path()
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    items = []
    for s in sources:
        if isinstance(s, DirectorySource):
            items.append({
                'kind': 'dir',
                'path': s.dir_path,
                'display_name': s.display_name,
                'tags': list(s.tags),
            })
        elif isinstance(s, MarkdownFileSource):
            items.append({
                'kind': 'file',
                'path': s.path,
                'display_name': s.display_name,
                'tags': list(s.tags),
            })
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'version': 1, 'sources': items}, f, ensure_ascii=False)
    os.replace(tmp, path)


def get_user_index():
    # type: () -> KnowledgeIndex
    """获取用户知识库索引（懒加载单例，从注册表恢复 source 列表）。"""
    global _USER_INDEX  # pylint: disable=global-statement
    if _USER_INDEX is not None:
        return _USER_INDEX
    with _USER_LOCK:
        if _USER_INDEX is not None:
            return _USER_INDEX
        idx = KnowledgeIndex('user')
        for s in _load_user_sources():
            idx.add_source(s)
        idx.load()
        _USER_INDEX = idx
        return idx


def add_user_source(path, kind='auto', display_name=None, tags=None):
    # type: (str, str, Optional[str], Optional[List[str]]) -> Dict[str, Any]
    """给用户库添加一个数据源并持久化注册表。

    :param path: 文件或目录绝对路径
    :param kind: 'auto' / 'file' / 'dir'
    """
    if not os.path.exists(path):
        return {'ok': False, 'error': '路径不存在: ' + str(path)}
    if kind == 'auto':
        kind = 'dir' if os.path.isdir(path) else 'file'
    idx = get_user_index()
    if kind == 'dir':
        src = DirectorySource(path, display_name=display_name, tags=tags)
    else:
        src = MarkdownFileSource(path, display_name=display_name, tags=tags)
    idx.add_source(src)
    # 持久化 sources 注册表（获取当前所有 source 对象）
    _save_user_sources(idx._sources)  # pylint: disable=protected-access
    stats = idx.rebuild()
    idx.save()
    return {'ok': True, 'source_id': src.source_id, 'stats': stats}


def remove_user_source(source_id):
    # type: (str) -> Dict[str, Any]
    idx = get_user_index()
    removed = idx.remove_source(source_id)
    if not removed:
        return {'ok': False, 'error': '未找到 source_id: ' + str(source_id)}
    _save_user_sources(idx._sources)  # pylint: disable=protected-access
    stats = idx.rebuild()
    idx.save()
    return {'ok': True, 'stats': stats}


__all__ = [
    'KnowledgeIndex',
    'get_maxhelp_index',
    'get_user_index',
    'add_user_source',
    'remove_user_source',
]
