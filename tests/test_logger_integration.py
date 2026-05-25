#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""logger 接入集成测试。

验证 config / reload / learn_tools / learn_rules 这 4 个文件在关键路径上
确实写入了日志，避免后续重构静默回退到 print。

测试策略：
- 不依赖文件系统：用 ``caplog`` (pytest 标准) 抓取 ``maxagent`` 命名空间日志
- 每个测试只断言"该路径产生了至少 1 条日志"，不死锁住具体文案，
  避免把可读性提示绑死成机械约束
"""

from __future__ import absolute_import
from __future__ import print_function

import json
import logging
import os
import tempfile

import pytest

from maxagent import config as cfg_mod
from maxagent.config import AppConfig
from maxagent.config import ConfigManager
from maxagent.config import LLMProfile
from maxagent.logger import setup_logging


@pytest.fixture(autouse=True)
def _ensure_logger_initialized():
    """确保 logger 在测试前完成初始化。

    setup_logging 是幂等的，重复调用不会叠加 handler。
    用 OFF 让真实文件 handler 不写盘，但仍允许 caplog 抓取（caplog
    挂在 root，需要 propagate=True 才能冒泡）。

    maxagent.logger 出于"不污染 Max root logger"的目的禁用了 propagate，
    在测试期临时打开它，结束再恢复。
    """
    setup_logging(level='DEBUG')
    root = logging.getLogger('maxagent')
    saved = root.propagate
    root.propagate = True
    try:
        yield
    finally:
        root.propagate = saved


@pytest.fixture
def _isolated_config_path(tmp_path):
    """临时配置文件路径。"""
    return str(tmp_path / 'config.json')


# ---------------------------------------------------------------------- #
# config.py
# ---------------------------------------------------------------------- #

def test_config_corrupt_load_logs_warning(caplog, _isolated_config_path):
    """配置文件损坏时，加载链路应记录 warning 日志。"""
    # 写入一个非法 JSON 让 from_dict 失败
    with open(_isolated_config_path, 'w', encoding='utf-8') as f:
        f.write('{ this is not valid json')

    with caplog.at_level(logging.DEBUG, logger='maxagent'):
        cm = ConfigManager(config_path=_isolated_config_path)

    # 应已加载默认 profile
    assert len(cm.list_profile_names()) > 0

    # 应该至少有一条"配置加载失败"或"已备份"的 warning/info
    msgs = [r.getMessage() for r in caplog.records]
    assert any('加载失败' in m or '已备份' in m for m in msgs), \
        '配置损坏时应产生 logger 记录, 实际: {}'.format(msgs)


def test_config_upsert_profile_logs_info(caplog, _isolated_config_path):
    """新增/更新 profile 应产生 INFO 日志。"""
    cm = ConfigManager(config_path=_isolated_config_path)

    new_profile = LLMProfile(
        name='UnitTestProfile',
        base_url='https://example.com/v1',
        model='test-model',
        api_key='',
    )

    with caplog.at_level(logging.INFO, logger='maxagent'):
        cm.upsert_profile(new_profile)

    msgs = [r.getMessage() for r in caplog.records]
    assert any('UnitTestProfile' in m for m in msgs), \
        'upsert_profile 应记录 profile 名, 实际: {}'.format(msgs)


def test_config_set_active_profile_logs_switch(caplog, _isolated_config_path):
    """切换激活 profile 应产生 INFO 日志。"""
    cm = ConfigManager(config_path=_isolated_config_path)
    names = cm.list_profile_names()
    if len(names) < 2:
        pytest.skip('需要至少 2 个内置 profile 才能测试切换')

    other_name = next(n for n in names if n != cm.get_active_profile_name())

    with caplog.at_level(logging.INFO, logger='maxagent'):
        cm.set_active_profile(other_name)

    msgs = [r.getMessage() for r in caplog.records]
    assert any('切换激活' in m for m in msgs), \
        '切换 profile 应记录日志, 实际: {}'.format(msgs)


def test_config_delete_profile_logs_info(caplog, _isolated_config_path):
    """删除 profile 应产生 INFO 日志。"""
    cm = ConfigManager(config_path=_isolated_config_path)

    cm.upsert_profile(LLMProfile(
        name='ToBeDeleted',
        base_url='https://x.test/v1',
        model='m',
    ))

    with caplog.at_level(logging.INFO, logger='maxagent'):
        cm.delete_profile('ToBeDeleted')

    msgs = [r.getMessage() for r in caplog.records]
    assert any('ToBeDeleted' in m and '删除' in m for m in msgs), \
        '删除 profile 应记录日志, 实际: {}'.format(msgs)


def test_config_no_more_naked_print():
    """config.py 中所有用户可见的 fallback 都已迁到 logger。

    保留一处仅当 logger 自身异常时的 stderr 兜底（标记为 broad-except 后
    才允许使用 print），其余 ``[maxagent]`` 风格的 print 应已清零。
    """
    cfg_path = cfg_mod.__file__
    with open(cfg_path, 'r', encoding='utf-8') as f:
        src = f.read()

    # 允许：try except 块内的最后兜底 print
    # 禁止：模块顶层或正常路径的 print('[maxagent] ...')
    print_lines = [
        ln for ln in src.splitlines()
        if "print(" in ln and "[maxagent]" in ln
    ]
    # 仅允许 1 处兜底（_get_logger 失败时的 fallback）
    assert len(print_lines) <= 1, (
        'config.py 仅允许保留 1 处 logger 兜底 print, 实际: {}'.format(
            print_lines,
        )
    )


# ---------------------------------------------------------------------- #
# reload.py
# ---------------------------------------------------------------------- #

def test_reload_module_imports_logger():
    """reload.py 已接入 logger 命名空间。"""
    from maxagent import reload as reload_mod
    assert hasattr(reload_mod, 'logger'), \
        'reload.py 必须暴露 module-level logger'
    assert reload_mod.logger.name.startswith('maxagent'), \
        'logger 必须在 maxagent 命名空间下'


def test_reload_register_maxscript_hook_no_pymxs(caplog):
    """非 Max 环境下 register_maxscript_hook 应静默返回 False，不抛。"""
    from maxagent.reload import register_maxscript_hook

    with caplog.at_level(logging.INFO, logger='maxagent'):
        ok = register_maxscript_hook()

    # 非 Max 环境应直接返回 False
    assert ok is False


# ---------------------------------------------------------------------- #
# learn_tools.py / learn_rules.py
# ---------------------------------------------------------------------- #

def test_learn_tools_imports_logger():
    """learn_tools.py 接入了 logger。"""
    from maxagent.tools import learn_tools
    assert hasattr(learn_tools, 'logger')
    assert learn_tools.logger.name.startswith('maxagent')


def test_learn_rules_imports_logger():
    """learn_rules.py 接入了 logger。"""
    from maxagent.tools import learn_rules
    assert hasattr(learn_rules, 'logger')
    assert learn_rules.logger.name.startswith('maxagent')


def test_learn_tools_validate_failure_logs_warning(caplog):
    """propose_new_tool 校验失败时应记录 warning。"""
    from maxagent.tools.learn_tools import propose_new_tool

    with caplog.at_level(logging.WARNING, logger='maxagent'):
        # 非法 name（含大写）会触发 validate_name 失败
        result = propose_new_tool(
            name='Invalid_Name',
            description='desc',
            code='def x(): pass',
        )

    assert result['approved'] is False
    msgs = [r.getMessage() for r in caplog.records]
    assert any('校验失败' in m for m in msgs), \
        'validate 失败应记录 warning, 实际: {}'.format(msgs)


def test_learn_rules_empty_title_logs_warning(caplog):
    """suggest_rule_addition 标题为空时应记录 warning。"""
    from maxagent.tools.learn_rules import suggest_rule_addition

    with caplog.at_level(logging.WARNING, logger='maxagent'):
        result = suggest_rule_addition(
            rule_id='valid_id',
            title='   ',
            content='这是一条规则内容，长度大于 10 字符。',
        )

    assert result['approved'] is False
    msgs = [r.getMessage() for r in caplog.records]
    assert any('标题为空' in m for m in msgs), \
        '空标题应记录 warning, 实际: {}'.format(msgs)
