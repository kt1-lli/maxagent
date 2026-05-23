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


# ---------------------------------------------------------------------- #
# Keycap emoji 归一化（issue: PySide2 上 1️⃣2️⃣3️⃣ 渲染为豆腐块）
# ---------------------------------------------------------------------- #
class TestKeycapEmojiNormalization:
    """LLM 输出的 keycap 序列 (digit + U+FE0F + U+20E3) 在 Qt5 上必糊，
    必须在渲染入口替换为 BMP 圆圈数字 ① ② ③，并保证：
    - 同样的逻辑也作用于"复制全部"按钮的剪贴板内容
    - 不影响真 emoji 的输出（PySide6 路径上）
    """

    def test_keycap_digits_replaced_in_render(self):
        # "## 2️⃣ 技能（Skills）" 必须被改写成 "## ② 技能（Skills）"
        out = render_markdown('## 2\ufe0f\u20e3 技能（Skills）')
        # 不再包含原始 keycap 序列
        assert '\u20e3' not in out
        # 圆圈数字已就位
        assert '②' in out

    def test_keycap_without_vs16_still_replaced(self):
        # 部分 LLM 省略 U+FE0F，只发 "1\u20E3"
        out = render_markdown('1\u20e3 第一')
        assert '\u20e3' not in out
        assert '①' in out

    def test_all_ten_digits_mapped(self):
        # 0~9 全数字都要有兜底
        from maxagent.ui.markdown_render import _normalize_text_for_qt
        for d, circled in zip(
            '0123456789',
            '⓪①②③④⑤⑥⑦⑧⑨',
        ):
            assert _normalize_text_for_qt(d + '\ufe0f\u20e3') == circled

    def test_hash_keycap_strips_modifier(self):
        # "#️⃣" 在 Qt5 同样糊，去掉修饰符保留 "#"
        from maxagent.ui.markdown_render import _normalize_text_for_qt
        assert _normalize_text_for_qt('#\ufe0f\u20e3') == '#'
        assert _normalize_text_for_qt('*\ufe0f\u20e3') == '*'

    def test_preserve_real_emoji_under_pyside6(self):
        # use_real_emoji=True 路径不应丢 VS16（emoji 变体选择符）
        from maxagent.ui import emoji_compat
        from maxagent.ui.markdown_render import _normalize_text_for_qt
        old = emoji_compat.use_real_emoji()
        try:
            emoji_compat.set_use_real_emoji(True)
            # 真 emoji ⚠️ (U+26A0 U+FE0F) 在 PySide6 应保留 VS16
            out = _normalize_text_for_qt('⚠\ufe0f 警告')
            assert '\ufe0f' in out
        finally:
            emoji_compat.set_use_real_emoji(old)

    def test_strip_vs16_under_pyside2(self):
        from maxagent.ui import emoji_compat
        from maxagent.ui.markdown_render import _normalize_text_for_qt
        old = emoji_compat.use_real_emoji()
        try:
            emoji_compat.set_use_real_emoji(False)
            out = _normalize_text_for_qt('⚠\ufe0f 警告')
            assert '\ufe0f' not in out
            assert '⚠' in out and '警告' in out
        finally:
            emoji_compat.set_use_real_emoji(old)

    def test_no_keycap_text_unchanged(self):
        from maxagent.ui.markdown_render import _normalize_text_for_qt
        # 没有 keycap 也没有 VS16 的纯文本应原样返回
        s = 'Hello 你好 1.2.3 (a) [b]'
        # 注意：本测试同时覆盖 PySide6 路径（不丢 VS16 也无影响）
        out = _normalize_text_for_qt(s)
        assert out == s

    def test_empty_input(self):
        from maxagent.ui.markdown_render import _normalize_text_for_qt
        assert _normalize_text_for_qt('') == ''
        assert _normalize_text_for_qt(None) is None

    def test_render_full_skill_help_paragraph(self):
        """模拟用户截图里的真实场景：LLM 输出 "1️⃣ 工具（Tools）" 等。"""
        md = (
            '## 1\ufe0f\u20e3 工具（Tools）\n'
            '通过 save_skill 保存的技能流程存放在...\n\n'
            '## 2\ufe0f\u20e3 技能（Skills）\n'
            '通过 save_skill 保存的技能流程存放在：\n'
        )
        out = render_markdown(md)
        # 渲染结果不应再含 keycap codepoint
        assert '\u20e3' not in out
        assert '①' in out
        assert '②' in out
        # 标题文字仍然完整
        assert '工具' in out
        assert '技能' in out
