#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图功能回归测试：防止再次出现"截图后主 UI 丢失"问题。

历史 bug：``_on_snip`` 中调用 ``self.window().hide()`` 在 3ds Max 内嵌
docked 模式下会破坏主窗 docked 状态，导致截图完成后主面板不再显示。
修复方式改为 ``setWindowOpacity(0.0)`` 临时隐形，不动窗口可见性。

本文件只做源码级静态检查，不启动 Qt 事件循环（CI 容器无显示器）。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCK_WIDGET = os.path.join(REPO_ROOT, 'maxagent', 'ui', 'dock_widget.py')
OVERLAY = os.path.join(REPO_ROOT, 'maxagent', 'ui', 'screenshot_overlay.py')


@pytest.fixture(scope='module')
def dock_source():
    with open(DOCK_WIDGET, 'r', encoding='utf-8') as fh:
        return fh.read()


@pytest.fixture(scope='module')
def overlay_source():
    with open(OVERLAY, 'r', encoding='utf-8') as fh:
        return fh.read()


class TestSnipDoesNotHideMainWindow:
    """核心回归点：截图流程绝不调用 self.window().hide()。"""

    def test_no_window_hide_call(self, dock_source):
        # 找到 _on_snip 函数体
        m = re.search(
            r'def _on_snip\(self\):(.+?)(?=\n    def \w|\nclass \w)',
            dock_source, re.DOTALL,
        )
        assert m is not None, '_on_snip 函数未找到'
        body = m.group(1)
        # 剔除 docstring 与 # 注释，避免文档里提到 hide() 误判
        body_no_doc = re.sub(r'""".*?"""', '', body, flags=re.DOTALL)
        body_clean_lines = []
        for line in body_no_doc.splitlines():
            stripped = line.split('#', 1)[0]
            body_clean_lines.append(stripped)
        body_clean = '\n'.join(body_clean_lines)
        # 不允许出现 window().hide() —— 这是历史 bug 的根因
        assert 'window().hide()' not in body_clean, (
            '_on_snip 不应调用 window().hide()，会破坏 Max docked 状态'
        )

    def test_uses_window_opacity_strategy(self, dock_source):
        # 改用透明度策略隐形主窗
        assert 'setWindowOpacity' in dock_source

    def test_opacity_restored_in_finally(self, dock_source):
        # 透明度恢复必须在 finally 中，保证抓屏失败也能恢复
        m = re.search(
            r'def _on_snip\(self\):(.+?)(?=\n    def \w|\nclass \w)',
            dock_source, re.DOTALL,
        )
        body = m.group(1)
        # finally 块中必须出现透明度恢复
        finally_match = re.search(
            r'finally:\s*\n(.+?)(?=\n        if pix is None|\n    def )',
            body, re.DOTALL,
        )
        assert finally_match is not None, '_on_snip 缺少 finally 块'
        assert 'setWindowOpacity' in finally_match.group(1), (
            'finally 块必须恢复 windowOpacity'
        )


class TestOverlayHardening:
    """蒙层自身回归点。"""

    def test_no_tool_flag(self, overlay_source):
        # Tool flag 在部分 WM 下抢不到键盘焦点，已移除
        assert 'WindowType.Tool' not in overlay_source

    def test_uses_close_event_hook(self, overlay_source):
        # 用 closeEvent + hook 退出嵌套 loop（替代不可靠的 destroyed 信号）
        assert 'closeEvent' in overlay_source
        assert '_on_close_hook' in overlay_source

    def test_no_destroyed_signal_dependency(self, overlay_source):
        # destroyed 触发时 C++ 对象已析构，取结果会 RuntimeError
        assert 'destroyed.connect' not in overlay_source

    def test_event_loop_quit_on_close(self, overlay_source):
        # 关闭时必须能退出 QEventLoop
        assert 'loop.quit()' in overlay_source
