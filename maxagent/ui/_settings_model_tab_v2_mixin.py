#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置页 · 模型 tab 的 V2 版本 mixin。

方案 Y（激进重构）落地：
- 左侧：运营商列表（Provider 粒度，一个运营商可挂多个模型）
- 右侧上半：运营商属性（Name / Base URL / API Key / Headers）
- 右侧中段：该运营商下的模型清单（可加/删/拉取/编辑覆盖参数）
- 右侧下半：运营商默认参数 + 一键共享

对话窗口顶部下拉：仍展示 <Provider> / <Model>，切换即用。

数据模型：走 config.Provider + config.ModelEntry；老 profiles 字段
仍保留在磁盘上作只读快照。为了老 test 兼容，本 mixin 会同步维护
`self.profile_list`（隐藏占位）与 `self.name_edit / base_url_edit /
api_key_edit / model_edit / temperature_spin / ...` 别名，让老代码
路径不崩。

被 mix 到 SettingsDialog；不单独实例化。
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from typing import List, Optional

from ..qt_compat import QtCore, QtGui, QtWidgets  # noqa: F401
from ..config import (
    BUILTIN_PROVIDER_PRESETS,
    ModelEntry,
    Provider,
)


def _btn_no_default(btn):
    """把按钮从 form 的 default/回车触发链里踢出去。"""
    btn.setAutoDefault(False)
    btn.setDefault(False)
    btn.setFocusPolicy(QtCore.Qt.NoFocus)
    return btn


class SettingsModelTabV2Mixin(object):
    """模型 tab V2 —— 直接以运营商为一等公民。"""

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_page_model_v2(self):
        # type: () -> QtWidgets.QWidget
        page = QtWidgets.QWidget()
        outer_v = QtWidgets.QVBoxLayout(page)
        outer_v.setContentsMargins(0, 0, 0, 0)
        outer_v.setSpacing(6)

        # ── 顶部：快速接入（内置预设一键新建运营商）
        quick_row = QtWidgets.QHBoxLayout()
        quick_row.setContentsMargins(0, 0, 0, 0)
        quick_row.setSpacing(4)
        quick_row.addWidget(QtWidgets.QLabel('快速接入:'))
        for preset in BUILTIN_PROVIDER_PRESETS:
            name = preset.get('name') or ''
            btn = QtWidgets.QPushButton('+ ' + name)
            btn.setToolTip(
                '新建一个运营商，Base URL 预填为 {}\n'
                '你需要手动填 API Key。'.format(preset.get('base_url') or '')
            )
            _btn_no_default(btn)
            btn.clicked.connect(
                lambda _=False, p=preset:
                self._on_quick_add_provider_v2(p),
            )
            quick_row.addWidget(btn)
        quick_row.addStretch(1)
        outer_v.addLayout(quick_row)

        # ── 主体：左右分栏
        main = QtWidgets.QHBoxLayout()
        main.setSpacing(8)

        # ---- 左：运营商列表 ---- #
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(QtWidgets.QLabel('运营商'))

        self.provider_list = QtWidgets.QListWidget()
        self.provider_list.setMinimumWidth(180)
        self.provider_list.currentItemChanged.connect(
            self._on_provider_selected_v2,
        )
        left.addWidget(self.provider_list, 1)

        left_btns = QtWidgets.QHBoxLayout()
        self.add_provider_btn = QtWidgets.QPushButton('＋ 新建')
        _btn_no_default(self.add_provider_btn)
        self.add_provider_btn.clicked.connect(self._add_provider_blank_v2)
        left_btns.addWidget(self.add_provider_btn)

        self.del_provider_btn = QtWidgets.QPushButton('✕ 删除')
        _btn_no_default(self.del_provider_btn)
        self.del_provider_btn.clicked.connect(self._del_provider_v2)
        left_btns.addWidget(self.del_provider_btn)
        left.addLayout(left_btns)

        left_hint = QtWidgets.QLabel(
            '提示：右上"+ Moonshot"等按钮一键接入常见运营商',
        )
        left_hint.setStyleSheet('color:#888; font-size:11px;')
        left_hint.setWordWrap(True)
        left.addWidget(left_hint)

        left_widget = QtWidgets.QWidget()
        left_widget.setLayout(left)
        main.addWidget(left_widget, 1)

        # ---- 右：详情区 ---- #
        right_v = QtWidgets.QVBoxLayout()
        right_v.setSpacing(8)

        # 右上：运营商基本属性
        prov_group = QtWidgets.QGroupBox('运营商属性')
        prov_form = QtWidgets.QFormLayout(prov_group)
        prov_form.setLabelAlignment(QtCore.Qt.AlignRight)

        self.provider_name_edit = QtWidgets.QLineEdit()
        self.provider_name_edit.setPlaceholderText('如: Moonshot')
        prov_form.addRow('名称:', self.provider_name_edit)

        # base_url + 预设下拉
        url_row = QtWidgets.QHBoxLayout()
        url_row.setContentsMargins(0, 0, 0, 0)
        url_row.setSpacing(4)
        self.provider_base_url_edit = QtWidgets.QLineEdit()
        self.provider_base_url_edit.setPlaceholderText(
            '如: https://api.moonshot.cn/v1',
        )
        url_row.addWidget(self.provider_base_url_edit, 1)
        self.preset_btn = QtWidgets.QPushButton('▼ 预设')
        _btn_no_default(self.preset_btn)
        self.preset_btn.setToolTip(
            '从内置运营商预设中选择，自动填充 Base URL',
        )
        self.preset_btn.clicked.connect(self._on_preset_clicked_v2)
        url_row.addWidget(self.preset_btn)
        url_row_widget = QtWidgets.QWidget()
        url_row_widget.setLayout(url_row)
        prov_form.addRow('Base URL:', url_row_widget)

        # API Key + 显示
        key_row = QtWidgets.QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(4)
        self.provider_api_key_edit = QtWidgets.QLineEdit()
        self.provider_api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.provider_api_key_edit.setPlaceholderText(
            '本地模型可留空',
        )
        key_row.addWidget(self.provider_api_key_edit, 1)
        self.show_key_btn = QtWidgets.QPushButton('👁 显示')
        self.show_key_btn.setCheckable(True)
        _btn_no_default(self.show_key_btn)
        # 视觉反馈：checked 时橙红提示"敏感态"（与老 UI 保持一致的
        # 语义，方便 test 检测 :checked 样式）
        self.show_key_btn.setStyleSheet(
            'QPushButton:checked {'
            ' background: #c0392b; color: #fff; font-weight: bold;'
            '}'
        )
        self.show_key_btn.toggled.connect(self._toggle_key_visible_v2)
        key_row.addWidget(self.show_key_btn)
        key_row_widget = QtWidgets.QWidget()
        key_row_widget.setLayout(key_row)
        prov_form.addRow('API Key:', key_row_widget)

        self.provider_headers_edit = QtWidgets.QPlainTextEdit()
        self.provider_headers_edit.setPlaceholderText(
            '可选：每行一个 KEY=VALUE\nX-Org-Id=foo',
        )
        self.provider_headers_edit.setMaximumHeight(80)
        prov_form.addRow('自定义 Header:', self.provider_headers_edit)

        # 测试连接结果
        self.test_label = QtWidgets.QLabel('')
        self.test_label.setStyleSheet('color:#888;')
        self.test_label.setWordWrap(True)
        self.test_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            | QtCore.Qt.TextInteractionFlag.TextSelectableByKeyboard,
        )
        prov_form.addRow('', self.test_label)

        right_v.addWidget(prov_group)

        # 右中：模型列表
        model_group = QtWidgets.QGroupBox('该运营商下的模型')
        model_v = QtWidgets.QVBoxLayout(model_group)
        model_v.setSpacing(4)

        self.model_list = QtWidgets.QListWidget()
        self.model_list.setMinimumHeight(100)
        self.model_list.setMaximumHeight(180)
        self.model_list.itemDoubleClicked.connect(
            self._on_model_double_clicked_v2,
        )
        self.model_list.currentItemChanged.connect(
            self._on_model_selected_v2,
        )
        model_v.addWidget(self.model_list, 1)

        model_btns = QtWidgets.QHBoxLayout()
        self.add_model_btn = QtWidgets.QPushButton('+ 添加模型')
        _btn_no_default(self.add_model_btn)
        self.add_model_btn.clicked.connect(self._add_model_v2)
        model_btns.addWidget(self.add_model_btn)

        self.del_model_btn = QtWidgets.QPushButton('✕ 删除')
        _btn_no_default(self.del_model_btn)
        self.del_model_btn.clicked.connect(self._del_model_v2)
        model_btns.addWidget(self.del_model_btn)

        self.set_default_model_btn = QtWidgets.QPushButton('★ 设为默认')
        _btn_no_default(self.set_default_model_btn)
        self.set_default_model_btn.setToolTip(
            '把选中的模型设为该运营商的激活模型',
        )
        self.set_default_model_btn.clicked.connect(
            self._set_default_model_v2,
        )
        model_btns.addWidget(self.set_default_model_btn)

        self.fetch_models_btn = QtWidgets.QPushButton('↻ 从 API 拉取')
        _btn_no_default(self.fetch_models_btn)
        self.fetch_models_btn.setToolTip(
            '从当前 Base URL 拉取模型清单，勾选后批量加入',
        )
        self.fetch_models_btn.clicked.connect(
            self._on_fetch_models_clicked_v2,
        )
        model_btns.addWidget(self.fetch_models_btn)
        model_btns.addStretch(1)
        model_v.addLayout(model_btns)

        right_v.addWidget(model_group)

        # 右下：运营商默认参数
        param_group = QtWidgets.QGroupBox('运营商默认参数（该运营商所有模型共享，除非模型单独覆盖）')
        param_form = QtWidgets.QFormLayout(param_group)
        param_form.setLabelAlignment(QtCore.Qt.AlignRight)

        # 温度 + 锁 1
        temp_row = QtWidgets.QHBoxLayout()
        temp_row.setContentsMargins(0, 0, 0, 0)
        temp_row.setSpacing(6)
        self.provider_temperature_spin = QtWidgets.QDoubleSpinBox()
        self.provider_temperature_spin.setRange(0.0, 2.0)
        self.provider_temperature_spin.setSingleStep(0.1)
        self.provider_temperature_spin.setValue(0.7)
        temp_row.addWidget(self.provider_temperature_spin)
        self.provider_force_temp_one_chk = QtWidgets.QCheckBox('锁定 1.0')
        self.provider_force_temp_one_chk.setToolTip(
            '部分模型（如 Moonshot kimi-k3）只接受 temperature=1',
        )
        temp_row.addWidget(self.provider_force_temp_one_chk)
        temp_row.addStretch(1)
        temp_row_widget = QtWidgets.QWidget()
        temp_row_widget.setLayout(temp_row)
        param_form.addRow('温度:', temp_row_widget)

        self.provider_max_tokens_spin = QtWidgets.QSpinBox()
        self.provider_max_tokens_spin.setRange(0, 200000)
        self.provider_max_tokens_spin.setSingleStep(256)
        self.provider_max_tokens_spin.setSpecialValueText('(由模型决定)')
        param_form.addRow('最大输出 token:', self.provider_max_tokens_spin)

        self.provider_timeout_spin = QtWidgets.QSpinBox()
        self.provider_timeout_spin.setRange(5, 3600)
        self.provider_timeout_spin.setValue(120)
        self.provider_timeout_spin.setSuffix(' 秒')
        param_form.addRow('请求超时:', self.provider_timeout_spin)

        self.provider_max_loops_spin = QtWidgets.QSpinBox()
        self.provider_max_loops_spin.setRange(4, 200)
        self.provider_max_loops_spin.setValue(40)
        self.provider_max_loops_spin.setSuffix(' 轮')
        param_form.addRow('工具调用上限:', self.provider_max_loops_spin)

        self.provider_max_history_tokens_spin = QtWidgets.QSpinBox()
        self.provider_max_history_tokens_spin.setRange(2000, 200000)
        self.provider_max_history_tokens_spin.setSingleStep(2000)
        self.provider_max_history_tokens_spin.setValue(32000)
        self.provider_max_history_tokens_spin.setSuffix(' tokens')
        param_form.addRow(
            '历史 token 预算:', self.provider_max_history_tokens_spin,
        )

        cap_row = QtWidgets.QHBoxLayout()
        cap_row.setContentsMargins(0, 0, 0, 0)
        cap_row.setSpacing(12)
        self.provider_stream_chk = QtWidgets.QCheckBox('流式')
        self.provider_stream_chk.setChecked(True)
        cap_row.addWidget(self.provider_stream_chk)
        self.provider_tools_chk = QtWidgets.QCheckBox('Function Calling')
        self.provider_tools_chk.setChecked(True)
        self.provider_tools_chk.setToolTip(
            '关闭后本运营商的对话不会携带 tools / tool_choice 字段。\n'
            '什么时候关：\n'
            '· 视觉专用模型（youtu-vita、qwen-vl 等）\n'
            '· 网关返回 "model engine error" / 502 upstream\n'
            '· 模型本身不支持 OpenAI Function Calling 协议\n'
            '关闭后 LLM 不会再调用任何工具，纯对话模式。',
        )
        cap_row.addWidget(self.provider_tools_chk)
        self.provider_vision_chk = QtWidgets.QCheckBox('视觉输入')
        cap_row.addWidget(self.provider_vision_chk)
        cap_row.addStretch(1)
        cap_row_widget = QtWidgets.QWidget()
        cap_row_widget.setLayout(cap_row)
        param_form.addRow('能力:', cap_row_widget)

        self.share_to_provider_btn = QtWidgets.QPushButton(
            '⇢ 应用到该运营商所有模型（清除单独覆盖）',
        )
        _btn_no_default(self.share_to_provider_btn)
        self.share_to_provider_btn.setToolTip(
            '把上面的默认参数一次性写到该运营商每个模型上，\n'
            '清除各模型之前的独立覆盖，让所有模型走统一参数。',
        )
        self.share_to_provider_btn.clicked.connect(
            self._on_share_to_provider_v2,
        )
        param_form.addRow('', self.share_to_provider_btn)

        right_v.addWidget(param_group)

        # 右下操作按钮
        op_row = QtWidgets.QHBoxLayout()
        op_row.setSpacing(6)
        op_row.addStretch(1)

        self.test_btn = QtWidgets.QPushButton('🔌 测试连接')
        _btn_no_default(self.test_btn)
        self.test_btn.setToolTip('发一次最简 ping，验证 Base URL + Key 可达')
        self.test_btn.clicked.connect(self._test_connection_v2)
        op_row.addWidget(self.test_btn)

        self.test_full_btn = QtWidgets.QPushButton('✅ 完整测试')
        _btn_no_default(self.test_full_btn)
        self.test_full_btn.setToolTip('携带流式 + tools schema 的完整请求')
        self.test_full_btn.clicked.connect(self._test_connection_full_v2)
        op_row.addWidget(self.test_full_btn)

        self.apply_btn = QtWidgets.QPushButton('💾 应用')
        self.apply_btn.setAutoDefault(True)
        self.apply_btn.setDefault(True)
        self.apply_btn.setToolTip('保存当前运营商修改')
        self.apply_btn.clicked.connect(self._apply_provider_v2)
        op_row.addWidget(self.apply_btn)

        right_v.addLayout(op_row)

        right_widget = QtWidgets.QWidget()
        right_widget.setLayout(right_v)
        main.addWidget(right_widget, 2)

        outer_v.addLayout(main, 1)

        # ---- legacy 兼容别名（供老 test / 老方法引用）---- #
        # 老 test 大量用这些名字，语义映射到新控件（编辑当前运营商 =
        # 编辑单个 llm 的近似语义）。少量结构差异的老 test 已 skip。
        self.name_edit = self.provider_name_edit
        self.base_url_edit = self.provider_base_url_edit
        self.api_key_edit = self.provider_api_key_edit
        self.model_edit = QtWidgets.QLineEdit()  # 隐藏占位，保留 API
        self.headers_edit = self.provider_headers_edit
        self.temperature_spin = self.provider_temperature_spin
        self.force_temp_one_chk = self.provider_force_temp_one_chk
        self.max_tokens_spin = self.provider_max_tokens_spin
        self.timeout_spin = self.provider_timeout_spin
        self.max_loops_spin = self.provider_max_loops_spin
        self.max_history_tokens_spin = self.provider_max_history_tokens_spin
        self.stream_chk = self.provider_stream_chk
        self.tools_chk = self.provider_tools_chk
        self.vision_supported_chk = self.provider_vision_chk
        # 老 add/del 按钮别名 → v2 里对应"新建/删除运营商"
        self.add_btn = self.add_provider_btn
        self.del_btn = self.del_provider_btn
        # profile_list：老 test 会用它 iterate；这里也保留一个隐藏占位
        # 内容与 provider_list 语义不同（老是 profile，新是 provider），
        # 引用它的老 test 大多标记为 skip
        self.profile_list = QtWidgets.QListWidget()
        self.profile_list.hide()
        # fallback_list：新 UI 暂不做备用 profile 链（可通过多运营商切换
        # 实现），保留隐藏占位避免 _apply / _reload 引用崩溃
        self.fallback_list = QtWidgets.QListWidget()
        self.fallback_list.hide()
        # 老 base_url_hint：新 UI 里没画，保留 QLabel 占位
        self.base_url_hint = QtWidgets.QLabel('')
        self.base_url_hint.hide()
        # 老 provider_hint_label / reset_default_btn：新 UI 无对应位置
        self.provider_hint_label = QtWidgets.QLabel('')
        self.provider_hint_label.hide()
        self.reset_default_btn = QtWidgets.QPushButton('恢复默认')
        self.reset_default_btn.setAutoDefault(False)
        self.reset_default_btn.setDefault(False)
        self.reset_default_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self.reset_default_btn.hide()

        return page

    # ------------------------------------------------------------------ #
    # 内部：从数据加载到 UI
    # ------------------------------------------------------------------ #
    def _reload_providers_v2(self):
        """按当前 config 刷新左侧运营商列表。"""
        self.provider_list.blockSignals(True)
        self.provider_list.clear()
        providers = self._config.list_providers()
        active = self._config.get_active_model_ref()
        active_pid = active[0] if active else None
        target_row = 0
        for i, p in enumerate(providers):
            item = QtWidgets.QListWidgetItem(p.name)
            item.setData(QtCore.Qt.UserRole, p.id)
            self.provider_list.addItem(item)
            if p.id == active_pid:
                target_row = i
        self.provider_list.blockSignals(False)
        if self.provider_list.count() > 0:
            self.provider_list.setCurrentRow(target_row)
        else:
            # 无运营商：清空右侧表单
            self._clear_provider_form_v2()

    def _current_provider_v2(self):
        # type: () -> Optional[Provider]
        item = self.provider_list.currentItem()
        if item is None:
            return None
        pid = item.data(QtCore.Qt.UserRole)
        for p in self._config.list_providers():
            if p.id == pid:
                return p
        return None

    def _current_model_entry_v2(self):
        # type: () -> Optional[ModelEntry]
        p = self._current_provider_v2()
        if p is None:
            return None
        item = self.model_list.currentItem()
        if item is None:
            return None
        mid = item.data(QtCore.Qt.UserRole)
        for m in p.models:
            if m.id == mid:
                return m
        return None

    def _clear_provider_form_v2(self):
        """右侧表单清空为空态。"""
        for w in [
            self.provider_name_edit,
            self.provider_base_url_edit,
            self.provider_api_key_edit,
        ]:
            w.blockSignals(True)
            w.setText('')
            w.blockSignals(False)
        self.provider_headers_edit.blockSignals(True)
        self.provider_headers_edit.setPlainText('')
        self.provider_headers_edit.blockSignals(False)
        self.model_list.clear()
        self._load_defaults_to_form_v2({})

    def _load_defaults_to_form_v2(self, defaults):
        # type: (dict) -> None
        self.provider_temperature_spin.setValue(
            float(defaults.get('temperature', 0.7)),
        )
        self.provider_force_temp_one_chk.setChecked(
            bool(defaults.get('force_temperature_one', False)),
        )
        self.provider_max_tokens_spin.setValue(
            int(defaults.get('max_tokens', 0) or 0),
        )
        self.provider_timeout_spin.setValue(
            int(defaults.get('timeout', 120)),
        )
        self.provider_max_loops_spin.setValue(
            int(defaults.get('max_loops', 40)),
        )
        self.provider_max_history_tokens_spin.setValue(
            int(defaults.get('max_history_tokens', 32000)),
        )
        self.provider_stream_chk.setChecked(
            bool(defaults.get('stream', True)),
        )
        self.provider_tools_chk.setChecked(
            bool(defaults.get('tools_enabled', True)),
        )
        self.provider_vision_chk.setChecked(
            bool(defaults.get('vision_supported', False)),
        )

    def _collect_defaults_from_form_v2(self):
        # type: () -> dict
        return {
            'temperature': self.provider_temperature_spin.value(),
            'force_temperature_one': (
                self.provider_force_temp_one_chk.isChecked()
            ),
            'max_tokens': self.provider_max_tokens_spin.value(),
            'timeout': self.provider_timeout_spin.value(),
            'max_loops': self.provider_max_loops_spin.value(),
            'max_history_tokens': (
                self.provider_max_history_tokens_spin.value()
            ),
            'stream': self.provider_stream_chk.isChecked(),
            'tools_enabled': self.provider_tools_chk.isChecked(),
            'vision_supported': self.provider_vision_chk.isChecked(),
        }

    # ------------------------------------------------------------------ #
    # 事件处理
    # ------------------------------------------------------------------ #
    def _on_provider_selected_v2(self, cur, _prev):
        if cur is None:
            self._clear_provider_form_v2()
            return
        p = self._current_provider_v2()
        if p is None:
            return
        self.provider_name_edit.setText(p.name)
        self.provider_base_url_edit.setText(p.base_url or '')
        self.provider_api_key_edit.setText(p.api_key or '')
        # headers
        header_lines = []
        for k, v in (p.extra_headers or {}).items():
            header_lines.append('{}={}'.format(k, v))
        self.provider_headers_edit.setPlainText('\n'.join(header_lines))
        # 参数
        self._load_defaults_to_form_v2(p.defaults or {})
        # 模型列表
        self._reload_models_for_provider_v2(p)
        # 兼容 legacy _apply / _read_form：把当前 provider 的默认模型
        # 同步到隐藏的 model_edit，避免老代码路径 (prof.model 为空)
        # 触发 QMessageBox 阻塞测试。
        active = self._config.get_active_model_ref()
        default_model = ''
        if active and active[0] == p.id:
            for m in p.models:
                if m.id == active[1]:
                    default_model = m.model or ''
                    break
        if not default_model and p.models:
            default_model = p.models[0].model or ''
        self.model_edit.setText(default_model)

    def _reload_models_for_provider_v2(self, provider):
        self.model_list.blockSignals(True)
        self.model_list.clear()
        active = self._config.get_active_model_ref()
        active_mid = None
        if active and active[0] == provider.id:
            active_mid = active[1]
        target_row = 0
        for i, m in enumerate(provider.models):
            label = m.label or m.model or m.id
            mark = '★ ' if m.id == active_mid else '  '
            item = QtWidgets.QListWidgetItem(
                '{}{}   [{}]'.format(mark, label, m.model or ''),
            )
            item.setData(QtCore.Qt.UserRole, m.id)
            self.model_list.addItem(item)
            if m.id == active_mid:
                target_row = i
        self.model_list.blockSignals(False)
        if self.model_list.count() > 0:
            self.model_list.setCurrentRow(target_row)

    def _on_model_selected_v2(self, _cur, _prev):
        # 目前 model 选中只是视觉反馈；参数编辑仍走"运营商默认"
        # 后续可加"编辑该模型独立覆盖"弹窗（TODO）
        pass

    def _on_model_double_clicked_v2(self, _item):
        """双击 = 设为该运营商激活模型。"""
        self._set_default_model_v2()

    def _on_quick_add_provider_v2(self, preset):
        """点顶部预设按钮 → 新建运营商，Base URL 预填。"""
        from .. import config as cfg_mod
        providers = self._config.list_providers()
        preset_name = preset.get('name') or 'Provider'
        # 命名冲突加 #2
        existing_names = {p.name for p in providers}
        name = preset_name
        n = 2
        while name in existing_names:
            name = '{} #{}'.format(preset_name, n)
            n += 1
        pid = cfg_mod._slugify(name) or 'provider_{}'.format(len(providers) + 1)  # noqa: E501
        # 保证 id 唯一
        existing_ids = {p.id for p in providers}
        base_pid = pid
        k = 2
        while pid in existing_ids:
            pid = '{}_{}'.format(base_pid, k)
            k += 1

        new = Provider(
            id=pid,
            name=name,
            base_url=preset.get('base_url') or '',
            api_key='',
            extra_headers={},
            defaults={
                'temperature': 0.7,
                'max_tokens': 0,
                'timeout': 120,
                'max_loops': 40,
                'max_history_tokens': 32000,
                'stream': True,
                'tools_enabled': True,
                'vision_supported': False,
                'force_temperature_one': False,
            },
            models=[],
        )
        providers.append(new)
        self._config.config.providers = providers
        self._config.save()
        self._reload_providers_v2()
        # 定位到新加入的
        for i in range(self.provider_list.count()):
            if self.provider_list.item(i).data(QtCore.Qt.UserRole) == pid:
                self.provider_list.setCurrentRow(i)
                break
        # 光标停在 API Key 上（用户下一步就是填 Key）
        self.provider_api_key_edit.setFocus()

    def _add_provider_blank_v2(self):
        """新建一个空白运营商。"""
        from .. import config as cfg_mod
        providers = self._config.list_providers()
        existing_names = {p.name for p in providers}
        name = 'NewProvider'
        n = 2
        while name in existing_names:
            name = 'NewProvider{}'.format(n)
            n += 1
        pid = cfg_mod._slugify(name)
        existing_ids = {p.id for p in providers}
        k = 2
        base_pid = pid
        while pid in existing_ids:
            pid = '{}_{}'.format(base_pid, k)
            k += 1
        new = Provider(
            id=pid,
            name=name,
            base_url='',
            api_key='',
            extra_headers={},
            defaults={},
            models=[],
        )
        providers.append(new)
        self._config.config.providers = providers
        self._config.save()
        self._reload_providers_v2()
        for i in range(self.provider_list.count()):
            if self.provider_list.item(i).data(QtCore.Qt.UserRole) == pid:
                self.provider_list.setCurrentRow(i)
                break
        self.provider_name_edit.setFocus()
        self.provider_name_edit.selectAll()

    def _del_provider_v2(self):
        p = self._current_provider_v2()
        if p is None:
            return
        ret = QtWidgets.QMessageBox.question(
            self, '确认删除',
            '删除运营商 "{}" 及其下所有模型？'.format(p.name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return
        providers = [
            x for x in self._config.list_providers() if x.id != p.id
        ]
        self._config.config.providers = providers
        # 若删的是激活的，切到剩下的第一个
        active = self._config.get_active_model_ref()
        if active and active[0] == p.id:
            if providers and providers[0].models:
                self._config.set_active_model_ref(
                    providers[0].id, providers[0].models[0].id,
                )
            else:
                self._config.config.active_model_ref = None
        self._config.save()
        self._reload_providers_v2()

    def _apply_provider_v2(self):
        p = self._current_provider_v2()
        if p is None:
            QtWidgets.QMessageBox.warning(
                self, '未选择运营商',
                '请先在左侧选择或新建一个运营商',
            )
            return
        p.name = self.provider_name_edit.text().strip() or p.name
        p.base_url = self.provider_base_url_edit.text().strip()
        p.api_key = self.provider_api_key_edit.text()
        # headers
        headers = {}
        for line in self.provider_headers_edit.toPlainText().splitlines():
            line = line.strip()
            if not line or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip()
            if k:
                headers[k] = v
        p.extra_headers = headers
        p.defaults = self._collect_defaults_from_form_v2()
        self._config.save()
        self._reload_providers_v2()
        # 保持当前选中
        for i in range(self.provider_list.count()):
            if self.provider_list.item(i).data(QtCore.Qt.UserRole) == p.id:
                self.provider_list.setCurrentRow(i)
                break
        self.test_label.setText('✅ 已保存')
        self.test_label.setStyleSheet('color:#4a9;')

    # ------------------------------------------------------------------ #
    # 模型子列表操作
    # ------------------------------------------------------------------ #
    def _add_model_v2(self):
        p = self._current_provider_v2()
        if p is None:
            QtWidgets.QMessageBox.warning(
                self, '未选择运营商',
                '请先在左侧选择或新建一个运营商',
            )
            return
        text, ok = QtWidgets.QInputDialog.getText(
            self, '添加模型',
            '模型名（如 kimi-k2 / gpt-4o-mini）：',
        )
        if not ok:
            return
        model = text.strip()
        if not model:
            return
        existing_ids = {m.id for m in p.models}
        mid = model
        k = 2
        while mid in existing_ids:
            mid = '{}_{}'.format(model, k)
            k += 1
        entry = ModelEntry(
            id=mid, model=model, label=model, overrides={}, default=False,
        )
        p.models.append(entry)
        # 如果这是第一个模型，自动设为激活
        if not self._config.get_active_model_ref():
            self._config.set_active_model_ref(p.id, entry.id)
        self._config.save()
        self._reload_models_for_provider_v2(p)

    def _del_model_v2(self):
        p = self._current_provider_v2()
        m = self._current_model_entry_v2()
        if p is None or m is None:
            return
        ret = QtWidgets.QMessageBox.question(
            self, '确认删除',
            '删除模型 "{}"？'.format(m.label or m.model),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return
        p.models = [x for x in p.models if x.id != m.id]
        active = self._config.get_active_model_ref()
        if active == (p.id, m.id):
            if p.models:
                self._config.set_active_model_ref(p.id, p.models[0].id)
            else:
                # 该 provider 没模型了，切到其它 provider 第一个模型
                for other in self._config.list_providers():
                    if other.id != p.id and other.models:
                        self._config.set_active_model_ref(
                            other.id, other.models[0].id,
                        )
                        break
                else:
                    self._config.config.active_model_ref = None
        self._config.save()
        self._reload_models_for_provider_v2(p)

    def _set_default_model_v2(self):
        p = self._current_provider_v2()
        m = self._current_model_entry_v2()
        if p is None or m is None:
            return
        self._config.set_active_model_ref(p.id, m.id)
        self._config.save()
        self._reload_models_for_provider_v2(p)

    def _on_fetch_models_clicked_v2(self):
        p = self._current_provider_v2()
        if p is None:
            QtWidgets.QMessageBox.information(
                self, '拉取模型', '请先选择运营商',
            )
            return
        # 用当前表单的 URL/Key（而不是磁盘上的），支持"填了还没应用"场景
        base_url = self.provider_base_url_edit.text().strip()
        api_key = self.provider_api_key_edit.text().strip()
        if not base_url:
            QtWidgets.QMessageBox.warning(
                self, '拉取模型', '请先填 Base URL',
            )
            return
        try:
            from ..llm_provider_probe import list_models
            models = list_models(base_url, api_key, timeout=15)
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(
                self, '拉取失败',
                '未能获取模型列表：\n{}\n\n'
                '若为本地模型，请确认服务已启动。'.format(exc),
            )
            return
        if not models:
            QtWidgets.QMessageBox.information(
                self, '拉取模型', '未返回任何模型',
            )
            return
        # 弹选择框
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle('选择要加入的模型 · 共 {} 个'.format(len(models)))
        dlg.resize(400, 500)
        vbox = QtWidgets.QVBoxLayout(dlg)
        vbox.addWidget(QtWidgets.QLabel(
            '勾选后点确定，将批量加入该运营商的模型列表。'
        ))
        existing_ids = {m.model for m in p.models}
        lst = QtWidgets.QListWidget()
        for m in models:
            mid = m.get('id') or ''
            if not mid:
                continue
            it = QtWidgets.QListWidgetItem(mid)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(
                QtCore.Qt.Unchecked if mid in existing_ids
                else QtCore.Qt.Unchecked,
            )
            if mid in existing_ids:
                it.setForeground(QtGui.QColor('#888'))
                it.setToolTip('已存在')
            lst.addItem(it)
        vbox.addWidget(lst, 1)
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        vbox.addWidget(btns)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        added = 0
        for i in range(lst.count()):
            it = lst.item(i)
            if it.checkState() != QtCore.Qt.Checked:
                continue
            model_id = it.text()
            if model_id in existing_ids:
                continue
            entry = ModelEntry(
                id=model_id, model=model_id, label=model_id,
                overrides={}, default=False,
            )
            p.models.append(entry)
            existing_ids.add(model_id)
            added += 1
        if added == 0:
            return
        if not self._config.get_active_model_ref() and p.models:
            self._config.set_active_model_ref(p.id, p.models[0].id)
        self._config.save()
        self._reload_models_for_provider_v2(p)
        self.test_label.setText('✅ 已加入 {} 个模型'.format(added))
        self.test_label.setStyleSheet('color:#4a9;')

    # ------------------------------------------------------------------ #
    # 预设 / 测试 / 共享
    # ------------------------------------------------------------------ #
    def _on_preset_clicked_v2(self):
        menu = QtWidgets.QMenu(self)
        for preset in BUILTIN_PROVIDER_PRESETS:
            name = preset.get('name') or ''
            url = preset.get('base_url') or ''
            act = menu.addAction('{}  ({})'.format(name, url))
            act.triggered.connect(
                lambda _=False, p=preset: self._apply_preset_v2(p)
            )
        menu.exec_(QtGui.QCursor.pos())

    def _apply_preset_v2(self, preset):
        url = preset.get('base_url') or ''
        name = preset.get('name') or ''
        self.provider_base_url_edit.setText(url)
        if not self.provider_name_edit.text().strip():
            self.provider_name_edit.setText(name)

    def _toggle_key_visible_v2(self, checked):
        self.provider_api_key_edit.setEchoMode(
            QtWidgets.QLineEdit.Normal if checked
            else QtWidgets.QLineEdit.Password,
        )
        self.show_key_btn.setText('👁 隐藏' if checked else '👁 显示')

    def _on_share_to_provider_v2(self):
        p = self._current_provider_v2()
        if p is None:
            return
        if not p.models:
            QtWidgets.QMessageBox.information(
                self, '共享参数', '该运营商下暂无模型，无需共享。',
            )
            return
        ret = QtWidgets.QMessageBox.question(
            self, '确认',
            '将清除该运营商下 {} 个模型的独立参数覆盖，'
            '全部改用上面的默认参数。是否继续？'.format(len(p.models)),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return
        p.defaults = self._collect_defaults_from_form_v2()
        for m in p.models:
            m.overrides = {}
        self._config.save()
        self.test_label.setText(
            '✅ 已把默认参数应用到 {} 个模型'.format(len(p.models)),
        )
        self.test_label.setStyleSheet('color:#4a9;')

    def _test_connection_v2(self):
        """转调老的 _test_connection——老逻辑通过 name_edit/base_url_edit 等
        别名读取数据，仍然可用。"""
        try:
            self._test_connection()
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('❌ 测试失败: {}'.format(exc))
            self.test_label.setStyleSheet('color:#c33;')

    def _test_connection_full_v2(self):
        try:
            self._test_connection_full()
        except Exception as exc:  # pylint: disable=broad-except
            self.test_label.setText('❌ 完整测试失败: {}'.format(exc))
            self.test_label.setStyleSheet('color:#c33;')
