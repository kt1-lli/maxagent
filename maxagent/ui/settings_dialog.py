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

import time
from typing import Any
from typing import Optional

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


class SettingsDialog(QtWidgets.QDialog):
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
    _NAV_ITEMS = [
        (_ee('🤖') + '  模型', 'model'),
        (_ee('🌐') + '  联网', 'network'),
        (_ee('🎨') + '  应用', 'app'),
        (_ee('👤') + '  助手形象', 'employee'),
        (_ee('📋') + '  我的规则', 'rules'),
        (_ee('📜') + '  日志', 'log'),
        (_ee('🔌') + '  IDE 接口', 'bridge'),
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
        self.stack.addWidget(self._build_page_rules())
        self.stack.addWidget(self._build_page_log())
        self.stack.addWidget(self._build_page_bridge())
        self.stack.addWidget(self._build_page_help())
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
        page = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        # ----- 左：Profile 列表 ----- #
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(QtWidgets.QLabel('Profile 列表'))
        self.profile_list = QtWidgets.QListWidget()
        self.profile_list.setMinimumWidth(180)
        # 启用右键菜单与双击重命名
        self.profile_list.setContextMenuPolicy(
            QtCore.Qt.CustomContextMenu,
        )
        self.profile_list.customContextMenuRequested.connect(
            self._on_profile_context_menu,
        )
        self.profile_list.itemDoubleClicked.connect(
            self._on_profile_double_clicked,
        )
        self.profile_list.currentItemChanged.connect(
            self._on_profile_selected,
        )
        left.addWidget(self.profile_list, 1)

        btns = QtWidgets.QHBoxLayout()
        # 全角 ＋ ／ ✕ 在 PySide2/6 都能直接渲染，无需走 emoji_compat
        self.add_btn = QtWidgets.QPushButton('＋ 新建')
        self.add_btn.clicked.connect(self._add_profile)
        btns.addWidget(self.add_btn)
        self.del_btn = QtWidgets.QPushButton('✕ 删除')
        self.del_btn.clicked.connect(self._del_profile)
        btns.addWidget(self.del_btn)
        left.addLayout(btns)

        # 提示：右键菜单功能入口
        hint = QtWidgets.QLabel(
            '提示：右键 Profile 可重命名 / 复制 / 设为默认',
        )
        hint.setStyleSheet('color:#888; font-size:11px;')
        hint.setWordWrap(True)
        left.addWidget(hint)

        outer.addLayout(left, 1)

        # ----- 右：编辑表单 ----- #
        right = QtWidgets.QFormLayout()
        right.setSpacing(8)
        right.setLabelAlignment(QtCore.Qt.AlignRight)
        # 修复：默认 FormAlignment 是水平居中 + 字段 AtSizeHint，
        # 当父容器宽度大于字段需求时，整列 label 会被推到中央
        # （视觉上像被"右移"），与底部"自定义 Header"行不在同一列。
        # 显式左上对齐 + 字段随容器拉伸，让所有行 label 列严格对齐。
        right.setFormAlignment(
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop,
        )
        right.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.ExpandingFieldsGrow,
        )

        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText('如: my-deepseek')
        right.addRow('名称:', self.name_edit)

        self.base_url_edit = QtWidgets.QLineEdit()
        self.base_url_edit.setPlaceholderText(
            '如: https://api.deepseek.com',
        )

        # base_url 静态体检提示（路径错填等常见问题）：
        # 把 base_url 输入框 + 提示 label 包在同一个 widget 里
        # 作为整体加入 form，避免提示 hide 时仍占用 form 行高，
        # 把下一行（API Key）的字段单元拉成超高块。
        base_url_box = QtWidgets.QWidget()
        base_url_box_layout = QtWidgets.QVBoxLayout(base_url_box)
        base_url_box_layout.setContentsMargins(0, 0, 0, 0)
        base_url_box_layout.setSpacing(2)
        # 子项顶部对齐，避免 form 给该单元分配多余高度时
        # base_url_edit 被向下推（resize 时表现为"漂移"）
        base_url_box_layout.setAlignment(QtCore.Qt.AlignTop)
        base_url_box_layout.addWidget(self.base_url_edit)

        self.base_url_hint = QtWidgets.QLabel('')
        self.base_url_hint.setStyleSheet('color:#b8923a;')
        self.base_url_hint.setWordWrap(True)
        self.base_url_hint.hide()
        base_url_box_layout.addWidget(self.base_url_hint)

        base_url_box.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )
        right.addRow('Base URL:', base_url_box)
        self.base_url_edit.textChanged.connect(self._refresh_base_url_hint)

        # API Key + 显示/隐藏
        key_row = QtWidgets.QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(4)
        # 强制水平排列时垂直居中，避免 resize 时按钮和输入框
        # 在垂直方向出现错位（"脱节"）
        key_row.setAlignment(QtCore.Qt.AlignVCenter)
        self.api_key_edit = QtWidgets.QLineEdit()
        self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_edit.setPlaceholderText(
            '本地模型可留空，或填 ollama / lmstudio 等占位符',
        )
        key_row.addWidget(self.api_key_edit, 1)
        self.show_key_btn = QtWidgets.QPushButton(_btn_label('👁', '显示'))
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.toggled.connect(self._toggle_key_visible)
        key_row.addWidget(self.show_key_btn)
        key_widget = QtWidgets.QWidget()
        key_widget.setLayout(key_row)
        # 限定 key_widget 垂直高度：使用 Fixed + 显式 maximumHeight
        # 锁死，避免 form 在 resize 时对该行重新分配高度，
        # 导致输入框相对相邻行漂移、与按钮脱节。
        key_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )
        # sizeHint 高度由内部 QLineEdit + QPushButton 中较高者决定
        # 这里直接让容器跟随该 hint，避免被 form 拉伸
        key_widget.setMaximumHeight(
            max(
                self.api_key_edit.sizeHint().height(),
                self.show_key_btn.sizeHint().height(),
            ),
        )
        right.addRow('API Key:', key_widget)

        self.model_edit = QtWidgets.QLineEdit()
        self.model_edit.setPlaceholderText(
            '如: deepseek-v4-flash / qwen2.5:7b / gpt-4o-mini',
        )
        right.addRow('模型:', self.model_edit)

        self.temperature_spin = QtWidgets.QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(0.7)
        right.addRow('温度:', self.temperature_spin)

        self.max_tokens_spin = QtWidgets.QSpinBox()
        self.max_tokens_spin.setRange(0, 200000)
        self.max_tokens_spin.setSingleStep(256)
        self.max_tokens_spin.setSpecialValueText('(由模型决定)')
        right.addRow('最大输出 token:', self.max_tokens_spin)

        self.timeout_spin = QtWidgets.QSpinBox()
        self.timeout_spin.setRange(5, 3600)
        self.timeout_spin.setValue(120)
        self.timeout_spin.setSuffix(' 秒')
        right.addRow('请求超时:', self.timeout_spin)

        self.max_loops_spin = QtWidgets.QSpinBox()
        self.max_loops_spin.setRange(4, 200)
        self.max_loops_spin.setValue(40)
        self.max_loops_spin.setSuffix(' 轮')
        self.max_loops_spin.setToolTip(
            '一次用户输入后，Agent 最多允许的「LLM↔工具」往返轮数。\n'
            '批量任务（如"测试所有工具"）可能需要 40-80 轮。\n'
            '接近上限时会自动提示模型收尾，超限后会保留已完成结果。',
        )
        right.addRow('工具调用上限:', self.max_loops_spin)

        self.max_history_tokens_spin = QtWidgets.QSpinBox()
        self.max_history_tokens_spin.setRange(2000, 200000)
        self.max_history_tokens_spin.setSingleStep(2000)
        self.max_history_tokens_spin.setValue(32000)
        self.max_history_tokens_spin.setSuffix(' tokens')
        self.max_history_tokens_spin.setToolTip(
            '历史对话保留的 token 预算上限。\n'
            '每次发请求前自动裁剪最早消息，但严格保护：\n'
            '  • system 提示\n'
            '  • 最近 4 条消息\n'
            '  • assistant(tool_calls) 与对应 tool 结果的配对',
        )
        right.addRow('历史 token 预算:', self.max_history_tokens_spin)

        self.stream_chk = QtWidgets.QCheckBox('启用流式输出')
        self.stream_chk.setChecked(True)
        right.addRow('', self.stream_chk)

        self.tools_chk = QtWidgets.QCheckBox('启用 Function Calling')
        self.tools_chk.setChecked(True)
        right.addRow('', self.tools_chk)

        # 高级：自定义 header
        self.headers_edit = QtWidgets.QPlainTextEdit()
        self.headers_edit.setPlaceholderText(
            '可选：每行一个 KEY=VALUE，例如\nX-Org-Id=foo\n',
        )
        self.headers_edit.setMaximumHeight(80)
        right.addRow('自定义 Header:', self.headers_edit)

        # 测试连接结果
        self.test_label = QtWidgets.QLabel('')
        self.test_label.setStyleSheet('color:#888;')
        self.test_label.setWordWrap(True)
        right.addRow('', self.test_label)

        # 操作按钮：测试连接 / 完整测试 / 应用
        op_row = QtWidgets.QHBoxLayout()
        op_row.setSpacing(6)
        op_row.addStretch(1)

        def _shape_btn(btn, fallback_min_w=80):
            text = btn.text() or ''
            char_lower = sum(14 if ord(c) > 127 else 7 for c in text) + 32
            try:
                fm = btn.fontMetrics()
                if hasattr(fm, 'horizontalAdvance'):
                    text_w = fm.horizontalAdvance(text)
                else:
                    text_w = fm.width(text)
                measured = int(text_w * 1.15) + 32
            except Exception:  # pylint: disable=broad-except
                measured = 0
            target = max(fallback_min_w, char_lower, measured)
            btn.setMinimumWidth(target)
            btn.setMinimumHeight(30)
            btn.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Fixed,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )

        self.test_btn = QtWidgets.QPushButton(_btn_label('🔌', '测试连接'))
        self.test_btn.setToolTip('发送一条最简单的非流式 ping，仅验证 base_url + key 基本可达。')
        self.test_btn.clicked.connect(self._test_connection)
        _shape_btn(self.test_btn)
        op_row.addWidget(self.test_btn)

        self.test_full_btn = QtWidgets.QPushButton(_btn_label('✅', '完整测试'))
        self.test_full_btn.setToolTip(
            '复刻真实对话的请求：开启流式 + 携带全部工具 schema。\n'
            '当"测试连接"通过但实际对话报错时，用此按钮定位差异。'
        )
        self.test_full_btn.clicked.connect(self._test_connection_full)
        _shape_btn(self.test_full_btn)
        op_row.addWidget(self.test_full_btn)

        self.apply_btn = QtWidgets.QPushButton(_btn_label('💾', '应用'))
        self.apply_btn.setToolTip('保存当前 Profile 修改')
        self.apply_btn.clicked.connect(self._apply)
        _shape_btn(self.apply_btn)
        op_row.addWidget(self.apply_btn)

        right_box = QtWidgets.QVBoxLayout()
        # form 不再 stretch=1：让它只占 sizeHint 高度，
        # 多余的纵向空间由 addStretch 吸收。
        # 这样窗口 resize 时，每一行高度严格按 sizeHint 决定，
        # 不会出现 API Key 行被分摊到多余高度而漂移的问题。
        right_box.addLayout(right)
        right_box.addStretch(1)
        right_box.addLayout(op_row)

        right_widget = QtWidgets.QWidget()
        right_widget.setLayout(right_box)
        outer.addWidget(right_widget, 2)
        return page

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
            '用选中 Provider 发起一次 "3ds Max" 搜索验证可用性',
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

        self.auto_show_chk = QtWidgets.QCheckBox(
            'Max 启动时自动显示 MaxAgent 面板',
        )
        self.auto_show_chk.setToolTip(
            '关闭后，Max 启动时不会自动弹出面板，需在 MAXScript Listener\n'
            '中执行 g_show_max_agent() 或通过菜单/快捷键手动显示。',
        )
        self.auto_show_chk.toggled.connect(self._on_app_setting_changed)
        form.addRow('', self.auto_show_chk)

        self.allow_escape_chk = QtWidgets.QCheckBox(
            '允许使用 run_maxscript / run_python 工具',
        )
        self.allow_escape_chk.setToolTip(
            '关闭后 LLM 无法执行任意脚本，仅能调用预定义工具。',
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
    # Page 5: 日志（三态：关闭 / 开启 / DEBUG）
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

        intro = QtWidgets.QLabel(
            '在 Max 内开启一个本地 TCP 端口，让外部 IDE（通过 '
            '<a href="https://gitee.com/cmqll/dcc-mcp" '
            'style="color:#4da6ff;">dcc-mcp</a> 这类 MCP Server）'
            '调用 maxagent 能力：<br>'
            '&nbsp;&nbsp;• <b>execute_python</b>：在 Max 主线程执行任意'
            ' Python 代码（pymxs 安全）<br>'
            '&nbsp;&nbsp;• <b>dispatch_task</b>：把整个自然语言任务派给'
            ' maxagent 自己跑（IDE Agent ↔ maxagent Agent 协作）<br>'
            '<span style="color:#888;">仅监听 127.0.0.1，不暴露外网。'
            '建议默认关闭，只在需要时手动开启。</span>',
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
        self.bridge_port_spin.setToolTip(
            '本地监听端口（默认 7003，与 dcc-mcp 3dsMax 预设一致）。\n'
            '修改后需关闭再重新启用 Bridge 才生效。',
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
        # Phase 2: 导入 / 导出（轻共享）
        export_btn = QtWidgets.QPushButton(_btn_label('📤', '导出选中'))
        export_btn.setToolTip('把当前选中的规则导出为 .maxagent-rule.json 文件')
        export_btn.clicked.connect(self._on_rules_export_selected)
        btn_row.addWidget(export_btn)
        export_all_btn = QtWidgets.QPushButton(_btn_label('📦', '导出全部'))
        export_all_btn.setToolTip('把所有已启用规则打包导出为 .maxagent-rules.json 文件')
        export_all_btn.clicked.connect(self._on_rules_export_all)
        btn_row.addWidget(export_all_btn)
        import_btn = QtWidgets.QPushButton(_btn_label('📥', '导入文件'))
        import_btn.setToolTip('从 .maxagent-rule(s).json 文件导入规则')
        import_btn.clicked.connect(self._on_rules_import_file)
        btn_row.addWidget(import_btn)
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
    # Page 5: 帮助
    # ================================================================== #
    def _build_page_help(self):
        # type: () -> QtWidgets.QWidget
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        title = QtWidgets.QLabel(_ee('❓') + '  使用帮助')
        title.setStyleSheet('font-size:16px; font-weight:bold;')
        layout.addWidget(title)

        text = QtWidgets.QTextBrowser()
        text.setOpenExternalLinks(True)
        # 直接给 QTextBrowser 设置高对比度的暗色调色板，避免依赖
        # 系统主题——某些 Max 主题下默认正文偏灰，<code> 无背景，
        # 阅读吃力。这里统一固定背景 + 高亮文字色。
        text.setStyleSheet(
            'QTextBrowser {'
            ' background:#1e1e1e;'
            ' color:#e8e8e8;'
            ' border:1px solid #3a3a3a;'
            ' padding:8px;'
            ' font-size:10pt;'
            ' line-height:160%;'
            '}'
        )
        text.setHtml(self._help_html())
        layout.addWidget(text, 1)
        return page

    @staticmethod
    def _help_html():
        # 颜色规范（与界面整体暗色主题对齐，确保 ≥ AA 级对比度）：
        #   正文       #e8e8e8（浅灰，对比 #1e1e1e ≈ 12:1）
        #   小标题     #ffd166（暖黄，吸引眼球）
        #   代码片段   背景 #2a2a2a + 文字 #ffe082
        #   强调      #a8e6a8（浅绿）
        #   警示      #ff9090（浅红）
        return (
            '<style>'
            'body { color:#e8e8e8; }'
            'h3 { color:#ffd166; margin:6px 0 4px 0; }'
            'h4 { color:#ffd166; margin:10px 0 4px 0;'
            '     border-left:3px solid #ffd166; padding-left:6px; }'
            'p { color:#e8e8e8; line-height:160%; }'
            'b { color:#ffffff; }'
            'code { background:#2a2a2a; color:#ffe082;'
            '       padding:1px 4px; border-radius:2px; }'
            '.tip { color:#a8e6a8; }'
            '.warn { color:#ff9090; }'
            'hr { border:0; border-top:1px solid #3a3a3a; margin:10px 0; }'
            'table { border-collapse:collapse; margin:4px 0; }'
            'td { padding:3px 8px; border:1px solid #3a3a3a;'
            '     color:#e8e8e8; }'
            'th { padding:3px 8px; border:1px solid #3a3a3a;'
            '     background:#2a2a2a; color:#ffd166; text-align:left; }'
            '</style>'
            '<h3>MaxAgent 设置帮助</h3>'

            '<h4>模型 Tab</h4>'
            '<p>管理多套大模型连接（Ollama / LM Studio / OpenAI / '
            'DeepSeek 等），右键 Profile 可<b>重命名 / 复制 / 设为默认</b>。</p>'

            '<p><b>Base URL</b>：OpenAI 兼容 API 的根地址，多数服务'
            '需要带 <code>/v1</code> 后缀（DeepSeek 官方推荐使用根域名）。'
            '<br>· Ollama：<code>http://localhost:11434/v1</code>'
            '<br>· LM Studio：<code>http://localhost:1234/v1</code>'
            '<br>· OpenAI：<code>https://api.openai.com/v1</code>'
            '<br>· DeepSeek：<code>https://api.deepseek.com</code>'
            '（推荐 <code>deepseek-v4-flash</code> / '
            '<code>deepseek-v4-pro</code>，旧模型 '
            '<code>deepseek-chat</code> / <code>deepseek-reasoner</code> '
            '将于 <span class="warn">2026/07/24</span> 弃用）</p>'

            '<p><b>API Key</b>：本地模型可留空或填占位符；商用 API 必填。</p>'
            '<p><b>模型</b>：模型名称需与服务端实际可用模型完全一致。</p>'
            '<p><b>温度</b>：0.0~2.0，越高越发散；建议 <b>0.2 ~ 0.7</b>。</p>'
            '<p><b>请求超时</b>：单次请求等待秒数，长上下文/慢模型可调大。</p>'
            '<p><b>工具调用上限</b>：单轮对话内 LLM 可触发的工具调用次数上限，'
            '防止无限循环。</p>'
            '<p><b>历史 token 预算</b>：发送给 LLM 时携带的对话历史 token 上限，'
            '超出会自动裁剪最早消息。</p>'

            # ---- 自定义 Header ----
            '<h4>自定义 Header（高级）</h4>'
            '<p>在 LLM HTTP 请求中附加自定义请求头，常用于：'
            '<b>企业网关追踪</b> / <b>Beta 通道开关</b> / <b>第三方代理鉴权</b>。'
            '<br><span class="tip">DeepSeek 直连官方 API 通常无需填写</span>。</p>'

            '<p><b>格式</b>：每行一对 <code>KEY=VALUE</code>，'
            '半角等号分隔；空行 / 不含 <code>=</code> 的行会被忽略，'
            '不支持冒号 / YAML / JSON。</p>'

            '<p><b>DeepSeek 场景示例</b>：</p>'
            '<table>'
            '<tr><th>场景</th><th>填写内容</th></tr>'
            '<tr><td>直连官方 API</td>'
            '<td><span class="tip">留空即可</span>'
            '（已自动处理 Bearer 鉴权）</td></tr>'
            '<tr><td>走企业网关</td>'
            '<td><code>X-Org-Id=team-rendering</code><br>'
            '<code>X-Project=maxagent</code></td></tr>'
            '<tr><td>调试追踪</td>'
            '<td><code>X-Trace-Id=maxagent-debug</code></td></tr>'
            '<tr><td>第三方 OpenAI 兼容网关</td>'
            '<td><code>X-API-Token=xxxxxxxxxx</code><br>'
            '<code>X-Tenant-Id=cg-team</code></td></tr>'
            '</table>'

            '<p class="warn"><b>⚠ 注意</b>：自定义 Header 优先级高于默认值，'
            '<b>请勿覆盖</b> <code>Authorization</code> / '
            '<code>Content-Type</code>，否则会破坏鉴权或被服务端拒收。</p>'

            '<hr>'

            # ---- 视觉 / 图片 ----
            '<h4>图片与视觉</h4>'
            '<p>支持 4 种插入方式：📎 工具栏选图 / ✂️ 截图 / Ctrl+V 粘贴 / '
            '直接拖拽到输入框。</p>'
            '<p>当前 profile 模型属于<b>视觉白名单</b>（如 GPT-4o / '
            'Claude-3+ / Gemini / DeepSeek-VL 等）时图片会原图发送；'
            '<span class="warn">不支持视觉时</span>会自动降级为'
            '<code>[图片] N 张</code> 的文本提示，并在输入框上方显示'
            '黄色提示条，可一键切换 Profile。</p>'
            '<p><b>气泡里的图片</b>：右键可<b>复制图片 / 复制路径 / '
            '另存为 / 查看大图</b>，方便粘贴到 Word / 微信 / PS。</p>'

            '<hr>'

            # ---- IDE 接口 / Bridge ----
            '<h4>IDE 接口（Bridge）🔌</h4>'
            '<p>在 Max 内开启一个本地 TCP 端口，让外部 IDE'
            '（Cursor / Claude Desktop / Cline 等）通过 '
            '<a href="https://gitee.com/cmqll/dcc-mcp" '
            'style="color:#4da6ff;">dcc-mcp</a> 连接到 maxagent，'
            '形成 <b>IDE Agent ↔ maxagent Agent</b> 协作。</p>'

            '<p><b>两种调用方式</b>：</p>'
            '<table>'
            '<tr><th>工具</th><th>谁出大脑</th><th>典型场景</th></tr>'
            '<tr><td><code>execute_python</code></td>'
            '<td>IDE LLM 写代码</td>'
            '<td>"创建 5 个 Box 沿 X 排列"等明确代码动作</td></tr>'
            '<tr><td><code>dispatch_task</code></td>'
            '<td>maxagent 自跑</td>'
            '<td>"测我刚写的工具"等需要规划+执行的任务</td></tr>'
            '</table>'

            '<p><b>快速接入</b>：</p>'
            '<p>① 打开 <b>IDE 接口</b> Tab → 勾选「启用 IDE Bridge」'
            '<br>② 点「<b>复制 dcc-mcp / Cursor 配置示例</b>」按钮'
            '<br>③ 粘贴到 IDE 的 MCP 配置文件（如 '
            '<code>~/.cursor/mcp.json</code>），重启 IDE 即可。</p>'

            '<p><b>关键设置</b>：</p>'
            '<table>'
            '<tr><th>字段</th><th>默认</th><th>说明</th></tr>'
            '<tr><td>监听端口</td><td>7003</td>'
            '<td>与 dcc-mcp 3dsMax 预设一致；改端口需同步 mcp.json</td></tr>'
            '<tr><td>访问令牌</td><td>空</td>'
            '<td>多人共用机器担心误连时填；本机回环用通常无需</td></tr>'
            '<tr><td>允许任务派发</td><td>开</td>'
            '<td>关闭后只暴露 execute_python（IDE 自己写代码）</td></tr>'
            '<tr><td>最大轮数</td><td>20</td>'
            '<td>dispatch_task 内 LLM↔工具循环上限，防死循环</td></tr>'
            '<tr><td>超时</td><td>300s</td>'
            '<td>dispatch_task 单任务总超时</td></tr>'
            '</table>'

            '<p class="warn"><b>⚠ 安全</b>：仅监听 <code>127.0.0.1</code>，'
            '外网不可达；<b>不要</b>手动改成 <code>0.0.0.0</code>。'
            'execute_python 完全开放，仅限本机使用。</p>'
            '<p class="tip">完整指南见 '
            '<code>maxagent/docs/IDE_MCP_USAGE.md</code></p>'

            '<hr>'

            # ---- 日志 / 测试 ----
            '<h4>日志与诊断</h4>'
            '<p><b>日志 Tab</b>：三态切换 <b>关闭 / 开启 / DEBUG</b>。'
            'DEBUG 级别下会全链路打印 LLM 请求 / 工具调用 / 截图 / '
            '附件操作 / 线程切换 / UI 信号延迟 / Bridge 连接与方法分发，'
            '方便排查偶发问题。'
            '日志只写文件不进控制台，路径见日志页底部。</p>'
            '<p><b>测试连接</b>：仅 ping，验证 base_url + key 基本可达。'
            '<br><b>完整测试</b>：复刻真实对话请求（流式 + 全部工具 schema），'
            '用于排查"测试连接通过但实际对话失败"类问题。</p>'
        )

    # ================================================================== #
    # Profile 加载/保存
    # ================================================================== #
    def _reload_profiles(self):
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        active = self._config.get_active_profile_name()
        for name in self._config.list_profile_names():
            item = QtWidgets.QListWidgetItem(name)
            if name == active:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            self.profile_list.addItem(item)
        # 选中 active
        for i in range(self.profile_list.count()):
            if self.profile_list.item(i).text() == active:
                self.profile_list.setCurrentRow(i)
                break
        self.profile_list.blockSignals(False)
        self._load_to_form(active)
        # 全局应用设置（与具体 Profile 无关）
        self._load_app_settings()

    def _load_app_settings(self):
        """从 AppConfig 把全局开关加载到对应复选框 + 日志级别。"""
        cfg = self._config.config
        for chk, val in (
            (self.auto_show_chk, cfg.auto_show_on_startup),
            (self.allow_escape_chk, cfg.allow_escape_hatch),
            (self.confirm_exec_chk, cfg.confirm_before_exec),
            (self.wrap_undo_chk, cfg.wrap_undo),
            (self.vision_enabled_chk, getattr(cfg, 'vision_enabled', True)),
        ):
            chk.blockSignals(True)
            chk.setChecked(bool(val))
            chk.blockSignals(False)

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

    def _on_app_setting_changed(self, _checked):
        cfg = self._config.config
        cfg.auto_show_on_startup = bool(self.auto_show_chk.isChecked())
        cfg.allow_escape_hatch = bool(self.allow_escape_chk.isChecked())
        cfg.confirm_before_exec = bool(self.confirm_exec_chk.isChecked())
        cfg.wrap_undo = bool(self.wrap_undo_chk.isChecked())
        cfg.vision_enabled = bool(self.vision_enabled_chk.isChecked())
        try:
            self._config.save()
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '保存失败', '应用设置写盘失败: {}'.format(exc),
            )

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
        env = {
            'DCC_MCP_NAME': '3dsMax',
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
        self.api_key_edit.setText(prof.api_key or '')
        self.model_edit.setText(prof.model)
        self.temperature_spin.setValue(float(prof.temperature))
        self.max_tokens_spin.setValue(int(prof.max_tokens or 0))
        self.timeout_spin.setValue(int(prof.timeout))
        self.max_loops_spin.setValue(int(getattr(prof, 'max_tool_loops', 40) or 40))
        self.max_history_tokens_spin.setValue(
            int(getattr(prof, 'max_history_tokens', 32000) or 32000),
        )
        self.stream_chk.setChecked(bool(prof.stream))
        self.tools_chk.setChecked(bool(prof.supports_tools))
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
            results = _do_search(
                '3ds Max maxscript',
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

        return LLMProfile(
            name=self.name_edit.text().strip(),
            base_url=self.base_url_edit.text().strip(),
            api_key=self.api_key_edit.text(),
            model=self.model_edit.text().strip(),
            temperature=float(self.temperature_spin.value()),
            max_tokens=(
                int(self.max_tokens_spin.value())
                if self.max_tokens_spin.value() > 0 else 4096
            ),
            timeout=int(self.timeout_spin.value()),
            max_tool_loops=int(self.max_loops_spin.value()),
            max_history_tokens=int(self.max_history_tokens_spin.value()),
            stream=bool(self.stream_chk.isChecked()),
            supports_tools=bool(self.tools_chk.isChecked()),
            extra_headers=headers,
        )

    def _on_profile_selected(self, cur, prev):
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
        if checked:
            self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Normal)
        else:
            self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)

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

    def _test_connection(self):
        try:
            prof = self._read_form()
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('{} 表单错误: {}'.format(_ee('❌'), exc))
            self.test_label.setStyleSheet('color:#e57373;')
            return
        self.test_label.setText('⏳ 测试中...')
        self.test_label.setStyleSheet('color:#888;')
        QtWidgets.QApplication.processEvents()
        try:
            client = build_client_from_profile(prof)
            resp = client.chat(
                messages=[
                    {'role': 'user', 'content': '回复一个字: ok'},
                ],
                stream=False,
                tools=None,
            )
            content = (resp.get('content') or '').strip()
            if content:
                self.test_label.setText(
                    '{} 连接成功，模型回复: "{}"'.format(
                        _ee('✅'), content[:40],
                    ),
                )
                self.test_label.setStyleSheet('color:#8fce8f;')
            else:
                self.test_label.setText('{} 连接成功（响应为空）'.format(_ee('✅')))
                self.test_label.setStyleSheet('color:#8fce8f;')
        except LLMError as exc:
            self.test_label.setText('{} 连接失败: {}'.format(_ee('❌'), exc))
            self.test_label.setStyleSheet('color:#e57373;')
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('{} 异常: {}'.format(_ee('❌'), exc))
            self.test_label.setStyleSheet('color:#e57373;')

    def _test_connection_full(self):
        try:
            prof = self._read_form()
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('{} 表单错误: {}'.format(_ee('❌'), exc))
            self.test_label.setStyleSheet('color:#e57373;')
            return
        self.test_label.setText('⏳ 完整测试中（流式+tools）...')
        self.test_label.setStyleSheet('color:#888;')
        QtWidgets.QApplication.processEvents()

        try:
            from ..tools import build_openai_tools_schema
            tools_schema = build_openai_tools_schema()
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('{} 加载工具 schema 失败: {}'.format(_ee('❌'), exc))
            self.test_label.setStyleSheet('color:#e57373;')
            return

        chunks = []

        def _on_delta(text):
            chunks.append(text)

        try:
            client = build_client_from_profile(prof)
            resp = client.chat(
                messages=[
                    {'role': 'system', 'content': 'You are a helpful assistant.'},
                    {'role': 'user', 'content': '回复一个字: ok'},
                ],
                tools=tools_schema,
                stream=True,
                on_delta=_on_delta,
            )
            content = (resp.get('content') or ''.join(chunks)).strip()
            if content:
                self.test_label.setText(
                    '{} 完整测试通过，模型回复: "{}"'.format(
                        _ee('✅'), content[:40],
                    ),
                )
                self.test_label.setStyleSheet('color:#8fce8f;')
            else:
                self.test_label.setText(
                    '{} 完整测试通过（响应为空，但握手成功）'.format(
                        _ee('✅'),
                    ),
                )
                self.test_label.setStyleSheet('color:#8fce8f;')
        except LLMError as exc:
            self.test_label.setText('{} 完整测试失败: {}'.format(_ee('❌'), exc))
            self.test_label.setStyleSheet('color:#e57373;')
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('{} 异常: {}'.format(_ee('❌'), exc))
            self.test_label.setStyleSheet('color:#e57373;')

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
