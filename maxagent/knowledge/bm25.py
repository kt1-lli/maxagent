#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BM25 全文检索引擎（纯 Python 实现，零外部依赖）。

BM25 是经典的信息检索算法，工业级搜索引擎（ES / Lucene）的默认排序算法。
本实现是 Okapi BM25 的标准变体，参数：

- k1 = 1.5（term saturation，通常 1.2~2.0）
- b  = 0.75（length normalization，通常 0.5~1.0）

时空复杂度：
- 建索引 O(N * avg_doc_len)
- 单次查询 O(Q * postings_per_term)，Q 为 query token 数
- 索引大小约为原文的 1.5~2x（含倒排 + 文档长度表）

序列化为 JSON，便于持久化和跨版本读取。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import math
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from .tokenizer import tokenize


# BM25 参数
_K1 = 1.5
_B = 0.75

# 索引文件格式版本，用于跨版本兼容检测
_INDEX_VERSION = 1


class BM25Index(object):
    """BM25 倒排索引。

    数据结构：
      - docs: List[Dict]  每个 doc 至少含 {'id', 'text', 'meta'}
      - doc_len: List[int]  每个 doc 的 token 数
      - avg_dl: float  平均文档长度
      - postings: Dict[token, List[Tuple[doc_idx, tf]]]  倒排表
      - df: Dict[token, int]  文档频率（含该词的文档数）
      - idf: Dict[token, float]  预计算的 IDF 权重
      - n_docs: int
    """

    def __init__(self):
        self.docs = []  # type: List[Dict[str, Any]]
        self.doc_len = []  # type: List[int]
        self.avg_dl = 0.0
        self.postings = {}  # type: Dict[str, List[Tuple[int, int]]]
        self.df = {}  # type: Dict[str, int]
        self.idf = {}  # type: Dict[str, float]
        self.n_docs = 0

    # ------------------------------------------------------------- #
    # 建索引
    # ------------------------------------------------------------- #
    def add_document(self, doc_id, text, meta=None):
        # type: (str, str, Optional[Dict[str, Any]]) -> int
        """加入一个文档到索引。返回文档在索引中的位置索引。"""
        idx = len(self.docs)
        self.docs.append({
            'id': str(doc_id),
            'text': text or '',
            'meta': dict(meta or {}),
        })
        tokens = tokenize(text or '')
        self.doc_len.append(len(tokens))

        # 统计 tf
        tf_map = {}  # type: Dict[str, int]
        for tk in tokens:
            tf_map[tk] = tf_map.get(tk, 0) + 1

        # 写入 postings + df
        for tk, tf in tf_map.items():
            self.postings.setdefault(tk, []).append((idx, tf))
            self.df[tk] = self.df.get(tk, 0) + 1

        return idx

    def finalize(self):
        # type: () -> None
        """建索引完成后必须调用一次，计算 avg_dl 和 IDF。"""
        self.n_docs = len(self.docs)
        if self.n_docs == 0:
            self.avg_dl = 0.0
            return
        total_len = sum(self.doc_len)
        self.avg_dl = float(total_len) / float(self.n_docs)
        # 预计算 IDF，避免查询时反复算 log
        # 使用 BM25 常见的 idf：log((N - df + 0.5) / (df + 0.5) + 1)
        # +1 是为了保证正值（Robertson-Sparck-Jones 变体）
        n = float(self.n_docs)
        for tk, df in self.df.items():
            self.idf[tk] = math.log((n - df + 0.5) / (df + 0.5) + 1.0)

    # ------------------------------------------------------------- #
    # 查询
    # ------------------------------------------------------------- #
    def search(self, query, topk=5):
        # type: (str, int) -> List[Dict[str, Any]]
        """检索。返回 topk 条结果，按分数降序。

        :param query: 自然语言查询
        :param topk: 返回条数上限
        :returns: [{'doc_id', 'score', 'text', 'meta'}, ...]
        """
        if self.n_docs == 0 or not query:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        # 去重（同一 token 在 query 里出现多次不额外加权）
        q_tokens = list(dict.fromkeys(q_tokens))

        scores = {}  # type: Dict[int, float]

        for tk in q_tokens:
            if tk not in self.postings:
                continue
            idf = self.idf.get(tk, 0.0)
            if idf <= 0:
                continue
            for doc_idx, tf in self.postings[tk]:
                dl = self.doc_len[doc_idx]
                if self.avg_dl > 0:
                    norm = 1.0 - _B + _B * (float(dl) / self.avg_dl)
                else:
                    norm = 1.0
                score = idf * (tf * (_K1 + 1)) / (tf + _K1 * norm)
                scores[doc_idx] = scores.get(doc_idx, 0.0) + score

        # 排序取 topk
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        out = []
        for doc_idx, sc in ranked[:topk]:
            d = self.docs[doc_idx]
            out.append({
                'doc_id': d['id'],
                'score': float(sc),
                'text': d['text'],
                'meta': d['meta'],
            })
        return out

    # ------------------------------------------------------------- #
    # 持久化
    # ------------------------------------------------------------- #
    def to_dict(self):
        # type: () -> Dict[str, Any]
        """序列化为可 JSON 存储的 dict。"""
        return {
            'version': _INDEX_VERSION,
            'docs': self.docs,
            'doc_len': self.doc_len,
            'avg_dl': self.avg_dl,
            # postings 里 tuple 要转 list，JSON 才认
            'postings': {
                tk: [[i, tf] for i, tf in lst]
                for tk, lst in self.postings.items()
            },
            'df': self.df,
            'idf': self.idf,
            'n_docs': self.n_docs,
        }

    @classmethod
    def from_dict(cls, data):
        # type: (Dict[str, Any]) -> 'BM25Index'
        """反序列化。"""
        v = int(data.get('version', 0))
        if v != _INDEX_VERSION:
            raise ValueError(
                '索引版本不兼容: got={} expect={}'.format(v, _INDEX_VERSION),
            )
        idx = cls()
        idx.docs = list(data.get('docs') or [])
        idx.doc_len = list(data.get('doc_len') or [])
        idx.avg_dl = float(data.get('avg_dl') or 0.0)
        raw_postings = data.get('postings') or {}
        idx.postings = {
            tk: [(int(i), int(tf)) for i, tf in lst]
            for tk, lst in raw_postings.items()
        }
        idx.df = {k: int(v) for k, v in (data.get('df') or {}).items()}
        idx.idf = {k: float(v) for k, v in (data.get('idf') or {}).items()}
        idx.n_docs = int(data.get('n_docs') or 0)
        return idx

    def save(self, path):
        # type: (str) -> None
        """保存到 JSON 文件（原子写）。"""
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(
                self.to_dict(), f,
                ensure_ascii=False, separators=(',', ':'),
            )
        os.replace(tmp, path)

    @classmethod
    def load(cls, path):
        # type: (str) -> 'BM25Index'
        """从 JSON 文件加载。"""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)


__all__ = ['BM25Index']
