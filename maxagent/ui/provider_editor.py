#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 编辑对话框。

让用户在 UI 上可视化地配置一个搜索后端：URL / 方法 / params / headers /
JSON body / API Key / 响应路径。

设计要点：
- 不强制让用户写 JSON：params / headers / body 通过"key=value 一行一条"
  的多行文本框输入，类似 LLM Profile 的 extra_headers 编辑方式，避免
  非技术用户被 JSON 语法吓退。
- 但保留高级模式：响应路径仍是点号字符串（如 ``data.webPages.value``），
  跟主流文档一致。
- 提供"模板"下拉，一键填入 Google CSE / Brave / Tavily 等示例配置。
"""

from __future__ import absolute_import
from __future__ import print_function

import json
from copy import deepcopy
from typing import Any
from typing import Dict
from typing import Optional

from ..logger import get_logger
from ..qt_compat import QtCore
from ..qt_compat import QtWidgets
from ..web_providers import BUILTIN_PROVIDERS
from ..web_providers import validate_id
from .emoji_compat import btn_label as _btn_label


logger = get_logger(__name__)


def _kv_to_text(d):
    # type: (Optional[Dict[str, Any]]) -> str
    """把 dict 转成 ``k=v`` 多行文本，方便用户编辑。"""
    if not d:
        return ''
    lines = []
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            v_str = json.dumps(v, ensure_ascii=False)
        else:
            v_str = str(v) if v is not None else ''
        lines.append('{}={}'.format(k, v_str))
    return '\n'.join(lines)


def _text_to_kv(text):
    # type: (str) -> Dict[str, str]
    """把 ``k=v`` 多行文本解析成 dict，跳过空行和注释。"""
    out = {}
    for line in (text or '').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        out[k.strip()] = v.strip()
    return out


def _json_to_text(d):
    # type: (Optional[Dict[str, Any]]) -> str
    if not d:
        return ''
    try:
        return json.dumps(d, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return ''


def _text_to_json(text):
    # type: (str) -> Dict[str, Any]
    s = (text or '').strip()
    if not s:
        return {}
    try:
        data = json.loads(s)
    except ValueError as exc:
        raise ValueError('JSON 解析失败: {}'.format(exc))
    if not isinstance(data, dict):
        raise ValueError('JSON body 必须是对象（dict），不能是 list/标量')
    return data


class ProviderEditorDialog(QtWidgets.QDialog):
    """编辑或新建一个搜索 Provider。

    :param provider: 要编辑的 provider dict（深拷贝后使用，原对象不会被修改）
    :param allow_id_edit: True 时允许修改 id（仅在新增/复制流程使用）
    """

    def __init__(self, provider, parent=None, allow_id_edit=False):
        # type: (Dict[str, Any], Optional[QtWidgets.QWidget], bool) -> None
        super(ProviderEditorDialog, self).__init__(parent)
        self.setWindowTitle('编辑搜索 Provider')
        self.resize(640, 720)
        self._provider = deepcopy(provider or {})
        self._allow_id_edit = bool(allow_id_edit)
        self._result = None  # type: Optional[Dict[str, Any]]
        self._build_ui()
        self._load_provider(self._provider)

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setSpacing(8)

        # 模板下拉：从 BUILTIN_PROVIDERS 派生
        tpl_row = QtWidgets.QHBoxLayout()
        tpl_row.addWidget(QtWidgets.QLabel('一键模板:'))
        self.tpl_combo = QtWidgets.QComboBox()
        self.tpl_combo.addItem('（不使用模板）', None)
        for builtin in BUILTIN_PROVIDERS:
            self.tpl_combo.addItem(builtin.get('name') or builtin['id'], builtin['id'])
        tpl_apply = QtWidgets.QPushButton(_btn_label('💾', '应用模板'))
        tpl_apply.setToolTip(
            '把模板的 url / params / headers / response 字段填入表单。'
            '\nid / name / api_key 不会被覆盖。',
        )
        tpl_apply.clicked.connect(self._apply_template)
        tpl_row.addWidget(self.tpl_combo, 1)
        tpl_row.addWidget(tpl_apply)
        outer.addLayout(tpl_row)

        # 顶部基础字段
        form = QtWidgets.QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(QtCore.Qt.AlignRight)

        self.id_edit = QtWidgets.QLineEdit()
        self.id_edit.setPlaceholderText('唯一标识，如 google_cse / my_search')
        self.id_edit.setEnabled(self._allow_id_edit)
        form.addRow('ID:', self.id_edit)

        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText('显示名，如 "Google Custom Search"')
        form.addRow('名称:', self.name_edit)

        self.enabled_chk = QtWidgets.QCheckBox('启用此 Provider')
        form.addRow('', self.enabled_chk)

        self.method_combo = QtWidgets.QComboBox()
        self.method_combo.addItems(['GET', 'POST'])
        form.addRow('HTTP 方法:', self.method_combo)

        self.url_edit = QtWidgets.QLineEdit()
        self.url_edit.setPlaceholderText('https://api.example.com/search')
        form.addRow('URL:', self.url_edit)

        self.api_key_edit = QtWidgets.QLineEdit()
        self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_edit.setPlaceholderText(
            '占位符 {{api_key}} 会用此值替换；无 Key 后端可留空',
        )
        form.addRow('API Key:', self.api_key_edit)

        self.timeout_spin = QtWidgets.QDoubleSpinBox()
        self.timeout_spin.setRange(1.0, 60.0)
        self.timeout_spin.setSingleStep(1.0)
        self.timeout_spin.setSuffix(' s')
        form.addRow('超时:', self.timeout_spin)

        outer.addLayout(form)

        # 占位符提示
        hint = QtWidgets.QLabel(
            '占位符可在 params / headers / body / extra 中使用：\n'
            '  {{query}}    - 用户搜索词\n'
            '  {{n}}        - 结果数（字符串，给 query string 用）\n'
            '  {{n_int}}    - 结果数（整数，给 JSON body 用）\n'
            '  {{api_key}}  - 上方 API Key 字段\n'
            '  {{extra.X}}  - extra 区填的自定义字段（如 cx）',
        )
        hint.setStyleSheet('color:#888; font-size:9pt;')
        hint.setWordWrap(True)
        outer.addWidget(hint)

        # Tab 区：params / headers / body / extra / response
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(True)

        # params
        self.params_edit = QtWidgets.QPlainTextEdit()
        self.params_edit.setPlaceholderText(
            '每行 "key=value"，例如：\n'
            'q={{query}}\n'
            'count={{n}}\n'
            'key={{api_key}}',
        )
        self.tabs.addTab(self._wrap_text_edit(self.params_edit), 'Query 参数')

        # headers
        self.headers_edit = QtWidgets.QPlainTextEdit()
        self.headers_edit.setPlaceholderText(
            '每行 "Header-Name=value"，例如：\n'
            'X-Subscription-Token={{api_key}}\n'
            'Authorization=Bearer {{api_key}}',
        )
        self.tabs.addTab(self._wrap_text_edit(self.headers_edit), 'Headers')

        # body
        self.body_edit = QtWidgets.QPlainTextEdit()
        self.body_edit.setPlaceholderText(
            'JSON 对象（仅 POST 时生效），例如：\n'
            '{\n'
            '  "api_key": "{{api_key}}",\n'
            '  "query": "{{query}}",\n'
            '  "max_results": "{{n_int}}"\n'
            '}',
        )
        self.tabs.addTab(self._wrap_text_edit(self.body_edit), 'JSON Body')

        # extra
        self.extra_edit = QtWidgets.QPlainTextEdit()
        self.extra_edit.setPlaceholderText(
            'provider 自定义字段，每行 "key=value"。\n'
            '占位符 {{extra.cx}} 会从这里取值。例如：\n'
            'cx=017abc...',
        )
        self.tabs.addTab(self._wrap_text_edit(self.extra_edit), 'Extra')

        # response
        resp_widget = QtWidgets.QWidget()
        resp_form = QtWidgets.QFormLayout(resp_widget)
        resp_form.setSpacing(6)
        self.html_chk = QtWidgets.QCheckBox(
            '响应是 HTML（非 JSON），按 DDG 模式解析',
        )
        resp_form.addRow('', self.html_chk)
        self.items_path_edit = QtWidgets.QLineEdit()
        self.items_path_edit.setPlaceholderText(
            '如 webPages.value / items / data.results',
        )
        resp_form.addRow('结果列表路径:', self.items_path_edit)
        self.title_path_edit = QtWidgets.QLineEdit()
        self.title_path_edit.setPlaceholderText('如 title / name')
        resp_form.addRow('标题字段:', self.title_path_edit)
        self.url_path_edit = QtWidgets.QLineEdit()
        self.url_path_edit.setPlaceholderText('如 url / link')
        resp_form.addRow('链接字段:', self.url_path_edit)
        self.snippet_path_edit = QtWidgets.QLineEdit()
        self.snippet_path_edit.setPlaceholderText('如 snippet / description / content')
        resp_form.addRow('摘要字段:', self.snippet_path_edit)
        self.tabs.addTab(resp_widget, '响应解析')

        outer.addWidget(self.tabs, 1)

        # 底部按钮
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        ok_btn = QtWidgets.QPushButton(_btn_label('💾', '保存'))
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_ok)
        cancel_btn = QtWidgets.QPushButton('取消')
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        outer.addLayout(btn_row)

    @staticmethod
    def _wrap_text_edit(edit):
        # type: (QtWidgets.QPlainTextEdit) -> QtWidgets.QWidget
        font = edit.font()
        font.setFamily('Consolas, Menlo, monospace')
        edit.setFont(font)
        wrap = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(edit)
        return wrap

    # ------------------------------------------------------------------ #
    def _load_provider(self, prov):
        # type: (Dict[str, Any]) -> None
        self.id_edit.setText(str(prov.get('id') or ''))
        self.name_edit.setText(str(prov.get('name') or ''))
        self.enabled_chk.setChecked(bool(prov.get('enabled', True)))
        method = (prov.get('method') or 'GET').upper()
        self.method_combo.setCurrentText(method if method in ('GET', 'POST') else 'GET')
        self.url_edit.setText(str(prov.get('url') or ''))
        self.api_key_edit.setText(str(prov.get('api_key') or ''))
        try:
            self.timeout_spin.setValue(float(prov.get('timeout_sec') or 8.0))
        except (TypeError, ValueError):
            self.timeout_spin.setValue(8.0)

        self.params_edit.setPlainText(_kv_to_text(prov.get('params')))
        self.headers_edit.setPlainText(_kv_to_text(prov.get('headers')))
        self.body_edit.setPlainText(_json_to_text(prov.get('body_json')))
        self.extra_edit.setPlainText(_kv_to_text(prov.get('extra')))

        resp = prov.get('response') or {}
        self.html_chk.setChecked(bool(resp.get('html_scrape')))
        self.items_path_edit.setText(str(resp.get('items_path') or ''))
        self.title_path_edit.setText(str(resp.get('title_path') or ''))
        self.url_path_edit.setText(str(resp.get('url_path') or ''))
        self.snippet_path_edit.setText(str(resp.get('snippet_path') or ''))

    def _apply_template(self):
        tpl_id = self.tpl_combo.currentData()
        if not tpl_id:
            return
        for builtin in BUILTIN_PROVIDERS:
            if builtin['id'] != tpl_id:
                continue
            tpl = deepcopy(builtin)
            # 保留用户已填的 id / name / api_key
            for keep in ('id', 'name', 'api_key'):
                cur = self._read_form_partial(keep)
                if cur:
                    tpl[keep] = cur
            self._load_provider(tpl)
            return

    def _read_form_partial(self, key):
        # type: (str) -> str
        if key == 'id':
            return self.id_edit.text().strip()
        if key == 'name':
            return self.name_edit.text().strip()
        if key == 'api_key':
            return self.api_key_edit.text()
        return ''

    def _read_form(self):
        # type: () -> Dict[str, Any]
        try:
            body_json = _text_to_json(self.body_edit.toPlainText())
        except ValueError as exc:
            raise
        return {
            'id': self.id_edit.text().strip(),
            'name': self.name_edit.text().strip() or self.id_edit.text().strip(),
            'builtin': bool(self._provider.get('builtin')),
            'enabled': bool(self.enabled_chk.isChecked()),
            'method': self.method_combo.currentText(),
            'url': self.url_edit.text().strip(),
            'params': _text_to_kv(self.params_edit.toPlainText()),
            'headers': _text_to_kv(self.headers_edit.toPlainText()),
            'body_json': body_json,
            'api_key': self.api_key_edit.text(),
            'extra': _text_to_kv(self.extra_edit.toPlainText()),
            'timeout_sec': float(self.timeout_spin.value()),
            'response': {
                'html_scrape': bool(self.html_chk.isChecked()),
                'items_path': self.items_path_edit.text().strip(),
                'title_path': self.title_path_edit.text().strip(),
                'url_path': self.url_path_edit.text().strip(),
                'snippet_path': self.snippet_path_edit.text().strip(),
            },
        }

    def _on_ok(self):
        try:
            data = self._read_form()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, '保存失败', str(exc))
            return
        if not validate_id(data['id']):
            QtWidgets.QMessageBox.warning(
                self, '保存失败',
                'ID 必须以字母开头，仅含字母数字和 _ -',
            )
            return
        if not data['url']:
            QtWidgets.QMessageBox.warning(self, '保存失败', 'URL 不能为空')
            return
        self._result = data
        self.accept()

    def result_provider(self):
        # type: () -> Dict[str, Any]
        return self._result or {}

    def exec_dialog(self):
        # type: () -> bool
        """跨 PySide2/6 的 exec 兼容入口。"""
        fn = getattr(self, 'exec', None)
        if callable(fn):
            ret = fn()
        else:
            ret = self.exec_()
        return ret == QtWidgets.QDialog.Accepted


__all__ = ['ProviderEditorDialog']
