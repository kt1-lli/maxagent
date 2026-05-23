#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""帮助文案 + 新增功能日志接入回归测试。

覆盖：
1. 帮助文案必须包含"自定义 Header"段及 DeepSeek 示例；
2. 帮助文案 HTML 必须显式上色（暗色主题下保证对比度）；
3. 截图 / 附件预览条 / 气泡图片操作 三个新增 UI 模块都已接入
   ``maxagent.logger.get_logger``。

只做静态扫描——不依赖 Qt 运行时，CI 稳定。
"""

from __future__ import absolute_import
from __future__ import print_function

import io
import os


ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)),
)
SETTINGS_FILE = os.path.join(ROOT, 'maxagent', 'ui', 'settings_dialog.py')
SCREENSHOT_FILE = os.path.join(
    ROOT, 'maxagent', 'ui', 'screenshot_overlay.py',
)
INPUT_ATT_FILE = os.path.join(
    ROOT, 'maxagent', 'ui', 'input_attachments.py',
)
BUBBLES_FILE = os.path.join(ROOT, 'maxagent', 'ui', 'bubbles.py')


def _read(path):
    with io.open(path, 'r', encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------------- #
# 1) 帮助文案
# ---------------------------------------------------------------------- #
def test_help_contains_custom_header_section():
    """帮助应有"自定义 Header"小节，包含 KEY=VALUE 格式说明。"""
    src = _read(SETTINGS_FILE)
    assert '自定义 Header' in src, '帮助应保留"自定义 Header"标题/字眼'
    assert 'KEY=VALUE' in src, '帮助应说明 KEY=VALUE 格式'
    # DeepSeek 直连留空建议
    assert '直连官方' in src or '留空即可' in src


def test_help_contains_deepseek_header_examples():
    """帮助应给出至少两个 DeepSeek 场景的 Header 示例。"""
    src = _read(SETTINGS_FILE)
    # 至少出现一个 X- 前缀的示例 Header
    assert 'X-Org-Id' in src or 'X-Trace-Id' in src, (
        '应给出 X-* 自定义 Header 示例'
    )


def test_help_warns_against_overriding_auth_header():
    """帮助必须警示不要覆盖 Authorization / Content-Type。"""
    src = _read(SETTINGS_FILE)
    assert 'Authorization' in src
    assert 'Content-Type' in src
    # 文案里应有"勿/不要/请勿/避免"之一
    assert any(w in src for w in ('请勿', '不要', '避免', '勿覆盖'))


def test_help_html_has_explicit_colors_for_contrast():
    """帮助 HTML 必须显式定义颜色，保证暗色主题下对比度。"""
    src = _read(SETTINGS_FILE)
    # 帮助样式块里至少应出现这些颜色锚点
    must_have = ['#e8e8e8', '#ffd166', '#2a2a2a']
    for c in must_have:
        assert c in src, '帮助文案缺少颜色 {}'.format(c)
    # QTextBrowser 自身也应被设为高对比度暗背景
    assert 'QTextBrowser' in src and 'background:#1e1e1e' in src


# ---------------------------------------------------------------------- #
# 2) 日志接入
# ---------------------------------------------------------------------- #
def _assert_logger_imported(path):
    src = _read(path)
    assert 'from ..logger import get_logger' in src, (
        '{} 未导入 logger'.format(os.path.basename(path))
    )
    assert 'get_logger(__name__)' in src, (
        '{} 未实例化 module logger'.format(os.path.basename(path))
    )


def test_screenshot_overlay_logger_attached():
    _assert_logger_imported(SCREENSHOT_FILE)
    src = _read(SCREENSHOT_FILE)
    # 关键路径埋点：完成 / 取消 至少各一次
    assert 'logger.info' in src
    assert 'logger.debug' in src


def test_input_attachments_logger_attached():
    _assert_logger_imported(INPUT_ATT_FILE)
    src = _read(INPUT_ATT_FILE)
    # 复制 + 加入/删除 都应有埋点
    assert 'clipboard_copy' in src
    assert 'attach_add' in src
    assert 'attach_remove' in src


def test_bubbles_logger_attached_for_image_ops():
    _assert_logger_imported(BUBBLES_FILE)
    src = _read(BUBBLES_FILE)
    # 三个右键操作各自有埋点关键字
    assert 'bubble_copy_image' in src
    assert 'bubble_save_image_as' in src
    assert 'bubble_open_viewer' in src


# ---------------------------------------------------------------------- #
# 3) 截图模块的兜底防呆
# ---------------------------------------------------------------------- #
def test_screenshot_logs_failures():
    """截图核心入口的两类失败必须有 warning 落盘。"""
    src = _read(SCREENSHOT_FILE)
    assert 'primaryScreen' in src
    assert 'logger.warning' in src
