#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emoji_compat 单元测试。

只覆盖纯逻辑（不依赖 QApplication 渲染）：
- e() 在 PySide2 / PySide6 下的返回值切换
- set_use_real_emoji 覆盖
- apply_font_fallback 不会因为 setFamilies 缺失而报错
"""

from __future__ import absolute_import
from __future__ import print_function

import pytest


from maxagent.ui import emoji_compat as ec


def test_e_returns_emoji_when_real_emoji_enabled():
    ec.set_use_real_emoji(True)
    try:
        assert ec.e('🌐', '[net]') == '🌐'
        assert ec.e('🤖', '[bot]') == '🤖'
    finally:
        # 恢复默认
        ec.set_use_real_emoji(ec.IS_PYSIDE6 if False else ec._USE_REAL_EMOJI)


def test_e_returns_fallback_when_real_emoji_disabled():
    ec.set_use_real_emoji(False)
    try:
        assert ec.e('🌐', '[网]') == '[网]'
        assert ec.e('🎨', '★') == '★'
    finally:
        ec.set_use_real_emoji(True)


def test_use_real_emoji_getter_setter_roundtrip():
    original = ec.use_real_emoji()
    ec.set_use_real_emoji(not original)
    assert ec.use_real_emoji() == (not original)
    ec.set_use_real_emoji(original)
    assert ec.use_real_emoji() == original


def test_apply_font_fallback_handles_none_widget():
    # 不应抛异常
    ec.apply_font_fallback(None)


def test_apply_font_fallback_with_fake_widget():
    """模拟 QWidget：只要有 font() / setFont() 就能调用。"""

    class FakeFont(object):
        def __init__(self):
            self.families_set = None
            self.family_str = None

        def setFamilies(self, fams):
            self.families_set = list(fams)

        def setFamily(self, name):
            self.family_str = name

    class FakeWidget(object):
        def __init__(self):
            self._font = FakeFont()
            self._applied = None

        def font(self):
            return self._font

        def setFont(self, f):
            self._applied = f

    w = FakeWidget()
    ec.apply_font_fallback(w, families=['MyFont', 'FallbackFont'])
    assert w._applied is w._font
    assert w._font.families_set == ['MyFont', 'FallbackFont']


def test_apply_font_fallback_old_qt_no_setfamilies():
    """Qt 5.12 之前没有 setFamilies；走 setFamily 兜底。"""

    class OldFont(object):
        def __init__(self):
            self.family_str = None

        def setFamily(self, name):
            self.family_str = name

    class OldWidget(object):
        def __init__(self):
            self._font = OldFont()

        def font(self):
            return self._font

        def setFont(self, _f):
            pass

    w = OldWidget()
    ec.apply_font_fallback(w, families=['F1', 'F2'])
    assert 'F1' in w._font.family_str
    assert 'F2' in w._font.family_str


# ---------------------------------------------------------------------- #
# ee(): 主题表查询
# ---------------------------------------------------------------------- #
def test_ee_table_returns_emoji_when_real_emoji_enabled():
    ec.set_use_real_emoji(True)
    try:
        assert ec.ee('🌐') == '🌐'
        assert ec.ee('🤖') == '🤖'
    finally:
        ec.set_use_real_emoji(False)


def test_ee_table_returns_bmp_when_disabled():
    """禁用真 emoji 时，按 EMOJI_FALLBACK_TABLE 返回 BMP 单字符。"""
    ec.set_use_real_emoji(False)
    try:
        # 几个关键 UI 字符必须在表里
        assert ec.ee('🌐') == ec.EMOJI_FALLBACK_TABLE['🌐']
        assert ec.ee('🤖') == ec.EMOJI_FALLBACK_TABLE['🤖']
        assert ec.ee('✅') == '✓'
        assert ec.ee('❌') == '✗'
        assert ec.ee('🟢') == '●'
    finally:
        ec.set_use_real_emoji(True)


def test_ee_falls_back_to_explicit_when_table_missing():
    ec.set_use_real_emoji(False)
    try:
        # 故意构造一个表里没有的 emoji
        unknown = '🦄'
        assert unknown not in ec.EMOJI_FALLBACK_TABLE
        assert ec.ee(unknown, '?') == '?'
        # 没传 fallback 时返回原字符
        assert ec.ee(unknown) == unknown
    finally:
        ec.set_use_real_emoji(True)


def test_ee_table_chars_are_all_bmp():
    """兜底字符必须全部在 BMP 范围内（U+0000~U+FFFF），保证 Qt5 渲染。"""
    for emoji, fallback in ec.EMOJI_FALLBACK_TABLE.items():
        # fallback 通常是 1 个字符；若为多个，每个字符都必须是 BMP
        for ch in fallback:
            assert ord(ch) <= 0xFFFF, (
                'EMOJI_FALLBACK_TABLE[{!r}] = {!r} 超出 BMP 范围（U+{:04X}）'
                .format(emoji, fallback, ord(ch))
            )


def test_ee_table_covers_main_ui_emojis():
    """主 UI 用到的所有 emoji 都必须在表里，避免运行时回退到原 emoji。

    这条测试强制约束 EMOJI_FALLBACK_TABLE 的覆盖度，防止后续新增
    emoji 时漏配。
    """
    required = [
        '🌐', '🤖', '🎨', '📜', '❓',  # Tab 导航
        '✏️', '✅', '❌', '⚠', '⚠️',   # 状态符
        '🟢', '🚀', '🧹', '📝', '👋',  # 主 UI 状态行
        '🔧', '⏳', '👤',
    ]
    missing = [e for e in required if e not in ec.EMOJI_FALLBACK_TABLE]
    assert not missing, 'EMOJI_FALLBACK_TABLE 缺失主 UI 必需键: {}'.format(missing)


# ---------------------------------------------------------------------- #
# apply_font_fallback(recursive=True)：递归覆盖子控件
# ---------------------------------------------------------------------- #
def test_apply_font_fallback_recursive_covers_all_children():
    """recursive=True 必须对每个子控件都 setFont，不依赖 Qt 自动级联。"""

    class FakeFont(object):
        def __init__(self):
            self.families_set = None

        def setFamilies(self, fams):
            self.families_set = list(fams)

        def setFamily(self, name):
            pass

    class FakeChild(object):
        def __init__(self):
            self._font = FakeFont()
            self.applied = False

        def font(self):
            return self._font

        def setFont(self, _f):
            self.applied = True

    class FakeRoot(object):
        def __init__(self, children):
            self._font = FakeFont()
            self._children = children
            self.applied = False

        def font(self):
            return self._font

        def setFont(self, _f):
            self.applied = True

        def findChildren(self, _cls):
            return list(self._children)

    children = [FakeChild() for _ in range(5)]
    root = FakeRoot(children)

    ec.apply_font_fallback(root, families=['F1', 'F2'], recursive=True)

    # 根控件被 setFont
    assert root.applied is True
    # 全部子控件都被 setFont（关键断言：不依赖 Qt 自动级联）
    assert all(c.applied for c in children), '部分子控件未被 setFont'


def test_apply_font_fallback_recursive_default_off():
    """默认 recursive=False，不应触发 findChildren 调用。"""

    class FakeFont(object):
        def setFamilies(self, _fams):
            pass

        def setFamily(self, _name):
            pass

    class FakeRoot(object):
        def __init__(self):
            self._font = FakeFont()
            self.find_children_called = False

        def font(self):
            return self._font

        def setFont(self, _f):
            pass

        def findChildren(self, _cls):
            self.find_children_called = True
            return []

    root = FakeRoot()
    ec.apply_font_fallback(root, families=['F1'])
    # recursive 默认 False，不应调用 findChildren
    assert root.find_children_called is False


def test_apply_font_fallback_recursive_resilient_to_child_failure():
    """单个子控件 setFont 失败不应影响兄弟节点。"""

    class FakeFont(object):
        def setFamilies(self, _fams):
            pass

        def setFamily(self, _name):
            pass

    class BadChild(object):
        def font(self):
            return FakeFont()

        def setFont(self, _f):
            raise RuntimeError('bad child')

    class GoodChild(object):
        def __init__(self):
            self.applied = False

        def font(self):
            return FakeFont()

        def setFont(self, _f):
            self.applied = True

    good = GoodChild()
    bad = BadChild()

    class FakeRoot(object):
        def font(self):
            return FakeFont()

        def setFont(self, _f):
            pass

        def findChildren(self, _cls):
            # 故意把坏的放在前面
            return [bad, good]

    ec.apply_font_fallback(FakeRoot(), families=['F1'], recursive=True)
    # 即便 bad 抛异常，good 仍然应被 setFont
    assert good.applied is True


# ---------------------------------------------------------------------- #
# install_app_font_fallback：QApplication 级别的回退族
# ---------------------------------------------------------------------- #
def test_install_app_font_fallback_no_app_instance(monkeypatch):
    """没有 QApplication 实例时不应抛异常。"""
    from maxagent import qt_compat

    class FakeQApp(object):
        @staticmethod
        def instance():
            return None

    monkeypatch.setattr(qt_compat.QtWidgets, 'QApplication', FakeQApp)
    # 不应抛异常
    ec.install_app_font_fallback()


def test_install_app_font_fallback_applies_to_app(monkeypatch):
    """有 QApplication 实例时，应给 app 设置带回退族的字体。"""
    from maxagent import qt_compat

    class FakeFont(object):
        def __init__(self):
            self.families_set = None

        def setFamilies(self, fams):
            self.families_set = list(fams)

        def setFamily(self, name):
            self.families_set = [name]

    class FakeApp(object):
        def __init__(self):
            self._font = FakeFont()
            self.applied = False

        def font(self):
            return self._font

        def setFont(self, _f):
            self.applied = True

    fake_app = FakeApp()

    class FakeQApp(object):
        @staticmethod
        def instance():
            return fake_app

    monkeypatch.setattr(qt_compat.QtWidgets, 'QApplication', FakeQApp)
    ec.install_app_font_fallback()

    assert fake_app.applied is True
    assert fake_app._font.families_set is not None
    assert len(fake_app._font.families_set) > 0


# ---------------------------------------------------------------------- #
# btn_label：图标 + 文本组装
# ---------------------------------------------------------------------- #
def test_btn_label_pyside6_uses_real_emoji():
    """PySide6 下应使用真 emoji 字符。"""
    ec.set_use_real_emoji(True)
    try:
        assert ec.btn_label('🔄', '重载') == '🔄 重载'
        assert ec.btn_label('⚙️', '设置') == '⚙️ 设置'
    finally:
        ec.set_use_real_emoji(False)


def test_btn_label_pyside2_uses_bmp_fallback():
    """PySide2 下应按 EMOJI_FALLBACK_TABLE 自动取 BMP 兜底。"""
    ec.set_use_real_emoji(False)
    try:
        # 表里有 '🔄' → '⟳'，'⚙️' → '✱'
        assert ec.btn_label('🔄', '重载') == '⟳ 重载'
        assert ec.btn_label('⚙️', '设置') == '✱ 设置'
        assert ec.btn_label('🚀', '发送') == '► 发送'
    finally:
        ec.set_use_real_emoji(True)


def test_btn_label_custom_separator():
    """自定义分隔符（中文 / 全角空格 / 多空格等）。"""
    ec.set_use_real_emoji(False)
    try:
        assert ec.btn_label('🔄', '重载', sep='') == '⟳重载'
        assert ec.btn_label('🔄', '重载', sep='  ') == '⟳  重载'
    finally:
        ec.set_use_real_emoji(True)


def test_btn_label_empty_emoji_returns_text_only():
    """emoji_char 为空时直接返回 text，不加无意义前缀。"""
    ec.set_use_real_emoji(False)
    try:
        assert ec.btn_label('', '保存') == '保存'
        assert ec.btn_label(None, '保存') == '保存'
    finally:
        ec.set_use_real_emoji(True)


def test_btn_label_empty_text_returns_icon_only():
    """text 为空时仅返回图标，去掉尾随分隔符。"""
    ec.set_use_real_emoji(False)
    try:
        assert ec.btn_label('🔄', '') == '⟳'
    finally:
        ec.set_use_real_emoji(True)


def test_btn_label_new_table_entries_present():
    """本次新增的按钮图标条目都必须在 EMOJI_FALLBACK_TABLE 中。

    若有人删除了表条目，按钮美化效果会回落到原 emoji（PySide2 上糊）。
    """
    new_required = ['🔄', '💬', '🗜️', '👁', '🔌', '💾', '📂']
    missing = [e for e in new_required if e not in ec.EMOJI_FALLBACK_TABLE]
    assert not missing, '本次按钮美化用 emoji 缺失表条目: {}'.format(missing)


def test_show_hide_pair_have_distinct_fallback():
    """API Key 显示按钮在 PySide2 下切换"显示↔隐藏"必须有清晰可辨的兜底。

    背景：上一轮迭代给 show_key_btn 加了"切图标"视觉反馈
    （👁 显示 ↔ 🙈 隐藏），但当时只把 👁 加进了表，🙈 漏掉了。
    PySide2 + Win 字体回退族中没有 🙈 的字形，按钮切到隐藏态后会变
    豆腐块，这次必须确保两端都有 BMP 兜底，且兜底字符不能相同——否则
    用户在 PySide2 上完全看不出按钮"切了态"。
    """
    assert '🙈' in ec.EMOJI_FALLBACK_TABLE, (
        '🙈 缺少 BMP 兜底，PySide2 上隐藏态会渲染异常'
    )
    show_fb = ec.EMOJI_FALLBACK_TABLE['👁']
    hide_fb = ec.EMOJI_FALLBACK_TABLE['🙈']
    assert show_fb != hide_fb, (
        '👁 / 🙈 兜底字符冲突 ({!r})，PySide2 上"显示↔隐藏"切态用户'
        '完全看不出区别'.format(show_fb)
    )


def test_resource_management_emojis_have_fallback():
    """「我的资源」相关的全部 SMP emoji 都必须有 BMP 兜底。

    重构「我的资源」Tab 时新增了一批 SMP 平面 emoji（📦/🎓/🧰/📤/📥
    等），如果漏在表外，PySide2 上整页 Tab 标题与按钮都会变豆腐块。
    """
    required = ['📦', '🎓', '🧰', '📤', '📥', '📎', '📷', '🗑️', '✂️']
    missing = [
        e for e in required if e not in ec.EMOJI_FALLBACK_TABLE
    ]
    assert not missing, (
        '资源管理类 emoji 缺失表条目: {}（PySide2 上会变豆腐块）'.format(
            missing,
        )
    )


def test_all_fallback_chars_are_bmp_only():
    """整张表的兜底字符都必须落在 BMP 平面（≤ U+FFFF）。

    一旦兜底字符自身又是 SMP 平面字符，就会和原 emoji 在 PySide2 上
    一起糊，整个兜底机制白费。
    """
    smp_offenders = []
    for src, fb in ec.EMOJI_FALLBACK_TABLE.items():
        for ch in fb:
            if ord(ch) > 0xFFFF:
                smp_offenders.append((src, fb, hex(ord(ch))))
    assert not smp_offenders, (
        '以下兜底字符落在 SMP 平面，无法在 PySide2 + Win 稳定渲染: '
        '{}'.format(smp_offenders)
    )
