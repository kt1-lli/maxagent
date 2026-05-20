#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 markdown 渲染器：粗体、代码块、列表、XSS 转义、行内代码。"""

from __future__ import absolute_import
from __future__ import print_function

from maxagent.ui.markdown_render import (
    extract_code_blocks,
    html_escape,
    render_markdown,
)


class TestHtmlEscape:
    def test_basic_special_chars(self):
        assert html_escape('<a>&b') == '&lt;a&gt;&amp;b'

    def test_none_returns_empty(self):
        assert html_escape(None) == ''

    def test_normal_text_passthrough(self):
        assert html_escape('hello world 你好') == 'hello world 你好'


class TestRenderInline:
    def test_bold(self):
        out = render_markdown('this is **bold** text')
        assert '<b>bold</b>' in out

    def test_italic(self):
        out = render_markdown('this is *italic* text')
        assert '<i>italic</i>' in out

    def test_inline_code(self):
        out = render_markdown('use `pymxs` here')
        assert '<code' in out
        assert 'pymxs' in out

    def test_link(self):
        out = render_markdown('see [docs](https://example.com)')
        assert 'href="https://example.com"' in out
        assert '>docs<' in out

    def test_xss_escape_in_plain(self):
        out = render_markdown('<script>alert(1)</script>')
        # 不能出现真正的 <script> 标签
        assert '<script>alert(1)</script>' not in out
        assert '&lt;script&gt;' in out

    def test_xss_inside_inline_code(self):
        # 即使在 inline code 里也不能逃出转义
        out = render_markdown('see `<script>` snippet')
        assert '<script>' not in out
        assert '&lt;script&gt;' in out


class TestRenderBlocks:
    def test_heading_levels(self):
        out = render_markdown('# Title\n## Sub')
        # 一级和二级都应渲染为 div + bold
        assert 'Title' in out and 'Sub' in out
        assert 'font-weight:bold' in out

    def test_unordered_list(self):
        out = render_markdown('- a\n- b\n- c')
        assert out.count('<li') == 3
        assert '<ul' in out

    def test_ordered_list(self):
        out = render_markdown('1. one\n2. two')
        assert out.count('<li') == 2
        assert '<ol' in out

    def test_fenced_code_block(self):
        md = '```python\nprint("hi")\n```'
        out = render_markdown(md)
        assert '<pre' in out
        assert 'print(&quot;hi&quot;)' in out or 'print("hi")' in out
        # 语言提示
        assert 'python' in out

    def test_quote_block(self):
        out = render_markdown('> quoted text')
        assert 'quoted text' in out
        assert 'border-left' in out

    def test_horizontal_rule(self):
        out = render_markdown('---')
        assert '<hr' in out

    def test_paragraph_separation(self):
        out = render_markdown('para1\n\npara2')
        # 两个段落应该分隔
        assert 'para1' in out and 'para2' in out

    def test_empty_text(self):
        assert render_markdown('') == ''
        assert render_markdown(None) == ''


class TestCodeBlocksExtract:
    def test_single_block(self):
        md = '```python\nx = 1\n```'
        blocks = extract_code_blocks(md)
        assert blocks == [('python', 'x = 1')]

    def test_multiple_blocks(self):
        md = '```py\na\n```\nmid\n```js\nb\n```'
        blocks = extract_code_blocks(md)
        assert len(blocks) == 2
        assert blocks[0] == ('py', 'a')
        assert blocks[1] == ('js', 'b')

    def test_no_code(self):
        assert extract_code_blocks('plain text') == []

    def test_unclosed_block_recovers(self):
        # 没有闭合 fence 的代码块应该被收到末尾
        md = '```py\nunclosed'
        blocks = extract_code_blocks(md)
        assert len(blocks) == 1
        assert 'unclosed' in blocks[0][1]
