#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中英混合分词器（零外部依赖）。

设计原则：
1. 只用标准库 re，不依赖 jieba / nltk 等第三方
2. 英文按空格 + 标点切，转小写，保留数字
3. 中文用 unigram + bigram 混合，兼顾单字精准和词组语义
4. 代码标识符（如 ``Noisemodifier`` / ``rt.setProperty``）保留原样

分词粒度对 BM25 召回率影响很大：
- 只 unigram（单字）：中文分词过细，召回率高但精度低
- 只 bigram（双字）：容易漏词
- unigram + bigram 混合：综合最优，索引膨胀约 2x（可接受）
"""

from __future__ import absolute_import
from __future__ import print_function

import re
from typing import List


# 英文单词 / 代码标识符 / 数字：字母数字下划线点组合
# ``rt.setProperty`` / ``Noisemodifier`` / ``3ds`` / ``2022`` 都会作为整体保留
_TOKEN_ASCII_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_\.]*|[0-9]+(?:\.[0-9]+)?')

# 中文字符范围（含扩展）
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]')


def _is_cjk(ch):
    # type: (str) -> bool
    """是否为 CJK 字符。"""
    return bool(_CJK_RE.match(ch))


def tokenize(text, min_len=1, max_len=64):
    # type: (str, int, int) -> List[str]
    """对输入文本分词，返回 token 列表。

    :param text: 原始文本
    :param min_len: 最小 token 长度，过短的丢弃
    :param max_len: 最大 token 长度，过长的截断
    :returns: 归一化后的 token 列表（英文小写，中文原样）

    分词规则：
      1. 英文 / 数字 / 代码标识符：整体保留，转小写
      2. 连续中文段：拆成 unigram + bigram
      3. 其它字符（标点、空白）作为分隔符
    """
    if not text:
        return []

    tokens = []  # type: List[str]

    # 先按 ASCII 正则捞出所有英文 / 数字 / 代码 token 及其位置
    # 剩下的连续中文段落再单独处理
    pos = 0
    length = len(text)

    while pos < length:
        # 尝试匹配 ASCII token
        m = _TOKEN_ASCII_RE.match(text, pos)
        if m:
            tok = m.group(0).lower()
            if min_len <= len(tok) <= max_len:
                tokens.append(tok)
            pos = m.end()
            continue

        ch = text[pos]
        # 中文段：收集连续中文字符
        if _is_cjk(ch):
            start = pos
            while pos < length and _is_cjk(text[pos]):
                pos += 1
            cjk_seg = text[start:pos]
            # unigram
            for c in cjk_seg:
                tokens.append(c)
            # bigram（相邻两字）
            for i in range(len(cjk_seg) - 1):
                bg = cjk_seg[i:i + 2]
                tokens.append(bg)
            continue

        # 其它字符（标点、空白）作为分隔符，直接跳过
        pos += 1

    return tokens


def unique_tokens(tokens):
    # type: (List[str]) -> List[str]
    """去重保序。"""
    seen = set()
    out = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


__all__ = ['tokenize', 'unique_tokens']
