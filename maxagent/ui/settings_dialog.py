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
        # 注意：以下两行顺序与 _NAV_ITEMS 严格对应。
        # IDE 接口排在日志之前——属于"功能性"配置，使用频率高于
        # "排错性"日志，先功能后辅助更符合用户心智模型。
        self.stack.addWidget(self._build_page_bridge())
        self.stack.addWidget(self._build_page_log())
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
        # 关键：关闭 autoDefault，否则在表单输入框按回车会被这个按钮
        # 的 default 行为吃掉，触发"新建 Profile"对话框
        self.add_btn.setAutoDefault(False)
        self.add_btn.setDefault(False)
        btns.addWidget(self.add_btn)
        self.del_btn = QtWidgets.QPushButton('✕ 删除')
        self.del_btn.clicked.connect(self._del_profile)
        self.del_btn.setAutoDefault(False)
        self.del_btn.setDefault(False)
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
        # 显示/隐藏按钮：
        # 1) 关闭 autoDefault/Default，防止表单中按 Enter 把 API Key
        #    明文显示出来（这是历史 bug，用户回车后 toggled 信号被
        #    意外触发 → setEchoMode(Normal) → 密码原文泄露）。
        # 2) 关闭 NoFocus 之外的焦点策略：避免 Tab 串过来后空格也能
        #    切换可见性。改为只能用鼠标点击切换，符合"敏感操作显式"
        #    交互预期。
        # 3) 视觉反馈：checked 时换图标 / 文案 / 背景色，
        #    让用户一眼分辨当前是"明文显示"还是"已隐藏"。
        self.show_key_btn = QtWidgets.QPushButton(_btn_label('👁', '显示'))
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.setAutoDefault(False)
        self.show_key_btn.setDefault(False)
        self.show_key_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self.show_key_btn.setToolTip(
            '点击切换 API Key 可见性。\n'
            '⚠ 显示状态下请避免分享屏幕或截图。',
        )
        # 视觉反馈：
        # - 默认（隐藏）：灰底，提示"安全态"
        # - 选中（显示）：橙红底 + 白字，提示"敏感态"
        # 颜色与"危险按钮"语义一致，让明文显示具有显著视觉警示效果。
        self.show_key_btn.setStyleSheet(
            'QPushButton {'
            ' padding: 4px 10px;'
            ' border: 1px solid #555;'
            ' border-radius: 3px;'
            ' background: #2d2d2d;'
            ' color: #ddd;'
            '}'
            'QPushButton:hover {'
            ' border-color: #888;'
            ' background: #3a3a3a;'
            '}'
            'QPushButton:checked {'
            ' background: #c0392b;'
            ' border-color: #e74c3c;'
            ' color: #ffffff;'
            ' font-weight: bold;'
            '}'
            'QPushButton:checked:hover {'
            ' background: #d63a2c;'
            '}',
        )
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

        self.force_temp_one_chk = QtWidgets.QCheckBox()
        self.force_temp_one_chk.setToolTip(
            '部分模型/网关（如 Moonshot kimi-k3）服务端只接受 temperature=1，\n'
            '开启后会向 param_overrides["temperature"] 写入 1.0，\n'
            '所有请求（含 reasoning 轮次和自动摘要）最终都会以 1.0 发送。',
        )
        right.addRow('强制 temperature=1:', self.force_temp_one_chk)

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
        # 视觉/严苛网关避坑提示：vita / claude-vision 等模型对 tools 字段
        # 极度敏感，开启后会让网关直接返回 5xx。把指南写进 tooltip 比
        # 让用户碰壁后再回来翻文档友好得多。
        self.tools_chk.setToolTip(
            '关闭后本 profile 的对话不会携带 tools / tool_choice 字段。\n'
            '什么时候关：\n'
            '· 视觉专用模型（youtu-vita、qwen-vl 等）\n'
            '· 网关返回 "model engine error" / 502 upstream\n'
            '· 模型本身不支持 OpenAI Function Calling 协议\n'
            '关闭后 LLM 不会再调用任何工具，纯对话模式。',
        )
        right.addRow('', self.tools_chk)

        self.vision_supported_chk = QtWidgets.QCheckBox('模型支持视觉输入')
        self.vision_supported_chk.setChecked(False)
        self.vision_supported_chk.setToolTip(
            '勾选后，Agent 在「需要视觉验证」的步骤会自动截取 3ds Max\n'
            '当前视口并作为 image_url 发送给该模型。\n'
            '只有真正支持 OpenAI 多模态协议的模型才应勾选，否则可能\n'
            '触发 400 / token 浪费。',
        )
        right.addRow('', self.vision_supported_chk)

        # 备用 Profile 链：触发速率限制或服务不可用时按顺序切换
        self.fallback_list = QtWidgets.QListWidget()
        self.fallback_list.setSelectionMode(
            QtWidgets.QAbstractItemView.NoSelection,
        )
        self.fallback_list.setMinimumHeight(80)
        self.fallback_list.setMaximumHeight(120)
        self.fallback_list.setToolTip(
            '触发 429 速率限制或 5xx 服务过载时，按勾选顺序切换到备用\n'
            'Profile 继续调用。\n'
            '典型场景：主 Profile 用 Kimi/Moonshot（高质量但配额有限），\n'
            '备用配 DeepSeek 或本地 Ollama 保底。\n'
            '⚠ 只能选择已存在的其他 Profile；当前 Profile 不会出现在列表中。',
        )
        right.addRow('备用 Profile:', self.fallback_list)


        self.headers_edit = QtWidgets.QPlainTextEdit()
        self.headers_edit.setPlaceholderText(
            '可选：每行一个 KEY=VALUE，例如\nX-Org-Id=foo\n',
        )
        # 垂直方向允许跟随对话框尺寸扩张：
        # - 最小高度 80（保留约 4 行可视区，避免太矮）
        # - SizePolicy=Expanding 让它在 form 拿到 stretch 时
        #   独享多余的纵向空间（其他行都是 Fixed/Preferred，
        #   不会跟着拉伸，因此 API Key 等行不会再漂移）
        self.headers_edit.setMinimumHeight(80)
        self.headers_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        right.addRow('自定义 Header:', self.headers_edit)

        # 测试连接结果
        self.test_label = QtWidgets.QLabel('')
        self.test_label.setStyleSheet('color:#888;')
        self.test_label.setWordWrap(True)
        # 错误信息可能很长（HTTP body + headers），允许用户用鼠标选中复制
        # 排错时把错误粘到搜索引擎或群里。
        self.test_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            | QtCore.Qt.TextInteractionFlag.TextSelectableByKeyboard,
        )
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

        # 恢复默认：把当前 profile 表单字段重置为"OpenAI 兼容"出厂模板
        # （名称 / 模型空，由用户重填；Base URL = https://api.openai.com/v1；
        #  其他参数全部回 dataclass 默认值；API Key 保留不动避免误清密钥）。
        # 关键点：
        # 1) 不写盘——只改 UI，避免把"名称为空的 profile"硬塞进配置文件
        #    破坏 profile 索引；用户填完后点"应用"才真正落盘。
        # 2) autoDefault=False + focusPolicy=NoFocus，回车永远不会落到这个
        #    按钮上（与之前修复 show_key_btn / 新建按钮误触的策略一致）。
        # 3) 弹二次确认——单击代价不小，避免手滑。
        self.reset_default_btn = QtWidgets.QPushButton(
            _btn_label('🔄', '恢复默认'),
        )
        self.reset_default_btn.setToolTip(
            '把当前 Profile 字段重置为 OpenAI 兼容出厂模板：\n'
            '  • 名称 / 模型 → 留空，由你重填\n'
            '  • Base URL → https://api.openai.com/v1\n'
            '  • API Key → 保留不变（避免误清密钥）\n'
            '  • 其他参数全部恢复默认\n'
            '注：仅修改表单显示，需点击"应用"才会落盘。',
        )
        self.reset_default_btn.clicked.connect(self._reset_profile_to_default)
        self.reset_default_btn.setAutoDefault(False)
        self.reset_default_btn.setDefault(False)
        self.reset_default_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        _shape_btn(self.reset_default_btn)
        op_row.addWidget(self.reset_default_btn)

        self.test_btn = QtWidgets.QPushButton(_btn_label('🔌', '测试连接'))
        self.test_btn.setToolTip('发送一条最简单的非流式 ping，仅验证 base_url + key 基本可达。')
        self.test_btn.clicked.connect(self._test_connection)
        self.test_btn.setAutoDefault(False)
        self.test_btn.setDefault(False)
        _shape_btn(self.test_btn)
        op_row.addWidget(self.test_btn)

        self.test_full_btn = QtWidgets.QPushButton(_btn_label('✅', '完整测试'))
        self.test_full_btn.setToolTip(
            '复刻真实对话的请求：开启流式 + 携带全部工具 schema。\n'
            '当"测试连接"通过但实际对话报错时，用此按钮定位差异。'
        )
        self.test_full_btn.clicked.connect(self._test_connection_full)
        self.test_full_btn.setAutoDefault(False)
        self.test_full_btn.setDefault(False)
        _shape_btn(self.test_full_btn)
        op_row.addWidget(self.test_full_btn)

        self.apply_btn = QtWidgets.QPushButton(_btn_label('💾', '应用'))
        self.apply_btn.setToolTip('保存当前 Profile 修改（在表单内按 Enter 也会触发）')
        self.apply_btn.clicked.connect(self._apply)
        # 让"应用"成为表单的 default 按钮——在表单输入框按 Enter 就会
        # 直接保存当前 Profile，符合用户直觉
        self.apply_btn.setAutoDefault(True)
        self.apply_btn.setDefault(True)
        _shape_btn(self.apply_btn)
        op_row.addWidget(self.apply_btn)

        right_box = QtWidgets.QVBoxLayout()
        # form 重新拿回 stretch=1，把整列纵向空间交给它。
        # 关键：QFormLayout 只会拉伸 SizePolicy=Expanding 的字段，
        # 因此多余空间只被 headers_edit 吸收（用户希望随窗口
        # 调整而变高），其他行（API Key / Base URL 等）SizePolicy
        # 是 Fixed/Preferred，高度严格按 sizeHint，不会漂移。
        right_box.addLayout(right, 1)
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
    # Page 6: 工具与技能（导入 / 导出 .maxagent-pack）
    # ================================================================== #
    def _build_page_pack(self):
        # type: () -> QtWidgets.QWidget
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setSpacing(10)

        title = QtWidgets.QLabel(_ee('📦') + '  工具与技能')
        title.setStyleSheet('font-size:16px; font-weight:bold;')
        layout.addWidget(title)

        intro = QtWidgets.QLabel(
            '把<b>自定义工具</b>、<b>技能</b>、<b>自定义规则</b>打包为 '
            '<code>.maxagent-pack</code> 文件，用于跨电脑同步、'
            '团队分享或社区交换。<br>'
            '<span style="color:#ff9090;">⚠ 不会包含 API Key / Profile / '
            '会话历史</span>，避免泄露敏感信息。',
        )
        intro.setTextFormat(QtCore.Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        intro.setStyleSheet(
            'QLabel { background:#2a2a2a; color:#e8e8e8;'
            ' border:1px solid #3a3a3a; padding:8px; border-radius:4px; }',
        )
        layout.addWidget(intro)

        # 三栏勾选 + 数量统计
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(10)

        self.pack_tool_list = self._make_pack_export_list(body, '🧰 自定义工具')
        self.pack_skill_list = self._make_pack_export_list(body, '🎓 技能')
        self.pack_rule_list = self._make_pack_export_list(body, '📋 自定义规则')

        layout.addLayout(body, 1)

        # 操作按钮（每栏的全选已收敛到栏顶复选框，这里仅保留刷新与导入导出）
        op_row = QtWidgets.QHBoxLayout()
        op_row.setSpacing(8)
        refresh_btn = QtWidgets.QPushButton(_ee('🔄') + ' 刷新')
        refresh_btn.clicked.connect(self._reload_pack_lists)
        op_row.addWidget(refresh_btn)
        op_row.addStretch(1)
        export_btn = QtWidgets.QPushButton(_ee('📤') + ' 导出选中…')
        export_btn.setStyleSheet(
            'QPushButton { background:#2d7d46; color:white;'
            ' border:1px solid #3a9c5a; padding:6px 12px; border-radius:3px; }'
            'QPushButton:hover { background:#3a9c5a; }'
        )
        export_btn.clicked.connect(self._on_pack_export)
        op_row.addWidget(export_btn)
        import_btn = QtWidgets.QPushButton(_ee('📥') + ' 导入资源包…')
        import_btn.clicked.connect(self._on_pack_import)
        op_row.addWidget(import_btn)
        layout.addLayout(op_row)

        # 包元信息输入（可选）
        meta_box = QtWidgets.QGroupBox('包元信息（可选）')
        meta_form = QtWidgets.QFormLayout(meta_box)
        meta_form.setLabelAlignment(QtCore.Qt.AlignLeft)
        meta_form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        meta_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.ExpandingFieldsGrow,
        )
        self.pack_name_edit = QtWidgets.QLineEdit()
        self.pack_name_edit.setPlaceholderText('如 "我的渲染工作流 v1"')
        meta_form.addRow('包名:', self.pack_name_edit)
        self.pack_author_edit = QtWidgets.QLineEdit()
        self.pack_author_edit.setPlaceholderText('作者署名（可空）')
        meta_form.addRow('作者:', self.pack_author_edit)
        self.pack_desc_edit = QtWidgets.QLineEdit()
        self.pack_desc_edit.setPlaceholderText('一句话说明该包的用途（可空）')
        meta_form.addRow('描述:', self.pack_desc_edit)
        layout.addWidget(meta_box)

        # 首次加载
        self._reload_pack_lists()

        return page

    def _make_pack_export_list(self, parent_layout, title):
        # type: (QtWidgets.QHBoxLayout, str) -> QtWidgets.QListWidget
        wrap = QtWidgets.QVBoxLayout()
        wrap.setSpacing(4)
        # 顶部行：标题 + 独立全选复选框
        head_row = QtWidgets.QHBoxLayout()
        head_row.setSpacing(6)
        title_lbl = QtWidgets.QLabel(title)
        title_lbl.setStyleSheet(
            'QLabel { color:#ffd166; font-weight:bold; }'
        )
        head_row.addWidget(title_lbl)
        head_row.addStretch(1)
        all_chk = QtWidgets.QCheckBox('全选')
        all_chk.setTristate(False)
        all_chk.setStyleSheet('QCheckBox { color:#cccccc; }')
        head_row.addWidget(all_chk)
        wrap.addLayout(head_row)

        lst = QtWidgets.QListWidget()
        lst.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        wrap.addWidget(lst, 1)
        parent_layout.addLayout(wrap, 1)

        # 全选 ↔ 列表 双向同步
        all_chk.stateChanged.connect(
            lambda state, _lst=lst: self._pack_toggle_section(_lst, state),
        )
        lst.itemChanged.connect(
            lambda _it, _lst=lst, _chk=all_chk:
                self._pack_sync_section_header(_lst, _chk),
        )
        # 暴露给后续刷新使用，方便重置 header
        lst._pack_select_all_chk = all_chk  # type: ignore[attr-defined]
        return lst

    def _reload_pack_lists(self):
        """从磁盘扫描已有的工具 / 技能 / 规则，刷新三栏。"""
        try:
            from .. import user_tools_loader as utl
            tools = utl.list_user_tools(include_meta=True)
        except Exception as exc:  # pylint: disable=broad-except
            tools = []
            logger.warning('扫描自定义工具失败: %s', exc)
        try:
            from .. import skills as skills_mod
            skill_objs = skills_mod.SkillManager().list_skills()
        except Exception as exc:  # pylint: disable=broad-except
            skill_objs = []
            logger.warning('扫描技能失败: %s', exc)
        try:
            from .. import user_rules_loader as url_mod
            rules = url_mod.list_rules()
        except Exception as exc:  # pylint: disable=broad-except
            rules = []
            logger.warning('扫描自定义规则失败: %s', exc)

        self.pack_tool_list.blockSignals(True)
        try:
            self.pack_tool_list.clear()
            for item in tools:
                name = item['name']
                meta = item.get('meta') or {}
                desc = (meta.get('description') or '').strip()
                label = name + ('  —  ' + desc if desc else '')
                it = QtWidgets.QListWidgetItem(label)
                it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                it.setCheckState(QtCore.Qt.Unchecked)
                it.setData(QtCore.Qt.UserRole, name)
                self.pack_tool_list.addItem(it)
            if not tools:
                placeholder = QtWidgets.QListWidgetItem('（暂无自定义工具）')
                placeholder.setFlags(QtCore.Qt.NoItemFlags)
                self.pack_tool_list.addItem(placeholder)
        finally:
            self.pack_tool_list.blockSignals(False)

        self.pack_skill_list.blockSignals(True)
        try:
            self.pack_skill_list.clear()
            for sk in skill_objs:
                desc = (sk.description or '').strip().replace('\n', ' ')
                if len(desc) > 40:
                    desc = desc[:40] + '…'
                label = sk.name + ('  —  ' + desc if desc else '')
                it = QtWidgets.QListWidgetItem(label)
                it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                it.setCheckState(QtCore.Qt.Unchecked)
                it.setData(QtCore.Qt.UserRole, sk.name)
                self.pack_skill_list.addItem(it)
            if not skill_objs:
                placeholder = QtWidgets.QListWidgetItem('（暂无技能）')
                placeholder.setFlags(QtCore.Qt.NoItemFlags)
                self.pack_skill_list.addItem(placeholder)
        finally:
            self.pack_skill_list.blockSignals(False)

        self.pack_rule_list.blockSignals(True)
        try:
            self.pack_rule_list.clear()
            for r in rules:
                rid = r.get('id') or ''
                title_txt = (r.get('title') or '').strip()
                label = rid + ('  —  ' + title_txt if title_txt else '')
                it = QtWidgets.QListWidgetItem(label)
                it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                it.setCheckState(QtCore.Qt.Unchecked)
                it.setData(QtCore.Qt.UserRole, rid)
                self.pack_rule_list.addItem(it)
            if not rules:
                placeholder = QtWidgets.QListWidgetItem('（暂无自定义规则）')
                placeholder.setFlags(QtCore.Qt.NoItemFlags)
                self.pack_rule_list.addItem(placeholder)
        finally:
            self.pack_rule_list.blockSignals(False)

        # 刷新后所有项均为未勾选 → 栏顶全选复选框也复位
        for lst in (self.pack_tool_list, self.pack_skill_list,
                    self.pack_rule_list):
            chk = getattr(lst, '_pack_select_all_chk', None)
            if chk is not None:
                self._pack_sync_section_header(lst, chk)

    def _pack_toggle_section(self, lst, state):
        # type: (QtWidgets.QListWidget, int) -> None
        """栏顶'全选'复选框切换 → 同步该栏所有可勾选项。"""
        # 兼容 PySide2(int) / PySide6(CheckState 或 int) 两种回调签名
        try:
            state_int = int(state)
        except (TypeError, ValueError):
            state_int = 2 if state == QtCore.Qt.Checked else 0
        target = (QtCore.Qt.Checked if state_int == 2
                  else QtCore.Qt.Unchecked)
        lst.blockSignals(True)
        try:
            for i in range(lst.count()):
                it = lst.item(i)
                if not (it.flags() & QtCore.Qt.ItemIsUserCheckable):
                    continue
                it.setCheckState(target)
        finally:
            lst.blockSignals(False)
        logger.info(
            'pack 栏全选切换: %s → %s',
            getattr(lst, 'objectName', lambda: '')() or 'list',
            'all' if target == QtCore.Qt.Checked else 'none',
        )

    def _pack_sync_section_header(self, lst, header_chk):
        # type: (QtWidgets.QListWidget, QtWidgets.QCheckBox) -> None
        """列表项勾选变化 → 反向同步栏顶'全选'复选框状态。"""
        total = 0
        checked = 0
        for i in range(lst.count()):
            it = lst.item(i)
            if not (it.flags() & QtCore.Qt.ItemIsUserCheckable):
                continue
            total += 1
            if it.checkState() == QtCore.Qt.Checked:
                checked += 1
        # 阻断回环：栏顶 checkbox 状态切换不应再触发 _pack_toggle_section
        header_chk.blockSignals(True)
        try:
            if total == 0:
                header_chk.setChecked(False)
            elif checked == total:
                header_chk.setChecked(True)
            else:
                header_chk.setChecked(False)
        finally:
            header_chk.blockSignals(False)

    def _pack_select_all(self):
        """[兼容入口] 把三栏的'全选'复选框全部勾上。

        新 UI 已把全选收敛到每栏顶部独立的复选框，但保留此方法供
        老测试 / 外部脚本继续调用，避免破坏向后兼容。
        """
        for lst in (self.pack_tool_list, self.pack_skill_list,
                    self.pack_rule_list):
            chk = getattr(lst, '_pack_select_all_chk', None)
            if chk is not None:
                chk.setChecked(True)
            else:
                # 极端兜底：栏顶 checkbox 不存在时直接遍历
                self._pack_toggle_section(lst, QtCore.Qt.Checked)

    def _pack_clear(self):
        """[兼容入口] 清空三栏的所有勾选。"""
        for lst in (self.pack_tool_list, self.pack_skill_list,
                    self.pack_rule_list):
            chk = getattr(lst, '_pack_select_all_chk', None)
            if chk is not None:
                chk.setChecked(False)
            else:
                self._pack_toggle_section(lst, QtCore.Qt.Unchecked)

    @staticmethod
    def _collect_checked_data(lst):
        # type: (QtWidgets.QListWidget) -> list
        out = []
        for i in range(lst.count()):
            it = lst.item(i)
            if not (it.flags() & QtCore.Qt.ItemIsUserCheckable):
                continue
            if it.checkState() == QtCore.Qt.Checked:
                out.append(it.data(QtCore.Qt.UserRole))
        return out

    def _on_pack_export(self):
        tools = self._collect_checked_data(self.pack_tool_list)
        skills = self._collect_checked_data(self.pack_skill_list)
        rules = self._collect_checked_data(self.pack_rule_list)
        if not (tools or skills or rules):
            QtWidgets.QMessageBox.information(
                self, '未选择',
                '请先在三个列表里勾选要导出的资源（至少一个）。',
            )
            return
        # 选保存路径
        from .. import pack as pack_mod
        suggested = (self.pack_name_edit.text().strip()
                     or 'maxagent_export') + pack_mod.PACK_SUFFIX
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self, '保存资源包', suggested,
            'MaxAgent Pack (*{})'.format(pack_mod.PACK_SUFFIX),
        )
        if not path:
            return
        if not path.endswith(pack_mod.PACK_SUFFIX):
            path += pack_mod.PACK_SUFFIX
        try:
            res = pack_mod.export_pack(
                output_path=path,
                tool_names=tools,
                skill_names=skills,
                rule_ids=rules,
                pack_name=self.pack_name_edit.text().strip(),
                description=self.pack_desc_edit.text().strip(),
                author=self.pack_author_edit.text().strip(),
            )
        except pack_mod.PackError as exc:
            QtWidgets.QMessageBox.warning(self, '导出失败', str(exc))
            return
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('导出资源包失败')
            QtWidgets.QMessageBox.critical(self, '导出异常', str(exc))
            return
        size_kb = max(1, res['size'] // 1024)
        QtWidgets.QMessageBox.information(
            self, '导出成功',
            '已写入: {}\n\n'
            '工具 {} 个 / 技能 {} 个 / 规则 {} 个\n大小: {} KB'.format(
                res['path'],
                len(res['tools']),
                len(res['skills']),
                len(res['rules']),
                size_kb,
            ),
        )

    def _on_pack_import(self):
        from .. import pack as pack_mod
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self, '选择资源包', '',
            'MaxAgent Pack (*{} *.zip)'.format(pack_mod.PACK_SUFFIX),
        )
        if not path:
            return
        try:
            parsed = pack_mod.parse_pack(path)
        except pack_mod.PackError as exc:
            QtWidgets.QMessageBox.warning(self, '解析失败', str(exc))
            return
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('解析资源包失败')
            QtWidgets.QMessageBox.critical(self, '解析异常', str(exc))
            return

        from .pack_dialog import PackImportDialog
        dlg = PackImportDialog(parsed, path, parent=self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        sel = dlg.selection() or {}
        try:
            summary = pack_mod.import_pack(
                pack_path=path,
                selected_tools=sel.get('tools'),
                selected_skills=sel.get('skills'),
                selected_rules=sel.get('rules'),
                overwrite=bool(sel.get('overwrite', False)),
            )
        except pack_mod.PackError as exc:
            QtWidgets.QMessageBox.warning(self, '导入失败', str(exc))
            return
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception('导入资源包失败')
            QtWidgets.QMessageBox.critical(self, '导入异常', str(exc))
            return

        # 汇总结果
        def _fmt(group):
            parts = []
            for k in ('imported', 'overwritten', 'skipped'):
                v = group.get(k) or []
                if v:
                    parts.append('{}={}'.format(k, len(v)))
            errs = group.get('errors') or []
            if errs:
                parts.append('错误={}'.format(len(errs)))
            return '、'.join(parts) or '无'

        msg = (
            '工具: {}\n技能: {}\n规则: {}'.format(
                _fmt(summary['tools']),
                _fmt(summary['skills']),
                _fmt(summary['rules']),
            )
        )
        # 列出错误细节（前 5 条）
        all_errs = (
            summary['tools'].get('errors', [])
            + summary['skills'].get('errors', [])
            + summary['rules'].get('errors', [])
        )
        if all_errs:
            msg += '\n\n错误详情（前 5 条）:'
            for e in all_errs[:5]:
                key = e.get('name') or e.get('rule_id') or '?'
                msg += '\n· {}: {}'.format(key, e.get('reason', ''))
        QtWidgets.QMessageBox.information(self, '导入完成', msg)
        self._reload_pack_lists()

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
            '<h4>🖼️ 图片与视觉</h4>'

            '<p><b>① 插入图片</b>（4 种方式任选）：'
            '<br>· 📎 工具栏「选图」按钮 → 文件对话框'
            '<br>· ✂️ 截图工具 → 自动入栏'
            '<br>· <code>Ctrl+V</code> 直接粘贴剪贴板图片'
            '<br>· 直接拖拽图片文件 / 网页缩略图到输入框</p>'

            '<p><b>② 多模态识别 = 三道开关同时打开</b>：'
            '<br>· <b>应用设置 → 启用图片视觉</b>（全局总开关）'
            '<br>· <b>当前 Profile 模型</b>命中下方"视觉模型白名单"任一子串'
            '<br>· <b>模型本身确实支持</b> OpenAI <code>image_url</code> 协议'
            '<br>三者全开 → 图片以原图发送给 LLM；任一不满足 → 自动降级为'
            '<code>[图片] N 张</code> 文本占位，输入框上方显示黄色提示条，'
            '可一键切换到支持视觉的 Profile。</p>'

            '<p><b>③ 视觉模型白名单</b>（应用设置页可编辑）：'
            '<br>· 每行一个<b>子串</b>，子串匹配且不区分大小写'
            '<br>· 内置覆盖 <code>gpt-4o</code> / <code>claude-3+</code>'
            ' / <code>gemini-1.5+</code> / <code>qwen-vl</code> /'
            ' <code>glm-4v</code> / <code>internvl</code> /'
            ' <code>youtu-vita</code> 等主流模型'
            '<br>· 新机型上线？直接添加一行子串即可识别（如新品牌的'
            ' <code>llava-next</code>）'
            '<br>· 修改即时生效，无需重启；一键「🔄 恢复默认」可回到出厂值</p>'

            '<p><b>④ 气泡里的图片操作</b>：'
            '<br>· 用户气泡：右键 → 复制图片 / 复制路径 / 另存为 / 查看大图'
            '<br>· 助手气泡：图片可点击放大查看'
            '<br>· 方便粘贴到 Word / 微信 / PS 工作流</p>'

            '<p><b>⑤ 测试连接对视觉模型的特殊行为</b>：'
            '<br>· 检测到当前 Profile 模型命中视觉白名单时，'
            '「🔌 测试连接」会<b>自动附带一张 8×8 灰色占位 PNG</b>，'
            '让 vita / claude vision 等"必须含 image_url"的网关也能握手成功'
            '<br>· 「✅ 完整测试」对视觉模型会<b>跳过 tools 字段</b>，'
            '避开 tokenhub 系网关对 tools 敏感导致 400 invalid_params 的坑'
            '<br>· 状态文案带<b>"（视觉）"</b>标识，方便区分进入了哪条路径</p>'

            '<hr>'

            # ---- 对话面板使用技巧 ----
            '<h4>对话面板使用技巧</h4>'
            '<p><b>调整对话区/输入区比例</b>：拖动两区之间的横向分隔条。'
            '<br>· <b>向下拖</b>（输入区收缩）：聊天区变大，原可见消息保持可见。'
            '<br>· <b>向上拖</b>（输入区扩张，方便编辑长 prompt）：'
            '如果你拖动前正<b>停在底部</b>看最新消息，'
            '面板会自动滚回底部，<b>不会</b>把最新消息挤出可见区；'
            '如果你正在<b>翻历史</b>，则保持原位置不打扰阅读。</p>'
            '<p><b>气泡操作</b>：'
            '<br>· 用户气泡里的图片右键 → 复制 / 路径 / 另存 / 查看大图'
            '<br>· 助手气泡的工具调用块可<b>展开/折叠</b>查看入参出参'
            '<br>· 顶部「🗜 压缩」按钮：让 LLM 总结早期对话替换为摘要，'
            '保留最近 2 轮，长对话省 token。</p>'
            '<p><b>智能克制</b>：本助手已内置「字面理解铁律」——'
            '你说"创建一个球"就只创建球，<b>不会</b>顺手补灯/相机/材质；'
            '需要完整场景请明确说"完整场景"、"打光"、"加摄像机"等关键词。'
            '工具完成后会立即给出确认，避免越做越多失控。</p>'

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

            # ---- 我的资源：规则 / 技能 / 工具 / 导入导出 ----
            '<h4>我的资源 📦</h4>'
            '<p>这一个 Tab 集中管理你为 AI 准备的 <b>规则 / 技能 / 工具</b>，'
            '内部用横向子 Tab 切换 4 个视图：</p>'
            '<table>'
            '<tr><th>子 Tab</th><th>用途</th></tr>'
            '<tr><td><b>规则</b></td>'
            '<td>从对话中沉淀的 LLM 行为规则；查看 / 启停 / 删除</td></tr>'
            '<tr><td><b>技能</b></td>'
            '<td>触发关键词命中时注入的流程模板；查看 / 启停 / 删除</td></tr>'
            '<tr><td><b>工具</b></td>'
            '<td>对话中"学习"出来的可执行 Python 工具；'
            '查看源码 / 启停 / 删除</td></tr>'
            '<tr><td><b>导入/导出</b></td>'
            '<td>三类资源<b>统一打包</b>为 <code>.maxagent-pack</code>'
            '，跨设备同步 / 团队分享</td></tr>'
            '</table>'
            '<p>三个管理子页底部按钮已对齐，统一为'
            '<b>查看 / 启用-禁用 / 删除 / 刷新</b>四颗按钮，'
            '所有导入导出能力都收敛到「导入/导出」子 Tab，避免分散。</p>'
            '<p><b>启用 / 禁用语义（关键）</b>：勾选项即"启用"，'
            '取消勾选即"禁用"——也可选中后点底部'
            '<b>启用/禁用</b>按钮翻转。'
            '<b>禁用后 LLM 完全感知不到该资源</b>：'
            '工具不会出现在 schema、技能不会出现在简介或被关键词触发、'
            '规则不会进入 system prompt。但磁盘文件保留，'
            '随时可重新启用。禁用名单存于 '
            '<code>{config_dir}/disabled.json</code>，'
            '删除该文件即可一键恢复全部。</p>'

            '<p><b>跨设备同步 / 团队分享</b>：</p>'
            '<p>① 打开「我的资源 → 导入/导出」子 Tab'
            '<br>② 每栏顶部独立的「<b>全选</b>」复选框可一键勾上该栏所有项；'
            '也可手动勾选个别项目<br>'
            '③ 填写包名 / 作者 / 描述（可选） → 点「<b>导出选中…</b>」'
            '<br>④ 对方点「<b>导入资源包…</b>」→ 在预览对话框勾选要采纳的项目'
            '<br>⑤ 同名冲突时勾选「覆盖」可强制更新，不勾默认跳过</p>'

            '<p class="warn"><b>⚠ 安全提示</b>：</p>'
            '<p>· 自定义工具是<b>可执行 Python 代码</b>，会在 Max 内运行——'
            '只导入<b>信任来源</b>的资源包；'
            '<br>· 包<b>不</b>含 API Key / Profile 配置 / 会话历史，避免敏感信息泄露；'
            '<br>· 包<b>不</b>含启用/禁用状态——导入到对方机器后默认全部启用，'
            '让对方自行决定要不要某项；'
            '<br>· 导入对话框对工具会强制二次确认，并按 '
            '<code>new / existing / invalid</code> 颜色标注每条状态。</p>'

            '<hr>'

            # ---- 日志 / 测试 ----
            '<h4>日志与诊断</h4>'
            '<p><b>日志 Tab</b>：三态切换 <b>关闭 / 开启 / DEBUG</b>。'
            'DEBUG 级别下会全链路打印 LLM 请求 / 工具调用 / 截图 / '
            '附件操作 / 线程切换 / UI 信号延迟 / Bridge 连接与方法分发，'
            '方便排查偶发问题。'
            '日志只写文件不进控制台，路径见日志页底部。</p>'
            '<p><b>测试连接</b>：仅 ping，验证 base_url + key 基本可达。'
            '<br><b>完整测试</b>：复刻真实对话请求'
            '（流式 + 全部工具 schema + <b>真实 system prompt</b>），'
            '用于排查"测试连接通过但实际对话失败"类问题。'
            '<br>失败时错误信息可<b>鼠标选中复制</b>（含 HTTP code / body / '
            'request-id / 关键 headers），方便排查"测试通过、对话失败"差异。</p>'

            '<p><b>🔄 恢复默认</b>：把当前 Profile 字段一键重置为 OpenAI '
            '兼容出厂模板。'
            '<br>· 名称 / 模型 → 留空，请你重填'
            '<br>· Base URL → <code>https://api.openai.com/v1</code>'
            '（与 DeepSeek / Moonshot / 智谱 / 自建 vllm 等绝大多数 OpenAI '
            '兼容网关开箱可用）'
            '<br>· API Key → <b>保留不变</b>（避免误清密钥）'
            '<br>· 其他参数（温度 / token / 超时 / 工具上限 / 流式 / Function '
            'Calling / 自定义 Header）→ 全部回到默认值'
            '<br>注：仅修改表单显示，需点击「应用」才会写盘——避免误把'
            '名称为空的 Profile 强行落盘破坏配置。</p>'
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

        # ---- 视觉白名单（每行一个） ---- #
        wl_list = list(getattr(cfg, 'vision_model_whitelist', []) or [])
        text = '\n'.join(wl_list)
        # 阻止 textChanged 触发"用户改设置"路径
        self.vision_whitelist_edit.blockSignals(True)
        self.vision_whitelist_edit.setPlainText(text)
        self.vision_whitelist_edit.blockSignals(False)

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

    # 1×1 灰色 PNG 占位图（约 100 字节）。
    # 设计目的：tokenhub 系视觉模型（如 youtu-vita）会在收到纯文本
    # messages 时返回 400 invalid_params，导致用户哪怕配置完全正确，
    # 点"测试连接"也永远是红叉。给视觉模型自动塞一张极小占位图，
    # 让握手能成功，按钮的判据才有意义。
    # 用 8×8 而非 1×1 是为了避免某些后端把 1×1 视为"无效图像"。
    _VISION_PLACEHOLDER_PNG_B64 = (
        'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAA'
        'GUlEQVR4nGNgYGD4z0AEYBpVSF+FRDsTAGBdAQHO7+hpAAAAAElFTkSuQmCC'
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
            self.test_label.setText('{} 连接失败: {}'.format(_ee('❌'), err_text))
            self.test_label.setStyleSheet('color:#e57373;')
            self.test_label.setToolTip(err_text)
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
                label = sk.name + status_tag + (
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
                label = name + ('  —  ' + desc if desc else '')
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
