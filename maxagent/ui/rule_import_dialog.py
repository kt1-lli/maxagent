#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规则导入对话框（Phase 2 - 轻共享）。

让用户在导入 ``.maxagent-rule.json`` / ``.maxagent-rules.json`` 文件时，
能在落盘前看到文件里**所有规则**的状态（新增 / 已存在 / 格式错误），
并逐条勾选要导入的规则。

UI 结构::

    +----------------------------------------------------------+
    | 来源文件: /path/to/foo.maxagent-rules.json               |
    | 共解析 N 条规则（M 条新增 / X 条已存在 / Y 条格式错误） |
    +----------------------------------------------------------+
    | [√] [ID]      [标题]            [字节] [状态]            |
    | [ ] my_rule_1 测试规则1          120   新增              |
    | [√] my_rule_2 测试规则2          340   新增              |
    | [ ] exists_id 已存在规则         200   已存在（默认不勾） |
    | [ ] bad_id    格式错误规则        --   格式错误：xxx     |
    +----------------------------------------------------------+
    | □ 允许覆盖同 ID 已存在规则       [全选可导入] [取消] [导入] |
    +----------------------------------------------------------+

规则：
- "新增" 默认勾选；"已存在" 默认不勾选；"格式错误" 不可勾选
- 用户勾选 "已存在" 条目时，必须先勾上 "允许覆盖" 才会真覆盖；
  否则该条仍按 skipped 处理
- 点 "导入" 后批量执行，弹结果汇总（导入 N / 跳过 M / 失败 X）
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..logger import get_logger
from ..qt_compat import QtCore
from ..qt_compat import QtWidgets
from .emoji_compat import btn_label as _btn_label
from .emoji_compat import ee as _ee


logger = get_logger(__name__)


class RuleImportDialog(QtWidgets.QDialog):
    """规则导入对话框：解析 → 展示 → 勾选 → 落盘。"""

    # 表格列定义
    COL_CHECK = 0
    COL_ID = 1
    COL_TITLE = 2
    COL_SIZE = 3
    COL_STATUS = 4

    def __init__(self, file_path, parent=None):
        # type: (str, Optional[QtWidgets.QWidget]) -> None
        super(RuleImportDialog, self).__init__(parent)
        self._file_path = file_path
        self._diffs = []  # type: List[Dict[str, Any]]
        self._import_result = None  # type: Optional[Dict[str, Any]]

        self.setWindowTitle(_ee('📥') + '  导入规则文件')
        self.resize(720, 480)
        self._build_ui()
        self._load_file()

    # ------------------------------------------------------------------ #
    # UI 构造
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)

        self._header_label = QtWidgets.QLabel('正在解析文件...')
        self._header_label.setWordWrap(True)
        self._header_label.setTextFormat(QtCore.Qt.RichText)
        layout.addWidget(self._header_label)

        # 表格
        self._table = QtWidgets.QTableWidget(0, 5, self)
        self._table.setHorizontalHeaderLabels(
            ['', 'ID', '标题', '字节', '状态'],
        )
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(
            self.COL_TITLE, QtWidgets.QHeaderView.Stretch,
        )
        self._table.horizontalHeader().setSectionResizeMode(
            self.COL_CHECK, QtWidgets.QHeaderView.Fixed,
        )
        self._table.setColumnWidth(self.COL_CHECK, 36)
        self._table.setColumnWidth(self.COL_ID, 160)
        self._table.setColumnWidth(self.COL_SIZE, 60)
        self._table.setColumnWidth(self.COL_STATUS, 100)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows,
        )
        self._table.setStyleSheet(
            'QTableWidget { background:#252525; color:#d4d4d4;'
            '               border:1px solid #444; gridline-color:#3a3a3a; }'
            'QTableWidget::item:selected { background:#3a5d8f; }'
            'QHeaderView::section { background:#2d2d2d; color:#d4d4d4;'
            '                       border:1px solid #444; padding:4px; }'
        )
        layout.addWidget(self._table, 1)

        # 覆盖选项
        self._overwrite_check = QtWidgets.QCheckBox(
            _ee('⚠️') + ' 允许覆盖同 ID 已存在的规则（请确认你了解后果）',
        )
        self._overwrite_check.setStyleSheet('color:#ffd166;')
        layout.addWidget(self._overwrite_check)

        # 按钮行
        btn_row = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton(_btn_label('☑', '全选可导入'))
        select_all_btn.clicked.connect(self._on_select_all_importable)
        btn_row.addWidget(select_all_btn)
        clear_btn = QtWidgets.QPushButton(_btn_label('☐', '全不选'))
        clear_btn.clicked.connect(self._on_clear_selection)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch(1)
        cancel_btn = QtWidgets.QPushButton(_btn_label('✕', '取消'))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self._import_btn = QtWidgets.QPushButton(_btn_label('📥', '导入勾选项'))
        self._import_btn.setDefault(True)
        self._import_btn.clicked.connect(self._on_do_import)
        btn_row.addWidget(self._import_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------ #
    # 文件解析
    # ------------------------------------------------------------------ #
    def _load_file(self):
        """解析文件并填充表格。"""
        try:
            from .. import user_rules_loader as url
        except Exception as exc:  # pylint: disable=broad-except
            self._show_fatal('加载规则模块失败: {}'.format(exc))
            return

        try:
            rule_list = url.parse_import_file(self._file_path)
        except ValueError as exc:
            self._show_fatal('解析文件失败: {}'.format(exc))
            return

        if not rule_list:
            self._show_fatal('文件中没有可导入的规则')
            return

        self._diffs = url.diff_import_rules(rule_list)
        self._render_table()
        self._update_header()

    def _show_fatal(self, msg):
        # type: (str) -> None
        """文件无法解析时禁用导入按钮、提示原因。"""
        self._header_label.setText(
            '<span style="color:#ff8888;">{}</span><br>'
            '<span style="color:#aaa;">来源文件: {}</span>'.format(
                msg, self._file_path,
            ),
        )
        if hasattr(self, '_import_btn'):
            self._import_btn.setEnabled(False)

    def _render_table(self):
        """根据 diff 结果渲染整张表。"""
        self._table.setRowCount(len(self._diffs))
        for row, d in enumerate(self._diffs):
            self._set_row(row, d)

    def _set_row(self, row, diff):
        # type: (int, Dict[str, Any]) -> None
        rule = diff.get('rule') or {}
        status = diff.get('status', 'invalid')
        reason = diff.get('reason', '')

        # 列 0：勾选框（invalid 不可选）
        check = QtWidgets.QCheckBox()
        if status == 'new':
            check.setChecked(True)
        elif status == 'existing':
            check.setChecked(False)
        else:
            check.setEnabled(False)
            check.setChecked(False)
        # 居中放进单元格
        wrapper = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(wrapper)
        h.setContentsMargins(0, 0, 0, 0)
        h.setAlignment(QtCore.Qt.AlignCenter)
        h.addWidget(check)
        self._table.setCellWidget(row, self.COL_CHECK, wrapper)

        # 列 1：ID
        rid_item = QtWidgets.QTableWidgetItem(rule.get('id') or '(无 ID)')
        self._table.setItem(row, self.COL_ID, rid_item)

        # 列 2：标题
        title_item = QtWidgets.QTableWidgetItem(rule.get('title') or '')
        self._table.setItem(row, self.COL_TITLE, title_item)

        # 列 3：字节数
        try:
            size_bytes = len(
                (rule.get('content') or '').encode('utf-8'),
            )
        except (UnicodeEncodeError, AttributeError):
            size_bytes = 0
        size_item = QtWidgets.QTableWidgetItem(str(size_bytes))
        size_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self._table.setItem(row, self.COL_SIZE, size_item)

        # 列 4：状态（带颜色）
        from ..qt_compat import QtGui
        if status == 'new':
            status_text = '新增'
            color = '#a8e6a8'
        elif status == 'existing':
            status_text = '已存在'
            color = '#ffd166'
        else:
            status_text = '格式错误'
            if reason:
                status_text += ': ' + reason[:40]
            color = '#ff8888'
        status_item = QtWidgets.QTableWidgetItem(status_text)
        status_item.setForeground(QtGui.QBrush(QtGui.QColor(color)))
        self._table.setItem(row, self.COL_STATUS, status_item)

        # 整行底色：invalid 行加灰底
        if status == 'invalid':
            for col in range(self._table.columnCount()):
                cell = self._table.item(row, col)
                if cell is not None:
                    cell.setBackground(QtGui.QBrush(QtGui.QColor('#3a2828')))

        # 把 row 与 diff 关联，方便导入时回查
        rid_item.setData(QtCore.Qt.UserRole, row)

    def _update_header(self):
        """计算并更新顶部摘要。"""
        n_total = len(self._diffs)
        n_new = sum(1 for d in self._diffs if d['status'] == 'new')
        n_existing = sum(1 for d in self._diffs if d['status'] == 'existing')
        n_invalid = sum(1 for d in self._diffs if d['status'] == 'invalid')
        self._header_label.setText(
            '<span style="color:#aaa;">来源文件:</span> '
            '<span style="color:#7ec0ff;">{path}</span><br>'
            '共解析 <b>{total}</b> 条规则：'
            '<span style="color:#a8e6a8;">{new} 新增</span> · '
            '<span style="color:#ffd166;">{existing} 已存在</span> · '
            '<span style="color:#ff8888;">{invalid} 格式错误</span>'.format(
                path=self._file_path,
                total=n_total, new=n_new,
                existing=n_existing, invalid=n_invalid,
            ),
        )

    # ------------------------------------------------------------------ #
    # 选择控制
    # ------------------------------------------------------------------ #
    def _row_check(self, row):
        # type: (int) -> Optional[QtWidgets.QCheckBox]
        wrapper = self._table.cellWidget(row, self.COL_CHECK)
        if wrapper is None:
            return None
        layout = wrapper.layout()
        if layout is None or layout.count() == 0:
            return None
        return layout.itemAt(0).widget()

    def _on_select_all_importable(self):
        for row, d in enumerate(self._diffs):
            check = self._row_check(row)
            if check is None:
                continue
            if d['status'] in ('new', 'existing'):
                check.setChecked(True)

    def _on_clear_selection(self):
        for row in range(self._table.rowCount()):
            check = self._row_check(row)
            if check is not None and check.isEnabled():
                check.setChecked(False)

    # ------------------------------------------------------------------ #
    # 执行导入
    # ------------------------------------------------------------------ #
    def _on_do_import(self):
        try:
            from .. import user_rules_loader as url
        except Exception as exc:  # pylint: disable=broad-except
            QtWidgets.QMessageBox.warning(self, '错误', str(exc))
            return

        overwrite = self._overwrite_check.isChecked()
        selected_diffs = []
        for row, d in enumerate(self._diffs):
            check = self._row_check(row)
            if check is None or not check.isChecked():
                continue
            if d['status'] == 'invalid':
                continue
            selected_diffs.append(d)

        if not selected_diffs:
            QtWidgets.QMessageBox.information(
                self, '提示', '请至少勾选一条规则后再导入。',
            )
            return

        # 若选了已存在的但没勾覆盖，做一次确认
        n_existing_selected = sum(
            1 for d in selected_diffs if d['status'] == 'existing'
        )
        if n_existing_selected > 0 and not overwrite:
            ans = QtWidgets.QMessageBox.question(
                self,
                '注意',
                '你勾选了 {} 条已存在的规则，但未勾选"允许覆盖"。\n'
                '这些规则会被跳过，确定继续吗？'.format(n_existing_selected),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if ans != QtWidgets.QMessageBox.Yes:
                return

        n_imported = 0
        n_skipped = 0
        n_overwritten = 0
        n_failed = 0
        failures = []
        for d in selected_diffs:
            try:
                result = url.import_rule(d['rule'], overwrite=overwrite)
            except (ValueError, OSError) as exc:
                n_failed += 1
                failures.append(
                    '[{}] {}'.format(d['rule'].get('id', '?'), exc),
                )
                continue
            status = result.get('status')
            if status == 'imported':
                n_imported += 1
            elif status == 'overwritten':
                n_overwritten += 1
            elif status == 'skipped':
                n_skipped += 1

        self._import_result = {
            'imported': n_imported,
            'overwritten': n_overwritten,
            'skipped': n_skipped,
            'failed': n_failed,
        }

        # 汇总弹窗
        msg_lines = [
            '导入完成：',
            '  · 新增 {} 条'.format(n_imported),
            '  · 覆盖 {} 条'.format(n_overwritten),
            '  · 跳过 {} 条'.format(n_skipped),
            '  · 失败 {} 条'.format(n_failed),
        ]
        if failures:
            msg_lines.append('')
            msg_lines.append('失败详情：')
            msg_lines.extend(failures[:10])
            if len(failures) > 10:
                msg_lines.append('  ...（共 {} 条失败）'.format(len(failures)))

        if n_failed > 0:
            QtWidgets.QMessageBox.warning(self, '部分失败', '\n'.join(msg_lines))
        else:
            QtWidgets.QMessageBox.information(self, '成功', '\n'.join(msg_lines))

        self.accept()

    def import_result(self):
        # type: () -> Optional[Dict[str, Any]]
        """对外暴露：导入完成后的统计字典。"""
        return self._import_result


__all__ = ['RuleImportDialog']
