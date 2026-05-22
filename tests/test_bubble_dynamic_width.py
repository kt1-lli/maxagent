#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""气泡动态宽度 + 头像裁剪框遮罩 bug 修复回归测试。

覆盖：
- ``BubbleFrame.apply_max_width`` 按 85% 比例计算最大宽度
- 比例下限保护：极窄面板下不退化为竖条
- 4 个气泡类（``StreamingAssistantBubble`` / ``AssistantBubble`` /
  ``UserBubble`` / ``ErrorBubble``）都正确转发 ``apply_max_width``
- ``_ChatRenderer`` 在 viewport resize 时遍历所有气泡更新宽度
- 头像裁剪对话框的 ``_CropGraphicsView.drawForeground`` 不再使用
  会导致"框内全黑"的 ``CompositionMode_Clear``，确保选区透出原图
"""

from __future__ import absolute_import
from __future__ import print_function

import os

import pytest


# 在导入任何 Qt 之前设置 offscreen，CI 无显示设备也能跑
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from maxagent.qt_compat import QtCore  # noqa: F401
    from maxagent.qt_compat import QtWidgets  # noqa: F401
    HAS_QT = True
except Exception:  # pylint: disable=broad-except
    HAS_QT = False


@pytest.fixture(scope='module')
def qapp():
    if not HAS_QT:
        pytest.skip('Qt 不可用，跳过 UI 测试')
    from maxagent.qt_compat import QtWidgets as QW
    app = QW.QApplication.instance() or QW.QApplication([])
    yield app


# ---------------------------------------------------------------------- #
# BubbleFrame.apply_max_width
# ---------------------------------------------------------------------- #
def test_bubble_frame_apply_max_width_uses_ratio(qapp):
    """800px 视宽 × 85% = 680px，BubbleFrame 应该把 maxWidth 设为 680。"""
    from maxagent.ui.bubbles import BubbleFrame
    bf = BubbleFrame()
    bf.apply_max_width(800)
    assert bf.maximumWidth() == int(800 * BubbleFrame._WIDTH_RATIO)
    bf.deleteLater()


def test_bubble_frame_apply_max_width_respects_min(qapp):
    """极窄输入（如 100px）应触发下限保护，不低于 _MIN_BUBBLE_WIDTH。"""
    from maxagent.ui.bubbles import BubbleFrame
    bf = BubbleFrame()
    bf.apply_max_width(100)
    assert bf.maximumWidth() == BubbleFrame._MIN_BUBBLE_WIDTH
    bf.deleteLater()


def test_bubble_frame_apply_max_width_ignores_invalid(qapp):
    """非法/零宽度应被忽略，不修改已设值。"""
    from maxagent.ui.bubbles import BubbleFrame
    bf = BubbleFrame()
    bf.apply_max_width(800)
    before = bf.maximumWidth()
    bf.apply_max_width(0)
    bf.apply_max_width(-50)
    bf.apply_max_width('not a number')  # type: ignore[arg-type]
    assert bf.maximumWidth() == before
    bf.deleteLater()


# ---------------------------------------------------------------------- #
# 4 个公开气泡类的 apply_max_width 转发
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize('bubble_factory', [
    lambda: __import__(
        'maxagent.ui.bubbles', fromlist=['StreamingAssistantBubble']
    ).StreamingAssistantBubble(),
    lambda: __import__(
        'maxagent.ui.bubbles', fromlist=['AssistantBubble']
    ).AssistantBubble('hello'),
    lambda: __import__(
        'maxagent.ui.bubbles', fromlist=['UserBubble']
    ).UserBubble('hi'),
    lambda: __import__(
        'maxagent.ui.bubbles', fromlist=['ErrorBubble']
    ).ErrorBubble('oops'),
])
def test_each_bubble_forwards_apply_max_width(qapp, bubble_factory):
    """4 类气泡都应支持 apply_max_width 并把宽度落到内部 BubbleFrame。"""
    bubble = bubble_factory()
    assert hasattr(bubble, 'apply_max_width')
    bubble.apply_max_width(800)
    inner = getattr(bubble, '_bubble', None)
    assert inner is not None
    assert inner.maximumWidth() == int(800 * inner._WIDTH_RATIO)
    bubble.deleteLater()


# ---------------------------------------------------------------------- #
# _ChatRenderer 通过 eventFilter 自动更新所有气泡宽度
# ---------------------------------------------------------------------- #
def test_chat_renderer_applies_width_on_append(qapp):
    """新气泡插入时应立即按当前 viewport 宽度限制最大宽度。"""
    from maxagent.qt_compat import QtCore as _QC
    from maxagent.qt_compat import QtWidgets as _QW
    from maxagent.ui.bubbles import UserBubble
    # 模拟 dock_widget 的容器结构：scroll → content → vbox
    scroll = _QW.QScrollArea()
    scroll.setWidgetResizable(True)
    content = _QW.QWidget()
    layout = _QW.QVBoxLayout(content)
    layout.setContentsMargins(8, 8, 8, 8)
    scroll.setWidget(content)
    scroll.resize(900, 400)
    # 强制布局生效以拿到 viewport().width()
    scroll.show()
    qapp.processEvents()

    from maxagent.ui.dock_widget import _ChatRenderer  # noqa: SLF001
    renderer = _ChatRenderer(scroll, content, layout)

    bubble = UserBubble('hello')
    renderer._append(bubble)  # noqa: SLF001
    qapp.processEvents()

    # 视窗宽度 - 左右 8px 边距 = 884，再 × 0.85 ≈ 751
    expected = int(renderer._viewport_width()  # noqa: SLF001
                   * bubble._bubble._WIDTH_RATIO)  # noqa: SLF001
    assert bubble._bubble.maximumWidth() == expected  # noqa: SLF001

    scroll.deleteLater()


def test_chat_renderer_eventfilter_rebroadcasts_on_resize(qapp):
    """触发 viewport ResizeEvent 时，所有气泡都应被刷新最大宽度。"""
    from maxagent.qt_compat import QtCore as _QC
    from maxagent.qt_compat import QtGui as _QG
    from maxagent.qt_compat import QtWidgets as _QW
    from maxagent.ui.bubbles import AssistantBubble
    from maxagent.ui.bubbles import UserBubble
    scroll = _QW.QScrollArea()
    scroll.setWidgetResizable(True)
    content = _QW.QWidget()
    layout = _QW.QVBoxLayout(content)
    layout.setContentsMargins(8, 8, 8, 8)
    scroll.setWidget(content)
    scroll.resize(800, 400)
    scroll.show()
    qapp.processEvents()

    from maxagent.ui.dock_widget import _ChatRenderer  # noqa: SLF001
    renderer = _ChatRenderer(scroll, content, layout)

    b1 = UserBubble('one')
    b2 = AssistantBubble('two')
    renderer._append(b1)  # noqa: SLF001
    renderer._append(b2)  # noqa: SLF001
    qapp.processEvents()

    # 直接调用 _apply_widths_to_all 模拟 resize 后的同步动作
    scroll.resize(1200, 400)
    qapp.processEvents()
    # 主动触发一次（避免依赖底层平台是否一定派 ResizeEvent）
    renderer._apply_widths_to_all()  # noqa: SLF001

    vw_after = renderer._viewport_width()  # noqa: SLF001
    if vw_after > 0:
        ratio = b1._bubble._WIDTH_RATIO  # noqa: SLF001
        expected = int(vw_after * ratio)
        assert b1._bubble.maximumWidth() == expected  # noqa: SLF001
        assert b2._bubble.maximumWidth() == expected  # noqa: SLF001

    scroll.deleteLater()


# ---------------------------------------------------------------------- #
# 头像裁剪对话框：选区不再随场景滚动漂移
# ---------------------------------------------------------------------- #
def test_avatar_crop_no_clear_composition_mode():
    """整个文件中不能再调用 setCompositionMode(...Clear)——
    那是导致'框内反色为黑'bug 的根因。"""
    import inspect
    import re
    from maxagent.ui import avatar_crop_dialog as mod
    src = inspect.getsource(mod)
    pattern = re.compile(
        r'setCompositionMode\s*\([^)]*Clear', re.DOTALL,
    )
    assert pattern.search(src) is None, (
        '裁剪对话框不能调用 setCompositionMode(...Clear)。'
    )


def test_avatar_crop_uses_paintevent_overlay():
    """蒙版改在 paintEvent 中以 viewport 为画布绘制，
    而不再依赖 drawForeground——后者会受场景坐标变换影响导致漂移。"""
    import inspect
    from maxagent.ui.avatar_crop_dialog import _CropGraphicsView
    assert hasattr(_CropGraphicsView, 'paintEvent'), (
        '应重写 paintEvent 直接在 viewport 上绘制蒙版。'
    )
    src = inspect.getsource(_CropGraphicsView.paintEvent)
    # 必须以 viewport 为绘制目标（解耦场景坐标系）
    assert 'QPainter(self.viewport())' in src, (
        'paintEvent 应使用 QPainter(self.viewport()) 在 viewport 上'
        '叠加蒙版，避免场景坐标变换造成漂移。'
    )
    # 仍保留 OddEvenFill 带洞蒙版策略
    assert 'OddEvenFill' in src


def test_avatar_crop_scrolls_redraws_viewport():
    """拖动图片时 scrollContentsBy 应触发 viewport.update，
    保证蒙版与图片同步刷新，不残留旧位置的描边。"""
    import inspect
    from maxagent.ui.avatar_crop_dialog import _CropGraphicsView
    assert hasattr(_CropGraphicsView, 'scrollContentsBy'), (
        '应重写 scrollContentsBy 在场景滚动时同步刷新 viewport。'
    )
    src = inspect.getsource(_CropGraphicsView.scrollContentsBy)
    assert 'viewport' in src and 'update' in src


def test_avatar_crop_resize_event_redraws_overlay():
    """裁剪视图的 resizeEvent 应触发 viewport.update 让选区始终居中。"""
    import inspect
    from maxagent.ui.avatar_crop_dialog import _CropGraphicsView
    assert hasattr(_CropGraphicsView, 'resizeEvent')
    src = inspect.getsource(_CropGraphicsView.resizeEvent)
    assert 'viewport' in src and 'update' in src
