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


# ---------------------------------------------------------------------- #
# 序号 / 中文粘连修复（issue: "1自定义工具" 渲染遮挡）
# ---------------------------------------------------------------------- #
class TestNumberedHeadingSpacing:
    """h2/h3 标题里"数字+点+中文"必须留出可见间距，
    否则 Qt RichText 在大字号 + 粗体下会把 "1." 和后续中文挤在一起。"""

    def test_h2_with_digit_dot_chinese(self):
        out = render_markdown('## 1. 自定义工具 (Learned Tools)')
        # 渲染必须包含数字、内容
        assert '自定义工具' in out
        # 关键：标题正文里 "1." 与汉字之间必须有非普通空格的硬间距
        # （NBSP \u00a0 或 全角空格 \u3000）确保 Qt RichText 不会压缩。
        assert '1.\u00a0\u00a0自定义工具' in out or \
               '1.\u00a0自定义工具' in out or \
               '1.\u3000自定义工具' in out

    def test_h2_no_space_between_digit_and_chinese(self):
        """LLM 偶尔输出 "## 1自定义工具"（直接贴），渲染层应自动补点+间距。"""
        out = render_markdown('## 1自定义工具')
        assert '1.\u00a0\u00a0自定义工具' in out or \
               '1.\u00a0自定义工具' in out

    def test_circled_number_still_padded(self):
        """老的圈圈数字逻辑不能被新规则破坏。"""
        out = render_markdown('## ① 自定义工具')
        assert '自定义工具' in out
        # 圈圈数字与汉字之间也要补半角空格
        assert '① 自定义工具' in out

    def test_ordered_list_padding_left(self):
        """有序列表 <ol> 必须有 padding-left，让序号 marker 与文字隔开。"""
        out = render_markdown('1. one\n2. two')
        assert '<ol' in out
        assert 'padding-left' in out

    def test_unordered_list_padding_left(self):
        out = render_markdown('- a\n- b')
        assert '<ul' in out
        assert 'padding-left' in out

    def test_normal_paragraph_unaffected(self):
        """普通正文里的 "1." 不会被错误改写。"""
        out = render_markdown('先做 1. 再做 2.')
        # 段落正文不走 heading 规则，"1." 不应被插入间距
        assert '先做 1.' in out
