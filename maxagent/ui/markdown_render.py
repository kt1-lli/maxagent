#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零依赖的迷你 Markdown 渲染器，把 LLM 文本输出转成 Qt RichText (HTML)。

设计原则：
- 不引入 markdown / mistune 等第三方库，符合 MaxAgent 的零外部依赖偏好
- 只覆盖 LLM 输出常见的 80%+ 元素：粗体 / 斜体 / inline code /
  fenced code block / 有序/无序列表 / 引用 / 链接 / 标题
- 必须先做 HTML 转义，再插入格式化标签，避免 XSS / 渲染错乱
- 渲染结果给 Qt RichText 用，所以避开 Qt 不支持或支持很弱的 CSS

非目标：
- 不支持表格、脚注、删除线、嵌套列表、混合复杂结构
- 不严格符合 CommonMark 规范
"""

from __future__ import absolute_import
from __future__ import print_function

import re
from typing import List
from typing import Tuple


# ---------------------------------------------------------------------- #
# HTML 基础转义
# ---------------------------------------------------------------------- #
def html_escape(text):
    # type: (str) -> str
    """对一段普通文本做 HTML 转义。"""
    if text is None:
        return ''
    return (
        str(text)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


# ---------------------------------------------------------------------- #
# 行内 markdown
# ---------------------------------------------------------------------- #
# 顺序很重要：先 inline code，再 bold/italic/link，避免误伤 code 内的
# 特殊字符。inline code 内部不再做 markdown 解析。
_RE_INLINE_CODE = re.compile(r'`([^`\n]+)`')
# 加粗 / 斜体支持嵌套：先把 inline code 抠出，再处理 bold（贪婪匹配最长），
# 最后处理 italic。bold 用 (?:.+?) 非贪婪避免吞掉后续多个 ** 段。
_RE_BOLD = re.compile(r'\*\*(?=\S)([^\n]+?)(?<=\S)\*\*')
# italic 必须避开两侧都是字母数字的情况（如 a_b_c, 1*2*3），
# 这里只做保守支持：前后非星号且内容非空。
_RE_ITALIC = re.compile(r'(?<![\*\w])\*(?=\S)([^\*\n]+?)(?<=\S)\*(?!\*)')
# [text](url) 形式
_RE_LINK = re.compile(r'\[([^\]]+)\]\(([^)\s]+)\)')


def _render_inline(text):
    # type: (str) -> str
    """对一行已转义文本做行内格式化。

    输入必须是已经 HTML 转义过的安全字符串（占位符 \x00...\x01 内部使用）。
    """
    # 1. inline code 先抠出来用占位符替换，避免后续规则误伤
    code_pieces = []  # type: List[str]

    def _stash_code(m):
        idx = len(code_pieces)
        # inline code 已经处于转义文本中，&lt; &gt; 等已被转，原样保留
        code_pieces.append(
            '<code style="background:#1a1a1a;color:#e7c46c;'
            'padding:1px 4px;border-radius:3px;'
            'font-family:Consolas,\'Courier New\',monospace;">'
            + m.group(1) + '</code>'
        )
        return '\x00CODE{}\x01'.format(idx)

    text = _RE_INLINE_CODE.sub(_stash_code, text)

    # 2. 链接 [text](url)
    def _replace_link(m):
        label = m.group(1)
        url = m.group(2)
        # url 已经在 escape 阶段处理过特殊字符
        return (
            '<a href="{u}" style="color:#7fb3d5;">{t}</a>'
            .format(u=url, t=label)
        )

    text = _RE_LINK.sub(_replace_link, text)

    # 3. 加粗、斜体
    text = _RE_BOLD.sub(r'<b>\1</b>', text)
    text = _RE_ITALIC.sub(r'<i>\1</i>', text)

    # 4. 把 inline code 占位符还原
    def _restore_code(m):
        idx = int(m.group(1))
        if 0 <= idx < len(code_pieces):
            return code_pieces[idx]
        return m.group(0)

    text = re.sub(r'\x00CODE(\d+)\x01', _restore_code, text)
    return text


# ---------------------------------------------------------------------- #
# 块级解析
# ---------------------------------------------------------------------- #
_RE_FENCE_OPEN = re.compile(r'^```([\w+\-]*)\s*$')
_RE_FENCE_CLOSE = re.compile(r'^```\s*$')
_RE_HEADING = re.compile(r'^(#{1,6})\s+(.*)$')
_RE_UL_ITEM = re.compile(r'^[\-\*\+]\s+(.+)$')
_RE_OL_ITEM = re.compile(r'^(\d+)\.\s+(.+)$')
_RE_QUOTE = re.compile(r'^>\s?(.*)$')
_RE_HR = re.compile(r'^[-*_]{3,}\s*$')


def render_markdown(text):
    # type: (str) -> str
    """把一段文本（可能含 markdown）渲染为 Qt RichText HTML。

    :param text: 原始 LLM 输出（未转义）
    :return: 安全的 HTML 字符串，可直接 setHtml / insertHtml
    """
    if not text:
        return ''
    lines = text.split('\n')
    out = []  # type: List[str]
    i = 0
    n = len(lines)

    # 当前正在累积的列表类型: None / 'ul' / 'ol'
    list_kind = None
    list_buf = []  # type: List[str]

    def _flush_list():
        # 把累计的列表项一次性吐出
        if not list_buf:
            return
        tag = 'ul' if list_kind == 'ul' else 'ol'
        out.append('<{tag} style="margin:4px 0 4px 18px;padding:0;">'.format(tag=tag))
        for item_html in list_buf:
            out.append(
                '<li style="margin:2px 0;">' + item_html + '</li>'
            )
        out.append('</{tag}>'.format(tag=tag))
        del list_buf[:]

    while i < n:
        line = lines[i]

        # 围栏代码块 ```lang ... ```
        m = _RE_FENCE_OPEN.match(line)
        if m:
            _flush_list()
            list_kind = None
            lang = m.group(1) or ''
            code_lines = []
            i += 1
            while i < n and not _RE_FENCE_CLOSE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            # 跳过 fence close（如果存在）
            if i < n:
                i += 1
            code_body = '\n'.join(code_lines)
            # 用 <pre> 标签让 Qt 渲染等宽，html_escape 防注入
            # data-lang 用于上层提取语言（虽然 Qt 会忽略未知属性）
            label = lang.strip() or 'code'
            out.append(
                '<div style="margin:6px 0;background:#1a1a1a;'
                'border:1px solid #333;border-radius:4px;">'
                '<div style="background:#252525;padding:3px 8px;'
                'color:#888;font-size:9pt;'
                'font-family:Consolas,monospace;border-bottom:1px solid #333;">'
                '⌨ {label}</div>'
                '<pre style="margin:0;padding:8px 10px;color:#e0e0e0;'
                'font-family:Consolas,\'Courier New\',monospace;'
                'font-size:10pt;white-space:pre-wrap;'
                'word-wrap:break-word;">{code}</pre>'
                '</div>'.format(
                    label=html_escape(label),
                    code=html_escape(code_body),
                )
            )
            continue

        # 标题
        m = _RE_HEADING.match(line)
        if m:
            _flush_list()
            list_kind = None
            level = len(m.group(1))
            raw_content = m.group(2)
            # 在 Unicode 圈圈数字 / 方块数字（① ② ❶ ⑴ 等）后面如果直接接
            # CJK 文字，Qt RichText 在大字号 + 加粗时会把圈圈字符渲染成
            # 偏大的方框，与下一个汉字重叠。统一在两类字符之间补一个
            # 半角空格，避免视觉粘连。
            raw_content = re.sub(
                r'([\u2460-\u24FF\u2776-\u2793\u3251-\u32BF])'
                r'([\u4e00-\u9fff])',
                r'\1 \2',
                raw_content,
            )
            content = _render_inline(html_escape(raw_content))
            # 字号收敛：h1=14, h2=13, h3=12, h4+=11，避免大字号引起 emoji
            # / 圈圈字符的方块外溢。line-height 给行高留呼吸空间。
            size_pt = max(11, 15 - level)
            out.append(
                '<div style="font-size:{sz}pt;font-weight:bold;'
                'margin:8px 0 4px 0;line-height:1.5;color:#ffffff;">'
                '{c}</div>'.format(
                    sz=size_pt, c=content,
                )
            )
            i += 1
            continue

        # 引用
        m = _RE_QUOTE.match(line)
        if m:
            _flush_list()
            list_kind = None
            # 把连续的 > 行收集起来
            quote_lines = []
            while i < n:
                mm = _RE_QUOTE.match(lines[i])
                if not mm:
                    break
                quote_lines.append(mm.group(1))
                i += 1
            body = '<br>'.join(
                _render_inline(html_escape(ln)) for ln in quote_lines
            )
            out.append(
                '<div style="border-left:3px solid #555;'
                'padding:4px 10px;margin:4px 0;color:#aaa;'
                'background:#252525;">{}</div>'.format(body)
            )
            continue

        # 分割线
        if _RE_HR.match(line):
            _flush_list()
            list_kind = None
            out.append(
                '<hr style="border:none;border-top:1px solid #444;'
                'margin:8px 0;">'
            )
            i += 1
            continue

        # 无序列表项
        m = _RE_UL_ITEM.match(line)
        if m:
            if list_kind != 'ul':
                _flush_list()
                list_kind = 'ul'
            list_buf.append(_render_inline(html_escape(m.group(1))))
            i += 1
            continue

        # 有序列表项
        m = _RE_OL_ITEM.match(line)
        if m:
            if list_kind != 'ol':
                _flush_list()
                list_kind = 'ol'
            list_buf.append(_render_inline(html_escape(m.group(2))))
            i += 1
            continue

        # 普通段落 / 空行
        if not line.strip():
            _flush_list()
            list_kind = None
            # 单个空行 -> 段间距，连续空行只算一次（中文阅读放宽到 10px）
            out.append('<div style="height:10px;"></div>')
            i += 1
            continue

        # 默认按段落处理：合并连续的非块级行
        if list_kind is not None:
            _flush_list()
            list_kind = None
        para_lines = []
        while i < n:
            cur = lines[i]
            if not cur.strip():
                break
            if _RE_FENCE_OPEN.match(cur):
                break
            if _RE_HEADING.match(cur):
                break
            if _RE_UL_ITEM.match(cur):
                break
            if _RE_OL_ITEM.match(cur):
                break
            if _RE_QUOTE.match(cur):
                break
            if _RE_HR.match(cur):
                break
            para_lines.append(cur)
            i += 1
        body = '<br>'.join(
            _render_inline(html_escape(ln)) for ln in para_lines
        )
        out.append(
            '<div style="margin:2px 0;line-height:1.6;">{}</div>'.format(body)
        )

    _flush_list()
    return ''.join(out)


# ---------------------------------------------------------------------- #
# 工具函数：从一段 markdown 中抽取所有代码块（给"复制代码"按钮用）
# ---------------------------------------------------------------------- #
def extract_code_blocks(text):
    # type: (str) -> List[Tuple[str, str]]
    """提取所有 ``` 围栏代码块，返回 [(lang, code), ...]。

    用于 UI 在助手回复下方生成"复制代码"按钮。
    """
    if not text:
        return []
    blocks = []  # type: List[Tuple[str, str]]
    lines = text.split('\n')
    i = 0
    n = len(lines)
    while i < n:
        m = _RE_FENCE_OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        lang = m.group(1) or ''
        i += 1
        buf = []
        while i < n and not _RE_FENCE_CLOSE.match(lines[i]):
            buf.append(lines[i])
            i += 1
        if i < n:
            i += 1
        blocks.append((lang.strip(), '\n'.join(buf)))
    return blocks


# ---------------------------------------------------------------------- #
# 段落切分：把 markdown 文本切成 [('text', md), ('code', lang, code), ...]
# 用途：UI 把代码块渲染为独立 widget，方便用户精确选中、复制
# ---------------------------------------------------------------------- #
def split_into_segments(text):
    # type: (str) -> List[Tuple]
    """把 markdown 文本按代码块切分。

    返回列表，每项是：
    - ``('text', md_string)``  普通文本段（含其他 markdown 元素）
    - ``('code', lang, code)`` 围栏代码块

    切分原则：保留原始顺序；普通文本段交给 render_markdown 渲染；
    代码段交给上层独立 widget 处理（不再 HTML 化）。

    :param text: 原始 LLM 输出
    :return: 段落序列
    """
    if not text:
        return []
    segments = []  # type: List[Tuple]
    lines = text.split('\n')
    i = 0
    n = len(lines)
    text_buf = []  # type: List[str]

    def _flush_text():
        if text_buf:
            segments.append(('text', '\n'.join(text_buf)))
            del text_buf[:]

    while i < n:
        m = _RE_FENCE_OPEN.match(lines[i])
        if not m:
            text_buf.append(lines[i])
            i += 1
            continue
        # 遇到代码块开围栏
        _flush_text()
        lang = (m.group(1) or '').strip()
        i += 1
        code_lines = []
        while i < n and not _RE_FENCE_CLOSE.match(lines[i]):
            code_lines.append(lines[i])
            i += 1
        if i < n:
            i += 1
        segments.append(('code', lang, '\n'.join(code_lines)))

    _flush_text()
    return segments
