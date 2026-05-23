#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置对话框 QFormLayout 对齐方式回归测试。

历史 bug：
    模型页右侧表单的 ``QFormLayout`` 没有显式设置 ``formAlignment``
    与 ``fieldGrowthPolicy``，Qt 默认会让 form 在父容器中**水平居中**
    ，并按字段 sizeHint 取最小宽度，导致整列 label 被推到容器中央，
    与底部"自定义 Header:"行视觉上不在同一列，看起来像分裂在两个布局。

修复后必须：
- formAlignment 含 ``Qt.AlignLeft``：表单整体靠左对齐；
- fieldGrowthPolicy 为 ``ExpandingFieldsGrow``：字段控件随容器拉伸，
  让所有行的 label 列位置严格一致。

本测试不构造完整的 SettingsDialog（依赖 AppConfig 和文件 IO 较重），
而是直接断言 ``settings_dialog.py`` 三处 ``QFormLayout`` 都设置了
正确的对齐属性 —— 静态扫描即可在 CI 上稳定运行。
"""

from __future__ import absolute_import
from __future__ import print_function

import io
import os


SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'maxagent', 'ui', 'settings_dialog.py',
)


def _read_source():
    with io.open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
        return f.read()


def test_settings_file_exists():
    assert os.path.exists(SETTINGS_FILE), '找不到 settings_dialog.py'


def test_form_alignment_set_for_all_forms():
    """三处 QFormLayout 都必须显式设置 setFormAlignment。

    避免 Qt 默认的水平居中行为再次让 label 列被推到中央。
    """
    src = _read_source()
    # 三个 form 容器：right / form(network) / form(app)
    form_count = src.count('QtWidgets.QFormLayout(')
    align_count = src.count('setFormAlignment(')
    assert form_count >= 3, '至少应有三处 QFormLayout，实际 {}'.format(form_count)
    assert align_count >= form_count, (
        'setFormAlignment 调用次数 ({}) 应不少于 QFormLayout 数 ({})'
        .format(align_count, form_count)
    )


def test_field_growth_policy_set_for_all_forms():
    """所有 QFormLayout 都应使用 ExpandingFieldsGrow，让字段随容器伸展。"""
    src = _read_source()
    form_count = src.count('QtWidgets.QFormLayout(')
    grow_count = src.count('setFieldGrowthPolicy(')
    expanding_count = src.count('ExpandingFieldsGrow')
    assert grow_count >= form_count, (
        'setFieldGrowthPolicy 应在所有 form 上调用 (form={}, called={})'
        .format(form_count, grow_count)
    )
    assert expanding_count >= form_count, (
        'ExpandingFieldsGrow 出现次数应 >= form 数 (form={}, found={})'
        .format(form_count, expanding_count)
    )


def test_align_left_appears_with_form_alignment():
    """setFormAlignment 必须传入 AlignLeft（而非默认居中）。"""
    src = _read_source()
    # 简单检查：setFormAlignment 调用附近应能找到 AlignLeft
    idx = 0
    found = 0
    while True:
        pos = src.find('setFormAlignment(', idx)
        if pos < 0:
            break
        # 取这一调用之后约 200 个字符，找 AlignLeft 关键字
        snippet = src[pos:pos + 200]
        if 'AlignLeft' in snippet:
            found += 1
        idx = pos + 1
    assert found >= 3, (
        'setFormAlignment 调用应均含 AlignLeft，实际命中 {}'.format(found)
    )
