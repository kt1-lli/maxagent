#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户气泡图片复制能力 + 视觉降级提示条回归测试。

覆盖：
- ``copy_attachment_to_clipboard``：单张图片附件写入剪贴板时
  同时填充 image / urls / text 三种 MIME，便于 Word/微信/资源管理器
  这类异构目标都能成功粘贴。
- ``UserBubble`` 缩略图右键菜单可用：弹出"复制图片/复制路径/另存为/查看大图"。
- ``VisionHintBar.set_state``：根据 has_attachments × vision_enabled
  × vision_supported 的真值组合控制显隐与文案。
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


def _png_bytes():
    """1×1 透明 PNG 的最小合法字节流（QPixmap 不一定能解码——
    某些版本 libpng 要求严格 IDAT，此处仅作 raw bytes 测试备用，
    QPixmap 相关 fixture 改用 ``QPixmap.save`` 自己生成图片）。"""
    return bytes([
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
        0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
        0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
        0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
        0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41,
        0x54, 0x78, 0x9C, 0x62, 0x00, 0x01, 0x00, 0x00,
        0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00,
        0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,
        0x42, 0x60, 0x82,
    ])


@pytest.fixture
def png_attachment(qapp, tmp_path):
    """在 tmp_path 落一张 4×4 红色 PNG，并构造 ``Attachment`` 实例。

    用 QPixmap 自己生成 + save，保证 QPixmap 加载时一定不为 null
    （写死字节流容易被 libpng 严格校验拒绝）。
    """
    from maxagent.attachments import Attachment
    from maxagent.qt_compat import QtCore as QC
    from maxagent.qt_compat import QtGui as QG
    pix = QG.QPixmap(4, 4)
    pix.fill(QG.QColor(255, 0, 0))
    p = tmp_path / 'sample.png'
    ok = pix.save(str(p), 'PNG')
    assert ok, 'fixture 准备：无法生成临时 PNG'
    return Attachment(
        kind=Attachment.KIND_IMAGE,
        path=str(p),
        mime='image/png',
        size=p.stat().st_size,
        name='sample.png',
    )


# ---------------------------------------------------------------------- #
# copy_attachment_to_clipboard
# ---------------------------------------------------------------------- #
class TestCopyAttachmentToClipboard(object):

    def test_copy_writes_image_url_and_text(self, qapp, png_attachment):
        """成功写入剪贴板，且同时含 image / urls / text 三种 MIME。"""
        from maxagent.qt_compat import QtWidgets as QW
        from maxagent.ui.input_attachments import (
            copy_attachment_to_clipboard,
        )
        ok = copy_attachment_to_clipboard(png_attachment)
        assert ok is True
        cb = QW.QApplication.clipboard()
        mime = cb.mimeData()
        assert mime.hasImage(), '剪贴板缺少 image MIME'
        assert mime.hasUrls(), '剪贴板缺少 text/uri-list MIME'
        # 路径文本作为兜底，部分目标只接受纯文本时也能识别
        assert mime.hasText()
        assert png_attachment.path in mime.text()

    def test_copy_none_attachment_returns_false(self, qapp):
        from maxagent.ui.input_attachments import (
            copy_attachment_to_clipboard,
        )
        assert copy_attachment_to_clipboard(None) is False

    def test_copy_missing_file_returns_false(self, qapp, tmp_path):
        """图片路径不存在时优雅失败，不写脏剪贴板、不抛异常。"""
        from maxagent.attachments import Attachment
        from maxagent.ui.input_attachments import (
            copy_attachment_to_clipboard,
        )
        ghost = Attachment(
            kind=Attachment.KIND_IMAGE,
            path=str(tmp_path / 'nope.png'),
            mime='image/png',
            size=0,
            name='nope.png',
        )
        assert copy_attachment_to_clipboard(ghost) is False


# ---------------------------------------------------------------------- #
# UserBubble 缩略图右键菜单
# ---------------------------------------------------------------------- #
class TestUserBubbleThumbnailMenu(object):

    def test_thumbnail_has_context_menu_policy(self, qapp, png_attachment):
        """缩略图启用了 CustomContextMenu，让右键路由到自定义菜单。"""
        from maxagent.qt_compat import QtCore as QC
        from maxagent.ui.bubbles import UserBubble
        bubble = UserBubble('看看这张图', attachments=[png_attachment])
        # _bubble._inner 第二项之后才是图片网格，统一在 widgets() 中找 QLabel
        labels = bubble.findChildren(QC.QObject)
        # 找到设置过 CustomContextMenu 的 QLabel —— 即缩略图
        thumb_labels = []
        for w in labels:
            if w.__class__.__name__ != 'QLabel':
                continue
            try:
                pol = w.contextMenuPolicy()
            except Exception:  # pylint: disable=broad-except
                continue
            if pol == QC.Qt.ContextMenuPolicy.CustomContextMenu:
                thumb_labels.append(w)
        assert thumb_labels, '至少应有一个缩略图 QLabel 启用了自定义右键菜单'
        bubble.deleteLater()

    def test_open_viewer_does_not_crash(self, qapp, png_attachment, monkeypatch):
        """``_open_viewer`` 不抛异常即可（不真的弹模态窗）。"""
        from maxagent.ui import input_attachments as ia
        from maxagent.ui.bubbles import UserBubble

        called = {'n': 0}

        class _DummyDlg(object):
            @classmethod
            def show_for(cls, attachment, parent=None):
                called['n'] += 1

        monkeypatch.setattr(ia, 'ImageViewerDialog', _DummyDlg)
        UserBubble._open_viewer(png_attachment)
        assert called['n'] == 1


# ---------------------------------------------------------------------- #
# VisionHintBar.set_state
# ---------------------------------------------------------------------- #
class TestVisionHintBar(object):

    def test_hidden_when_no_attachments(self, qapp):
        from maxagent.ui.input_attachments import VisionHintBar
        bar = VisionHintBar()
        bar.set_state(
            has_attachments=False, vision_enabled=True,
            vision_supported=False, model_name='deepseek-chat',
        )
        assert bar.isVisible() is False
        bar.deleteLater()

    def test_hidden_when_vision_supported(self, qapp):
        """有附件 + 模型支持视觉：不应显示提示。"""
        from maxagent.ui.input_attachments import VisionHintBar
        bar = VisionHintBar()
        bar.show()  # 模拟之前显示过，确认 set_state 能正确收起
        bar.set_state(
            has_attachments=True, vision_enabled=True,
            vision_supported=True, model_name='gpt-4o',
        )
        assert bar.isVisible() is False
        bar.deleteLater()

    def test_shown_when_model_unsupported(self, qapp):
        from maxagent.ui.input_attachments import VisionHintBar
        bar = VisionHintBar()
        # offscreen 平台 isVisible 需要先 show 到顶层；这里用 _label 文案判断
        bar.set_state(
            has_attachments=True, vision_enabled=True,
            vision_supported=False, model_name='deepseek-chat',
        )
        # 文案里要含模型名，让用户知道是哪个 profile 的问题
        assert 'deepseek-chat' in bar._label.text()  # noqa: SLF001
        bar.deleteLater()

    def test_shown_when_vision_disabled_globally(self, qapp):
        from maxagent.ui.input_attachments import VisionHintBar
        bar = VisionHintBar()
        bar.set_state(
            has_attachments=True, vision_enabled=False,
            vision_supported=True, model_name='gpt-4o',
        )
        # 全局关闭时文案应不再绑定具体模型名，而强调"视觉已关闭"
        text = bar._label.text()  # noqa: SLF001
        assert '视觉' in text and '关闭' in text
        bar.deleteLater()

    def test_shown_text_html_escapes_model_name(self, qapp):
        """模型名含 ``<`` 时必须做 HTML 转义，防止注入。"""
        from maxagent.ui.input_attachments import VisionHintBar
        bar = VisionHintBar()
        bar.set_state(
            has_attachments=True, vision_enabled=True,
            vision_supported=False, model_name='<evil>tag',
        )
        text = bar._label.text()  # noqa: SLF001
        assert '<evil>' not in text
        assert '&lt;evil&gt;' in text
        bar.deleteLater()

    def test_switch_signal_emits(self, qapp):
        """点击"切换模型"按钮会发射 ``switch_profile_requested`` 信号。"""
        from maxagent.ui.input_attachments import VisionHintBar
        bar = VisionHintBar()
        captured = {'n': 0}
        bar.switch_profile_requested.connect(
            lambda: captured.update(n=captured['n'] + 1)
        )
        bar._switch_btn.click()  # noqa: SLF001
        assert captured['n'] == 1
        bar.deleteLater()


# ---------------------------------------------------------------------- #
# 视觉白名单默认值（DeepSeek-VL / pixtral / llama-3.2-vision）
# ---------------------------------------------------------------------- #
class TestVisionWhitelistDefaults(object):

    def test_new_models_in_default_whitelist(self):
        """新增的视觉模型应出现在默认白名单中（仅默认值，不影响老配置）。"""
        from maxagent.config import AppConfig
        cfg = AppConfig()
        wl = [s.lower() for s in cfg.vision_model_whitelist]
        for kw in ('deepseek-vl', 'pixtral', 'llama-3.2-vision',
                   'qwen-vl-max'):
            assert kw in wl, '{} 应在默认视觉白名单中'.format(kw)
