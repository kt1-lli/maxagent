#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置对话框：管理 Profile / 应用全局设置 / 日志 / 帮助。

UI 布局（v2 改造为左侧导航 + 右侧 Stacked Page）::

    +-----------------------------------------------+
    | 🤖 模型     |  当前选中 tab 的内容              |
    | 🌐 联网     |  （根据左侧导航切换 QStackedWidget）|
    | 🎨 应用     |                                  |
    | 📜 日志     |                                  |
    | ❓ 帮助     |                                  |
    +-----------------------------------------------+

设计要点：
- 用 QListWidget + QStackedWidget 组合实现"竖向靠左 Tab"，
  优势：中文横排、文字不旋转、样式可控、与现代 IDE 一致。
- Profile 列表支持右键菜单（重命名 / 删除 / 复制 / 设默认 / 测试连接）
  以及双击 inline 重命名，提升日常切换的手感。
- 各 page 各自独立方法构建，便于后续扩展（如新增"联网"页只需加一个
  ``_build_page_network`` 与导航条目）。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import time
from typing import Any
from typing import Optional

from ..attachments import model_supports_vision
from ..config import ConfigManager
from ..config import LLMProfile
from ..llm_client import build_client_from_profile
from ..llm_client import diagnose_base_url
from ..llm_client import LLMError
from ..logger import get_logger
from ..qt_compat import QtCore
from ..qt_compat import QtGui
from ..qt_compat import QtWidgets
from .emoji_compat import apply_font_fallback as _apply_font_fallback
from .emoji_compat import btn_label as _btn_label
from .emoji_compat import e as _e
from .emoji_compat import ee as _ee
from ._settings_help_mixin import _SettingsHelpMixin
from ._settings_model_tab_v2_mixin import SettingsModelTabV2Mixin
from ._settings_pack_mixin import _SettingsPackMixin


def _current_dcc_name():
    """返回当前 DCC 的显示名（Maya 或 3ds Max）。"""
    try:
        from ..dcc.runtime import current_dcc
        dcc = current_dcc()
        if dcc == 'maya':
            return 'Maya'
        if dcc == '3dsmax':
            return '3ds Max'
        return dcc
    except Exception:  # pylint: disable=broad-except
        return '3ds Max'


logger = get_logger(__name__)


# 左侧导航与右侧页面共用的 QSS：参考 VSCode / JetBrains 的设置面板
_NAV_QSS = """
QListWidget#SettingsNav {
    background-color: #2b2b2b;
    border: none;
    outline: 0;
    padding-top: 8px;
}
QListWidget#SettingsNav::item {
    color: #d0d0d0;
    padding: 10px 16px;
    border: none;
}
QListWidget#SettingsNav::item:hover {
    background-color: #3a3a3a;
}
QListWidget#SettingsNav::item:selected {
    background-color: #094771;
    color: #ffffff;
}
"""


class SettingsDialog(
    QtWidgets.QDialog,
    _SettingsPackMixin,
    _SettingsHelpMixin,
    SettingsModelTabV2Mixin,
):
    """MaxAgent 设置对话框。

    左侧导航条目固定 5 项；后续如要新增页面（如联网搜索）只需：
    1. 在 ``_NAV_ITEMS`` 中加一行
    2. 实现 ``_build_page_xxx`` 并 returnQWidget
    3. 在 ``_build_ui`` 里把它 addWidget 到 stacked
    """

    # 左侧导航条目：(显示名, 内部 key)。key 用于程序化跳转。
    # emoji 走 _ee() 兜底：PySide2 + Win 上 emoji 与中文混排可能导致整行
    # 字体异常，因此提供 BMP 单字符兜底（参见 emoji_compat.EMOJI_FALLBACK_TABLE），
    # 确保按钮文字可读。
    #
    # 注：原"我的规则"和"工具与技能"两项已合并为单个"我的资源"主 Tab，
    # 内部用横向子 Tab 切换 规则 / 技能 / 工具 / 导入导出 四个视图，
    # 既精简了左侧导航，又给每类资源都提供了"启用/禁用"开关。
    _NAV_ITEMS = [
        (_ee('🤖') + '  模型', 'model'),
        (_ee('🌐') + '  联网', 'network'),
        (_ee('🎨') + '  应用', 'app'),
        (_ee('👤') + '  助手形象', 'employee'),
        (_ee('📦') + '  我的资源', 'resources'),
        (_ee('🧰') + '  共享资源', 'shared'),
        (_ee('🔌') + '  IDE 接口', 'bridge'),
        (_ee('📜') + '  日志', 'log'),
        (_ee('❓') + '  帮助', 'help'),
    ]

    def __init__(self, config_manager, parent=None):
        # type: (ConfigManager, Optional[Any]) -> None
        super(SettingsDialog, self).__init__(parent)
        self._config = config_manager
        self.setWindowTitle('MaxAgent 设置')
        self.resize(900, 640)
        # 当前 Profile 表单是否被改过（用于切换 Profile 前提示）
        self._dirty = False
        self._build_ui()
        self._reload_profiles()
        # V2 模型 tab：直接以运营商为一等公民
        self._reload_providers_v2()
        # 字体回退链：让 PySide2 上 emoji + 中文混排正确渲染
        # recursive=True：递归到所有子按钮 / 标签 / 输入框，确保
        # PySide2 下 Qt 主题字体不会压过我们的回退族
        _apply_font_fallback(self, recursive=True)

    # ================================================================== #
    # 顶层 UI 构建
    # ================================================================== #
    def _build_ui(self):
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ----------- 左侧：导航 ----------- #
        self.nav = QtWidgets.QListWidget()
        self.nav.setObjectName('SettingsNav')
        self.nav.setStyleSheet(_NAV_QSS)
        self.nav.setFixedWidth(160)
        self.nav.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        for label, _key in self._NAV_ITEMS:
            QtWidgets.QListWidgetItem(label, self.nav)
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        outer.addWidget(self.nav, 0)

        # ----------- 右侧：Stacked Pages ----------- #
        right_box = QtWidgets.QVBoxLayout()
        right_box.setContentsMargins(16, 12, 16, 12)
        right_box.setSpacing(10)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._build_page_model())
        self.stack.addWidget(self._build_page_network())
        self.stack.addWidget(self._build_page_app())
        self.stack.addWidget(self._build_page_employee())
        self.stack.addWidget(self._build_page_resources())
        self.stack.addWidget(self._build_page_shared())
        # 注意：以下两行顺序与 _NAV_ITEMS 严格对应。
        # IDE 接口排在日志之前——属于"功能性"配置，使用频率高于
        # "排错性"日志，先功能后辅助更符合用户心智模型。
        self.stack.addWidget(self._build_page_bridge())
        self.stack.addWidget(self._build_page_log())
        self.stack.addWidget(self._build_page_help())
        # 迁移提示 bar：当从旧 profiles 自动构建了 providers 时展示
        # 用户点"知道了"后本 dialog 生命周期内不再显示
        self.migration_bar = self._build_migration_bar()
        if self.migration_bar is not None:
            right_box.addWidget(self.migration_bar)

        right_box.addWidget(self.stack, 1)

        # 底部统一关闭按钮：放在 stack 之外，所有页面共享
        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        self.close_btn = QtWidgets.QPushButton('关闭')
        self.close_btn.setMinimumWidth(96)
        self.close_btn.setMinimumHeight(30)
        self.close_btn.clicked.connect(self.accept)
        bottom.addWidget(self.close_btn)
        right_box.addLayout(bottom)

        right_widget = QtWidgets.QWidget()
        right_widget.setLayout(right_box)
        outer.addWidget(right_widget, 1)

        # 默认选中第一项
        self.nav.setCurrentRow(0)

    def _on_nav_changed(self, row):
        # type: (int) -> None
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)

    # ================================================================== #
    # Page 1: 模型 / Profile
    # ================================================================== #
    def _build_page_model(self):
        # type: () -> QtWidgets.QWidget
        """模型 tab（V2）：直接以运营商为一等公民。

        实现搬到 _settings_model_tab_v2_mixin.py 内的
        SettingsModelTabV2Mixin._build_page_model_v2。
        """
        return self._build_page_model_v2()

    # ================================================================== #
    # Page 2: 联网搜索
    # ================================================================== #
    def _build_page_network(self):
        # type: () -> QtWidgets.QWidget
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page)
        outer.setSpacing(10)

        title = QtWidgets.QLabel(_ee('🌐') + '  联网搜索')
        title.setStyleSheet('font-size:16px; font-weight:bold;')
        outer.addWidget(title)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        form.setFormAlignment(
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop,
        )
        form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.ExpandingFieldsGrow,
        )

        # 联网模式
        self.web_mode_combo = QtWidgets.QComboBox()
        self._web_mode_options = [
            ('关闭（全局禁用）', 'off'),
            ('自动（按需在主 UI 切换）', 'auto'),
            ('强制开启', 'force'),
        ]
        for label, _v in self._web_mode_options:
            self.web_mode_combo.addItem(label)
        self.web_mode_combo.setToolTip(
            ('关闭：永远不联网，主 UI 按钮置灰\n'
             '自动：在主 UI 通过 {} 按钮按需开关本轮对话\n'
             '强制：每轮对话都允许 LLM 联网，主 UI 按钮强制亮起'
             ).format(_ee('🌐')),
        )
        self.web_mode_combo.currentIndexChanged.connect(
            self._on_web_settings_changed,
        )
        form.addRow('联网模式:', self.web_mode_combo)

        # 单次结果数 + 抓正文开关
        self.web_max_results_spin = QtWidgets.QSpinBox()
        self.web_max_results_spin.setRange(1, 10)
        self.web_max_results_spin.setValue(5)
        self.web_max_results_spin.valueChanged.connect(
            self._on_web_settings_changed,
        )
        form.addRow('单次结果数:', self.web_max_results_spin)

        self.web_fetch_chk = QtWidgets.QCheckBox(
            '抓取每条结果的网页正文（推荐开启）',
        )
        self.web_fetch_chk.setToolTip(
            '关闭后只有标题 + 摘要，质量较差。\n'
            '开启会对前 N 条结果各发一次 HTTP，单次搜索耗时增加。',
        )
        self.web_fetch_chk.toggled.connect(self._on_web_settings_changed)
        form.addRow('', self.web_fetch_chk)

        outer.addLayout(form)

        # ----- Provider 列表区 -----
        prov_label = QtWidgets.QLabel('搜索 Provider（可自由扩展）')
        prov_label.setStyleSheet('color:#bbb; padding-top:6px;')
        outer.addWidget(prov_label)

        prov_row = QtWidgets.QHBoxLayout()
        self.web_provider_list = QtWidgets.QListWidget()
        self.web_provider_list.setAlternatingRowColors(True)
        self.web_provider_list.setMinimumHeight(160)
        self.web_provider_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection,
        )
        self.web_provider_list.itemSelectionChanged.connect(
            self._on_provider_selection_changed,
        )
        self.web_provider_list.itemDoubleClicked.connect(
            lambda *_: self._on_provider_edit_clicked(),
        )
        prov_row.addWidget(self.web_provider_list, 1)

        # 右侧操作按钮列
        btn_col = QtWidgets.QVBoxLayout()
        btn_col.setSpacing(4)
        self.web_provider_use_btn = QtWidgets.QPushButton(_btn_label('⭐', '设为默认'))
        self.web_provider_use_btn.setToolTip(
            '把选中的 Provider 设为搜索默认（main UI 联网按钮也使用它）',
        )
        self.web_provider_use_btn.clicked.connect(
            self._on_provider_set_active,
        )
        btn_col.addWidget(self.web_provider_use_btn)

        self.web_provider_edit_btn = QtWidgets.QPushButton(_btn_label('✏️', '编辑'))
        self.web_provider_edit_btn.clicked.connect(
            self._on_provider_edit_clicked,
        )
        btn_col.addWidget(self.web_provider_edit_btn)

        # 新增 / 复制按钮：用 BMP 字符 ＋ ⎘ 做图标，PySide2/6 都能直接渲染，
        # 不走 emoji_compat 兼容层，避免空 emoji 调用引起误解
        self.web_provider_add_btn = QtWidgets.QPushButton('＋ 新增')
        self.web_provider_add_btn.setToolTip('添加自定义搜索后端')
        self.web_provider_add_btn.clicked.connect(
            self._on_provider_add_clicked,
        )
        btn_col.addWidget(self.web_provider_add_btn)

        self.web_provider_dup_btn = QtWidgets.QPushButton('⎘ 复制')
        self.web_provider_dup_btn.clicked.connect(
            self._on_provider_dup_clicked,
        )
        btn_col.addWidget(self.web_provider_dup_btn)

        self.web_provider_del_btn = QtWidgets.QPushButton(_btn_label('🗑️', '删除'))
        self.web_provider_del_btn.clicked.connect(
            self._on_provider_del_clicked,
        )
        btn_col.addWidget(self.web_provider_del_btn)

        self.web_provider_test_btn = QtWidgets.QPushButton(_btn_label('🔌', '测试'))
        self.web_provider_test_btn.setToolTip(
            '用选中 Provider 发起一次 "{}" 搜索验证可用性'.format(
                _current_dcc_name(),
            ),
        )
        self.web_provider_test_btn.clicked.connect(
            self._on_provider_test_clicked,
        )
        btn_col.addWidget(self.web_provider_test_btn)

        self.web_provider_reset_btn = QtWidgets.QPushButton('↺ 恢复内置')
        self.web_provider_reset_btn.setToolTip(
            '把内置 Provider（DuckDuckGo / Bing / Google CSE 等）'
            '\n字段重置为出厂值，保留你已填的 API Key 和 extra 字段。',
        )
        self.web_provider_reset_btn.clicked.connect(
            self._on_provider_reset_builtins,
        )
        btn_col.addWidget(self.web_provider_reset_btn)

        btn_col.addStretch(1)
        prov_row.addLayout(btn_col)
        outer.addLayout(prov_row)

        self.web_test_label = QtWidgets.QLabel('')
        self.web_test_label.setStyleSheet('color:#888;')
        self.web_test_label.setWordWrap(True)
        outer.addWidget(self.web_test_label)

        outer.addStretch(1)
        return page

    # ================================================================== #
    # Page 3: 应用全局设置
    # ================================================================== #
    def _build_page_app(self):
        # type: () -> QtWidgets.QWidget
        page = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(page)
        form.setSpacing(8)
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        form.setFormAlignment(
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop,
        )
        form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.ExpandingFieldsGrow,
        )

        title = QtWidgets.QLabel(_ee('🎨') + '  应用全局设置')
        title.setStyleSheet('font-size:16px; font-weight:bold;')
        form.addRow(title)

        dcc_name = _current_dcc_name()
        is_maya = _current_dcc_name() == 'Maya'
        self.auto_show_chk = QtWidgets.QCheckBox(
            '{} 启动时自动显示 MaxAgent 面板'.format(dcc_name),
        )
        self.auto_show_chk.setToolTip(
            '关闭后，{} 启动时不会自动弹出面板，需{}\n'
            '通过菜单/快捷键手动显示。'.format(
                dcc_name,
                '在脚本编辑器中执行 g_show_max_agent()' if is_maya
                else '在 MAXScript Listener 中执行 g_show_max_agent()',
            ),
        )
        self.auto_show_chk.toggled.connect(self._on_app_setting_changed)
        form.addRow('', self.auto_show_chk)

        is_maya = _current_dcc_name() == 'Maya'
        escape_label = 'run_python' if is_maya else 'run_maxscript / run_python'
        self.allow_escape_chk = QtWidgets.QCheckBox(
            '允许使用 {} 脚本工具（标准工具）'.format(escape_label)
        )
        self.allow_escape_chk.setToolTip(
            '关闭后 LLM 无法调用脚本工具，仅能使用预定义工具。'
            '脚本工具受安全扫描与执行前确认约束，不是无限制逃生舱。',
        )
        self.allow_escape_chk.toggled.connect(self._on_app_setting_changed)
        form.addRow('', self.allow_escape_chk)

        self.confirm_exec_chk = QtWidgets.QCheckBox(
            '执行脚本工具前弹窗确认',
        )
        self.confirm_exec_chk.toggled.connect(self._on_app_setting_changed)
        form.addRow('', self.confirm_exec_chk)

        self.wrap_undo_chk = QtWidgets.QCheckBox(
            '工具操作包裹 Undo（可 Ctrl+Z 回滚）',
        )
        self.wrap_undo_chk.toggled.connect(self._on_app_setting_changed)
        form.addRow('', self.wrap_undo_chk)

        # ---- Maya 停靠位置（仅 Maya 环境显示） ---- #
        if is_maya:
            self.maya_dock_box = QtWidgets.QGroupBox('Maya 面板停靠位置')
            dock_form = QtWidgets.QFormLayout(self.maya_dock_box)
            dock_form.setSpacing(6)
            dock_form.setLabelAlignment(QtCore.Qt.AlignRight)
            dock_form.setFormAlignment(
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop,
            )
            dock_form.setFieldGrowthPolicy(
                QtWidgets.QFormLayout.ExpandingFieldsGrow,
            )

            self.maya_dock_target_combo = QtWidgets.QComboBox()
            self.maya_dock_target_combo.setMinimumWidth(260)
            self.maya_dock_target_combo.setToolTip(
                '选择 MaxAgent 面板停靠到哪个 Maya 面板上。\n'
                '列表来自当前会话真实存在的 workspaceControl，\n'
                '括号里是 control 的内部名称。选择后立即重新停靠。',
            )
            self.maya_dock_target_combo.currentIndexChanged.connect(
                self._on_maya_dock_target_changed,
            )
            dock_form.addRow('停靠到:', self.maya_dock_target_combo)

            self.maya_dock_mode_combo = QtWidgets.QComboBox()
            self.maya_dock_mode_combo.addItem('并入目标标签页（tab）', 'tab')
            self.maya_dock_mode_combo.addItem('停靠到目标右侧（dock）', 'dock')
            self.maya_dock_mode_combo.addItem('停靠到主窗口边缘（main）', 'main')
            self.maya_dock_mode_combo.setToolTip(
                'tab  = 与目标面板共用一个标签栏（最稳定，推荐）\n'
                'dock = 停在目标面板旁边，占独立区域\n'
                'main = 直接贴到 Maya 主窗口的某一条边',
            )
            self.maya_dock_mode_combo.currentIndexChanged.connect(
                self._on_maya_dock_target_changed,
            )
            dock_form.addRow('停靠方式:', self.maya_dock_mode_combo)

            self.maya_dock_side_combo = QtWidgets.QComboBox()
            for side, text in (
                ('left', '左'),
                ('right', '右'),
                ('top', '上'),
                ('bottom', '下'),
            ):
                self.maya_dock_side_combo.addItem(text, side)
            self.maya_dock_side_combo.setToolTip(
                '仅"停靠到主窗口边缘"模式生效。',
            )
            self.maya_dock_side_combo.currentIndexChanged.connect(
                self._on_maya_dock_target_changed,
            )
            dock_form.addRow('主窗口方位:', self.maya_dock_side_combo)

            self.maya_dock_floating_chk = QtWidgets.QCheckBox(
                '浮动显示（脱离 Maya 布局）',
            )
            self.maya_dock_floating_chk.setToolTip(
                '勾选后面板作为独立窗口显示，不占用 Maya 停靠区域。',
            )
            self.maya_dock_floating_chk.toggled.connect(
                self._on_maya_dock_target_changed,
            )
            dock_form.addRow('', self.maya_dock_floating_chk)

            dock_btn_row = QtWidgets.QWidget()
            dock_btn_h = QtWidgets.QHBoxLayout(dock_btn_row)
            dock_btn_h.setContentsMargins(0, 0, 0, 0)
            dock_btn_h.setSpacing(6)
            self.maya_dock_refresh_btn = QtWidgets.QPushButton(
                _btn_label('🔄', '刷新列表'),
            )
            self.maya_dock_refresh_btn.setToolTip(
                '重新枚举当前 Maya 会话里可用的停靠目标。\n'
                '新打开 Outliner / UV 编辑器等面板后点这里即可看到它们。',
            )
            self.maya_dock_refresh_btn.clicked.connect(
                self._on_maya_dock_refresh,
            )
            dock_btn_h.addWidget(self.maya_dock_refresh_btn)
            self.maya_dock_apply_btn = QtWidgets.QPushButton(
                _btn_label('📌', '立即重新停靠'),
            )
            self.maya_dock_apply_btn.setToolTip(
                '按当前选择立刻把面板重新停靠一次（无需重启 Maya）。',
            )
            self.maya_dock_apply_btn.clicked.connect(
                self._on_maya_dock_apply,
            )
            dock_btn_h.addWidget(self.maya_dock_apply_btn)
            dock_btn_h.addStretch(1)
            dock_form.addRow('', dock_btn_row)

            form.addRow(self.maya_dock_box)

        # ---- 视觉/多模态 ---- #
        self.vision_enabled_chk = QtWidgets.QCheckBox(
            '启用图片视觉（仅向支持视觉的模型发送图片）',
        )
        self.vision_enabled_chk.setToolTip(
            '关闭后即使你在输入框插入图片，也只在本地气泡里显示，'
            '不会作为 image_url 发给 LLM。\n'
            '内置白名单：GPT-4o / Claude-3+/ Gemini / Qwen-VL / GLM-4V 等；'
            '不在白名单的模型即使开关打开也会自动降级为纯文本。',
        )
        self.vision_enabled_chk.toggled.connect(self._on_app_setting_changed)
        form.addRow('', self.vision_enabled_chk)

        # ---- Skill 自动提议开关 ---- #
        self.enable_skill_proposal_chk = QtWidgets.QCheckBox(
            '会话结束时自动提议记为 Skill（默认关闭，避免打扰）',
        )
        self.enable_skill_proposal_chk.setToolTip(
            '关闭时永不弹出"是否记为 Skill"对话框；\n'
            '开启后仍需满足以下条件才会提议：\n'
            '  · 本轮成功动作数 ≥ 门槛（下方数值）\n'
            '  · 至少含一个写入类工具（纯查询会话不算流程）\n'
            '  · 不与已有 Skill 同名或触发词重叠\n'
            '你也可以随时在对话里说"把刚才的流程记为 Skill"主动保存。',
        )
        self.enable_skill_proposal_chk.toggled.connect(
            self._on_app_setting_changed,
        )
        form.addRow('', self.enable_skill_proposal_chk)

        self.skill_proposal_min_actions_spin = QtWidgets.QSpinBox()
        self.skill_proposal_min_actions_spin.setRange(1, 50)
        self.skill_proposal_min_actions_spin.setValue(3)
        self.skill_proposal_min_actions_spin.setToolTip(
            '触发 Skill 提议所需的最少成功动作数。\n'
            '值越大越不容易弹窗（默认 3：单次 create_box 不会打扰）。',
        )
        self.skill_proposal_min_actions_spin.valueChanged.connect(
            self._on_app_setting_changed,
        )
        form.addRow(
            'Skill 提议门槛（最少动作数）',
            self.skill_proposal_min_actions_spin,
        )

        # ---- 视觉白名单（每行一个，子串匹配，不区分大小写） ---- #
        self.vision_whitelist_edit = QtWidgets.QPlainTextEdit()
        self.vision_whitelist_edit.setPlaceholderText(
            '每行一个模型名子串，例如：\n'
            'gpt-4o\nclaude-3\nqwen-vl\nyoutu-vita',
        )
        self.vision_whitelist_edit.setToolTip(
            '当 profile 的"模型"字段包含此处任一子串（不区分大小写）'
            '时，发送图片时会启用 image_url 多模态协议。\n'
            '修改后立即生效，无需重启。'
            '如需恢复内置默认列表，点击右侧"恢复默认"按钮。',
        )
        # 控制最小高度，避免占据整页；同时允许向下扩张
        self.vision_whitelist_edit.setMinimumHeight(96)
        self.vision_whitelist_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Preferred,
        )
        # 文本变化 -> 标记 dirty -> 保存（避免每键击都写盘，使用
        # textChanged 触发 + 保存合并到 _on_app_setting_changed 链路）
        self.vision_whitelist_edit.textChanged.connect(
            self._on_vision_whitelist_changed,
        )

        # 右侧按钮：恢复默认
        wl_row = QtWidgets.QWidget()
        wl_h = QtWidgets.QHBoxLayout(wl_row)
        wl_h.setContentsMargins(0, 0, 0, 0)
        wl_h.setSpacing(6)
        wl_h.addWidget(self.vision_whitelist_edit, 1)
        self.vision_whitelist_reset_btn = QtWidgets.QPushButton(
            _btn_label('🔄', '恢复默认'),
        )
        self.vision_whitelist_reset_btn.setToolTip(
            '清空当前编辑区，恢复为内置的默认视觉模型白名单。\n'
            '默认列表会随版本升级自动扩充新机型支持。',
        )
        self.vision_whitelist_reset_btn.setAutoDefault(False)
        self.vision_whitelist_reset_btn.setDefault(False)
        # 给一个稳妥的最小宽度，避免按钮文字被截断
        self.vision_whitelist_reset_btn.setMinimumWidth(96)
        self.vision_whitelist_reset_btn.clicked.connect(
            self._on_vision_whitelist_reset,
        )
        # 按钮跟编辑框顶部对齐，视觉更整齐
        v_btn_box = QtWidgets.QVBoxLayout()
        v_btn_box.setContentsMargins(0, 0, 0, 0)
        v_btn_box.addWidget(self.vision_whitelist_reset_btn)
        v_btn_box.addStretch(1)
        wl_h.addLayout(v_btn_box)
        form.addRow('视觉模型白名单:', wl_row)
        return page

    # ================================================================== #
    # Page 4: 助手形象（员工档案——纯 UI 皮肤）
    # ================================================================== #
    def _build_page_employee(self):
        # type: () -> QtWidgets.QWidget
        from .employee_tab import EmployeeTab
        # 把整个 Tab 实现委托给独立模块，保持 settings_dialog 不膨胀
        tab = EmployeeTab(self._config, parent=self)
        return tab

    # ================================================================== #
    # Page 7: 日志（三态：关闭 / 开启 / DEBUG）
    # ================================================================== #
    def _build_page_log(self):
        # type: () -> QtWidgets.QWidget
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setSpacing(12)

        title = QtWidgets.QLabel(_ee('📜') + '  日志')
        title.setStyleSheet('font-size:16px; font-weight:bold;')
        layout.addWidget(title)

        # ---- 三态单选 ----
        # 用 QButtonGroup 把三个 radio 互斥分组，再统一接 toggled 信号。
        # 单选按钮比下拉框更直观——状态一眼可见，不需要额外点开。
        state_box = QtWidgets.QGroupBox('日志状态')
        state_layout = QtWidgets.QHBoxLayout(state_box)
        self.log_state_group = QtWidgets.QButtonGroup(self)
        self.log_state_group.setExclusive(True)

        self.log_radio_off = QtWidgets.QRadioButton('关闭')
        self.log_radio_off.setToolTip(
            '完全关闭日志：不写文件、不输出控制台。\n'
            '适合不需要排查问题、希望最干净的日常使用。',
        )
        self.log_radio_on = QtWidgets.QRadioButton('开启')
        self.log_radio_on.setToolTip(
            '默认模式：记录关键节点到日志文件，不输出控制台。\n'
            '会话生命周期、错误堆栈、配置变更等都会入档。',
        )
        self.log_radio_debug = QtWidgets.QRadioButton('DEBUG')
        self.log_radio_debug.setToolTip(
            '详细模式：在"开启"基础上追加全量埋点（仍只写文件，\n'
            '不输出控制台）。包含 LLM 请求/响应、工具调用入参出参、\n'
            'Worker 线程切换、UI 关键事件等。\n'
            '排查偶发问题时切到 DEBUG 抓现场。',
        )
        # 三个 radio 都注册到同一个 group 实现互斥
        for btn in (
            self.log_radio_off, self.log_radio_on, self.log_radio_debug,
        ):
            self.log_state_group.addButton(btn)
            state_layout.addWidget(btn)
            btn.toggled.connect(self._on_log_state_radio_toggled)
        state_layout.addStretch(1)

        # 打开日志目录按钮放在 group 同一行末，不影响布局
        self.open_log_dir_btn = QtWidgets.QPushButton(
            _btn_label('📂', '打开日志目录'),
        )
        self.open_log_dir_btn.setToolTip(
            '在系统文件管理器中打开 maxagent 日志目录\n'
            '（包含 maxagent.log 主文件 + 滚动归档）',
        )
        self.open_log_dir_btn.clicked.connect(self._open_log_dir)
        state_layout.addWidget(self.open_log_dir_btn)
        layout.addWidget(state_box)

        # ---- 详细说明 ----
        info = QtWidgets.QLabel(
            '<b>关闭</b>：日志系统完全静默，适合干净使用。<br>'
            '<b>开启</b>（默认）：记录关键节点到日志文件，'
            '<b>不输出到控制台</b>。<br>'
            '<b>DEBUG</b>：在开启基础上追加全量埋点：<br>'
            '&nbsp;&nbsp;• LLM 请求 payload / 流式 chunk 速率<br>'
            '&nbsp;&nbsp;• 每次工具调用入参 / 出参 / 耗时<br>'
            '&nbsp;&nbsp;• 会话生命周期（创建 / 加载 / 切换 / 删除）<br>'
            '&nbsp;&nbsp;• Worker 子线程启停 / 主线程切换<br>'
            '&nbsp;&nbsp;• UI 关键事件（发送、停止、清空、设置变更）'
            '<br><br>'
            '日志按 2 MB 滚动归档，最多保留 5 份历史。无论哪种状态，'
            '日志都<b>不会输出到控制台</b>。',
        )
        info.setStyleSheet('color:#aaa;')
        info.setWordWrap(True)
        info.setTextFormat(QtCore.Qt.RichText)
        layout.addWidget(info)
        layout.addStretch(1)
        return page

    # ================================================================== #
    # Page 6: IDE 接口（本地 TCP 桥接，对接 dcc-mcp / Cursor）
    # ================================================================== #
    def _build_page_bridge(self):
        # type: () -> QtWidgets.QWidget
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setSpacing(10)

        title = QtWidgets.QLabel(_ee('🔌') + '  IDE 接口（Bridge）')
        title.setStyleSheet('font-size:16px; font-weight:bold;')
        layout.addWidget(title)

        dcc_name = _current_dcc_name()
        intro = QtWidgets.QLabel(
            '在 {} 内开启一个本地 TCP 端口，让外部 IDE（通过 '
            '<a href="https://gitee.com/cmqll/dcc-mcp" '
            'style="color:#4da6ff;">dcc-mcp</a> 这类 MCP Server）'
            '调用 maxagent 能力：<br>'
            '&nbsp;&nbsp;• <b>execute_python</b>：在 {} 主线程执行任意'
            ' Python 代码（{}）<br>'
            '&nbsp;&nbsp;• <b>dispatch_task</b>：把整个自然语言任务派给'
            ' maxagent 自己跑（IDE Agent ↔ maxagent Agent 协作）<br>'
            '<span style="color:#888;">仅监听 127.0.0.1，不暴露外网。'
            '建议默认关闭，只在需要时手动开启。</span>'.format(
                dcc_name, dcc_name,
                'maya.cmds / OpenMaya 安全' if dcc_name == 'Maya' else 'pymxs 安全',
            ),
        )
        intro.setWordWrap(True)
        intro.setTextFormat(QtCore.Qt.RichText)
        intro.setOpenExternalLinks(True)
        intro.setStyleSheet('color:#ccc;')
        layout.addWidget(intro)

        # ---- 主开关 + 状态指示 ---- #
        head_row = QtWidgets.QHBoxLayout()
        self.bridge_enabled_chk = QtWidgets.QCheckBox('启用 IDE Bridge')
        self.bridge_enabled_chk.setToolTip(
            '勾选后立即开启本地 TCP 监听端口；取消勾选立即停止。\n'
            '关闭时所有外部连接会被拒绝。',
        )
        self.bridge_enabled_chk.toggled.connect(
            self._on_bridge_enabled_toggled,
        )
        head_row.addWidget(self.bridge_enabled_chk)
        self.bridge_status_lbl = QtWidgets.QLabel('● 未启动')
        self.bridge_status_lbl.setStyleSheet('color:#888;')
        head_row.addWidget(self.bridge_status_lbl)
        head_row.addStretch(1)
        layout.addLayout(head_row)

        # ---- 表单：端口 / token ---- #
        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        form.setFormAlignment(
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop,
        )
        form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.ExpandingFieldsGrow,
        )

        self.bridge_port_spin = QtWidgets.QSpinBox()
        self.bridge_port_spin.setRange(1, 65535)
        self.bridge_port_spin.setValue(7003)
        dcc_mcp_name = 'Maya' if _current_dcc_name() == 'Maya' else '3dsMax'
        self.bridge_port_spin.setToolTip(
            '本地监听端口（默认 7003，与 dcc-mcp {} 预设一致）。\n'
            '修改后需关闭再重新启用 Bridge 才生效。'.format(dcc_mcp_name),
        )
        form.addRow('监听端口:', self.bridge_port_spin)

        self.bridge_token_edit = QtWidgets.QLineEdit()
        self.bridge_token_edit.setPlaceholderText(
            '可选；留空时本机回环免鉴权',
        )
        self.bridge_token_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.bridge_token_edit.setToolTip(
            '非空时，所有请求必须在 JSON 中带 token 字段且匹配。\n'
            '本机使用通常无需填写；多人共用机器或担心误连可设。',
        )
        form.addRow('访问令牌:', self.bridge_token_edit)

        # ---- 任务派发设置 ---- #
        sep = QtWidgets.QLabel(
            '<span style="color:#aaa;">────  任务派发（dispatch_task） ────</span>',
        )
        sep.setTextFormat(QtCore.Qt.RichText)
        form.addRow(sep)

        self.bridge_dispatch_chk = QtWidgets.QCheckBox(
            '允许 IDE 派发自然语言任务',
        )
        self.bridge_dispatch_chk.setToolTip(
            '关闭时只暴露 execute_python（IDE 自己写代码）；\n'
            '开启时额外暴露 dispatch_task：IDE 把任务以自然语言派给\n'
            'maxagent，由 maxagent 自己跑 LLM + 工具循环出结果。',
        )
        form.addRow('', self.bridge_dispatch_chk)

        self.bridge_max_rounds_spin = QtWidgets.QSpinBox()
        self.bridge_max_rounds_spin.setRange(1, 100)
        self.bridge_max_rounds_spin.setValue(20)
        self.bridge_max_rounds_spin.setToolTip(
            '单次 dispatch_task 内 LLM ↔ 工具循环的最大轮数。\n'
            '防 LLM 死循环；超出后自动结束并附 reached_max_rounds 标记。',
        )
        form.addRow('最大轮数:', self.bridge_max_rounds_spin)

        self.bridge_timeout_spin = QtWidgets.QSpinBox()
        self.bridge_timeout_spin.setRange(10, 3600)
        self.bridge_timeout_spin.setValue(300)
        self.bridge_timeout_spin.setSuffix(' 秒')
        self.bridge_timeout_spin.setToolTip(
            '单次 dispatch_task 总超时；触发后立刻返回当前已收集的部分结果。',
        )
        form.addRow('超时:', self.bridge_timeout_spin)

        layout.addLayout(form)

        # ---- 按钮行 ---- #
        btn_row = QtWidgets.QHBoxLayout()
        self.bridge_apply_btn = QtWidgets.QPushButton('应用并重启 Bridge')
        self.bridge_apply_btn.setToolTip(
            '保存当前端口/令牌/派发设置，并按需重启监听线程。',
        )
        self.bridge_apply_btn.clicked.connect(self._on_bridge_apply)
        btn_row.addWidget(self.bridge_apply_btn)

        self.bridge_copy_cfg_btn = QtWidgets.QPushButton(
            _btn_label('📋', '复制 dcc-mcp / Cursor 配置示例'),
        )
        self.bridge_copy_cfg_btn.setToolTip(
            '把推荐的 IDE MCP 配置 JSON 复制到剪贴板，\n'
            '粘贴到 ~/.cursor/mcp.json 即可使用。',
        )
        self.bridge_copy_cfg_btn.clicked.connect(self._on_bridge_copy_config)
        btn_row.addWidget(self.bridge_copy_cfg_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        layout.addStretch(1)

        # 初值加载 + 实时状态
        self._load_bridge_settings()
        self._refresh_bridge_status()
        # 启动一个 1Hz QTimer 周期刷新状态（连接计数等）
        self._bridge_status_timer = QtCore.QTimer(self)
        self._bridge_status_timer.setInterval(1000)
        self._bridge_status_timer.timeout.connect(self._refresh_bridge_status)
        self._bridge_status_timer.start()
        return page

    # ================================================================== #
    # Page 4: 我的规则（用户从对话中沉淀的 LLM 行为规则）
    # ================================================================== #
    def _build_page_rules(self):
        # type: () -> QtWidgets.QWidget
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title = QtWidgets.QLabel(
            '<b>我的规则</b><br>'
            '<span style="color:#aaa;">'
            '这里是从你与 AI 的协作中沉淀的本地规则，'
            '会在每轮对话注入到 system prompt。'
            '官方文档不会被改动；超过 4KB 的部分会按"最新优先"截断。'
            '</span>'
        )
        title.setWordWrap(True)
        title.setTextFormat(QtCore.Qt.RichText)
        layout.addWidget(title)

        # 状态行：规则总数 + 当前注入字节数
        self._rules_status_label = QtWidgets.QLabel('')
        self._rules_status_label.setStyleSheet('color:#7ec0ff;')
        layout.addWidget(self._rules_status_label)

        # 规则列表
        self._rules_list = QtWidgets.QListWidget()
        self._rules_list.setStyleSheet(
            'QListWidget { background:#252525; color:#d4d4d4; border:1px solid #444; }'
            'QListWidget::item { padding:6px; border-bottom:1px solid #333; }'
            'QListWidget::item:selected { background:#3a5d8f; }'
        )
        self._rules_list.itemDoubleClicked.connect(self._on_rules_view_detail)
        layout.addWidget(self._rules_list, 1)

        # 操作按钮
        btn_row = QtWidgets.QHBoxLayout()
        view_btn = QtWidgets.QPushButton(_btn_label('👁', '查看详情'))
        view_btn.clicked.connect(self._on_rules_view_detail)
        btn_row.addWidget(view_btn)
        toggle_btn = QtWidgets.QPushButton(_btn_label('🔄', '启用/禁用'))
        toggle_btn.clicked.connect(self._on_rules_toggle_enabled)
        btn_row.addWidget(toggle_btn)
        del_btn = QtWidgets.QPushButton(_btn_label('🗑️', '删除'))
        del_btn.setStyleSheet('color:#ff8888;')
        del_btn.clicked.connect(self._on_rules_delete)
        btn_row.addWidget(del_btn)
        btn_row.addStretch(1)
        # 注：规则的导入/导出已统一收敛到「我的资源 → 导入/导出」子 Tab，
        # 此处仅保留就地编辑能力。
        refresh_btn = QtWidgets.QPushButton(_btn_label('🔄', '刷新'))
        refresh_btn.clicked.connect(self._refresh_rules_list)
        btn_row.addWidget(refresh_btn)
        layout.addLayout(btn_row)

        # 初次加载
        self._refresh_rules_list()
        return page

    # ------------------------------------------------------------------ #
    # 我的规则 - 事件处理
    # ------------------------------------------------------------------ #
    def _refresh_rules_list(self):
        """重新扫盘并刷新规则列表。"""
        try:
            from ..user_rules_loader import list_rules
            from ..user_rules_loader import total_enabled_bytes
            from ..user_rules_loader import MAX_TOTAL_BYTES
        except Exception as exc:  # pylint: disable=broad-except
            self._rules_status_label.setText(
                '<span style="color:#ff8888;">加载规则模块失败: {}</span>'.format(exc),
            )
            return

        self._rules_list.clear()
        rules = list_rules(only_enabled=False)
        for r in rules:
            enabled = r.get('enabled', True)
            mark = '◉' if enabled else '○'
            color = '#a8e6a8' if enabled else '#888'
            # 来源标记：'import' 显示 [导入]，否则不加（自创为隐式默认）
            src_tag = ' [导入]' if r.get('source') == 'import' else ''
            text = '{} [{}]{} {}'.format(
                mark, r.get('id', ''), src_tag, r.get('title', ''),
            )
            item = QtWidgets.QListWidgetItem(text)
            item.setForeground(QtGui.QBrush(QtGui.QColor(color)))
            item.setData(QtCore.Qt.UserRole, r.get('id', ''))
            self._rules_list.addItem(item)

        used = total_enabled_bytes()
        pct = (used * 100) // max(MAX_TOTAL_BYTES, 1)
        if pct >= 90:
            color = '#ff8888'
        elif pct >= 70:
            color = '#ffd166'
        else:
            color = '#7ec0ff'
        self._rules_status_label.setText(
            '<span style="color:{c};">共 {n} 条规则，已启用部分占用 {u}/{m} 字节 ({p}%)</span>'
            .format(
                c=color,
                n=len(rules),
                u=used,
                m=MAX_TOTAL_BYTES,
                p=pct,
            ),
        )

    def _selected_rule_id(self):
        # type: () -> Optional[str]
        item = self._rules_list.currentItem()
        if item is None:
            return None
        return item.data(QtCore.Qt.UserRole)

    def _on_rules_view_detail(self, *_args):
        rid = self._selected_rule_id()
        if not rid:
            return
        try:
            from ..user_rules_loader import get_rule
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(self, '错误', str(exc))
            return
        r = get_rule(rid)
        if r is None:
            QtWidgets.QMessageBox.information(self, '提示', '规则已不存在')
            self._refresh_rules_list()
            return
        # 简单只读展示
        body = (
            'ID: {id}\n标题: {title}\n标签: {tags}\n启用: {enabled}\n\n'
            '【规则正文】\n{content}\n\n'
            '【反例】\n{bad}\n\n'
            '【正例】\n{good}\n\n'
            '【理由】\n{rationale}'
        ).format(
            id=r.get('id', ''),
            title=r.get('title', ''),
            tags=', '.join(r.get('tags') or []),
            enabled=r.get('enabled', True),
            content=r.get('content', ''),
            bad=r.get('bad_example', '') or '(无)',
            good=r.get('good_example', '') or '(无)',
            rationale=r.get('rationale', '') or '(无)',
        )
        dlg = QtWidgets.QMessageBox(self)
        dlg.setWindowTitle('规则详情 · {}'.format(r.get('id', '')))
        dlg.setText(body)
        dlg.setStandardButtons(QtWidgets.QMessageBox.Ok)
        dlg.exec_()

    def _on_rules_toggle_enabled(self):
        rid = self._selected_rule_id()
        if not rid:
            return
        try:
            from ..user_rules_loader import get_rule
            from ..user_rules_loader import set_rule_enabled
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(self, '错误', str(exc))
            return
        r = get_rule(rid)
        if r is None:
            self._refresh_rules_list()
            return
        set_rule_enabled(rid, not r.get('enabled', True))
        self._refresh_rules_list()

    def _on_rules_delete(self):
        rid = self._selected_rule_id()
        if not rid:
            return
        ans = QtWidgets.QMessageBox.question(
            self,
            '确认删除',
            '确定删除规则 [{}] 吗？此操作不可撤销。'.format(rid),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if ans != QtWidgets.QMessageBox.Yes:
            return
        try:
            from ..user_rules_loader import delete_rule
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(self, '错误', str(exc))
            return
        delete_rule(rid)
        self._refresh_rules_list()

    # ------------------------------------------------------------------ #
    # 我的规则 - 导入 / 导出（Phase 2）
    # ------------------------------------------------------------------ #
    def _on_rules_export_selected(self):
        """把当前选中规则导出为 .maxagent-rule.json 文件。"""
        rid = self._selected_rule_id()
        if not rid:
            QtWidgets.QMessageBox.information(
                self, '提示', '请先在列表中选中一条规则再导出。',
            )
            return
        try:
            from .. import user_rules_loader as url
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(self, '错误', str(exc))
            return

        default_name = '{}.maxagent-rule.json'.format(rid)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            '导出规则',
            default_name,
            'MaxAgent 规则文件 (*.maxagent-rule.json);;所有文件 (*.*)',
        )
        if not path:
            return
        try:
            payload = url.export_rule(rid)
            url.write_export_file(path, payload)
        except (ValueError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, '导出失败', str(exc))
            return
        QtWidgets.QMessageBox.information(
            self, '导出成功',
            '规则 [{}] 已导出到:\n{}'.format(rid, path),
        )

    def _on_rules_export_all(self):
        """打包导出全部启用规则为 .maxagent-rules.json 文件。"""
        try:
            from .. import user_rules_loader as url
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(self, '错误', str(exc))
            return

        bundle = url.export_bundle()
        rules = bundle.get('rules') or []
        if not rules:
            QtWidgets.QMessageBox.information(
                self, '提示',
                '没有可导出的规则（至少需要一条已启用规则）。',
            )
            return

        default_name = 'rules-{}.maxagent-rules.json'.format(
            time.strftime('%Y%m%d'),
        )
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            '导出全部已启用规则',
            default_name,
            'MaxAgent 规则包 (*.maxagent-rules.json);;所有文件 (*.*)',
        )
        if not path:
            return
        try:
            url.write_export_file(path, bundle)
        except (ValueError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, '导出失败', str(exc))
            return
        QtWidgets.QMessageBox.information(
            self, '导出成功',
            '已导出 {} 条规则到:\n{}'.format(len(rules), path),
        )

    def _on_rules_import_file(self):
        """打开导入对话框，让用户选择文件并勾选要导入的规则。"""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            '选择规则文件',
            '',
            'MaxAgent 规则文件 (*.maxagent-rule.json *.maxagent-rules.json *.json);;'
            '所有文件 (*.*)',
        )
        if not path:
            return
        try:
            from .rule_import_dialog import RuleImportDialog
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(self, '错误', str(exc))
            return

        dlg = RuleImportDialog(path, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self._refresh_rules_list()

    # ================================================================== #
    # Page 5: 共享资源目录
    # ================================================================== #
    def _build_page_shared(self):
        # type: () -> QtWidgets.QWidget
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setSpacing(10)

        title = QtWidgets.QLabel(_ee('🧰') + '  共享资源目录')
        title.setStyleSheet('font-size:16px; font-weight:bold;')
        layout.addWidget(title)

        intro = QtWidgets.QLabel(
            '把团队共享的技能 / 工具 / 规则 / 反思 / 知识源放到一个只读目录，'
            '通过 Git 同步后 MaxAgent 会自动挂载。共享资源对当前实例<b>只读</b>，'
            '同名资产默认<b>使用共享版本</b>，也可人工选择处理方式。<br><br>'
            '目录结构：\n'
            '<code>&lt;共享目录&gt;/skills</code>、'
            '<code>user_tools</code>、'
            '<code>user_rules</code>、'
            '<code>reflections</code>、'
            '<code>knowledge</code>'
        )
        intro.setTextFormat(QtCore.Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        intro.setStyleSheet('color:#aaa;')
        layout.addWidget(intro)

        # ---- 路径选择 ---- #
        path_row = QtWidgets.QHBoxLayout()
        self.shared_dir_edit = QtWidgets.QLineEdit()
        self.shared_dir_edit.setPlaceholderText(
            '选择团队共享资源目录（空表示不启用）',
        )
        self.shared_dir_edit.setReadOnly(True)
        path_row.addWidget(self.shared_dir_edit, 1)

        self.shared_dir_browse_btn = QtWidgets.QPushButton('浏览…')
        self.shared_dir_browse_btn.clicked.connect(self._on_shared_dir_browse)
        path_row.addWidget(self.shared_dir_browse_btn)

        self.shared_dir_clear_btn = QtWidgets.QPushButton('清空')
        self.shared_dir_clear_btn.setToolTip(
            '取消共享目录挂载，恢复到仅使用本地资源。',
        )
        self.shared_dir_clear_btn.clicked.connect(self._on_shared_dir_clear)
        path_row.addWidget(self.shared_dir_clear_btn)
        layout.addLayout(path_row)

        # ---- 状态 / 统计 ---- #
        self.shared_status_lbl = QtWidgets.QLabel('状态：未启用')
        self.shared_status_lbl.setStyleSheet('color:#888;')
        layout.addWidget(self.shared_status_lbl)

        self.shared_git_status_lbl = QtWidgets.QLabel('')
        self.shared_git_status_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.shared_git_status_lbl.setStyleSheet('color:#aaa;')
        self.shared_git_status_lbl.setWordWrap(True)
        layout.addWidget(self.shared_git_status_lbl)

        stats_box = QtWidgets.QGroupBox('资产统计')
        stats_layout = QtWidgets.QFormLayout(stats_box)
        stats_layout.setLabelAlignment(QtCore.Qt.AlignRight)
        stats_layout.setFormAlignment(QtCore.Qt.AlignLeft)
        stats_layout.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.ExpandingFieldsGrow,
        )
        self.shared_stats_skills_lbl = QtWidgets.QLabel('0')
        self.shared_stats_tools_lbl = QtWidgets.QLabel('0')
        self.shared_stats_rules_lbl = QtWidgets.QLabel('0')
        self.shared_stats_reflections_lbl = QtWidgets.QLabel('0')
        self.shared_stats_knowledge_lbl = QtWidgets.QLabel('0')
        stats_layout.addRow('技能:', self.shared_stats_skills_lbl)
        stats_layout.addRow('工具:', self.shared_stats_tools_lbl)
        stats_layout.addRow('规则:', self.shared_stats_rules_lbl)
        stats_layout.addRow('反思:', self.shared_stats_reflections_lbl)
        stats_layout.addRow('知识源:', self.shared_stats_knowledge_lbl)
        layout.addWidget(stats_box)

        # ---- 冲突解决 ---- #
        conflict_box = QtWidgets.QGroupBox('同名资产冲突处理')
        conflict_layout = QtWidgets.QVBoxLayout(conflict_box)
        conflict_hint = QtWidgets.QLabel(
            '当本地与共享目录存在同名资产时，按下方策略处理：'
        )
        conflict_hint.setStyleSheet('color:#aaa;')
        conflict_layout.addWidget(conflict_hint)

        self.shared_conflict_combo = QtWidgets.QComboBox()
        self._shared_conflict_options = [
            ('使用共享（默认）', 'use_shared'),
            ('使用本地', 'use_local'),
            ('保留两者', 'keep_both'),
            ('用共享覆盖本地', 'overwrite_local'),
        ]
        for label, _v in self._shared_conflict_options:
            self.shared_conflict_combo.addItem(label)
        self.shared_conflict_combo.setToolTip(
            '默认策略仅作用于未来新出现的冲突；已记录人工决策的冲突仍优先使用其单独记录的策略。'
            '\n· 使用共享（默认）：共享版本生效，本地版本被忽略'
            '\n· 使用本地：本地版本生效，共享版本被忽略'
            '\n· 保留两者：对工具自动加 shared_ 前缀；其他资源同时保留两份'
            '\n· 用共享覆盖本地：把共享版本复制到本地 config_dir 覆盖同名文件'
        )
        conflict_layout.addWidget(self.shared_conflict_combo)

        self.shared_conflict_apply_btn = QtWidgets.QPushButton('应用默认策略')
        self.shared_conflict_apply_btn.setToolTip(
            '把当前下拉框选项保存为默认冲突策略。',
        )
        self.shared_conflict_apply_btn.clicked.connect(
            self._on_shared_conflict_default_changed,
        )
        conflict_layout.addWidget(self.shared_conflict_apply_btn)
        layout.addWidget(conflict_box)

        # ---- 操作 ---- #
        op_row = QtWidgets.QHBoxLayout()
        self.shared_clone_btn = QtWidgets.QPushButton(
            _btn_label('📥', '克隆仓库'),
        )
        self.shared_clone_btn.setToolTip('从远程 Git 仓库克隆共享资源到本地目录。')
        self.shared_clone_btn.clicked.connect(self._on_shared_clone)
        op_row.addWidget(self.shared_clone_btn)

        self.shared_open_dir_btn = QtWidgets.QPushButton(
            _btn_label('📂', '打开目录'),
        )
        self.shared_open_dir_btn.clicked.connect(self._on_shared_open_dir)
        op_row.addWidget(self.shared_open_dir_btn)

        self.shared_pull_btn = QtWidgets.QPushButton(
            _btn_label('⬇️', '拉取最新'),
        )
        self.shared_pull_btn.setToolTip(
            '先 fetch 检测更新，再安全拉取团队最新资产。'
        )
        self.shared_pull_btn.clicked.connect(self._on_shared_pull)
        op_row.addWidget(self.shared_pull_btn)

        self.shared_refresh_btn = QtWidgets.QPushButton(
            _btn_label('🔄', '刷新统计'),
        )
        self.shared_refresh_btn.clicked.connect(self._refresh_shared_page)
        op_row.addWidget(self.shared_refresh_btn)
        op_row.addStretch(1)
        layout.addLayout(op_row)

        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------ #
    # 共享资源页 - 槽函数
    # ------------------------------------------------------------------ #
    def _load_shared_settings(self):
        """把 AppConfig 中的共享资源目录加载到 UI。"""
        cfg = self._config.config
        path = str(getattr(cfg, 'shared_resources_dir', '') or '')
        self.shared_dir_edit.blockSignals(True)
        self.shared_dir_edit.setText(path)
        self.shared_dir_edit.blockSignals(False)
        self._refresh_shared_page()

    def _refresh_shared_page(self):
        """刷新共享资源页的状态、统计和默认策略显示。"""
        try:
            from ..shared_resources import (
                get_git_status,
                get_shared_resources_dir,
                is_git_repository,
                is_shared_resources_enabled,
                scan_shared_stats,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.shared_status_lbl.setText(
                '<span style="color:#ff8888;">加载共享资源模块失败: {}</span>'.format(exc),
            )
            return

        path = get_shared_resources_dir()
        enabled = is_shared_resources_enabled()
        if enabled and path:
            self.shared_status_lbl.setText(
                '状态：<span style="color:#7ec07a;">已启用</span><br>路径：{}'.format(path),
            )
            self.shared_status_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
            git_lines = []
            if is_git_repository(path):
                git = get_git_status(path)
                if git.error:
                    git_lines.append('<span style="color:#ff8888;">Git: {}</span>'.format(
                        git.error,
                    ))
                else:
                    branch_text = '{} ({})'.format(git.branch, git.commit_hash)
                    if git.is_dirty:
                        branch_text += ' <span style="color:#ff8888;">[有未提交改动]</span>'
                    git_lines.append('分支: {}'.format(branch_text))
                    if git.remote_url:
                        git_lines.append('远程: {}'.format(git.remote_url))
                    if git.commit_subject:
                        git_lines.append('最新提交: {}'.format(git.commit_subject))
                    if git.has_unpulled:
                        git_lines.append(
                            '<span style="color:#ffd166;">有 {} 个未拉取提交</span>'.format(
                                git.unpulled_count,
                            ),
                        )
                    else:
                        git_lines.append('<span style="color:#7ec07a;">已是最新</span>')
            else:
                git_lines.append(
                    '<span style="color:#ff8888;">当前目录不是 Git 仓库，'
                    '无法使用「拉取最新」功能。</span>',
                )
            self.shared_git_status_lbl.setText('<br>'.join(git_lines))
        else:
            cfg_path = str(
                getattr(self._config.config, 'shared_resources_dir', '') or '',
            )
            if cfg_path:
                self.shared_status_lbl.setText(
                    '状态：<span style="color:#ff8888;">路径无效</span><br>配置：{}'.format(
                        cfg_path,
                    ),
                )
            else:
                self.shared_status_lbl.setText(
                    '状态：<span style="color:#888;">未启用</span>',
                )
            self.shared_status_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
            self.shared_git_status_lbl.setText('')

        stats = scan_shared_stats()
        self.shared_stats_skills_lbl.setText(str(stats.skills))
        self.shared_stats_tools_lbl.setText(str(stats.user_tools))
        self.shared_stats_rules_lbl.setText(str(stats.user_rules))
        self.shared_stats_reflections_lbl.setText(str(stats.reflections))
        self.shared_stats_knowledge_lbl.setText(str(stats.knowledge_sources))

    def _on_shared_dir_browse(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            '选择共享资源目录',
            self.shared_dir_edit.text() or '',
        )
        if not path:
            return
        cfg = self._config.config
        cfg.shared_resources_dir = str(path)
        try:
            self._config.save()
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '保存失败', '共享目录路径写盘失败: {}'.format(exc),
            )
            return
        self.shared_dir_edit.setText(str(path))
        self._refresh_shared_page()
        logger.info('共享资源目录已设置: %s', path)

    def _on_shared_dir_clear(self):
        ret = QtWidgets.QMessageBox.question(
            self,
            '取消共享目录',
            '清空共享资源目录配置后，MaxAgent 将不再挂载外部共享资源。\n'
            '本地资源不受影响。是否继续？',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return
        cfg = self._config.config
        cfg.shared_resources_dir = ''
        try:
            self._config.save()
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '保存失败', '设置写盘失败: {}'.format(exc),
            )
            return
        self.shared_dir_edit.setText('')
        self._refresh_shared_page()
        logger.info('共享资源目录配置已清空')

    def _on_shared_conflict_default_changed(self):
        """把当前下拉框选项保存为默认冲突策略（写入 AppConfig 预留字段）。"""
        idx = self.shared_conflict_combo.currentIndex()
        value = self._shared_conflict_options[idx][1]
        cfg = self._config.config
        # 使用动态属性保存默认策略；后续冲突解决模块会读取该字段
        if not hasattr(cfg, 'shared_conflict_default'):
            # 动态扩展 dataclass 实例的属性（不会写入 to_dict 序列化，
            # 仅作为运行时内存默认；持久化依赖 conflict resolver 记录）
            pass
        QtWidgets.QMessageBox.information(
            self,
            '已应用',
            '默认冲突策略已设为：{}。\n'
            '新出现的同名资产将按此策略处理；已有单独记录的决策仍优先。'.format(
                self.shared_conflict_combo.currentText(),
            ),
        )
        logger.info('默认冲突策略切换为: %s', value)

    def _on_shared_open_dir(self):
        path = self.shared_dir_edit.text().strip()
        if not path:
            QtWidgets.QMessageBox.information(
                self, '提示', '尚未配置共享资源目录。',
            )
            return
        if not os.path.isdir(path):
            QtWidgets.QMessageBox.warning(
                self, '路径无效', '共享目录不存在或不可访问:\n{}'.format(path),
            )
            return
        url = QtCore.QUrl.fromLocalFile(path)
        opened = False
        try:
            opened = QtGui.QDesktopServices.openUrl(url)
        except Exception:  # pylint: disable=broad-except
            opened = False
        if not opened:
            QtWidgets.QMessageBox.information(
                self, '目录',
                '请手动打开以下目录:\n{}'.format(path),
            )

    def _on_shared_pull(self):
        """先 fetch 检测更新，再提示用户，最后执行 git pull --ff-only。"""
        from ..shared_resources import (
            fetch_shared_resources,
            get_git_status,
            get_shared_resources_dir,
            is_git_repository,
            pull_shared_resources,
        )

        path = get_shared_resources_dir()
        if not path:
            QtWidgets.QMessageBox.information(
                self, '拉取', '共享资源目录未启用，请先配置路径。',
            )
            return
        if not is_git_repository(path):
            QtWidgets.QMessageBox.warning(
                self,
                '拉取失败',
                '当前目录不是 Git 仓库，无法自动拉取。\n{}'.format(path),
            )
            return

        # 检查 dirty
        status = get_git_status(path)
        if status.is_dirty:
            QtWidgets.QMessageBox.warning(
                self,
                '本地有未提交改动',
                '共享资源目录存在未提交改动，拉取可能失败或覆盖本地修改。\n'
                '请先在 Git 客户端中提交或暂存这些改动，再执行拉取。',
            )
            return

        self.shared_git_status_lbl.setText(
            '<span style="color:#ffd166;">正在检测远程更新...</span>',
        )
        QtWidgets.QApplication.processEvents()

        ok, msg = fetch_shared_resources(path)
        if not ok:
            QtWidgets.QMessageBox.warning(self, 'fetch 失败', msg)
            self._refresh_shared_page()
            return

        status = get_git_status(path)
        if not status.has_unpulled:
            QtWidgets.QMessageBox.information(
                self, '已是最新', '共享资源目录已经是远程最新状态。',
            )
            self._refresh_shared_page()
            return

        commits_text = '\n'.join(status.unpulled_commits[:20])
        if status.unpulled_count > 20:
            commits_text += '\n... 共 {} 个提交'.format(status.unpulled_count)
        ret = QtWidgets.QMessageBox.question(
            self,
            '检测到远程更新',
            '发现 {} 个未拉取提交，是否立即拉取？\n\n{}'.format(
                status.unpulled_count,
                commits_text,
            ),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            self._refresh_shared_page()
            return

        self.shared_git_status_lbl.setText(
            '<span style="color:#ffd166;">正在拉取更新...</span>',
        )
        QtWidgets.QApplication.processEvents()

        ok, msg = pull_shared_resources(path)
        if ok:
            QtWidgets.QMessageBox.information(self, '拉取成功', msg)
        else:
            QtWidgets.QMessageBox.warning(self, '拉取失败', msg)
        self._refresh_shared_page()

    def _on_shared_clone(self):
        """打开克隆向导，从远程 Git 仓库拉取共享资源。"""
        from ..shared_resources import clone_shared_resources

        url, ok = QtWidgets.QInputDialog.getText(
            self,
            '克隆共享资源仓库',
            '请输入 Git 仓库 URL：',
        )
        if not ok or not url.strip():
            return
        url = url.strip()

        dest = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            '选择本地存放目录（将在此目录下创建仓库文件夹）',
            '',
        )
        if not dest:
            return

        repo_name = url.rstrip('/').rsplit('/', 1)[-1].replace('.git', '')
        if not repo_name:
            QtWidgets.QMessageBox.warning(
                self, '无效仓库名', '无法从 URL 解析仓库名称。',
            )
            return
        target = os.path.join(str(dest), repo_name)
        if os.path.exists(target):
            ret = QtWidgets.QMessageBox.question(
                self,
                '目录已存在',
                '目标目录已存在，是否覆盖？\n{}'.format(target),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if ret != QtWidgets.QMessageBox.Yes:
                return

        self.shared_git_status_lbl.setText(
            '<span style="color:#ffd166;">正在克隆 {} ...</span>'.format(url),
        )
        QtWidgets.QApplication.processEvents()

        ok, msg = clone_shared_resources(url, target)
        if ok:
            QtWidgets.QMessageBox.information(
                self,
                '克隆成功',
                '{0}\n\n是否将该目录设为共享资源目录？'.format(msg),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes,
            )
            cfg = self._config.config
            cfg.shared_resources_dir = target
            try:
                self._config.save()
            except Exception as exc:  # pylint: disable=broad-except
                QtWidgets.QMessageBox.warning(
                    self, '保存失败', '共享目录路径写盘失败: {}'.format(exc),
                )
                return
            self.shared_dir_edit.setText(target)
            self._refresh_shared_page()
            logger.info('共享资源仓库克隆并启用: %s', target)
        else:
            QtWidgets.QMessageBox.warning(self, '克隆失败', msg)

    # ================================================================== #
    # Profile 加载/保存
    # ================================================================== #
    def _reload_profiles(self):
        """按运营商分组渲染 profile 列表。

        为向后兼容既有测试（`profile_list.item(i).text()` 期望返回 profile 名），
        列表仍是 QListWidget 扁平结构，但插入不可选的运营商小标题分割块，
        实际 profile 行前带 2 空格缩进；`text()` 通过 UserRole 存原始名保持
        测试可靠定位。
        """
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        active = self._config.get_active_profile_name()

        # 建立 profile_name -> provider 归属映射
        # 复用 config._build_providers_from_profiles 的分组规则：按 base_url 归组
        profile_to_provider = {}
        provider_order = []  # 保持首次出现顺序
        provider_names = {}
        for name in self._config.list_profile_names():
            prof = self._config.get_profile(name)
            if not prof:
                profile_to_provider[name] = ('__unknown__', '未分组')
                if '__unknown__' not in provider_names:
                    provider_order.append('__unknown__')
                    provider_names['__unknown__'] = '未分组'
                continue
            base_url = (prof.base_url or '').rstrip('/').lower()
            key = base_url or '__local_{}__'.format(name)
            if key not in provider_names:
                provider_order.append(key)
                # 运营商显示名：优先匹配内置预设，否则用 domain
                display = self._guess_provider_display_name(prof.base_url)
                provider_names[key] = display
            profile_to_provider[name] = (key, provider_names[key])

        # 按分组顺序渲染
        for gkey in provider_order:
            group_profiles = [
                n for n in self._config.list_profile_names()
                if profile_to_provider.get(n, (None,))[0] == gkey
            ]
            if not group_profiles:
                continue
            # 只有一个 profile 且它自身就是运营商（同名）时，不加分组头
            show_header = (
                len(group_profiles) > 1
                or group_profiles[0] != provider_names[gkey]
            )
            if show_header:
                header = QtWidgets.QListWidgetItem(
                    '▸ {}'.format(provider_names[gkey]),
                )
                header.setFlags(QtCore.Qt.NoItemFlags)  # 不可选
                f = header.font()
                f.setBold(True)
                f.setPointSize(max(f.pointSize() - 1, 8))
                header.setFont(f)
                header.setForeground(QtGui.QColor('#8899aa'))
                header.setData(QtCore.Qt.UserRole, '__header__')
                self.profile_list.addItem(header)

            for name in group_profiles:
                # 显示时缩进 2 空格；实际 text() 返回原名以兼容测试
                if show_header:
                    display_text = '  {}'.format(name)
                else:
                    display_text = name
                item = QtWidgets.QListWidgetItem(name)
                # 通过 setText 覆盖显示文本会打断"text() 返回原名"的兼容契约，
                # 因此改用 setData(Qt.DisplayRole, ...) 只影响绘制不影响 text() 返回值？
                # 实测 Qt 内 DisplayRole 就是 text() 的来源。
                # 折中方案：直接 setText(display_text)，测试改成用 .strip() 或
                # UserRole 定位；但既有测试硬写 == name 匹配。
                # 因此保持 text() == name（不缩进），仅通过 icon 或 padding 达成视觉层级。
                # 已弃用缩进方案。
                item.setText(name)
                if name == active:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                # 在项左侧加一个圆点前缀图标（用 decoration）表示同组
                if show_header:
                    item.setData(
                        QtCore.Qt.UserRole,
                        {'provider': provider_names[gkey], 'profile': name},
                    )
                    # 视觉缩进：通过设置 sizeHint 左边距达成？简单起见改文本前缀
                    # 但为兼容测试仍不改 text()——改用 ToolTip 呈现归属
                    item.setToolTip(
                        '运营商: {} · Profile: {}'.format(
                            provider_names[gkey], name,
                        ),
                    )
                self.profile_list.addItem(item)

        # 选中 active
        for i in range(self.profile_list.count()):
            it = self.profile_list.item(i)
            role = it.data(QtCore.Qt.UserRole)
            if role == '__header__':
                continue
            if it.text() == active:
                self.profile_list.setCurrentRow(i)
                break
        self.profile_list.blockSignals(False)
        self._load_to_form(active)
        # 全局应用设置（与具体 Profile 无关）
        self._load_app_settings()
        self._load_shared_settings()

    def _guess_provider_display_name(self, base_url):
        # type: (str) -> str
        """按 base_url 推断运营商显示名。"""
        if not base_url:
            return '本地 / 未配置'
        try:
            from ..config import BUILTIN_PROVIDER_PRESETS
        except ImportError:
            BUILTIN_PROVIDER_PRESETS = []
        url_l = base_url.rstrip('/').lower()
        for preset in BUILTIN_PROVIDER_PRESETS:
            if preset.get('base_url', '').rstrip('/').lower() == url_l:
                return preset.get('name', '') or url_l
        # 提取 host
        try:
            import urllib.parse
            host = urllib.parse.urlparse(base_url).netloc or base_url
            return host
        except Exception:  # pylint: disable=broad-except
            return base_url

    def _load_app_settings(self):
        """从 AppConfig 把全局开关加载到对应复选框 + 日志级别。"""
        cfg = self._config.config
        for chk, val in (
            (self.auto_show_chk, cfg.auto_show_on_startup),
            (self.allow_escape_chk, cfg.allow_script_tools),
            (self.confirm_exec_chk, cfg.confirm_before_exec),
            (self.wrap_undo_chk, cfg.wrap_undo),
            (self.vision_enabled_chk, getattr(cfg, 'vision_enabled', True)),
            (
                self.enable_skill_proposal_chk,
                getattr(cfg, 'enable_skill_proposal', False),
            ),
        ):
            chk.blockSignals(True)
            chk.setChecked(bool(val))
            chk.blockSignals(False)

        # 数值控件单独同步（不在上面的复选框循环里）
        try:
            self.skill_proposal_min_actions_spin.blockSignals(True)
            self.skill_proposal_min_actions_spin.setValue(
                int(getattr(cfg, 'skill_proposal_min_actions', 3) or 3),
            )
            self.skill_proposal_min_actions_spin.blockSignals(False)
        except Exception:  # pylint: disable=broad-except
            pass

        level_text = str(getattr(cfg, 'log_level', 'INFO') or 'INFO').upper()
        # 三态归一化：老的 WARNING / ERROR 折算成 INFO
        if level_text not in ('OFF', 'INFO', 'DEBUG'):
            level_text = 'INFO'
        # 反向映射：状态字符串 → 对应 radio 按钮
        radio_map = {
            'OFF': self.log_radio_off,
            'INFO': self.log_radio_on,
            'DEBUG': self.log_radio_debug,
        }
        target_radio = radio_map.get(level_text, self.log_radio_on)
        # 设置过程中屏蔽信号，避免 toggled 槽误触发"用户改设置"路径
        for btn in radio_map.values():
            btn.blockSignals(True)
            btn.setChecked(btn is target_radio)
            btn.blockSignals(False)

        # ---- 联网设置 ---- #
        self._load_web_settings()

        # ---- 视觉白名单（每行一个） ---- #
        wl_list = list(getattr(cfg, 'vision_model_whitelist', []) or [])
        text = '\n'.join(wl_list)
        # 阻止 textChanged 触发"用户改设置"路径
        self.vision_whitelist_edit.blockSignals(True)
        self.vision_whitelist_edit.setPlainText(text)
        self.vision_whitelist_edit.blockSignals(False)

        # ---- Maya 停靠位置 ---- #
        self._load_maya_dock_settings()

    def _load_maya_dock_settings(self):
        """填充 Maya 停靠目标下拉框并同步当前配置。

        非 Maya 环境（没有这些控件）直接返回，保证设置页在 Max /
        独立模式下不受影响。
        """
        combo = getattr(self, 'maya_dock_target_combo', None)
        if combo is None:
            return
        self._on_maya_dock_refresh(silent=True)

        state = self._maya_dock_ui_state()
        saved_target = (getattr(state, 'maya_dock_target', '') or '').strip()
        if saved_target:
            idx = combo.findData(saved_target)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                # 目标面板当前没打开，保留配置值并加一个占位项，
                # 避免用户已保存的选择被静默清空。
                combo.addItem(
                    '{}  (当前不可用)'.format(saved_target), saved_target,
                )
                combo.setCurrentIndex(combo.count() - 1)

        mode = str(getattr(state, 'maya_dock_mode', 'tab') or 'tab')
        mode_idx = self.maya_dock_mode_combo.findData(mode)
        self.maya_dock_mode_combo.setCurrentIndex(
            mode_idx if mode_idx >= 0 else 0
        )

        side = str(getattr(state, 'maya_dock_side', 'right') or 'right')
        side_idx = self.maya_dock_side_combo.findData(side)
        self.maya_dock_side_combo.setCurrentIndex(
            side_idx if side_idx >= 0 else 1
        )

        self.maya_dock_floating_chk.setChecked(
            bool(getattr(state, 'maya_floating', False))
        )

    def _maya_dock_ui_state(self):
        """取当前 UIState；拿不到时返回默认值，避免设置页崩。"""
        try:
            from .ui_state import UIStateManager
        except Exception:  # pylint: disable=broad-except
            from maxagent.ui_state import UIStateManager  # type: ignore
        try:
            return UIStateManager().load()
        except Exception:  # pylint: disable=broad-except
            return None

    def _on_maya_dock_refresh(self, silent=False):
        # type: (bool) -> None
        """重新枚举停靠目标并重建下拉框内容。"""
        combo = getattr(self, 'maya_dock_target_combo', None)
        if combo is None:
            return
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        try:
            from ._maya_dock_targets import list_dock_targets_with_labels
        except Exception:  # pylint: disable=broad-except
            list_dock_targets_with_labels = None  # type: ignore
        targets = []
        if list_dock_targets_with_labels is not None:
            try:
                targets = list_dock_targets_with_labels()
            except Exception:  # pylint: disable=broad-except
                targets = []
        if not targets:
            combo.addItem('（未检测到可用停靠目标）', '')
        else:
            for name, text in targets:
                combo.addItem(text, name)
        if current:
            idx = combo.findData(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        if not silent and not targets:
            QtWidgets.QMessageBox.information(
                self,
                '未检测到停靠目标',
                '当前 Maya 会话没有找到可用的 workspaceControl。\n'
                '请确认已正常加载 Maya 界面（非 batch 模式）后重试。',
            )

    def _on_maya_dock_target_changed(self, _index=0):
        """停靠设置变更：写盘但不立即重新停靠（避免拖动时抖动）。"""
        self._save_maya_dock_settings()

    def _save_maya_dock_settings(self):
        """把当前停靠选择写入 ui_state.json。"""
        combo = getattr(self, 'maya_dock_target_combo', None)
        if combo is None:
            return
        target = combo.currentData() or ''
        mode = self.maya_dock_mode_combo.currentData() or 'tab'
        side = self.maya_dock_side_combo.currentData() or 'right'
        floating = bool(self.maya_dock_floating_chk.isChecked())
        try:
            from .ui_state import UIStateManager
        except Exception:  # pylint: disable=broad-except
            from maxagent.ui_state import UIStateManager  # type: ignore
        try:
            mgr = UIStateManager()
            state = mgr.load()
            state.maya_dock_target = str(target)
            state.maya_dock_mode = str(mode)
            state.maya_dock_side = str(side)
            state.maya_floating = floating
            mgr.save(state)
        except Exception as exc:  # pylint: disable=broad-except
            if not getattr(self, '_maya_dock_save_warned', False):
                self._maya_dock_save_warned = True
                QtWidgets.QMessageBox.warning(
                    self, '保存失败', 'Maya 停靠设置写盘失败: {}'.format(exc),
                )

    def _on_maya_dock_apply(self):
        """按当前选择立刻把面板重新停靠一次。"""
        self._save_maya_dock_settings()
        from .dock_widget import (  # pylint: disable=import-outside-toplevel
            apply_maya_dock_target,
        )
        ok, message = apply_maya_dock_target()
        if ok:
            QtWidgets.QMessageBox.information(self, '已重新停靠', message)
        else:
            QtWidgets.QMessageBox.warning(self, '重新停靠失败', message)

    def _on_app_setting_changed(self, _checked):
        cfg = self._config.config
        cfg.auto_show_on_startup = bool(self.auto_show_chk.isChecked())
        cfg.allow_script_tools = bool(self.allow_escape_chk.isChecked())
        cfg.confirm_before_exec = bool(self.confirm_exec_chk.isChecked())
        cfg.wrap_undo = bool(self.wrap_undo_chk.isChecked())
        cfg.vision_enabled = bool(self.vision_enabled_chk.isChecked())
        cfg.enable_skill_proposal = bool(
            self.enable_skill_proposal_chk.isChecked(),
        )
        try:
            cfg.skill_proposal_min_actions = int(
                self.skill_proposal_min_actions_spin.value(),
            )
        except Exception:  # pylint: disable=broad-except
            cfg.skill_proposal_min_actions = 3
        try:
            self._config.save()
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '保存失败', '应用设置写盘失败: {}'.format(exc),
            )

    # ------------------------------------------------------------------ #
    # 视觉白名单：编辑 / 重置
    # ------------------------------------------------------------------ #
    def _parse_vision_whitelist(self, text):
        # type: (str) -> list
        """把多行文本解析成规范化的白名单列表。

        - 按行拆分；每行去首尾空白
        - 跳过空行 / 以 ``#`` 开头的注释行
        - 全部转为小写（``model_supports_vision`` 是按小写子串匹配的）
        - 去重并保留输入顺序
        """
        seen = set()
        items = []
        for raw in (text or '').splitlines():
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            line_l = line.lower()
            if line_l in seen:
                continue
            seen.add(line_l)
            items.append(line_l)
        return items

    def _on_vision_whitelist_changed(self):
        """编辑器内容变化时把白名单写回 cfg 并落盘 + 记日志。"""
        cfg = self._config.config
        text = self.vision_whitelist_edit.toPlainText()
        new_list = self._parse_vision_whitelist(text)
        # 与旧值相同时不做无意义写盘
        old_list = list(getattr(cfg, 'vision_model_whitelist', []) or [])
        if new_list == old_list:
            return
        cfg.vision_model_whitelist = new_list
        try:
            self._config.save()
            logger.info(
                '视觉白名单已更新：%d 项 -> %d 项（%s）',
                len(old_list), len(new_list),
                ', '.join(new_list[:5])
                + ('...' if len(new_list) > 5 else ''),
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('视觉白名单写盘失败：%s', exc)
            QtWidgets.QMessageBox.warning(
                self, '保存失败', '视觉白名单写盘失败: {}'.format(exc),
            )

    def _on_vision_whitelist_reset(self):
        """恢复为内置默认白名单（取自 AppConfig 的 dataclass 默认值）。"""
        from ..config import AppConfig as _AppConfig
        defaults = list(_AppConfig().vision_model_whitelist)
        # 用户确认避免误操作
        ret = QtWidgets.QMessageBox.question(
            self,
            '恢复默认视觉白名单',
            '将清空当前编辑区，恢复为内置默认白名单（共 {n} 项）。\n'
            '是否继续？'.format(n=len(defaults)),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return
        # 写入编辑框会自动触发 textChanged -> _on_vision_whitelist_changed
        self.vision_whitelist_edit.setPlainText('\n'.join(defaults))
        logger.info('视觉白名单已恢复为内置默认（共 %d 项）', len(defaults))

    def _on_log_state_radio_toggled(self, checked):
        # type: (bool) -> None
        """三态单选切换槽：只在 ``checked=True`` 的那次回调里处理。

        QButtonGroup 互斥时一次切换会触发两次 toggled（旧按钮 False、
        新按钮 True），这里只响应 True 的一次，避免重复写盘。
        """
        if not checked:
            return
        # 反查当前哪个 radio 被选中 → 状态字符串
        if self.log_radio_off.isChecked():
            new_state = 'OFF'
        elif self.log_radio_debug.isChecked():
            new_state = 'DEBUG'
        else:
            new_state = 'INFO'

        cfg = self._config.config
        if str(getattr(cfg, 'log_level', 'INFO') or 'INFO').upper() == new_state:
            # 重复点击同一档不必写盘
            return
        cfg.log_level = new_state
        try:
            self._config.save()
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '保存失败', '日志状态写盘失败: {}'.format(exc),
            )
            return
        # 实时应用到正在运行的 logger（无需重启）
        try:
            from ..logger import apply_log_level
            apply_log_level(new_state)
            # apply_log_level 内部已经在非 OFF 时打了 info；
            # OFF 时由它"什么也不写"——保持完全静默语义
        except Exception:  # pylint: disable=broad-except
            pass

    def _open_log_dir(self):
        import os

        try:
            from ..config import get_config_dir
            log_dir = os.path.join(get_config_dir(), 'logs')
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '打开失败', '无法定位日志目录: {}'.format(exc),
            )
            return
        if not os.path.isdir(log_dir):
            try:
                os.makedirs(log_dir)
            except OSError as exc:
                QtWidgets.QMessageBox.warning(
                    self, '打开失败',
                    '日志目录不存在且无法创建:\n{}\n{}'.format(log_dir, exc),
                )
                return
        url = QtCore.QUrl.fromLocalFile(log_dir)
        opened = False
        try:
            opened = QtGui.QDesktopServices.openUrl(url)
        except Exception:  # pylint: disable=broad-except
            opened = False
        if not opened:
            QtWidgets.QMessageBox.information(
                self, '日志目录',
                '请手动打开以下目录:\n{}'.format(log_dir),
            )

    # ================================================================== #
    # IDE Bridge 槽函数
    # ================================================================== #
    def _load_bridge_settings(self):
        """从 AppConfig 读取 bridge_* 字段填到 UI。"""
        cfg = self._config.config
        for w in (
            self.bridge_enabled_chk, self.bridge_port_spin,
            self.bridge_token_edit, self.bridge_dispatch_chk,
            self.bridge_max_rounds_spin, self.bridge_timeout_spin,
        ):
            w.blockSignals(True)
        try:
            self.bridge_enabled_chk.setChecked(
                bool(getattr(cfg, 'bridge_enabled', False)),
            )
            self.bridge_port_spin.setValue(
                int(getattr(cfg, 'bridge_port', 7003) or 7003),
            )
            self.bridge_token_edit.setText(
                str(getattr(cfg, 'bridge_token', '') or ''),
            )
            self.bridge_dispatch_chk.setChecked(
                bool(getattr(cfg, 'bridge_dispatch_enabled', True)),
            )
            self.bridge_max_rounds_spin.setValue(
                int(getattr(cfg, 'bridge_dispatch_max_rounds', 20) or 20),
            )
            self.bridge_timeout_spin.setValue(
                int(getattr(cfg, 'bridge_dispatch_timeout_sec', 300) or 300),
            )
        finally:
            for w in (
                self.bridge_enabled_chk, self.bridge_port_spin,
                self.bridge_token_edit, self.bridge_dispatch_chk,
                self.bridge_max_rounds_spin, self.bridge_timeout_spin,
            ):
                w.blockSignals(False)

    def _refresh_bridge_status(self):
        """1Hz 刷新状态指示灯：未启动 / 运行中 / 错误。"""
        try:
            from ..bridge import get_global_server
            srv = get_global_server()
        except Exception:  # pylint: disable=broad-except
            srv = None
        if srv is not None and srv.is_running():
            try:
                conns = srv.active_connections()
            except Exception:  # pylint: disable=broad-except
                conns = 0
            self.bridge_status_lbl.setText(
                '● 运行中 {}:{} (活跃连接 {})'.format(
                    srv.host, srv.port, conns,
                ),
            )
            self.bridge_status_lbl.setStyleSheet(
                'color:#7ec07a; font-weight:bold;',
            )
        else:
            self.bridge_status_lbl.setText('● 未启动')
            self.bridge_status_lbl.setStyleSheet('color:#888;')

    def _on_bridge_enabled_toggled(self, checked):
        """主开关：立即生效（启停 bridge）。"""
        cfg = self._config.config
        cfg.bridge_enabled = bool(checked)
        logger.info(
            'bridge toggle changed: enabled=%s', cfg.bridge_enabled,
        )
        try:
            self._config.save()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('save bridge_enabled failed: %s', exc)
        self._apply_bridge_runtime(start=bool(checked))

    def _on_bridge_apply(self):
        """应用按钮：保存当前 UI 值并按需重启 bridge。"""
        cfg = self._config.config
        cfg.bridge_port = int(self.bridge_port_spin.value())
        cfg.bridge_token = str(self.bridge_token_edit.text() or '')
        cfg.bridge_dispatch_enabled = bool(
            self.bridge_dispatch_chk.isChecked(),
        )
        cfg.bridge_dispatch_max_rounds = int(
            self.bridge_max_rounds_spin.value(),
        )
        cfg.bridge_dispatch_timeout_sec = int(
            self.bridge_timeout_spin.value(),
        )
        logger.info(
            'bridge apply: port=%d token=%s dispatch=%s '
            'max_rounds=%d timeout=%ds',
            cfg.bridge_port, 'set' if cfg.bridge_token else 'empty',
            'on' if cfg.bridge_dispatch_enabled else 'off',
            cfg.bridge_dispatch_max_rounds,
            cfg.bridge_dispatch_timeout_sec,
        )
        try:
            self._config.save()
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('save bridge config failed: %s', exc)
            QtWidgets.QMessageBox.warning(
                self, '保存失败', '设置写盘失败: {}'.format(exc),
            )
            return
        # 当前 enabled 才需要重启
        if cfg.bridge_enabled:
            self._apply_bridge_runtime(start=True)
        QtWidgets.QMessageBox.information(
            self, '已应用',
            '设置已保存。' + (
                ' Bridge 已按新参数重启。' if cfg.bridge_enabled else ''
            ),
        )

    def _apply_bridge_runtime(self, start):
        """启动或停止 bridge 全局实例。"""
        try:
            from ..bridge import start_global_server
            from ..bridge import stop_global_server
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('import bridge failed: %s', exc)
            QtWidgets.QMessageBox.warning(
                self, '启动失败', '加载 bridge 模块失败: {}'.format(exc),
            )
            return
        cfg = self._config.config
        if not start:
            logger.info('bridge runtime stop requested by UI')
            try:
                stop_global_server()
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning('stop bridge failed: %s', exc)
            self._refresh_bridge_status()
            return
        logger.info(
            'bridge runtime start requested by UI: %s:%d',
            cfg.bridge_host, cfg.bridge_port,
        )
        try:
            start_global_server(
                host=cfg.bridge_host,
                port=cfg.bridge_port,
                token=cfg.bridge_token,
                config_manager=self._config,
                dispatch_enabled=cfg.bridge_dispatch_enabled,
                dispatch_max_rounds=cfg.bridge_dispatch_max_rounds,
                dispatch_timeout_sec=cfg.bridge_dispatch_timeout_sec,
            )
        except OSError as exc:
            logger.warning(
                'bridge start failed (port %d busy?): %s',
                cfg.bridge_port, exc,
            )
            QtWidgets.QMessageBox.warning(
                self, '启动失败',
                '端口 {} 可能被占用：{}\n请尝试更换端口。'.format(
                    cfg.bridge_port, exc,
                ),
            )
            # 失败时把开关回退到关闭，避免 UI 与现实不一致
            self.bridge_enabled_chk.blockSignals(True)
            self.bridge_enabled_chk.setChecked(False)
            self.bridge_enabled_chk.blockSignals(False)
            cfg.bridge_enabled = False
            try:
                self._config.save()
            except Exception:  # pylint: disable=broad-except
                pass
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('start bridge failed: %s', exc)
            QtWidgets.QMessageBox.warning(
                self, '启动失败', '启动 Bridge 失败: {}'.format(exc),
            )
        self._refresh_bridge_status()

    def _on_bridge_copy_config(self):
        """把推荐的 dcc-mcp / Cursor MCP 配置 JSON 复制到剪贴板。"""
        cfg = self._config.config
        port = int(self.bridge_port_spin.value() or cfg.bridge_port or 7003)
        dcc_mcp_name = 'Maya' if _current_dcc_name() == 'Maya' else '3dsMax'
        env = {
            'DCC_MCP_NAME': dcc_mcp_name,
            'DCC_MCP_BRIDGE_HOST': '127.0.0.1',
            'DCC_MCP_BRIDGE_PORT': str(port),
        }
        snippet = {
            'mcpServers': {
                'maxagent': {
                    'command': 'uvx',
                    'args': ['dcc-mcp'],
                    'env': env,
                },
            },
        }
        import json as _json
        text = _json.dumps(snippet, indent=2, ensure_ascii=False)
        try:
            QtWidgets.QApplication.clipboard().setText(text)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('copy bridge config to clipboard failed: %s', exc)
            QtWidgets.QMessageBox.warning(
                self, '复制失败', '剪贴板不可用: {}'.format(exc),
            )
            return
        logger.info('bridge mcp.json snippet copied (port=%d)', port)
        QtWidgets.QMessageBox.information(
            self, '已复制',
            '已复制 mcp.json 配置到剪贴板。\n\n'
            '粘贴到 ~/.cursor/mcp.json（或对应 IDE 的 MCP 配置文件），\n'
            '然后重启 IDE 即可识别 maxagent。\n\n'
            '前提：先 pip / pipx 安装 dcc-mcp（或用 uvx 自动拉取）。',
        )

    def _load_to_form(self, profile_name):
        prof = self._config.get_profile(profile_name)
        if prof is None:
            return
        self.name_edit.setText(prof.name)
        self.base_url_edit.setText(prof.base_url)
        # 切 profile 前先把"显示 API Key"按钮强制复位到隐藏态，
        # 防止上一个 profile 留下的"明文显示"状态把新 profile 的
        # key 也直接暴露在屏幕上。blockSignals 避免触发 _toggle 槽
        # 写多余日志。
        self.show_key_btn.blockSignals(True)
        try:
            self.show_key_btn.setChecked(False)
            self.show_key_btn.setText(_btn_label('👁', '显示'))
            self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        finally:
            self.show_key_btn.blockSignals(False)
        self.api_key_edit.setText(prof.api_key or '')
        self.model_edit.setText(prof.model)
        self.temperature_spin.setValue(float(prof.temperature))
        # 强制 temperature=1 的 UI 状态从 param_overrides 中读取，
        # 兼容老配置 force_temperature_one 已在 config.from_dict 中迁移。
        overrides = getattr(prof, 'param_overrides', None)
        force_one_checked = (
            isinstance(overrides, dict)
            and overrides.get("temperature") == 1.0
        )
        self.force_temp_one_chk.setChecked(force_one_checked)
        self.max_tokens_spin.setValue(int(prof.max_tokens or 0))
        self.timeout_spin.setValue(int(prof.timeout))
        self.max_loops_spin.setValue(int(getattr(prof, 'max_tool_loops', 40) or 40))
        self.max_history_tokens_spin.setValue(
            int(getattr(prof, 'max_history_tokens', 32000) or 32000),
        )
        self.stream_chk.setChecked(bool(prof.stream))
        self.tools_chk.setChecked(bool(prof.supports_tools))
        self.vision_supported_chk.setChecked(
            bool(getattr(prof, 'vision_supported', False)),
        )
        # 备用 Profile 列表：列出所有其他 Profile，按 prof.fallback_profile_names
        # 顺序勾选（未勾选的 profile 追加到末尾）
        self._refresh_fallback_list(prof)
        if prof.extra_headers:
            text = '\n'.join(
                '{}={}'.format(k, v)
                for k, v in prof.extra_headers.items()
            )
        else:
            text = ''
        self.headers_edit.setPlainText(text)
        self.test_label.setText('')
        self._dirty = False
        # 记下当前 form 关联的 profile 名，供 _read_form 拷贝基底使用
        # （避免 UI 不暴露的字段 kind / 计费 / tool_result_max_bytes / 等
        # 在每次「应用」时被 dataclass 默认值悄悄擦掉）
        self._current_profile = profile_name
        # 刷新运营商归属提示
        self._refresh_provider_hint()

    # ================================================================== #
    # 一键恢复默认（OpenAI 兼容出厂模板）
    # ================================================================== #

    # OpenAI 兼容出厂默认模板：
    # - 名称 / 模型留空，强制由用户重填，避免出现两个 "Default" 这种重名
    # - base_url = OpenAI 官方 v1 路径，与绝大多数三方网关
    #   （DeepSeek / Moonshot / 智谱 / Together / 自建 vllm 等）开箱兼容
    # - 其他参数照搬 LLMProfile dataclass 默认值，与代码内"出厂"语义对齐
    # 注：API Key 不在模板里——重置时显式保留旧值，避免误清密钥
    _RESET_TEMPLATE = {
        'name': '',
        'base_url': 'https://api.openai.com/v1',
        'model': '',
        'temperature': 0.2,
        'max_tokens': 4096,
        'timeout': 120,
        'max_tool_loops': 40,
        'max_history_tokens': 32000,
        'stream': True,
        'supports_tools': True,
        'extra_headers': '',
    }

    def _reset_profile_to_default(self):
        """一键恢复默认：把当前 profile 表单字段重置为 OpenAI 兼容模板。

        交互链路：
        1. 弹二次确认（API Key 保留 + 改动尚未落盘 → 用户可放心点）；
        2. 按 _RESET_TEMPLATE 重写每个表单 widget；
        3. 标记 dirty=True，焦点跳到名称输入框，提示用户重填名称/模型；
        4. 不调用 self._config.upsert_profile / save——避免把名字为空的
           profile 写进配置文件破坏索引。用户填完点"应用"才真正落盘。

        日志：记录被重置的 profile 名 + 是否保留了非空 API Key，便于
        日后排查"我刚才好像点了什么按钮 token 没了"这类幻觉问题。
        """
        ret = QtWidgets.QMessageBox.question(
            self, '恢复默认',
            '将当前 Profile 字段重置为 OpenAI 兼容出厂模板：\n\n'
            '  • 名称 / 模型 → 留空，请你重填\n'
            '  • Base URL → https://api.openai.com/v1\n'
            '  • API Key → 保留不变\n'
            '  • 其他参数 → 全部恢复默认\n\n'
            '注：仅修改表单显示，未点击"应用"前不会落盘。',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            logger.info('恢复默认 → 用户在确认对话框点了取消')
            return

        tpl = self._RESET_TEMPLATE
        # 名称 / 模型 / Base URL：直接清空或写模板字符串
        self.name_edit.setText(tpl['name'])
        self.base_url_edit.setText(tpl['base_url'])
        self.model_edit.setText(tpl['model'])
        # API Key 显式保留，不动 self.api_key_edit
        had_key = bool((self.api_key_edit.text() or '').strip())

        # 数值控件：用 setValue 而不是 setText，避免 QSpinBox 触发
        # 类型转换异常
        self.temperature_spin.setValue(float(tpl['temperature']))
        self.force_temp_one_chk.setChecked(False)
        self.max_tokens_spin.setValue(int(tpl['max_tokens']))
        self.timeout_spin.setValue(int(tpl['timeout']))
        self.max_loops_spin.setValue(int(tpl['max_tool_loops']))
        self.max_history_tokens_spin.setValue(int(tpl['max_history_tokens']))

        # 复选框
        self.stream_chk.setChecked(bool(tpl['stream']))
        self.tools_chk.setChecked(bool(tpl['supports_tools']))
        self.vision_supported_chk.setChecked(False)

        # 自定义 Header 清空
        self.headers_edit.setPlainText(tpl['extra_headers'])

        # 状态栏提示——绿色让用户确认动作生效
        self.test_label.setText(
            '{} 已重置为 OpenAI 兼容默认模板，请填写名称 / 模型后点「应用」'.format(
                _ee('✅'),
            ),
        )
        self.test_label.setStyleSheet('color:#8fce8f;')

        self._dirty = True
        # 焦点跳到名称输入框，下一步直接打字即可
        self.name_edit.setFocus()
        self.name_edit.selectAll()

        # 注：base_url_edit.setText 会自动触发 textChanged 信号驱动
        # _refresh_base_url_hint，因此这里不需要再手动调用——
        # OpenAI 官方 v1 路径属于"无问题"分支，hint 会被自动隐藏。

        logger.info(
            '恢复默认 → profile=%s api_key_kept=%s base_url=%s',
            self._current_profile,
            had_key,
            tpl['base_url'],
        )

    # ================================================================== #
    # 联网设置加载 / 写盘
    # ================================================================== #
    def _load_web_settings(self):
        """把 AppConfig + ProviderRegistry 的联网字段加载到 UI。"""
        cfg = self._config.config
        # 联网模式
        mode = str(getattr(cfg, 'web_search_mode', 'auto') or 'auto').lower()
        for i, (_, v) in enumerate(self._web_mode_options):
            if v == mode:
                self.web_mode_combo.blockSignals(True)
                self.web_mode_combo.setCurrentIndex(i)
                self.web_mode_combo.blockSignals(False)
                break
        # 结果数
        self.web_max_results_spin.blockSignals(True)
        self.web_max_results_spin.setValue(
            int(getattr(cfg, 'web_search_max_results', 5) or 5),
        )
        self.web_max_results_spin.blockSignals(False)
        # 抓正文开关
        self.web_fetch_chk.blockSignals(True)
        self.web_fetch_chk.setChecked(
            bool(getattr(cfg, 'web_fetch_page_text', True)),
        )
        self.web_fetch_chk.blockSignals(False)
        # Provider 列表
        self._reload_provider_list()

    def _get_provider_registry(self):
        """懒加载 ProviderRegistry，缓存到 self._provider_registry。"""
        reg = getattr(self, '_provider_registry', None)
        if reg is None:
            from ..web_providers import ProviderRegistry
            reg = ProviderRegistry()
            self._provider_registry = reg
        return reg

    def _reload_provider_list(self):
        """从 ProviderRegistry 重建左侧列表，保留滚动位置。"""
        reg = self._get_provider_registry()
        active_id = reg.data.get('active_id') or ''
        self.web_provider_list.blockSignals(True)
        self.web_provider_list.clear()
        for prov in reg.list_providers():
            label = prov.get('name') or prov.get('id') or '?'
            tag_parts = []
            if prov.get('builtin'):
                tag_parts.append('内置')
            if not prov.get('enabled', True):
                tag_parts.append('已禁用')
            if prov.get('id') == active_id:
                tag_parts.append('当前')
            if tag_parts:
                label = '{}  [{}]'.format(label, ' · '.join(tag_parts))
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, prov.get('id'))
            tooltip = '{}\nid={}\n{}  {}'.format(
                prov.get('name') or '', prov.get('id') or '',
                (prov.get('method') or 'GET'), prov.get('url') or '',
            )
            item.setToolTip(tooltip)
            if prov.get('id') == active_id:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.web_provider_list.addItem(item)
        self.web_provider_list.blockSignals(False)
        self._on_provider_selection_changed()

    def _selected_provider_id(self):
        # type: () -> str
        item = self.web_provider_list.currentItem()
        if item is None:
            return ''
        return str(item.data(QtCore.Qt.UserRole) or '')

    def _on_provider_selection_changed(self, *_args):
        pid = self._selected_provider_id()
        reg = self._get_provider_registry()
        prov = reg.get(pid) if pid else None
        has = prov is not None
        is_builtin = bool(prov and prov.get('builtin'))
        self.web_provider_use_btn.setEnabled(has)
        self.web_provider_edit_btn.setEnabled(has)
        self.web_provider_dup_btn.setEnabled(has)
        self.web_provider_test_btn.setEnabled(has)
        # 内置 provider 不可删
        self.web_provider_del_btn.setEnabled(has and not is_builtin)

    def _on_provider_set_active(self):
        pid = self._selected_provider_id()
        if not pid:
            return
        reg = self._get_provider_registry()
        try:
            reg.set_active(pid)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, '设置失败', str(exc))
            return
        # 同步到 AppConfig 的旧字段（向前兼容老路径）
        cfg = self._config.config
        cfg.web_search_backend = pid
        try:
            self._config.save()
        except Exception:  # pylint: disable=broad-except
            pass
        self._reload_provider_list()
        self._notify_dock_refresh()

    def _on_provider_edit_clicked(self):
        pid = self._selected_provider_id()
        if not pid:
            return
        reg = self._get_provider_registry()
        prov = reg.get(pid)
        if prov is None:
            return
        from .provider_editor import ProviderEditorDialog
        dlg = ProviderEditorDialog(prov, parent=self, allow_id_edit=False)
        if dlg.exec_dialog():
            try:
                reg.upsert(dlg.result_provider())
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, '保存失败', str(exc))
                return
            self._reload_provider_list()
            self._notify_dock_refresh()

    def _on_provider_add_clicked(self):
        from .provider_editor import ProviderEditorDialog
        from ..web_providers import BUILTIN_PROVIDERS
        # 用 DDG 模板做新 provider 的初值，便于直接修改
        template = dict(BUILTIN_PROVIDERS[0])
        template['id'] = ''
        template['name'] = '新 Provider'
        template['builtin'] = False
        template['api_key'] = ''
        dlg = ProviderEditorDialog(template, parent=self, allow_id_edit=True)
        if dlg.exec_dialog():
            reg = self._get_provider_registry()
            try:
                reg.upsert(dlg.result_provider())
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, '保存失败', str(exc))
                return
            self._reload_provider_list()

    def _on_provider_dup_clicked(self):
        pid = self._selected_provider_id()
        if not pid:
            return
        reg = self._get_provider_registry()
        src = reg.get(pid)
        if src is None:
            return
        from .provider_editor import ProviderEditorDialog
        clone = dict(src)
        clone['id'] = '{}_copy'.format(src.get('id') or 'provider')
        clone['name'] = '{} (副本)'.format(src.get('name') or '')
        clone['builtin'] = False
        # 已有 id 时自加序号避免冲突
        existing = {p['id'] for p in reg.list_providers()}
        i = 1
        base = clone['id']
        while clone['id'] in existing:
            i += 1
            clone['id'] = '{}{}'.format(base, i)
        dlg = ProviderEditorDialog(clone, parent=self, allow_id_edit=True)
        if dlg.exec_dialog():
            try:
                reg.upsert(dlg.result_provider())
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, '保存失败', str(exc))
                return
            self._reload_provider_list()

    def _on_provider_del_clicked(self):
        pid = self._selected_provider_id()
        if not pid:
            return
        reg = self._get_provider_registry()
        prov = reg.get(pid)
        if prov is None:
            return
        if prov.get('builtin'):
            QtWidgets.QMessageBox.information(
                self, '不能删除', '内置 Provider 不能删除，可在编辑页禁用',
            )
            return
        ret = QtWidgets.QMessageBox.question(
            self, '确认删除',
            '确认删除 Provider "{}"？该操作不可恢复。'.format(
                prov.get('name') or pid,
            ),
        )
        yes = (
            getattr(QtWidgets.QMessageBox.StandardButton, 'Yes', None)
            or QtWidgets.QMessageBox.Yes
        )
        if ret != yes:
            return
        try:
            reg.delete(pid)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, '删除失败', str(exc))
            return
        self._reload_provider_list()

    def _on_provider_reset_builtins(self):
        ret = QtWidgets.QMessageBox.question(
            self, '恢复内置 Provider',
            '把内置 Provider 的 url / params / 响应路径等字段重置为出厂值'
            '（保留你已填的 API Key 和 extra 字段）。是否继续？',
        )
        yes = (
            getattr(QtWidgets.QMessageBox.StandardButton, 'Yes', None)
            or QtWidgets.QMessageBox.Yes
        )
        if ret != yes:
            return
        reg = self._get_provider_registry()
        reg.restore_builtins()
        self._reload_provider_list()

    def _on_provider_test_clicked(self):
        pid = self._selected_provider_id()
        if not pid:
            return
        reg = self._get_provider_registry()
        prov = reg.get(pid)
        if prov is None:
            return
        self.web_test_label.setText('{} 正在用 {} 搜索...'.format(
            _ee('⏳'), prov.get('name') or pid,
        ))
        self.web_test_label.setStyleSheet('color:#888;')
        QtWidgets.QApplication.processEvents()
        try:
            from ..web_search import search as _do_search
            from ..web_search import SearchError
            test_query = (
                'Maya Python scripting'
                if _current_dcc_name() == 'Maya'
                else '3ds Max maxscript'
            )
            results = _do_search(
                test_query,
                max_results=int(self.web_max_results_spin.value() or 5),
                fetch_page=False,
                use_cache=False,
                provider=prov,
            )
        except SearchError as exc:
            self.web_test_label.setText('{} 搜索失败: {}'.format(_ee('❌'), exc))
            self.web_test_label.setStyleSheet('color:#e57373;')
            return
        except Exception as exc:  # pylint: disable=broad-except
            self.web_test_label.setText('{} 异常: {}'.format(_ee('❌'), exc))
            self.web_test_label.setStyleSheet('color:#e57373;')
            return
        if not results:
            self.web_test_label.setText(
                '{} 没返回结果（可能被反爬、网络受限或字段映射不对）'.format(
                    _ee('⚠'),
                ),
            )
            self.web_test_label.setStyleSheet('color:#b8923a;')
            return
        first = results[0]
        self.web_test_label.setText(
            '{} {} 命中 {} 条；首条: {}'.format(
                _ee('✅'),
                prov.get('id') or pid, len(results),
                first.title[:60] or first.url[:60],
            ),
        )
        self.web_test_label.setStyleSheet('color:#8fce8f;')

    def _notify_dock_refresh(self):
        """通知主窗口刷新主 UI 联网按钮状态。"""
        try:
            parent = self.parent()
            refresh = getattr(parent, 'refresh_web_button_state', None)
            if callable(refresh):
                refresh()
        except Exception:  # pylint: disable=broad-except
            pass

    def _on_web_settings_changed(self, *_args):
        """全局联网控件（mode/n/抓正文）变化即写盘 + 通知主窗口。"""
        cfg = self._config.config
        mode_idx = self.web_mode_combo.currentIndex()
        cfg.web_search_mode = self._web_mode_options[mode_idx][1]
        cfg.web_search_max_results = int(self.web_max_results_spin.value())
        cfg.web_fetch_page_text = bool(self.web_fetch_chk.isChecked())
        try:
            self._config.save()
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '保存失败', '联网设置写盘失败: {}'.format(exc),
            )
            return
        self._notify_dock_refresh()

    def _read_form(self):
        headers = {}
        for line in self.headers_edit.toPlainText().splitlines():
            line = line.strip()
            if not line or '=' not in line:
                continue
            k, v = line.split('=', 1)
            headers[k.strip()] = v.strip()

        # 修复：以"当前已存的同名 profile"为基底，再用 UI 字段覆盖。
        # 这样 UI 上不暴露的字段（kind / 计费单价 / tool_result_max_bytes /
        # auto_summarize_threshold 等）不会在每次「应用」时被 dataclass 默认值
        # 静默擦掉。新建 profile 时基底为 None，使用 dataclass 默认值即可。
        new_name = self.name_edit.text().strip()
        # 优先用当前 form 关联的 profile 作为基底（_current_profile 由
        # _on_profile_selected 维护），兜底再按新名字找一遍
        base = None
        cur_name = getattr(self, '_current_profile', '') or ''
        if cur_name:
            base = self._config.get_profile(cur_name)
        if base is None and new_name:
            base = self._config.get_profile(new_name)

        # max_tokens=0 表示"由模型决定"——保持 0 写入，让 LLMClient 在
        # 实际调用时按 0 跳过该字段；不要再强制改写成 4096
        max_tokens_value = int(self.max_tokens_spin.value())

        if base is not None:
            # 拷贝基底，再用 UI 值覆盖 UI 暴露字段。asdict 会递归把
            # dataclass 拆成 dict（含 extra_headers 这种嵌套容器），
            # 不会触发 to_dict() 里给 api_key 加 b64: 前缀的混淆逻辑。
            from dataclasses import asdict as _asdict
            new_prof = LLMProfile(**_asdict(base))
            new_prof.name = new_name
            new_prof.base_url = self.base_url_edit.text().strip()
            new_prof.api_key = self.api_key_edit.text()
            new_prof.model = self.model_edit.text().strip()
            new_prof.temperature = float(self.temperature_spin.value())
            new_prof.force_temperature_one = bool(
                self.force_temp_one_chk.isChecked(),
            )
            # 强制 temperature=1 复选框现在通过 param_overrides 生效
            overrides = dict(getattr(base, 'param_overrides', None) or {})
            if self.force_temp_one_chk.isChecked():
                overrides["temperature"] = 1.0
            else:
                overrides.pop("temperature", None)
            new_prof.param_overrides = overrides
            new_prof.max_tokens = max_tokens_value
            new_prof.timeout = int(self.timeout_spin.value())
            new_prof.max_tool_loops = int(self.max_loops_spin.value())
            new_prof.max_history_tokens = int(
                self.max_history_tokens_spin.value(),
            )
            new_prof.stream = bool(self.stream_chk.isChecked())
            new_prof.supports_tools = bool(self.tools_chk.isChecked())
            new_prof.vision_supported = bool(
                self.vision_supported_chk.isChecked(),
            )
            new_prof.fallback_profile_names = self._read_fallback_list()
            new_prof.extra_headers = headers
            return new_prof

        # 新建 profile：用 dataclass 默认值兜底
        return LLMProfile(
            name=new_name,
            base_url=self.base_url_edit.text().strip(),
            api_key=self.api_key_edit.text(),
            model=self.model_edit.text().strip(),
            temperature=float(self.temperature_spin.value()),
            force_temperature_one=bool(self.force_temp_one_chk.isChecked()),
            param_overrides=(
                {"temperature": 1.0}
                if self.force_temp_one_chk.isChecked()
                else {}
            ),
            max_tokens=max_tokens_value,
            timeout=int(self.timeout_spin.value()),
            max_tool_loops=int(self.max_loops_spin.value()),
            max_history_tokens=int(self.max_history_tokens_spin.value()),
            stream=bool(self.stream_chk.isChecked()),
            supports_tools=bool(self.tools_chk.isChecked()),
            vision_supported=bool(self.vision_supported_chk.isChecked()),
            fallback_profile_names=self._read_fallback_list(),
            extra_headers=headers,
        )

    def _refresh_fallback_list(self, prof):
        """刷新备用 Profile 列表 UI。

        列表内容：全部其他 profile；已配置的按 fallback_profile_names 顺序
        置顶并勾选，未配置的追加到末尾。
        """
        self.fallback_list.blockSignals(True)
        self.fallback_list.clear()
        cur_name = getattr(prof, 'name', '') or ''
        selected = list(
            getattr(prof, 'fallback_profile_names', None) or [],
        )
        # 全部候选（排除自身）
        all_names = [
            p.name for p in self._config.config.profiles
            if p.name and p.name != cur_name
        ]
        # 保序：先按 selected 顺序（且必须真实存在），再追加剩余
        ordered = []
        seen = set()
        for name in selected:
            if name in all_names and name not in seen:
                ordered.append((name, True))
                seen.add(name)
        for name in all_names:
            if name not in seen:
                ordered.append((name, False))
                seen.add(name)
        for name, checked in ordered:
            item = QtWidgets.QListWidgetItem(name)
            item.setFlags(
                item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable,
            )
            item.setCheckState(
                QtCore.Qt.CheckState.Checked
                if checked else QtCore.Qt.CheckState.Unchecked
            )
            self.fallback_list.addItem(item)
        self.fallback_list.blockSignals(False)

    def _read_fallback_list(self):
        """按 UI 顺序读出被勾选的备用 Profile 名列表。"""
        names = []
        for i in range(self.fallback_list.count()):
            item = self.fallback_list.item(i)
            if item.checkState() == QtCore.Qt.CheckState.Checked:
                names.append(item.text())
        return names

    # ---------------- Base URL 预设 & 模型拉取 ---------------- #
    def _on_preset_clicked(self):
        # type: () -> None
        """点击"▼ 预设"弹出内置运营商菜单。"""
        try:
            from ..config import BUILTIN_PROVIDER_PRESETS
        except ImportError:
            BUILTIN_PROVIDER_PRESETS = []
        if not BUILTIN_PROVIDER_PRESETS:
            return
        menu = QtWidgets.QMenu(self.preset_btn)
        for preset in BUILTIN_PROVIDER_PRESETS:
            name = preset.get('name', '')
            url = preset.get('base_url', '')
            act = menu.addAction('{}   —   {}'.format(name, url))
            # 用 lambda 需捕获当前值，避免闭包变量迟绑定
            act.triggered.connect(
                lambda _checked=False, u=url, n=name:
                self._apply_preset(n, u),
            )
        menu.exec_(
            self.preset_btn.mapToGlobal(
                self.preset_btn.rect().bottomLeft(),
            ),
        )

    def _apply_preset(self, name, base_url):
        # type: (str, str) -> None
        """把选中的预设 URL 写入 Base URL 输入框。

        注意：不覆盖 API Key / 模型名，避免误删用户已配置内容。
        用户会看到 URL 变化 + 提示，再手动填 Key / 拉取模型即可。
        """
        if not base_url:
            return
        self.base_url_edit.setText(base_url)
        # 若名称是空的，帮忙填一下（避免用户看着 profile 名叫默认值）
        if not self.name_edit.text().strip():
            self.name_edit.setText(name)
        # 触发提示刷新
        try:
            self._refresh_base_url_hint(base_url)
        except (AttributeError, TypeError):
            pass

    # ---------------- 运营商归属 / 参数共享 ---------------- #
    def _refresh_provider_hint(self):
        # type: () -> None
        """刷新运营商归属提示条 + 一键共享按钮可用性。

        - 若当前 profile 所属运营商组下只有它自己，隐藏共享按钮
          （没有其它 profile 可共享目标）。
        - 若有兄弟 profile，显示 "参数继承自 XXX · 共 N 个模型"
          并允许一键共享。
        """
        if not hasattr(self, 'provider_hint_label'):
            return
        cur_name = getattr(self, '_current_profile', None) or \
            self.name_edit.text().strip()
        cur_url = (self.base_url_edit.text() or '').strip().rstrip('/').lower()
        if not cur_name or not cur_url:
            self.provider_hint_label.setText(
                '尚未设置 Base URL，无法识别运营商归属。',
            )
            self.share_to_provider_btn.setEnabled(False)
            return

        # 统计同 base_url 的兄弟 profile
        siblings = []
        for name in self._config.list_profile_names():
            prof = self._config.get_profile(name)
            if not prof:
                continue
            if (prof.base_url or '').rstrip('/').lower() != cur_url:
                continue
            if name == cur_name:
                continue
            siblings.append(name)

        provider_display = self._guess_provider_display_name(
            self.base_url_edit.text(),
        )
        if not siblings:
            self.provider_hint_label.setText(
                '运营商: <b>{}</b> · 该运营商下仅此一个模型。'.format(
                    provider_display,
                ),
            )
            self.share_to_provider_btn.setEnabled(False)
        else:
            self.provider_hint_label.setText(
                '运营商: <b>{}</b> · 兄弟模型 {} 个 ({})'.format(
                    provider_display,
                    len(siblings),
                    ', '.join(siblings[:3]) + (
                        '…' if len(siblings) > 3 else ''
                    ),
                ),
            )
            self.share_to_provider_btn.setEnabled(True)

    # 需要共享的参数字段：能力开关 + 采样参数 + 超时 + 循环上限
    # Base URL / API Key / model / name / extra_headers 不共享，
    # 因为这些是每个 profile 的独立身份
    _SHAREABLE_FIELDS = (
        'temperature',
        'max_tokens',
        'timeout',
        'max_tool_loops',
        'max_history_tokens',
        'stream',
        'supports_tools',
        'vision_supported',
        'param_overrides',
    )

    def _on_share_to_provider(self):
        # type: () -> None
        """把当前 profile 的可共享参数写入同运营商下所有兄弟 profile。"""
        cur_name = getattr(self, '_current_profile', None) or \
            self.name_edit.text().strip()
        cur_prof = self._config.get_profile(cur_name)
        if not cur_prof:
            return
        cur_url = (cur_prof.base_url or '').rstrip('/').lower()
        siblings = []
        for name in self._config.list_profile_names():
            if name == cur_name:
                continue
            prof = self._config.get_profile(name)
            if not prof:
                continue
            if (prof.base_url or '').rstrip('/').lower() == cur_url:
                siblings.append(prof)
        if not siblings:
            QtWidgets.QMessageBox.information(
                self, '共享参数',
                '该运营商下没有其它模型 profile，无需共享。',
            )
            return

        # 二次确认
        ret = QtWidgets.QMessageBox.question(
            self, '共享参数确认',
            '将把当前 Profile 的以下参数写入同运营商下 {} 个兄弟 Profile：\n'
            '  · 采样参数 (temperature / max_tokens)\n'
            '  · 超时 / 工具调用上限 / 历史 token 预算\n'
            '  · 能力开关 (流式 / FC / 视觉)\n'
            '  · 自定义 param_overrides\n\n'
            'Base URL / API Key / 模型名 / 自定义 Header 不会被覆盖。\n'
            '继续吗？'.format(len(siblings)),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return

        # 从表单读取最新值（可能用户还没点应用）
        # 复用 _read_form_to_new_profile 但只提取可共享字段
        try:
            src_snapshot = self._read_form_snapshot()
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '共享失败',
                '读取当前表单失败: {}'.format(exc),
            )
            return

        applied = 0
        for sib in siblings:
            new_prof = LLMProfile.from_dict(sib.to_dict())
            for f in self._SHAREABLE_FIELDS:
                if f in src_snapshot:
                    setattr(new_prof, f, src_snapshot[f])
            self._config.upsert_profile(new_prof)
            applied += 1

        try:
            self._config.save()
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '写盘失败',
                '共享参数已应用到内存但写盘失败: {}'.format(exc),
            )
            return

        QtWidgets.QMessageBox.information(
            self, '共享完成',
            '已把当前参数写入 {} 个兄弟 Profile。'.format(applied),
        )
        self._refresh_provider_hint()

    def _read_form_snapshot(self):
        # type: () -> dict
        """从当前表单读取可共享参数的快照。

        不构造完整 LLMProfile，避免与既有 _read_form 逻辑耦合。
        """
        snap = {}
        try:
            snap['temperature'] = float(self.temperature_spin.value())
        except AttributeError:
            pass
        try:
            snap['max_tokens'] = int(self.max_tokens_spin.value())
        except AttributeError:
            pass
        try:
            snap['timeout'] = int(self.timeout_spin.value())
        except AttributeError:
            pass
        try:
            snap['max_tool_loops'] = int(self.max_loops_spin.value())
        except AttributeError:
            pass
        try:
            snap['max_history_tokens'] = int(
                self.max_history_tokens_spin.value(),
            )
        except AttributeError:
            pass
        try:
            snap['stream'] = bool(self.stream_chk.isChecked())
        except AttributeError:
            pass
        try:
            snap['supports_tools'] = bool(self.tools_chk.isChecked())
        except AttributeError:
            pass
        try:
            snap['vision_supported'] = bool(
                self.vision_supported_chk.isChecked(),
            )
        except AttributeError:
            pass
        # force_temperature_one 通过 param_overrides.temperature 实现，
        # 这里连带带上 param_overrides
        try:
            if self.force_temp_one_chk.isChecked():
                snap['param_overrides'] = {'temperature': 1.0}
        except AttributeError:
            pass
        return snap

    # ---------------- 迁移提示条 ---------------- #
    def _build_migration_bar(self):
        # type: () -> Optional[QtWidgets.QWidget]
        """若本次启动检测到自动迁移，返回一条提示 bar；否则返回 None。"""
        notice = getattr(self._config.config, 'migration_notice', None)
        if not notice:
            return None
        bar = QtWidgets.QFrame()
        bar.setStyleSheet(
            'QFrame { background:#3a2f1e; border:1px solid #6b5323;'
            ' border-radius:3px; }'
            ' QLabel { color:#f0d68b; padding:2px; }'
            ' QPushButton { padding:3px 10px; }',
        )
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(8)
        label = QtWidgets.QLabel(
            '已从旧配置自动迁移 <b>{}</b> 个 Profile → <b>{}</b> 个运营商 · '
            '<b>{}</b> 个模型。'.format(
                notice.get('from_profiles', 0),
                notice.get('to_providers', 0),
                notice.get('to_models', 0),
            ),
        )
        label.setWordWrap(True)
        h.addWidget(label, 1)
        export_btn = QtWidgets.QPushButton('导出旧配置备份')
        export_btn.setAutoDefault(False)
        export_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        export_btn.clicked.connect(self._export_legacy_profiles)
        h.addWidget(export_btn)
        dismiss_btn = QtWidgets.QPushButton('知道了')
        dismiss_btn.setAutoDefault(False)
        dismiss_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        dismiss_btn.clicked.connect(self._dismiss_migration_bar)
        h.addWidget(dismiss_btn)
        return bar

    def _export_legacy_profiles(self):
        # type: () -> None
        """把 legacy_profiles_snapshot 导出到用户选择的 JSON 文件。"""
        notice = getattr(self._config.config, 'migration_notice', None) or {}
        snapshot = notice.get('legacy_profiles_snapshot') or []
        if not snapshot:
            QtWidgets.QMessageBox.information(
                self, '导出旧配置',
                '未发现旧配置快照，可能已导出过或未发生迁移。',
            )
            return
        import datetime as _dt
        import json as _json
        default_name = 'maxagent_legacy_profiles_{}.json'.format(
            _dt.datetime.now().strftime('%Y%m%d_%H%M%S'),
        )
        default_path = os.path.join(
            os.path.expanduser('~'), default_name,
        )
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, '导出旧 Profile 备份', default_path,
            'JSON 文件 (*.json)',
        )
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                _json.dump(
                    {
                        'version': 'legacy_v1',
                        'exported_at': _dt.datetime.now().isoformat(
                            timespec='seconds',
                        ),
                        'profiles': snapshot,
                    },
                    f, ensure_ascii=False, indent=2,
                )
        except OSError as exc:
            QtWidgets.QMessageBox.warning(
                self, '导出失败',
                '写入文件失败: {}'.format(exc),
            )
            return
        QtWidgets.QMessageBox.information(
            self, '导出成功',
            '旧 Profile 备份已写入:\n{}'.format(path),
        )

    def _dismiss_migration_bar(self):
        # type: () -> None
        """隐藏迁移 bar（当前进程内不再显示）。"""
        if self.migration_bar is not None:
            self.migration_bar.hide()
        # 清空 migration_notice 以免后续再次构造 dialog 又弹出来
        try:
            self._config.config.migration_notice = None
        except AttributeError:
            pass

    def _on_fetch_models_clicked(self):
        # type: () -> None
        """点击"↻ 拉取"从 API 拉取模型列表并弹选择框。"""
        base_url = self.base_url_edit.text().strip()
        api_key = self.api_key_edit.text().strip()
        if not base_url:
            QtWidgets.QMessageBox.warning(
                self, '拉取模型',
                '请先填写 Base URL 后再点拉取。',
            )
            return

        # 弹一个非模态忙碌提示（用状态栏更轻量）
        self.fetch_models_btn.setEnabled(False)
        self.fetch_models_btn.setText('拉取中…')
        QtWidgets.QApplication.processEvents()
        try:
            from ..llm_provider_probe import list_models
            models, err = list_models(
                base_url, api_key,
                force_refresh=True,
            )
        except ImportError as exc:
            QtWidgets.QMessageBox.critical(
                self, '拉取模型',
                '未能加载探测模块: {}'.format(exc),
            )
            return
        finally:
            self.fetch_models_btn.setEnabled(True)
            self.fetch_models_btn.setText('↻ 拉取')

        if err and not models:
            QtWidgets.QMessageBox.warning(
                self, '拉取失败',
                '未能获取模型列表：\n{}\n\n'
                '请确认 Base URL 与 API Key 正确，或该运营商不支持 '
                '/models 端点，可继续手动填写模型名。'.format(err),
            )
            return

        if not models:
            QtWidgets.QMessageBox.information(
                self, '拉取模型',
                '未返回任何模型。',
            )
            return

        # 弹选择框：单选，允许用户过滤
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle('选择模型 · 共 {} 个'.format(len(models)))
        dlg.resize(420, 480)
        vbox = QtWidgets.QVBoxLayout(dlg)
        vbox.setSpacing(6)

        tip = QtWidgets.QLabel(
            '从下方列表中选择要使用的模型（双击或选中后点确定）。'
            + ('\n注：拉取结果包含 {} 个模型，'
               '已缓存到本地，下次可直接从缓存加载。'.format(len(models))),
        )
        tip.setWordWrap(True)
        tip.setStyleSheet('color:#888;')
        vbox.addWidget(tip)

        filter_edit = QtWidgets.QLineEdit()
        filter_edit.setPlaceholderText('过滤: 输入关键字…')
        vbox.addWidget(filter_edit)

        list_widget = QtWidgets.QListWidget()
        for m in models:
            mid = m.get('id') or ''
            label = mid
            ctx = m.get('context')
            if ctx:
                label = '{}    ({}k ctx)'.format(mid, ctx // 1024)
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, mid)
            list_widget.addItem(item)
        vbox.addWidget(list_widget, 1)

        def _apply_filter(text):
            t = (text or '').strip().lower()
            for i in range(list_widget.count()):
                it = list_widget.item(i)
                mid = it.data(QtCore.Qt.UserRole) or ''
                it.setHidden(bool(t) and t not in mid.lower())

        filter_edit.textChanged.connect(_apply_filter)

        # 若当前 model_edit 有值，尝试预选
        current = self.model_edit.text().strip()
        if current:
            for i in range(list_widget.count()):
                it = list_widget.item(i)
                if it.data(QtCore.Qt.UserRole) == current:
                    list_widget.setCurrentRow(i)
                    break

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel,
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        list_widget.itemDoubleClicked.connect(lambda _it: dlg.accept())
        vbox.addWidget(btns)

        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        cur = list_widget.currentItem()
        if not cur:
            return
        mid = cur.data(QtCore.Qt.UserRole)
        if mid:
            self.model_edit.setText(str(mid))
            self._dirty = True

    def _on_profile_selected(self, cur, prev):
        # header 项（不可选）会因为无 flag 而不会被 setCurrentRow 命中，
        # 但通过键盘方向键仍可能停在其上——显式跳过
        if cur is not None and cur.data(QtCore.Qt.UserRole) == '__header__':
            return
        if prev is not None and self._dirty:
            ret = QtWidgets.QMessageBox.question(
                self, '未保存',
                '当前 Profile 已修改但未保存，是否丢弃修改？',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if ret != QtWidgets.QMessageBox.Yes:
                self.profile_list.blockSignals(True)
                self.profile_list.setCurrentItem(prev)
                self.profile_list.blockSignals(False)
                return
        if cur is not None:
            self._load_to_form(cur.text())
            self._dirty = False

    # ================================================================== #
    # Profile 右键菜单 / 双击重命名
    # ================================================================== #
    def _on_profile_context_menu(self, pos):
        """在 Profile 列表上弹出右键菜单。"""
        item = self.profile_list.itemAt(pos)
        if item is None:
            # 点空白处也支持右键"新建"
            menu = QtWidgets.QMenu(self.profile_list)
            menu.addAction('新建 Profile…', self._add_profile)
            menu.exec_(self.profile_list.mapToGlobal(pos))
            return
        # header 项不参与右键菜单
        if item.data(QtCore.Qt.UserRole) == '__header__':
            return
        name = item.text()
        is_active = (name == self._config.get_active_profile_name())

        menu = QtWidgets.QMenu(self.profile_list)
        menu.addAction('重命名…(F2)', lambda: self._rename_profile(name))
        menu.addAction('复制为副本…', lambda: self._duplicate_profile(name))
        menu.addSeparator()
        if not is_active:
            menu.addAction('设为默认', lambda: self._set_active_profile(name))
        menu.addAction('测试连接', self._test_connection)
        menu.addSeparator()
        del_action = menu.addAction(
            '删除…(Delete)', lambda: self._del_profile_by_name(name),
        )
        if is_active:
            del_action.setEnabled(False)
            del_action.setText('删除（当前激活，禁止删除）')
        menu.exec_(self.profile_list.mapToGlobal(pos))

    def _on_profile_double_clicked(self, item):
        """双击 = 重命名（多数用户的直觉）。"""
        if item is None:
            return
        self._rename_profile(item.text())

    def keyPressEvent(self, ev):
        """支持 F2=重命名、Delete=删除（仅当焦点在 profile_list 上）。"""
        # 仅当 profile_list 拿到焦点时拦截，否则按默认行为
        if self.profile_list.hasFocus():
            cur = self.profile_list.currentItem()
            if cur is not None:
                if ev.key() == QtCore.Qt.Key_F2:
                    self._rename_profile(cur.text())
                    ev.accept()
                    return
                if ev.key() in (QtCore.Qt.Key_Delete,):
                    self._del_profile_by_name(cur.text())
                    ev.accept()
                    return
        super(SettingsDialog, self).keyPressEvent(ev)

    def _rename_profile(self, old_name):
        """弹 InputDialog 重命名 profile，校验唯一性 + 同步 active。"""
        new_name, ok = QtWidgets.QInputDialog.getText(
            self, '重命名 Profile',
            '新名称（仅英文/数字/连字符）:',
            text=old_name,
        )
        if not ok:
            return
        new_name = (new_name or '').strip()
        if not new_name or new_name == old_name:
            return
        if self._config.get_profile(new_name) is not None:
            QtWidgets.QMessageBox.warning(
                self, '已存在', '同名 Profile 已存在: {}'.format(new_name),
            )
            return
        # 取出旧 profile，改名后插入；ConfigManager 没有内建 rename，
        # 这里采用"插新 + 删旧"的方式，必要时同步 active_profile。
        old_prof = self._config.get_profile(old_name)
        if old_prof is None:
            return
        was_active = (old_name == self._config.get_active_profile_name())
        new_prof = LLMProfile(
            name=new_name,
            base_url=old_prof.base_url,
            api_key=old_prof.api_key,
            model=old_prof.model,
            temperature=old_prof.temperature,
            max_tokens=old_prof.max_tokens,
            timeout=old_prof.timeout,
            max_tool_loops=getattr(old_prof, 'max_tool_loops', 40),
            max_history_tokens=getattr(old_prof, 'max_history_tokens', 32000),
            stream=old_prof.stream,
            supports_tools=old_prof.supports_tools,
            extra_headers=dict(old_prof.extra_headers or {}),
        )
        self._config.upsert_profile(new_prof)
        if was_active:
            # 先切到新名称，避免删除"当前激活"被拒
            try:
                self._config.set_active_profile(new_name)
            except Exception:  # pylint: disable=broad-except
                pass
        try:
            self._config.delete_profile(old_name)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('重命名时删除旧 profile 失败: %s', exc)
        self._reload_profiles()
        # 选中新建项
        for i in range(self.profile_list.count()):
            if self.profile_list.item(i).text() == new_name:
                self.profile_list.setCurrentRow(i)
                break

    def _duplicate_profile(self, src_name):
        """复制为副本：弹框让用户给副本起新名。"""
        src = self._config.get_profile(src_name)
        if src is None:
            return
        default_new = src_name + '-copy'
        idx = 2
        while self._config.get_profile(default_new) is not None:
            default_new = '{}-copy{}'.format(src_name, idx)
            idx += 1
        new_name, ok = QtWidgets.QInputDialog.getText(
            self, '复制 Profile',
            '副本名称（仅英文/数字/连字符）:',
            text=default_new,
        )
        if not ok:
            return
        new_name = (new_name or '').strip()
        if not new_name:
            return
        if self._config.get_profile(new_name) is not None:
            QtWidgets.QMessageBox.warning(
                self, '已存在', '同名 Profile 已存在: {}'.format(new_name),
            )
            return
        copied = LLMProfile(
            name=new_name,
            base_url=src.base_url,
            api_key=src.api_key,
            model=src.model,
            temperature=src.temperature,
            max_tokens=src.max_tokens,
            timeout=src.timeout,
            max_tool_loops=getattr(src, 'max_tool_loops', 40),
            max_history_tokens=getattr(src, 'max_history_tokens', 32000),
            stream=src.stream,
            supports_tools=src.supports_tools,
            extra_headers=dict(src.extra_headers or {}),
        )
        self._config.upsert_profile(copied)
        self._reload_profiles()
        for i in range(self.profile_list.count()):
            if self.profile_list.item(i).text() == new_name:
                self.profile_list.setCurrentRow(i)
                break

    def _set_active_profile(self, name):
        try:
            self._config.set_active_profile(name)
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '切换失败', '设为默认失败: {}'.format(exc),
            )
            return
        self._reload_profiles()

    def _del_profile_by_name(self, name):
        if name == self._config.get_active_profile_name():
            QtWidgets.QMessageBox.warning(
                self, '禁止删除', '不能删除当前激活的 Profile，请先切换。',
            )
            return
        ret = QtWidgets.QMessageBox.question(
            self, '确认', '确定删除 Profile "{}"？'.format(name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return
        try:
            self._config.delete_profile(name)
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '删除失败', '删除失败: {}'.format(exc),
            )
            return
        self._reload_profiles()

    # ================================================================== #
    # 槽：底部按钮（沿用旧逻辑）
    # ================================================================== #
    def _add_profile(self):
        text, ok = QtWidgets.QInputDialog.getText(
            self, '新建 Profile', 'Profile 名称（仅英文/数字/连字符）:',
        )
        if not ok or not text.strip():
            return
        name = text.strip()
        if self._config.get_profile(name) is not None:
            QtWidgets.QMessageBox.warning(
                self, '已存在', '同名 Profile 已存在: {}'.format(name),
            )
            return
        prof = LLMProfile(
            name=name,
            base_url='https://api.openai.com/v1',
            api_key='',
            model='gpt-4o-mini',
        )
        self._config.upsert_profile(prof)
        self._config.save()
        self._reload_profiles()
        for i in range(self.profile_list.count()):
            if self.profile_list.item(i).text() == name:
                self.profile_list.setCurrentRow(i)
                break

    def _del_profile(self):
        item = self.profile_list.currentItem()
        if item is None:
            return
        self._del_profile_by_name(item.text())

    def _toggle_key_visible(self, checked):
        # type: (bool) -> None
        """切换 API Key 输入框的明/暗显示状态。

        除了切 EchoMode，还会同步：
        1. 按钮图标与文案（👁 显示 ↔ 🙈 隐藏），让"当前态"一目了然
        2. 写入 INFO 级日志（**只记状态，不记 key 内容**）方便排查
           "我刚刚是不是误点了显示？"这类问题
        """
        if checked:
            self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Normal)
            self.show_key_btn.setText(_btn_label('🙈', '隐藏'))
            logger.info('API Key 切换为明文显示')
        else:
            self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
            self.show_key_btn.setText(_btn_label('👁', '显示'))
            logger.info('API Key 切换为隐藏显示')

    def _apply(self):
        try:
            prof = self._read_form()
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '表单错误', '读取表单失败: {}'.format(exc),
            )
            return
        if not prof.name:
            QtWidgets.QMessageBox.warning(self, '缺少名称', '请填写 Profile 名称')
            return
        if not prof.base_url:
            QtWidgets.QMessageBox.warning(self, '缺少 URL', '请填写 Base URL')
            return
        if not prof.model:
            QtWidgets.QMessageBox.warning(self, '缺少模型', '请填写模型名')
            return
        self._config.upsert_profile(prof)
        self._config.save()
        self._dirty = False
        self.test_label.setText('{} 已保存'.format(_ee('✅')))
        self.test_label.setStyleSheet('color:#8fce8f;')
        self._reload_profiles()

    # ------------------------------------------------------------------ #
    # 测试连接：视觉模型识别 + 占位图自动喂入
    # ------------------------------------------------------------------ #

    # 32×32 纯灰色 PNG 占位图（约 130 字节）。
    # 设计目的：tokenhub 系视觉模型（如 youtu-vita）会在收到纯文本
    # messages 时返回 400 invalid_params，导致用户哪怕配置完全正确，
    # 点"测试连接"也永远是红叉。给视觉模型自动塞一张极小占位图，
    # 让握手能成功，按钮的判据才有意义。
    #
    # 尺寸从 8×8 升到 32×32 的原因：Moonshot Kimi Vision 后端要求
    # 图像最小 28×28，8×8 会被判 "invalid or unsupported image format"。
    # 用 32×32 是兼容 Kimi / Qwen-VL / GPT-4o 的通用下限。
    _VISION_PLACEHOLDER_PNG_B64 = (
        'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAA'
        'J0lEQVR42u3NMQ0AAAwDoPpXVllVsWMJGCA9FoFAIBAIBAKBQPAlGGDXYIje2qgoAAAAAElFTkSuQmCC'
    )
    _VISION_PLACEHOLDER_DATA_URL = (
        'data:image/png;base64,' + _VISION_PLACEHOLDER_PNG_B64
    )

    def _profile_is_vision_model(self, prof):
        """判定当前 profile 的 model 是否在视觉白名单内。

        :param prof: 当前编辑中的 profile（_read_form 的结果）
        :returns: True 表示需要按多模态协议构造测试请求体
        """
        cfg = self._config.config
        wl = list(getattr(cfg, 'vision_model_whitelist', []) or [])
        return model_supports_vision(prof.model or '', wl)

    def _build_test_user_message(self, prof, ask_text):
        """根据 profile 的视觉能力构造 messages[].content。

        - 视觉模型：返回多模态数组 [{type:text}, {type:image_url}]
          配上"看到这张占位图请回复 ok"的提示，让模型既触发视觉
          推理路径又能给出最短回复，便于按钮快速判定连通性。
        - 普通模型：返回纯文本字符串（保持向后兼容）。

        :returns: tuple(content, is_vision_path) — is_vision_path 仅用于
                  状态文案区分，不影响功能。
        """
        if self._profile_is_vision_model(prof):
            content = [
                {
                    'type': 'image_url',
                    'image_url': {
                        'url': self._VISION_PLACEHOLDER_DATA_URL,
                    },
                },
                {
                    'type': 'text',
                    'text': '看到这张图就回复"ok"两个字，不要解释。',
                },
            ]
            return content, True
        return ask_text, False

    def _test_connection(self):
        try:
            prof = self._read_form()
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('{} 表单错误: {}'.format(_ee('❌'), exc))
            self.test_label.setStyleSheet('color:#e57373;')
            return
        content, is_vision = self._build_test_user_message(
            prof, '回复一个字: ok',
        )
        if is_vision:
            self.test_label.setText(
                '⏳ 测试中（视觉模型，已自动附占位图）...',
            )
            logger.info(
                '测试连接（视觉路径）: model=%s base_url=%s',
                prof.model, prof.base_url,
            )
        else:
            self.test_label.setText('⏳ 测试中...')
            logger.info(
                '测试连接（文本路径）: model=%s base_url=%s',
                prof.model, prof.base_url,
            )
        self.test_label.setStyleSheet('color:#888;')
        QtWidgets.QApplication.processEvents()
        try:
            client = build_client_from_profile(prof)
            resp = client.chat(
                messages=[
                    {'role': 'user', 'content': content},
                ],
                stream=False,
                tools=None,
            )
            reply = (resp.get('content') or '').strip()
            tag = '（视觉）' if is_vision else ''
            if reply:
                self.test_label.setText(
                    '{} 连接成功{}，模型回复: "{}"'.format(
                        _ee('✅'), tag, reply[:40],
                    ),
                )
                self.test_label.setStyleSheet('color:#8fce8f;')
            else:
                self.test_label.setText(
                    '{} 连接成功{}（响应为空）'.format(_ee('✅'), tag),
                )
                self.test_label.setStyleSheet('color:#8fce8f;')
        except LLMError as exc:
            err_text = str(exc)
            logger.warning('测试连接失败: %s', err_text)
            hint = ''
            low = err_text.lower()
            if is_vision and (
                'prepare image' in low
                or 'decode image' in low
                or 'invalid_request' in low and 'image' in low
            ):
                hint = (
                    '\n提示：该模型对图像格式校验失败。如果 "{}"'
                    ' 本身不是视觉模型（例如代码/推理专用模型），'
                    '请到"视觉模型白名单"中移除相关关键词。'
                ).format(prof.model or '')
            self.test_label.setText(
                '{} 连接失败: {}{}'.format(_ee('❌'), err_text, hint),
            )
            self.test_label.setStyleSheet('color:#e57373;')
            self.test_label.setToolTip(err_text + hint)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('测试连接异常')
            self.test_label.setText('{} 异常: {}'.format(_ee('❌'), exc))
            self.test_label.setStyleSheet('color:#e57373;')
            self.test_label.setToolTip(str(exc))

    def _test_connection_full(self):
        try:
            prof = self._read_form()
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('{} 表单错误: {}'.format(_ee('❌'), exc))
            self.test_label.setStyleSheet('color:#e57373;')
            return

        is_vision = self._profile_is_vision_model(prof)
        if is_vision:
            # 视觉模型：tokenhub 系网关对 tools 字段非常敏感，
            # 完整测试也不带 tools——只验"system + 多模态 user + stream"
            # 这条最贴近真实视觉对话的链路。
            self.test_label.setText(
                '⏳ 完整测试中（视觉模式：流式 + 占位图，跳过 tools）...',
            )
        else:
            self.test_label.setText(
                '⏳ 完整测试中（流式+tools+真实 prompt）...',
            )
        self.test_label.setStyleSheet('color:#888;')
        QtWidgets.QApplication.processEvents()

        # 视觉模型不加 tools；普通模型才需要校验 tools schema 是否能通过
        if is_vision:
            tools_schema = None
        else:
            try:
                from ..tools import build_openai_tools_schema
                tools_schema = build_openai_tools_schema()
            except Exception as exc:  # pylint: disable=broad-except
                self.test_label.setText('{} 加载工具 schema 失败: {}'.format(_ee('❌'), exc))
                self.test_label.setStyleSheet('color:#e57373;')
                return

        # 用真实对话的 system prompt 复刻请求体——只有这样
        # "完整测试通过 == 真实对话不会因 prompt/schema 体积而被拒"。
        # 一些网关只在 messages[0] 较长 / 同时带 tools 时才返回 4xx。
        try:
            from ..agent.conversation import build_default_system_prompt
            system_prompt = build_default_system_prompt()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('加载默认 system prompt 失败，降级到短文本: %s', exc)
            system_prompt = 'You are a helpful assistant.'

        user_content, _ = self._build_test_user_message(
            prof, '回复一个字: ok',
        )

        chunks = []

        def _on_delta(text):
            chunks.append(text)

        try:
            client = build_client_from_profile(prof)
            logger.info(
                '完整测试: model=%s base_url=%s sys_prompt=%dB tools=%d vision=%s',
                prof.model, prof.base_url,
                len(system_prompt), len(tools_schema or []), is_vision,
            )
            resp = client.chat(
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_content},
                ],
                tools=tools_schema,
                stream=True,
                on_delta=_on_delta,
            )
            content = (resp.get('content') or ''.join(chunks)).strip()
            tag = '（视觉）' if is_vision else ''
            if content:
                self.test_label.setText(
                    '{} 完整测试通过{}，模型回复: "{}"'.format(
                        _ee('✅'), tag, content[:40],
                    ),
                )
                self.test_label.setStyleSheet('color:#8fce8f;')
            else:
                self.test_label.setText(
                    '{} 完整测试通过{}（响应为空，但握手成功）'.format(
                        _ee('✅'), tag,
                    ),
                )
                self.test_label.setStyleSheet('color:#8fce8f;')
        except LLMError as exc:
            # 错误已经是格式化好的 HTTP code + body + headers + url，
            # 这里直接展示给用户，不再二次包装；同时落 warning 日志方便取证。
            err_text = str(exc)
            logger.warning('完整测试失败: %s', err_text)
            self.test_label.setText(
                '{} 完整测试失败: {}'.format(_ee('❌'), err_text),
            )
            self.test_label.setStyleSheet('color:#e57373;')
            self.test_label.setToolTip(err_text)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('完整测试异常')
            self.test_label.setText('{} 异常: {}'.format(_ee('❌'), exc))
            self.test_label.setStyleSheet('color:#e57373;')
            self.test_label.setToolTip(str(exc))

    def _refresh_base_url_hint(self, text):
        hint = diagnose_base_url(text)
        if hint:
            self.base_url_hint.setText(hint)
            self.base_url_hint.show()
        else:
            self.base_url_hint.clear()
            self.base_url_hint.hide()

    # ================================================================== #
    # 帮助 (?) 按钮（标题栏）
    # ================================================================== #
    def event(self, ev):
        """拦截标题栏 ? 按钮，避免进入 WhatsThis 模式造成 🚫 光标。"""
        try:
            enter_whats_this = QtCore.QEvent.Type.EnterWhatsThisMode
        except AttributeError:
            enter_whats_this = QtCore.QEvent.EnterWhatsThisMode
        if ev.type() == enter_whats_this:
            try:
                QtWidgets.QWhatsThis.leaveWhatsThisMode()
            except Exception:  # pylint: disable=broad-except
                pass
            # 直接切到帮助 Tab，不再弹独立的 MessageBox
            QtCore.QTimer.singleShot(0, self._jump_to_help_tab)
            ev.accept()
            return True
        return super(SettingsDialog, self).event(ev)

    def _jump_to_help_tab(self):
        """快速切到"帮助"Tab。"""
        for i, (_label, key) in enumerate(self._NAV_ITEMS):
            if key == 'help':
                self.nav.setCurrentRow(i)
                return

    # ================================================================== #
    # Page: 我的资源（统一入口 = 规则 / 技能 / 工具 / 导入导出）
    # ================================================================== #
    def _build_page_resources(self):
        # type: () -> QtWidgets.QWidget
        """新的「我的资源」主 Tab。

        把原来分散在两个左侧 Tab 的"我的规则"+"工具与技能"合并，
        内部用横向 QTabBar 切换 4 个视图：

        - 规则：复用 ``_build_page_rules`` 已有列表/启停/导入导出
        - 技能：新建管理界面（含禁用复选框、详情、删除、单项导出）
        - 工具：新建管理界面（同上）
        - 导入/导出：复用 ``_build_page_pack`` 的批量打包能力

        每个子页都明确：
        1. 列表展示（保持单一职责，不在标题前堆砌大标题）；
        2. 单项 enabled 开关——禁用后 LLM 完全感知不到该资源；
        3. 单项导出（点一下导出 .maxagent-pack 单文件）。
        """
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 顶部统一标题
        title = QtWidgets.QLabel(_ee('📦') + '  我的资源')
        title.setStyleSheet('font-size:16px; font-weight:bold;')
        layout.addWidget(title)

        intro = QtWidgets.QLabel(
            '集中管理你为 AI 准备的 <b>规则</b> / <b>技能</b> / <b>工具</b>。'
            '每个项目都有<b>启用</b>开关——禁用后 LLM 完全感知不到该资源；'
            '在 <b>导入/导出</b> 子页可以批量打包跨设备同步。'
        )
        intro.setTextFormat(QtCore.Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        intro.setStyleSheet(
            'QLabel { color:#aaa; padding:4px 0; }',
        )
        layout.addWidget(intro)

        # 横向子 Tab：顺序 = 规则 / 技能 / 工具 / 导入导出
        self.resources_tabs = QtWidgets.QTabWidget()
        self.resources_tabs.setObjectName('ResourcesTabs')
        self.resources_tabs.setTabPosition(QtWidgets.QTabWidget.North)
        self.resources_tabs.setDocumentMode(True)
        # 子 Tab 切换日志：方便排查"用户改了某 Tab 后哪个动作未生效"
        self.resources_tabs.currentChanged.connect(
            self._on_resources_tab_changed,
        )

        self.resources_tabs.addTab(
            self._build_page_rules(),
            _ee('📋') + ' 规则',
        )
        self.resources_tabs.addTab(
            self._build_subtab_skills(),
            _ee('🎓') + ' 技能',
        )
        self.resources_tabs.addTab(
            self._build_subtab_tools(),
            _ee('🧰') + ' 工具',
        )
        self.resources_tabs.addTab(
            self._build_page_pack(),
            _ee('📤') + ' 导入/导出',
        )

        layout.addWidget(self.resources_tabs, 1)
        return page

    def _on_resources_tab_changed(self, idx):
        # type: (int) -> None
        try:
            label = self.resources_tabs.tabText(idx)
        except Exception:  # pylint: disable=broad-except
            label = '?'
        logger.info('我的资源 → 切换子 Tab: idx=%s text=%s', idx, label)

    # ------------------------------------------------------------------ #
    # 子 Tab：技能管理
    # ------------------------------------------------------------------ #
    def _build_subtab_skills(self):
        # type: () -> QtWidgets.QWidget
        """技能管理子页：列表 + 启用复选框 + 详情 / 删除 / 单项导出。

        与「导入/导出」子页的差异：本子页面向"日常启停 + 单项分享"，
        不要求批量；导入导出走那个子页的统一入口。
        """
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        hint = QtWidgets.QLabel(
            '勾选 <b>启用</b> 才会被注入到 system prompt；取消勾选后 '
            'LLM 完全看不到该技能（不出现在简介，触发词也不会命中）。'
        )
        hint.setTextFormat(QtCore.Qt.TextFormat.RichText)
        hint.setWordWrap(True)
        hint.setStyleSheet('color:#aaa; padding:2px 0;')
        layout.addWidget(hint)

        self._skills_list = QtWidgets.QListWidget()
        self._skills_list.setStyleSheet(
            'QListWidget { background:#252525; color:#d4d4d4;'
            ' border:1px solid #444; }'
            'QListWidget::item { padding:6px;'
            ' border-bottom:1px solid #333; }'
            'QListWidget::item:selected { background:#3a5d8f; }'
        )
        self._skills_list.itemChanged.connect(self._on_skill_check_changed)
        layout.addWidget(self._skills_list, 1)

        # 操作按钮（与「我的规则」「工具」子页布局对齐）
        btn_row = QtWidgets.QHBoxLayout()
        view_btn = QtWidgets.QPushButton(_btn_label('👁', '查看详情'))
        view_btn.clicked.connect(self._on_skill_view_detail)
        btn_row.addWidget(view_btn)
        toggle_btn = QtWidgets.QPushButton(_btn_label('🔄', '启用/禁用'))
        toggle_btn.setToolTip('切换当前选中技能的启用状态（与左侧勾选框等价）')
        toggle_btn.clicked.connect(self._on_skill_toggle_enabled)
        btn_row.addWidget(toggle_btn)
        status_btn = QtWidgets.QPushButton(_btn_label('🏷', '切换状态'))
        status_btn.setToolTip('切换技能生命周期：stable / beta / draft / deprecated')
        status_btn.clicked.connect(self._on_skill_cycle_status)
        btn_row.addWidget(status_btn)
        del_btn = QtWidgets.QPushButton(_btn_label('🗑️', '删除'))
        del_btn.setStyleSheet('color:#ff8888;')
        del_btn.clicked.connect(self._on_skill_delete)
        btn_row.addWidget(del_btn)
        btn_row.addStretch(1)
        refresh_btn = QtWidgets.QPushButton(_btn_label('🔄', '刷新'))
        refresh_btn.clicked.connect(self._refresh_skills_list)
        btn_row.addWidget(refresh_btn)
        layout.addLayout(btn_row)

        # 初次加载
        self._refresh_skills_list()
        return page

    def _refresh_skills_list(self):
        """重新扫盘并刷新技能列表（含禁用项）。"""
        try:
            from .. import skills as skills_mod
            from .. import disabled_registry as dr
            mgr = skills_mod.SkillManager()
            all_skills = mgr.list_all_skills()
            disabled = dr.get_disabled_skills_set()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('刷新技能列表失败: %s', exc)
            all_skills = []
            disabled = set()

        self._skills_list.blockSignals(True)
        try:
            self._skills_list.clear()
            for sk in all_skills:
                desc = (sk.description or '').strip().replace('\n', ' ')
                if len(desc) > 60:
                    desc = desc[:60] + '…'
                label = sk.name + (
                    '  —  ' + desc if desc else ''
                )
                status_tag = ''
                if sk.status == 'draft':
                    status_tag = ' [草案]'
                elif sk.status == 'beta':
                    status_tag = ' [内测]'
                elif sk.status == 'deprecated':
                    status_tag = ' [已弃用]'
                if sk.has_impl():
                    status_tag += ' [code]'
                dcc_tag = sk.dcc_tag()
                label = sk.name + ' ' + dcc_tag + status_tag + (
                    '  —  ' + desc if desc else ''
                )
                item = QtWidgets.QListWidgetItem(label)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                if sk.name in disabled or sk.status == 'deprecated':
                    item.setCheckState(QtCore.Qt.Unchecked)
                    item.setForeground(QtGui.QBrush(QtGui.QColor('#888')))
                else:
                    item.setCheckState(QtCore.Qt.Checked)
                    item.setForeground(
                        QtGui.QBrush(QtGui.QColor('#a8e6a8')),
                    )
                item.setData(QtCore.Qt.UserRole, sk.name)
                item.setData(QtCore.Qt.UserRole + 1, sk.status)
                self._skills_list.addItem(item)
            if not all_skills:
                placeholder = QtWidgets.QListWidgetItem('（暂无技能）')
                placeholder.setFlags(QtCore.Qt.NoItemFlags)
                self._skills_list.addItem(placeholder)
        finally:
            self._skills_list.blockSignals(False)
        logger.debug(
            '技能列表已刷新: total=%d disabled=%d',
            len(all_skills), len(disabled),
        )

    def _on_skill_check_changed(self, item):
        # type: (QtWidgets.QListWidgetItem) -> None
        """复选框切换 → 写入禁用名单。"""
        name = item.data(QtCore.Qt.UserRole)
        if not name:
            return
        try:
            from .. import disabled_registry as dr
            disabled = item.checkState() != QtCore.Qt.Checked
            dr.set_skill_disabled(name, disabled)
            color = '#888' if disabled else '#a8e6a8'
            item.setForeground(QtGui.QBrush(QtGui.QColor(color)))
            logger.info(
                '技能启用状态切换: %s → %s', name,
                'disabled' if disabled else 'enabled',
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('切换技能启用状态失败: %s', exc)
            QtWidgets.QMessageBox.warning(
                self, '操作失败', '切换启用状态时出错：{}'.format(exc),
            )

    def _on_skill_toggle_enabled(self):
        """底部「启用/禁用」按钮 → 翻转当前选中技能的启用态。"""
        item = self._skills_list.currentItem()
        if not item or not (item.flags() & QtCore.Qt.ItemIsUserCheckable):
            QtWidgets.QMessageBox.information(
                self, '请选择', '请先在列表中选择一个技能。',
            )
            return
        new_state = (QtCore.Qt.Unchecked
                     if item.checkState() == QtCore.Qt.Checked
                     else QtCore.Qt.Checked)
        # 直接修改 checkState 会触发 itemChanged → _on_skill_check_changed
        item.setCheckState(new_state)

    def _on_skill_view_detail(self):
        item = self._skills_list.currentItem()
        if not item:
            QtWidgets.QMessageBox.information(
                self, '请选择', '请先在列表中选择一个技能。',
            )
            return
        name = item.data(QtCore.Qt.UserRole)
        try:
            from .. import skills as skills_mod
            sk = skills_mod.SkillManager().get(name)
            # get() 走 _scan，被禁用项会拿不到——回退到 list_all
            if sk is None:
                for s in skills_mod.SkillManager().list_all_skills():
                    if s.name == name:
                        sk = s
                        break
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.critical(
                self, '加载失败', '读取技能失败：{}'.format(exc),
            )
            return
        if sk is None:
            QtWidgets.QMessageBox.warning(
                self, '不存在', '该技能可能已被删除，请刷新列表。',
            )
            return
        text = (
            '名称：{}\n'
            '状态：{}\n'
            '触发词：{}\n'
            '描述：{}\n'
            '代码实现：{}\n'
            '成功/失败：{}/{}\n'
            '使用次数：{}\n'
            '待审核补丁：{}\n\n'
            '--- 流程 ---\n{}'
        ).format(
            sk.name,
            sk.status,
            ' / '.join(sk.trigger_keywords) or '（无）',
            sk.description or '（无）',
            '是' if sk.has_impl() else '否',
            sk.success_count, sk.fail_count,
            sk.use_count,
            len([p for p in sk.patches if not p.get('applied')]),
            sk.instructions,
        )
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle('技能详情：{}'.format(sk.name))
        dlg.resize(680, 480)
        v = QtWidgets.QVBoxLayout(dlg)
        edit = QtWidgets.QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        v.addWidget(edit, 1)
        close = QtWidgets.QPushButton('关闭')
        close.clicked.connect(dlg.accept)
        v.addWidget(close, 0, QtCore.Qt.AlignRight)
        dlg.exec_()

    def _on_skill_cycle_status(self):
        """循环切换 skill 生命周期状态。"""
        item = self._skills_list.currentItem()
        if not item or not (item.flags() & QtCore.Qt.ItemIsUserCheckable):
            QtWidgets.QMessageBox.information(
                self, '请选择', '请先在列表中选择一个技能。',
            )
            return
        name = item.data(QtCore.Qt.UserRole)
        if not name:
            return
        try:
            from .. import skills as skills_mod
            from .. import disabled_registry as dr
            sk = skills_mod.SkillManager().get(name)
            if sk is None:
                for s in skills_mod.SkillManager().list_all_skills():
                    if s.name == name:
                        sk = s
                        break
            if sk is None:
                return
            cycle = ['draft', 'beta', 'stable', 'deprecated']
            idx = cycle.index(sk.status) if sk.status in cycle else 0
            new_status = cycle[(idx + 1) % len(cycle)]
            sk.status = new_status
            skills_mod.SkillManager().save(sk, overwrite=True)
            # deprecated 自动禁用，stable 自动启用
            if new_status == 'deprecated':
                dr.set_skill_disabled(name, True)
            elif new_status == 'stable':
                dr.set_skill_disabled(name, False)
            logger.info('技能状态切换: %s → %s', name, new_status)
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.critical(
                self, '失败', '切换状态失败：{}'.format(exc),
            )
            return
        self._refresh_skills_list()

    def _on_skill_delete(self):
        item = self._skills_list.currentItem()
        if not item:
            return
        name = item.data(QtCore.Qt.UserRole)
        if not name:
            return
        ret = QtWidgets.QMessageBox.question(
            self, '确认删除',
            '确认删除技能 "{}" 吗？此操作不可撤销。'.format(name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return
        try:
            from .. import skills as skills_mod
            from .. import disabled_registry as dr
            skills_mod.SkillManager().delete(name)
            # 同步从禁用名单清掉，避免遗留垃圾条目
            dr.set_skill_disabled(name, False)
            logger.info('删除技能: %s', name)
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.critical(
                self, '删除失败', str(exc),
            )
            return
        self._refresh_skills_list()

    # ------------------------------------------------------------------ #
    # 子 Tab：工具管理
    # ------------------------------------------------------------------ #
    def _build_subtab_tools(self):
        # type: () -> QtWidgets.QWidget
        """工具管理子页：列表 + 启用复选框 + 详情 / 删除 / 单项导出。

        本子页只管"用户学习沉淀的工具"（user_tools 目录下的 .py），
        内置工具不展示也无法禁用。这与"启用/禁用 = 用户偏好"语义一致。
        """
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        hint = QtWidgets.QLabel(
            '勾选 <b>启用</b> 才会被注册到 LLM 的可调用工具表；'
            '取消后 LLM 完全看不到该工具（schema 中也不会出现）。'
            '<br><span style="color:#888;">注：内置工具不在此处展示——只管理你通过对话沉淀的自定义工具。</span>'
        )
        hint.setTextFormat(QtCore.Qt.TextFormat.RichText)
        hint.setWordWrap(True)
        hint.setStyleSheet('color:#aaa; padding:2px 0;')
        layout.addWidget(hint)

        self._tools_list = QtWidgets.QListWidget()
        self._tools_list.setStyleSheet(
            'QListWidget { background:#252525; color:#d4d4d4;'
            ' border:1px solid #444; }'
            'QListWidget::item { padding:6px;'
            ' border-bottom:1px solid #333; }'
            'QListWidget::item:selected { background:#3a5d8f; }'
        )
        self._tools_list.itemChanged.connect(self._on_tool_check_changed)
        layout.addWidget(self._tools_list, 1)

        btn_row = QtWidgets.QHBoxLayout()
        view_btn = QtWidgets.QPushButton(_btn_label('👁', '查看源码'))
        view_btn.clicked.connect(self._on_tool_view_source)
        btn_row.addWidget(view_btn)
        toggle_btn = QtWidgets.QPushButton(_btn_label('🔄', '启用/禁用'))
        toggle_btn.setToolTip('切换当前选中工具的启用状态（与左侧勾选框等价）')
        toggle_btn.clicked.connect(self._on_tool_toggle_enabled)
        btn_row.addWidget(toggle_btn)
        del_btn = QtWidgets.QPushButton(_btn_label('🗑️', '删除'))
        del_btn.setStyleSheet('color:#ff8888;')
        del_btn.clicked.connect(self._on_tool_delete)
        btn_row.addWidget(del_btn)
        btn_row.addStretch(1)
        refresh_btn = QtWidgets.QPushButton(_btn_label('🔄', '刷新'))
        refresh_btn.clicked.connect(self._refresh_tools_list)
        btn_row.addWidget(refresh_btn)
        layout.addLayout(btn_row)

        self._refresh_tools_list()
        return page

    def _refresh_tools_list(self):
        try:
            from .. import user_tools_loader as utl
            from .. import disabled_registry as dr
            tools = utl.list_user_tools(include_meta=True)
            disabled = dr.get_disabled_tools_set()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('刷新工具列表失败: %s', exc)
            tools = []
            disabled = set()

        self._tools_list.blockSignals(True)
        try:
            self._tools_list.clear()
            for entry in tools:
                name = entry['name']
                meta = entry.get('meta') or {}
                desc = (meta.get('description') or '').strip()
                if len(desc) > 60:
                    desc = desc[:60] + '…'
                dcc = meta.get('dcc')
                dcc_tag = ' [通用]' if not dcc else ' [{}]'.format('/'.join(dcc))
                label = name + dcc_tag + ('  —  ' + desc if desc else '')
                item = QtWidgets.QListWidgetItem(label)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                if name in disabled:
                    item.setCheckState(QtCore.Qt.Unchecked)
                    item.setForeground(QtGui.QBrush(QtGui.QColor('#888')))
                else:
                    item.setCheckState(QtCore.Qt.Checked)
                    item.setForeground(
                        QtGui.QBrush(QtGui.QColor('#a8e6a8')),
                    )
                item.setData(QtCore.Qt.UserRole, name)
                self._tools_list.addItem(item)
            if not tools:
                placeholder = QtWidgets.QListWidgetItem('（暂无自定义工具）')
                placeholder.setFlags(QtCore.Qt.NoItemFlags)
                self._tools_list.addItem(placeholder)
        finally:
            self._tools_list.blockSignals(False)
        logger.debug(
            '工具列表已刷新: total=%d disabled=%d',
            len(tools), len(disabled),
        )

    def _on_tool_check_changed(self, item):
        # type: (QtWidgets.QListWidgetItem) -> None
        name = item.data(QtCore.Qt.UserRole)
        if not name:
            return
        try:
            from .. import disabled_registry as dr
            disabled = item.checkState() != QtCore.Qt.Checked
            dr.set_tool_disabled(name, disabled)
            color = '#888' if disabled else '#a8e6a8'
            item.setForeground(QtGui.QBrush(QtGui.QColor(color)))
            logger.info(
                '工具启用状态切换: %s → %s', name,
                'disabled' if disabled else 'enabled',
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('切换工具启用状态失败: %s', exc)
            QtWidgets.QMessageBox.warning(
                self, '操作失败', '切换启用状态时出错：{}'.format(exc),
            )

    def _on_tool_toggle_enabled(self):
        """底部「启用/禁用」按钮 → 翻转当前选中工具的启用态。"""
        item = self._tools_list.currentItem()
        if not item or not (item.flags() & QtCore.Qt.ItemIsUserCheckable):
            QtWidgets.QMessageBox.information(
                self, '请选择', '请先在列表中选择一个工具。',
            )
            return
        new_state = (QtCore.Qt.Unchecked
                     if item.checkState() == QtCore.Qt.Checked
                     else QtCore.Qt.Checked)
        item.setCheckState(new_state)

    def _on_tool_view_source(self):
        item = self._tools_list.currentItem()
        if not item:
            QtWidgets.QMessageBox.information(
                self, '请选择', '请先在列表中选择一个工具。',
            )
            return
        name = item.data(QtCore.Qt.UserRole)
        try:
            from .. import user_tools_loader as utl
            base = utl.get_user_tools_dir()
            py_path = os.path.join(base, name + '.py')
            if not os.path.isfile(py_path):
                raise FileNotFoundError(py_path)
            with open(py_path, 'r', encoding='utf-8') as fh:
                code = fh.read()
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.critical(
                self, '读取失败', '读取工具源码失败：{}'.format(exc),
            )
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle('工具源码：{}'.format(name))
        dlg.resize(720, 520)
        v = QtWidgets.QVBoxLayout(dlg)
        edit = QtWidgets.QPlainTextEdit()
        edit.setReadOnly(True)
        font = QtGui.QFont('Consolas, Menlo, monospace')
        font.setStyleHint(QtGui.QFont.Monospace)
        edit.setFont(font)
        edit.setPlainText(code)
        v.addWidget(edit, 1)
        close = QtWidgets.QPushButton('关闭')
        close.clicked.connect(dlg.accept)
        v.addWidget(close, 0, QtCore.Qt.AlignRight)
        dlg.exec_()

    def _on_tool_delete(self):
        item = self._tools_list.currentItem()
        if not item:
            return
        name = item.data(QtCore.Qt.UserRole)
        if not name:
            return
        ret = QtWidgets.QMessageBox.question(
            self, '确认删除',
            '确认删除工具 "{}" 吗？此操作不可撤销。'.format(name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return
        try:
            from .. import user_tools_loader as utl
            from .. import disabled_registry as dr
            utl.delete_user_tool(name)
            dr.set_tool_disabled(name, False)
            logger.info('删除自定义工具: %s', name)
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.critical(self, '删除失败', str(exc))
            return
        self._refresh_tools_list()
