#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目编码规范守门测试：禁止任何 .py 文件出现 f-string。

为什么禁止 f-string？
=====================
本项目目标运行环境为 3ds Max 2022~2027（Python 3.7+），虽然语法层面
完全支持 f-string，但用户 / 团队制定了"统一用 .format()"的内部
风格规则，理由：

1. 历史项目（曾经支持 PySide2 + 老 Max 版本）里大量使用 .format()，
   保持新代码同款风格，减少同一份代码两种字符串模板并存的视觉碎片。
2. f-string 的表达式部分会被静默拼接到字符串里，重构搜索（grep
   ``str.format`` / ``%`` 占位）会漏掉这一支；而 .format() 把模板和
   参数清晰分开，更容易做静态分析与替换。
3. 部分 lint 规则（如 flake8 + 自定义插件）按 .format() 检查格式串与
   参数数量匹配；混用 f-string 会绕过这层检查。

实现原理
========
扫描 ``maxagent/`` 与 ``tests/`` 下所有 ``.py``，用 ``ast`` 解析后
查找 ``ast.JoinedStr`` 节点（这是 f-string 在 AST 里的表示，包括
普通 ``f""`` / 原始 ``rf""`` / 字节-否兼容 ``fr""`` 等所有变体）。
任何一处命中即让本测试 fail，并打印精确文件 + 行号 + 行内容。

为什么不用正则匹配 ``\\bf['"]`` ？
- 正则会把字符串字面量里恰好以 ``f`` 结尾的拼接（如 ``'#2c5d8f'``）
  误判为 f-string；ast 解析则只认真正的语法 f-string。
- 正则也无法识别多行 f-string 续行场景。
"""

from __future__ import absolute_import
from __future__ import print_function

import ast
import os

import pytest


# 项目根目录：tests/ 上一级
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 需要扫描的子目录（相对 PROJECT_ROOT）
SCAN_DIRS = (
    'maxagent',
    'tests',
)

# 不扫描的目录（构建产物 / 第三方源码 / 缓存）
EXCLUDE_DIR_NAMES = frozenset((
    '__pycache__',
    '.git',
    '.venv',
    'venv',
    'env',
    '.tox',
    '.pytest_cache',
    'build',
    'dist',
    'node_modules',
))


def _iter_py_files():
    """遍历需要扫描的 .py 文件，跳过排除目录。"""
    for sub in SCAN_DIRS:
        root = os.path.join(PROJECT_ROOT, sub)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # in-place 修改 dirnames 避免 os.walk 进入排除目录
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]
            for name in filenames:
                if name.endswith('.py'):
                    yield os.path.join(dirpath, name)


def _find_fstrings_in_file(path):
    """返回 [(lineno, col, text)] 形式的 f-string 命中列表。"""
    try:
        with open(path, 'rb') as f:
            source = f.read()
    except (IOError, OSError):
        return []
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        # 测试本身不应因目标文件语法错误而失败——交给其他测试 / lint 处理
        return []

    # 按行号定位用：把源码按行切开，方便给出可读的 hit 摘要
    text_lines = source.decode('utf-8', errors='replace').splitlines()
    hits = []
    for node in ast.walk(tree):
        # ast.JoinedStr 即 f-string；ast.FormattedValue 是其子节点
        # （表达式段），命中父节点就足够诊断了
        if isinstance(node, ast.JoinedStr):
            lineno = getattr(node, 'lineno', 0)
            col = getattr(node, 'col_offset', 0)
            line_text = text_lines[lineno - 1].rstrip() if 0 < lineno <= len(text_lines) else ''
            hits.append((lineno, col, line_text))
    return hits


def test_no_fstring_in_project():
    """整个项目源码（含测试）禁止出现 f-string。

    若需要给某个文件临时豁免（极不推荐），请：
    1. 在该文件顶部加 ``# noqa: NO_FSTRING`` 注释；
    2. 把文件路径加入 ``ALLOWLIST``（见下）；
    3. 在 PR / commit message 里说明原因。

    ALLOWLIST 长期保持为空集；任何新增条目都需 code review 卡审。
    """
    allowlist = frozenset()  # 显式留空集，避免被默默跳过
    offenders = []
    for path in _iter_py_files():
        rel = os.path.relpath(path, PROJECT_ROOT).replace(os.sep, '/')
        if rel in allowlist:
            continue
        hits = _find_fstrings_in_file(path)
        for lineno, col, line_text in hits:
            offenders.append((rel, lineno, col, line_text))

    if offenders:
        report = ['项目禁止使用 f-string，请改用 .format() 或 % 格式化。命中：']
        for rel, lineno, col, line_text in offenders:
            report.append('  {}:{}:{}  {}'.format(rel, lineno, col, line_text))
        pytest.fail('\n'.join(report))


# ------------------------------------------------------------------ #
# 自检：保证扫描器本身工作正常（避免静默通过）
# ------------------------------------------------------------------ #
def test_fstring_detector_recognizes_real_fstring(tmp_path):
    """喂给扫描器一个真 f-string 样本，必须能被检出。

    防止有人改坏 _find_fstrings_in_file 后，主守门测试静默通过。
    """
    sample = tmp_path / 'sample.py'
    sample.write_text('x = 1\nprint(f"hi {x}")\n', encoding='utf-8')
    hits = _find_fstrings_in_file(str(sample))
    assert hits, 'f-string 扫描器失效：未识别真实的 f-string'
    lineno, _col, _text = hits[0]
    assert lineno == 2


def test_fstring_detector_ignores_string_starting_with_f(tmp_path):
    """普通字符串里以 ``f`` 开头的内容不应误报。

    例如 ``'#2c5d8f'`` / ``'fragment'`` / ``"foo"`` 这些。
    """
    sample = tmp_path / 'sample.py'
    sample.write_text(
        'COLOR = "#2c5d8f"\n'
        'WORD = "fragment"\n'
        'NAME = "foo"\n',
        encoding='utf-8',
    )
    hits = _find_fstrings_in_file(str(sample))
    assert not hits, '误把普通字符串识别成 f-string: {}'.format(hits)


def test_fstring_detector_recognizes_raw_fstring(tmp_path):
    """rf"" / fr"" 这两种变体也得能识别（AST 同样是 JoinedStr）。"""
    sample = tmp_path / 'sample.py'
    sample.write_text(
        'x = 1\n'
        r'a = rf"path\to\{x}"' '\n'
        r'b = fr"path\to\{x}"' '\n',
        encoding='utf-8',
    )
    hits = _find_fstrings_in_file(str(sample))
    assert len(hits) == 2, 'rf/fr 变体应被全部识别，实际命中: {}'.format(hits)
