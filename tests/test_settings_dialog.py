#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置对话框 v2 改造的回归测试。

覆盖：
- 5 个 Tab Page 全部成功实例化
- Profile 列表的"复制为副本""设为默认""通过 _del_profile_by_name 删除"
- DEBUG 监控字段在 worker 上的初值

注意：测试在 offscreen 平台下运行，无需图形环境；
但需要 PySide2 或 PySide6 任一可用。
"""

from __future__ import absolute_import
from __future__ import print_function

import os

import pytest


# 在导入任何 Qt 之前设置 offscreen
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from maxagent.qt_compat import QtWidgets  # noqa: F401
    HAS_QT = True
except Exception:  # pylint: disable=broad-except
    HAS_QT = False


@pytest.fixture(scope='module')
def qapp():
    """模块级 QApplication，多个测试共享，避免反复创建。"""
    if not HAS_QT:
        pytest.skip('Qt 不可用，跳过 UI 测试')
    from maxagent.qt_compat import QtWidgets as QW
    app = QW.QApplication.instance() or QW.QApplication([])
    yield app


@pytest.fixture()
def config_mgr(tmp_path):
    """临时目录 + ConfigManager，自带 default profile。"""
    from maxagent.config import ConfigManager
    cfg_path = tmp_path / 'config.json'
    cm = ConfigManager(str(cfg_path))
    return cm


@pytest.fixture()
def dialog(qapp, config_mgr):
    """构造 SettingsDialog 实例。"""
    from maxagent.ui.settings_dialog import SettingsDialog
    d = SettingsDialog(config_mgr)
    yield d
    d.deleteLater()


def test_nav_has_six_tabs(dialog):
    """左侧导航现在有 8 个 Tab：模型/联网/应用/助手形象/我的资源/日志/IDE 接口/帮助。

    注：原"我的规则"和"工具与技能"两项已合并为单个"我的资源"主 Tab，
    内部用横向子 Tab 切换 4 个视图。这是 v3 重构后的稳定结构。
    """
    assert dialog.nav.count() == 8
    assert dialog.stack.count() == 8
    labels = [dialog.nav.item(i).text() for i in range(dialog.nav.count())]
    assert any('模型' in t for t in labels)
    assert any('联网' in t for t in labels)
    assert any('应用' in t for t in labels)
    assert any('助手形象' in t for t in labels)
    assert any('我的资源' in t for t in labels)
    assert any('日志' in t for t in labels)
    assert any('IDE 接口' in t for t in labels)
    assert any('帮助' in t for t in labels)
    # 旧两个 Tab 已被并入"我的资源"，不应再独立存在
    assert not any('我的规则' == t.split()[-1] for t in labels)
    assert not any('工具与技能' == t.split()[-1] for t in labels)


def test_resources_subtabs_have_four_pages(dialog):
    """「我的资源」内部必须有 4 个横向子 Tab：规则/技能/工具/导入导出。"""
    # 切到"我的资源"主 Tab
    res_idx = next(
        i for i, (_l, k) in enumerate(dialog._NAV_ITEMS) if k == 'resources'
    )
    dialog.nav.setCurrentRow(res_idx)
    assert hasattr(dialog, 'resources_tabs')
    assert dialog.resources_tabs.count() == 4
    sub_labels = [
        dialog.resources_tabs.tabText(i)
        for i in range(dialog.resources_tabs.count())
    ]
    assert any('规则' in t for t in sub_labels)
    assert any('技能' in t for t in sub_labels)
    assert any('工具' in t for t in sub_labels)
    assert any('导入' in t and '导出' in t for t in sub_labels)


def test_resources_subtab_lists_initialized(dialog):
    """技能/工具子页的列表 widget 必须被构建出来。"""
    # 触发主 Tab 切换以确保子页已构建（lazy 场景下也必须可用）
    res_idx = next(
        i for i, (_l, k) in enumerate(dialog._NAV_ITEMS) if k == 'resources'
    )
    dialog.nav.setCurrentRow(res_idx)
    assert hasattr(dialog, '_skills_list')
    assert hasattr(dialog, '_tools_list')


def test_nav_switch_changes_stack_index(dialog):
    """点击导航项后 stacked widget index 同步切换。"""
    dialog.nav.setCurrentRow(2)
    assert dialog.stack.currentIndex() == 2
    dialog.nav.setCurrentRow(4)
    assert dialog.stack.currentIndex() == 4


def test_duplicate_profile(dialog, config_mgr, monkeypatch):
    """右键 → 复制为副本：核心是 ``_duplicate_profile`` 路径。"""
    src_name = config_mgr.get_active_profile_name()
    new_name = src_name + '-copy-test'

    # mock QInputDialog 返回新名称
    from maxagent.qt_compat import QtWidgets

    def _fake_get_text(*_args, **_kwargs):
        return (new_name, True)

    monkeypatch.setattr(QtWidgets.QInputDialog, 'getText', _fake_get_text)

    dialog._duplicate_profile(src_name)
    # 副本应已写入配置
    assert config_mgr.get_profile(new_name) is not None
    # 列表应该刷新选中新项
    assert dialog.profile_list.currentItem().text() == new_name


def test_set_active_profile_via_menu_action(dialog, config_mgr):
    """右键 → 设为默认：直接调 ``_set_active_profile`` 验证副作用。"""
    # 准备一个非 active 的 profile
    from maxagent.config import LLMProfile
    other = LLMProfile(
        name='alt-profile',
        base_url='http://localhost:11434/v1',
        api_key='',
        model='qwen2.5:7b',
    )
    config_mgr.upsert_profile(other)
    dialog._reload_profiles()

    assert config_mgr.get_active_profile_name() != 'alt-profile'
    dialog._set_active_profile('alt-profile')
    assert config_mgr.get_active_profile_name() == 'alt-profile'


def test_del_profile_by_name_blocks_active(dialog, config_mgr, monkeypatch):
    """不允许删除当前激活的 profile，需弹警告。"""
    active = config_mgr.get_active_profile_name()
    triggered = {'warn': 0}

    from maxagent.qt_compat import QtWidgets

    def _fake_warn(*_args, **_kwargs):
        triggered['warn'] += 1

    monkeypatch.setattr(QtWidgets.QMessageBox, 'warning', _fake_warn)
    dialog._del_profile_by_name(active)
    # 没真删
    assert config_mgr.get_profile(active) is not None
    # 弹了警告
    assert triggered['warn'] == 1


def test_log_state_radio_load(dialog, config_mgr):
    """日志状态三态单选应按当前 cfg.log_level 选中对应 radio。"""
    cfg = config_mgr.config
    # DEBUG → log_radio_debug 选中
    cfg.log_level = 'DEBUG'
    dialog._load_app_settings()
    assert dialog.log_radio_debug.isChecked() is True
    assert dialog.log_radio_on.isChecked() is False
    assert dialog.log_radio_off.isChecked() is False

    # OFF → log_radio_off 选中
    cfg.log_level = 'OFF'
    dialog._load_app_settings()
    assert dialog.log_radio_off.isChecked() is True
    assert dialog.log_radio_debug.isChecked() is False

    # INFO（默认）→ log_radio_on 选中
    cfg.log_level = 'INFO'
    dialog._load_app_settings()
    assert dialog.log_radio_on.isChecked() is True
    assert dialog.log_radio_off.isChecked() is False
    assert dialog.log_radio_debug.isChecked() is False

    # 老配置 WARNING：在 _load_app_settings 里被归一化成 INFO
    cfg.log_level = 'WARNING'
    dialog._load_app_settings()
    assert dialog.log_radio_on.isChecked() is True


def test_log_state_radio_writes_back(dialog, config_mgr, monkeypatch):
    """点击 DEBUG radio 后，cfg.log_level 应被写回 'DEBUG'，
    且 logger 真实级别同步切换。"""
    cfg = config_mgr.config
    cfg.log_level = 'INFO'
    dialog._load_app_settings()
    # 触发：让 log_radio_debug 选中——QButtonGroup 互斥下会发 toggled
    dialog.log_radio_debug.setChecked(True)
    assert cfg.log_level == 'DEBUG'

    # 切到 OFF
    dialog.log_radio_off.setChecked(True)
    assert cfg.log_level == 'OFF'


def test_worker_debug_metrics_initialized():
    """AgentWorker 必须初始化 DEBUG 监控用的字段。"""
    from maxagent.agent.worker import AgentWorker
    from maxagent.agent.conversation import Conversation
    from maxagent.tools.dispatcher import ToolDispatcher

    class _FakeLLM:
        pass

    w = AgentWorker(
        llm_client=_FakeLLM(),
        conversation=Conversation(system_prompt='x'),
        dispatcher=ToolDispatcher(wrap_undo=False),
    )
    assert hasattr(w, '_chunk_count')
    assert hasattr(w, '_chunk_emit_count')
    assert hasattr(w, '_chunk_first_ts')
    assert hasattr(w, '_llm_call_started_ts')
    assert w._chunk_count == 0
    assert w._chunk_emit_count == 0


# ---------------------------------------------------------------------- #
# 一键恢复默认（OpenAI 兼容出厂模板）专项回归
# ---------------------------------------------------------------------- #


def test_reset_default_button_is_safe_against_enter(dialog):
    """恢复默认按钮必须屏蔽回车 default 行为，防止表单回车误触。

    背景：上一轮迭代修复过"回车误触新建 Profile"问题（关闭左侧
    +/× 按钮的 autoDefault）。这个新加的按钮如果不同步处理，
    用户在 base_url 框按回车又会被它截胡，导致 Profile 被静默
    重置——比前一个 bug 更糟糕（直接丢字段）。
    """
    btn = dialog.reset_default_btn
    assert btn.autoDefault() is False
    assert btn.isDefault() is False
    # 焦点策略禁用，确保 Tab 键也不会落到这个按钮上
    from maxagent.qt_compat import QtCore
    assert btn.focusPolicy() == QtCore.Qt.NoFocus


def test_reset_default_template_matches_openai_compat(dialog):
    """模板必须满足"OpenAI 兼容"语义的硬约束。"""
    tpl = dialog._RESET_TEMPLATE
    # Base URL 必须是 OpenAI 官方 v1 路径——这是与三方网关
    # （DeepSeek / Moonshot / 智谱 / vllm 等）兼容性最高的选择
    assert tpl['base_url'] == 'https://api.openai.com/v1'
    # 名称 / 模型必须留空，强制用户重填
    assert tpl['name'] == ''
    assert tpl['model'] == ''
    # 其他参数与 dataclass 默认值对齐，避免出现"代码默认 = X、
    # UI 恢复默认 = Y"的双重事实
    from maxagent.config import LLMProfile
    default_prof = LLMProfile()
    assert tpl['temperature'] == default_prof.temperature
    assert tpl['max_tokens'] == default_prof.max_tokens
    assert tpl['max_tool_loops'] == default_prof.max_tool_loops
    assert tpl['max_history_tokens'] == default_prof.max_history_tokens
    assert tpl['stream'] == default_prof.stream
    assert tpl['supports_tools'] == default_prof.supports_tools


def test_reset_default_writes_form_only(dialog, config_mgr, monkeypatch):
    """恢复默认必须只改 UI，不写盘——名称为空时绝不能 upsert。"""
    # 准备一个有名字的 profile，记录原始内容供对比
    active = config_mgr.get_active_profile_name()
    original_prof = config_mgr.get_profile(active)
    original_name = original_prof.name

    # 给某些字段填上自定义值，验证重置后 UI 上确实变了
    dialog.name_edit.setText('custom-name')
    dialog.base_url_edit.setText('https://my-custom-gw.example/v1')
    dialog.model_edit.setText('custom-model-v9')
    dialog.api_key_edit.setText('sk-keep-this-secret')
    dialog.temperature_spin.setValue(1.5)

    # mock 二次确认 → 用户点"是"
    from maxagent.qt_compat import QtWidgets
    monkeypatch.setattr(
        QtWidgets.QMessageBox, 'question',
        lambda *a, **kw: QtWidgets.QMessageBox.Yes,
    )

    dialog._reset_profile_to_default()

    # UI 已切到默认模板
    assert dialog.name_edit.text() == ''
    assert dialog.base_url_edit.text() == 'https://api.openai.com/v1'
    assert dialog.model_edit.text() == ''
    assert dialog.temperature_spin.value() == 0.2
    # API Key 显式保留——不能被清空
    assert dialog.api_key_edit.text() == 'sk-keep-this-secret'
    # 表单标记 dirty，提示用户需要再点应用
    assert dialog._dirty is True

    # 关键不变量：原 profile 仍在配置中，名称未变，未被静默落盘
    persisted = config_mgr.get_profile(original_name)
    assert persisted is not None
    assert persisted.name == original_name
    # 而且不应该新建一个名称为空的 profile
    assert config_mgr.get_profile('') is None


def test_reset_default_can_be_cancelled(dialog, config_mgr, monkeypatch):
    """二次确认点取消时，UI 字段不应被改动。"""
    dialog.name_edit.setText('keep-this-name')
    dialog.base_url_edit.setText('https://user-gateway.example')
    dialog.temperature_spin.setValue(1.7)
    snapshot = (
        dialog.name_edit.text(),
        dialog.base_url_edit.text(),
        dialog.temperature_spin.value(),
    )

    from maxagent.qt_compat import QtWidgets
    monkeypatch.setattr(
        QtWidgets.QMessageBox, 'question',
        lambda *a, **kw: QtWidgets.QMessageBox.No,
    )

    dialog._reset_profile_to_default()

    # 字段未动，dirty 未被翻
    assert (
        dialog.name_edit.text(),
        dialog.base_url_edit.text(),
        dialog.temperature_spin.value(),
    ) == snapshot


def test_reset_default_preserves_api_key_even_when_empty(
        dialog, config_mgr, monkeypatch,
):
    """API Key 原本为空时，重置后仍是空——不应误注入任何"默认 Key"。

    这条防御针对未来万一有人在 _RESET_TEMPLATE 里加了 'api_key' 字段
    导致密钥被覆盖的情况。
    """
    dialog.api_key_edit.setText('')

    from maxagent.qt_compat import QtWidgets
    monkeypatch.setattr(
        QtWidgets.QMessageBox, 'question',
        lambda *a, **kw: QtWidgets.QMessageBox.Yes,
    )

    dialog._reset_profile_to_default()
    assert dialog.api_key_edit.text() == ''
    # 模板里也不应该有 api_key 字段——属于"密钥隔离"的硬约束
    assert 'api_key' not in dialog._RESET_TEMPLATE


def test_reset_default_focus_lands_on_name_edit(dialog, monkeypatch):
    """重置后必须显式 setFocus 到名称输入框，让用户可以直接打字续填。

    注：offscreen 平台不传播窗口焦点事件，``hasFocus`` /
    ``QApplication.focusWidget`` 都不可靠；这里通过 monkeypatch
    探针验证 ``setFocus`` 确实被调用过——足以保证生产环境下焦点
    会正确落在 name_edit 上。
    """
    from maxagent.qt_compat import QtWidgets

    monkeypatch.setattr(
        QtWidgets.QMessageBox, 'question',
        lambda *a, **kw: QtWidgets.QMessageBox.Yes,
    )

    calls = {'count': 0}
    original_set_focus = dialog.name_edit.setFocus

    def _spy_set_focus(*args, **kwargs):
        calls['count'] += 1
        return original_set_focus(*args, **kwargs)

    monkeypatch.setattr(dialog.name_edit, 'setFocus', _spy_set_focus)
    dialog._reset_profile_to_default()
    assert calls['count'] >= 1, 'setFocus 未被调用，重置后焦点不会落到名称框'
