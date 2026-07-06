#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长期记忆检索：跨 INSTRUCTIONS.md / MEMORY.md / topic/*.md 的关键词 + 打分搜索。

设计
====
- 无 embedding 依赖：用"关键词命中数 + IDF 权重 + 位置加权"做排序。
- 文件粒度切块：每个文件按空行拆成"chunk"（约 200~800 字），既保留
  上下文，又能返回细粒度片段。
- 返回结构与 Knot 记忆搜索保持一致：``file_path`` + ``snippet`` + ``score``。
"""

from __future__ import absolute_import
from __future__ import print_function

import math
import os
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ..logger import get_logger
from .store import LongTermMemoryStore
from .store import get_memory_store

logger = get_logger(__name__)

# 单个 chunk 的最大字符数（超出继续切分）
_CHUNK_MAX_CHARS = 800


def _iter_all_files(store):
    # type: (LongTermMemoryStore) -> List[Tuple[str, str]]
    """列出记忆库里所有可搜索文件，返回 [(logical_path, absolute_path)]。"""
    root = store._root  # pylint: disable=protected-access
    files = []  # type: List[Tuple[str, str]]
    for name in ('INSTRUCTIONS.md', 'MEMORY.md'):
        p = os.path.join(root, name)
        if os.path.exists(p):
            files.append((name, p))
    topic_dir = os.path.join(root, 'topic')
    if os.path.isdir(topic_dir):
        for fname in sorted(os.listdir(topic_dir)):
            if fname.endswith('.md'):
                files.append(
                    ('topic/' + fname, os.path.join(topic_dir, fname)),
                )
    return files


def _split_chunks(text):
    # type: (str) -> List[str]
    """按空行切段，超长再按字符数切。"""
    if not text:
        return []
    raw_chunks = re.split(r'\n\s*\n', text)
    chunks = []
    for ch in raw_chunks:
        ch = ch.strip('\n')
        if not ch.strip():
            continue
        if len(ch) <= _CHUNK_MAX_CHARS:
            chunks.append(ch)
        else:
            for i in range(0, len(ch), _CHUNK_MAX_CHARS):
                chunks.append(ch[i:i + _CHUNK_MAX_CHARS])
    return chunks


def _tokenize(text):
    # type: (str) -> List[str]
    """极简分词：空格分隔，全部转小写。中文按整段子串匹配（不做分词）。"""
    if not text:
        return []
    return [t for t in str(text).lower().split() if t]


def _score_chunk(chunk_lower, tokens, cn_terms):
    # type: (str, List[str], List[str]) -> float
    """给单个 chunk 打分。tokens 命中一个 +1，中文 term 命中 +2（更稀有）。"""
    if not chunk_lower:
        return 0.0
    score = 0.0
    for t in tokens:
        cnt = chunk_lower.count(t)
        if cnt > 0:
            score += 1.0 + math.log1p(cnt)
    for term in cn_terms:
        cnt = chunk_lower.count(term)
        if cnt > 0:
            score += 2.0 + math.log1p(cnt)
    return score


def _extract_cn_terms(text):
    # type: (str) -> List[str]
    """从 query 中抽出连续中文串（≥2 字），作为整段匹配 term。"""
    if not text:
        return []
    return [m.group(0) for m in re.finditer(r'[\u4e00-\u9fff]{2,}', text)]


def _make_snippet(chunk, tokens, cn_terms, max_chars=280):
    # type: (str, List[str], List[str], int) -> str
    """生成命中片段：以第一个命中位置为中心的窗口。"""
    if not chunk:
        return ''
    lower = chunk.lower()
    hit_pos = -1
    for term in cn_terms:
        pos = lower.find(term)
        if pos >= 0:
            hit_pos = pos
            break
    if hit_pos < 0:
        for t in tokens:
            pos = lower.find(t)
            if pos >= 0:
                hit_pos = pos
                break
    if hit_pos < 0:
        return chunk[:max_chars]
    half = max_chars // 2
    start = max(0, hit_pos - half)
    end = min(len(chunk), start + max_chars)
    snippet = chunk[start:end]
    if start > 0:
        snippet = '…' + snippet
    if end < len(chunk):
        snippet = snippet + '…'
    return snippet


def search_memory(query='', keyword='', topk=10, store=None):
    # type: (str, str, int, Optional[LongTermMemoryStore]) -> List[Dict[str, Any]]
    """在长期记忆中搜索。

    :param query: 语义查询串（自然语言）；本地无 embedding，退化为分词命中。
    :param keyword: 精确关键词（AND 语义）；命中数量作为强约束。
    :param topk: 返回条数上限
    :returns: 列表，每项形如
        ``{"file_path": "topic/xxx.md", "snippet": "...", "score": float}``
    """
    st = store or get_memory_store()
    query_tokens = _tokenize(query)
    kw_tokens = _tokenize(keyword)
    cn_terms = _extract_cn_terms(query) + _extract_cn_terms(keyword)

    if not query_tokens and not kw_tokens and not cn_terms:
        return []

    all_files = _iter_all_files(st)
    hits = []  # type: List[Tuple[float, str, str]]
    for logical, abs_path in all_files:
        try:
            with open(abs_path, 'r', encoding='utf-8') as fh:
                text = fh.read()
        except OSError:
            continue
        for chunk in _split_chunks(text):
            lower = chunk.lower()
            # keyword AND：全部必须命中
            if kw_tokens:
                if not all(t in lower for t in kw_tokens):
                    continue
            score = _score_chunk(
                lower,
                query_tokens + kw_tokens,
                cn_terms,
            )
            if score <= 0:
                continue
            # 文件位置加权：INSTRUCTIONS 最高、MEMORY 次之、topic 常规
            if logical == 'INSTRUCTIONS.md':
                score *= 1.5
            elif logical == 'MEMORY.md':
                score *= 1.2
            hits.append((score, logical, chunk))

    hits.sort(key=lambda x: x[0], reverse=True)
    hits = hits[:max(1, int(topk or 10))]

    results = []
    for score, logical, chunk in hits:
        results.append({
            'file_path': logical,
            'score': round(float(score), 4),
            'snippet': _make_snippet(chunk, query_tokens + kw_tokens, cn_terms),
        })
    return results


__all__ = ['search_memory']
