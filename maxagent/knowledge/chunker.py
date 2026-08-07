#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown / 纯文本文档切块器。

BM25 检索的粒度决定了返回结果的可用性：
- 整篇文档做一个 doc：召回后仍需人肉翻找，LLM 也看不完
- 一行一 doc：上下文丢失、噪声大
- 按标题分段 + 大段落再切：兼顾语义完整与粒度

策略：
1. Markdown：按 heading（#、##、###）切段；heading 层级作为 meta
2. 单段过长（> max_chars）：按段落 → 句子逐级切
3. 每块保留 heading 路径（"# 3ds Max > ## Primitive > ### Box"），
   便于结果展示和 LLM 理解上下文
4. 纯文本：按空行切段，同上限逻辑
"""

from __future__ import absolute_import
from __future__ import print_function

import re
from typing import Any
from typing import Dict
from typing import List


# 单块最大字符数（超过则细切）
DEFAULT_MAX_CHARS = 800
# 单块最小字符数（低于则合并到下一块，避免过多琐碎块）
DEFAULT_MIN_CHARS = 40

_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+?)\s*$')


def _split_long(text, max_chars):
    # type: (str, int) -> List[str]
    """把过长文本按段落 → 句子逐级切。"""
    if len(text) <= max_chars:
        return [text]

    # 先按空行切段落
    parts = re.split(r'\n\s*\n', text)
    out = []
    buf = ''
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) > max_chars:
            # 段落本身也过长，按句号 / 换行硬切
            # 中英文句号 + 感叹号 + 问号 + 换行
            sentences = re.split(r'(?<=[。！？!?\n])', p)
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                if len(buf) + len(s) + 1 > max_chars and buf:
                    out.append(buf)
                    buf = s
                else:
                    buf = buf + ('\n' if buf else '') + s
        else:
            if len(buf) + len(p) + 2 > max_chars and buf:
                out.append(buf)
                buf = p
            else:
                buf = buf + ('\n\n' if buf else '') + p
    if buf:
        out.append(buf)
    return out


def chunk_markdown(text, max_chars=DEFAULT_MAX_CHARS, min_chars=DEFAULT_MIN_CHARS,
                   source_name=''):
    # type: (str, int, int, str) -> List[Dict[str, Any]]
    """把 markdown 文本切成块。

    :param text: markdown 原文
    :param max_chars: 单块最大字符数
    :param min_chars: 单块最小字符数（低于则合并）
    :param source_name: 数据源标签，写入每块的 meta
    :returns: List[dict]，每项含：
        - text: 块内容
        - heading_path: 标题路径，如 "Primitive > Box"
        - heading_level: 最深标题层级 int
        - source: source_name
        - offset: 在原文中的字符起点
    """
    if not text:
        return []

    lines = text.splitlines()
    # 当前 heading 栈：[(level, title), ...]
    heading_stack = []  # type: List[List[Any]]

    blocks = []  # type: List[Dict[str, Any]]
    buf_lines = []  # type: List[str]
    buf_start_line = 0

    def _flush(end_line):
        # 把 buf_lines 作为一个 "raw block" 存下来，稍后统一细切
        if not buf_lines:
            return
        raw = '\n'.join(buf_lines).strip()
        if not raw:
            return
        path = ' > '.join(h[1] for h in heading_stack) if heading_stack else ''
        level = heading_stack[-1][0] if heading_stack else 0
        # 计算 offset（近似：按行号）
        offset = sum(len(l) + 1 for l in lines[:buf_start_line])
        blocks.append({
            'text': raw,
            'heading_path': path,
            'heading_level': level,
            'source': source_name,
            'offset': offset,
            'line_start': buf_start_line + 1,
            'line_end': end_line,
        })

    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            # 遇到新标题，先 flush 前一块
            _flush(i)
            buf_lines = []
            buf_start_line = i
            level = len(m.group(1))
            title = m.group(2).strip()
            # 更新 heading 栈：弹出 >= level 的
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append([level, title])
            # heading 本身也算入 buf（作为块首行）
            buf_lines.append(line)
        else:
            buf_lines.append(line)

    _flush(len(lines))

    # 第二阶段：把 raw block 里过长的细切；过短的合并
    out = []  # type: List[Dict[str, Any]]
    for blk in blocks:
        raw = blk['text']
        if len(raw) <= max_chars:
            if out and len(out[-1]['text']) + len(raw) < max_chars \
                    and out[-1]['heading_path'] == blk['heading_path'] \
                    and len(out[-1]['text']) < min_chars:
                # 同一标题下且前一块过短，合并
                out[-1]['text'] += '\n\n' + raw
                out[-1]['line_end'] = blk['line_end']
            else:
                out.append(dict(blk))
        else:
            # 细切
            parts = _split_long(raw, max_chars)
            for j, p in enumerate(parts):
                sub = dict(blk)
                sub['text'] = p
                if j > 0:
                    # 后续片段偏移递增
                    sub['offset'] = sub['offset'] + sum(
                        len(x) for x in parts[:j]
                    )
                out.append(sub)

    return out


def chunk_plaintext(text, max_chars=DEFAULT_MAX_CHARS, source_name=''):
    # type: (str, int, str) -> List[Dict[str, Any]]
    """纯文本切块：按空行切段 + 过长细切。"""
    if not text:
        return []
    paragraphs = re.split(r'\n\s*\n', text)
    out = []  # type: List[Dict[str, Any]]
    offset = 0
    line = 1
    for p in paragraphs:
        stripped = p.strip()
        if not stripped:
            offset += len(p) + 2
            line += p.count('\n') + 1
            continue
        pieces = _split_long(stripped, max_chars)
        for piece in pieces:
            out.append({
                'text': piece,
                'heading_path': '',
                'heading_level': 0,
                'source': source_name,
                'offset': offset,
                'line_start': line,
                'line_end': line + piece.count('\n'),
            })
        offset += len(p) + 2
        line += p.count('\n') + 1
    return out


__all__ = [
    'chunk_markdown',
    'chunk_plaintext',
    'DEFAULT_MAX_CHARS',
    'DEFAULT_MIN_CHARS',
]
