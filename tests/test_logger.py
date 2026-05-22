#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maxagent.logger 模块单元测试。

覆盖：
- setup_logging 能在指定目录创建日志文件
- 日志按级别正确过滤，DEBUG/INFO/WARNING/ERROR 都能写入
- logger.exception 自动带 traceback
- 重复调用 setup_logging 不会叠加 handler（幂等）
- shutdown_logging 后能再次 setup
- get_logger 自动加 maxagent. 前缀
- RotatingFileHandler 在超出 maxBytes 后会滚动
"""

from __future__ import absolute_import
from __future__ import print_function

import logging
import os
import shutil
import tempfile

import pytest


@pytest.fixture
def tmp_data_dir(monkeypatch):
    """用 MAXAGENT_DATA_DIR 把 logger 的输出目录引到临时盘。

    每条用例独占一份，保证互不污染；用例结束清理。
    """
    tmp = tempfile.mkdtemp(prefix='maxagent_log_test_')
    monkeypatch.setenv('MAXAGENT_DATA_DIR', tmp)
    # 每条用例都要拿到全新的 logger 状态，避免幂等机制屏蔽测试逻辑
    from maxagent import logger as logger_mod
    logger_mod.shutdown_logging()
    yield tmp
    logger_mod.shutdown_logging()
    shutil.rmtree(tmp, ignore_errors=True)


def _read_log(tmp):
    log_path = os.path.join(tmp, 'logs', 'maxagent.log')
    with open(log_path, 'r', encoding='utf-8') as fh:
        return fh.read()


class TestSetupLogging:
    def test_creates_log_file(self, tmp_data_dir):
        from maxagent.logger import get_logger
        from maxagent.logger import setup_logging
        setup_logging(level='DEBUG', use_stderr=False)
        log = get_logger('maxagent.test')
        log.info('hello')
        log_path = os.path.join(tmp_data_dir, 'logs', 'maxagent.log')
        assert os.path.exists(log_path)
        content = _read_log(tmp_data_dir)
        assert 'hello' in content

    def test_level_filters_debug(self, tmp_data_dir):
        """INFO 级别下 DEBUG 不应进入文件——文件 handler 也跟随？

        约定：文件 handler 自身设到 DEBUG，但 root logger 级别决定能不能
        进 logging 流水线。所以这里把 root 设 INFO，DEBUG 应被滤掉。
        """
        from maxagent.logger import get_logger
        from maxagent.logger import setup_logging
        setup_logging(level='INFO', use_stderr=False)
        log = get_logger('maxagent.test')
        log.debug('this-is-debug')
        log.info('this-is-info')
        content = _read_log(tmp_data_dir)
        assert 'this-is-info' in content
        assert 'this-is-debug' not in content

    def test_exception_includes_traceback(self, tmp_data_dir):
        from maxagent.logger import get_logger
        from maxagent.logger import setup_logging
        setup_logging(level='DEBUG', use_stderr=False)
        log = get_logger('maxagent.test')
        try:
            raise ValueError('boom')
        except ValueError:
            log.exception('caught error')
        content = _read_log(tmp_data_dir)
        assert 'caught error' in content
        assert 'ValueError: boom' in content
        # 关键：traceback 行号 / 文件名都该有
        assert 'Traceback' in content


class TestIdempotent:
    def test_setup_twice_same_handlers(self, tmp_data_dir):
        from maxagent import logger as logger_mod
        logger_mod.setup_logging(level='INFO', use_stderr=False)
        root = logging.getLogger(logger_mod.ROOT_NAME)
        first_count = len(root.handlers)
        # 再调一次：handler 数应不变
        logger_mod.setup_logging(level='INFO', use_stderr=False)
        assert len(root.handlers) == first_count

    def test_shutdown_then_setup(self, tmp_data_dir):
        from maxagent import logger as logger_mod
        logger_mod.setup_logging(level='DEBUG', use_stderr=False)
        logger_mod.shutdown_logging()
        # shutdown 后应可以再次初始化
        logger_mod.setup_logging(level='DEBUG', use_stderr=False)
        log = logger_mod.get_logger('maxagent.test')
        log.info('after-restart')
        assert 'after-restart' in _read_log(tmp_data_dir)


class TestGetLogger:
    def test_auto_prefix(self, tmp_data_dir):
        from maxagent.logger import get_logger
        from maxagent.logger import ROOT_NAME
        from maxagent.logger import setup_logging
        setup_logging(level='DEBUG', use_stderr=False)
        # 不带 maxagent. 前缀的名字应被自动加上
        log = get_logger('foo.bar')
        assert log.name == ROOT_NAME + '.foo.bar'

    def test_keeps_existing_prefix(self, tmp_data_dir):
        from maxagent.logger import get_logger
        from maxagent.logger import ROOT_NAME
        from maxagent.logger import setup_logging
        setup_logging(level='DEBUG', use_stderr=False)
        log = get_logger('maxagent.agent.worker')
        assert log.name == 'maxagent.agent.worker'
        # 子 logger 的输出最终也应通过 root 写到文件
        log.warning('child-msg')
        assert 'child-msg' in _read_log(tmp_data_dir)


class TestRotation:
    def test_rotates_when_exceeds_max_bytes(self, tmp_data_dir):
        """让 maxBytes=1KB，写够数据观察归档文件出现。"""
        from maxagent.logger import get_logger
        from maxagent.logger import setup_logging
        setup_logging(
            level='DEBUG',
            max_bytes=1024,
            backup_count=2,
            use_stderr=False,
        )
        log = get_logger('maxagent.test')
        # 每条 ~120 字节，发 50 条肯定超 1KB
        for i in range(50):
            log.info('rotation test message %03d padding-padding', i)
        log_dir = os.path.join(tmp_data_dir, 'logs')
        files = sorted(os.listdir(log_dir))
        # 至少应该出现 maxagent.log 和 maxagent.log.1
        assert 'maxagent.log' in files
        assert any(f.startswith('maxagent.log.') for f in files)


# ====================================================================== #
# 三态状态机：OFF / INFO / DEBUG（本次重构核心行为）
# ====================================================================== #
class TestThreeStateLevel:
    """覆盖三态语义、归一化、运行期切换、不输出 stderr。"""

    def test_off_state_writes_nothing_to_file(self, tmp_data_dir):
        """OFF 状态下任何级别的 logger 调用都不应入档。"""
        from maxagent.logger import get_logger
        from maxagent.logger import setup_logging
        setup_logging(level='OFF', use_stderr=False)
        log = get_logger('maxagent.test')
        log.debug('debug-msg')
        log.info('info-msg')
        log.warning('warn-msg')
        log.error('error-msg')
        # 文件可能根本未被创建，也可能创建但是空——两种都算成功
        log_path = os.path.join(tmp_data_dir, 'logs', 'maxagent.log')
        if os.path.exists(log_path):
            with open(log_path, 'r', encoding='utf-8') as fh:
                content = fh.read()
        else:
            content = ''
        for needle in ('debug-msg', 'info-msg', 'warn-msg', 'error-msg'):
            assert needle not in content

    def test_info_state_records_info_not_debug(self, tmp_data_dir):
        from maxagent.logger import get_logger
        from maxagent.logger import setup_logging
        setup_logging(level='INFO', use_stderr=False)
        log = get_logger('maxagent.test')
        log.debug('drop-this-debug')
        log.info('keep-this-info')
        content = _read_log(tmp_data_dir)
        assert 'keep-this-info' in content
        assert 'drop-this-debug' not in content

    def test_debug_state_records_debug(self, tmp_data_dir):
        from maxagent.logger import get_logger
        from maxagent.logger import setup_logging
        setup_logging(level='DEBUG', use_stderr=False)
        log = get_logger('maxagent.test')
        log.debug('keep-this-debug')
        log.info('keep-this-info')
        content = _read_log(tmp_data_dir)
        assert 'keep-this-debug' in content
        assert 'keep-this-info' in content


class TestNormalize:
    """老配置兼容：WARNING / ERROR / 非法值 → INFO；数字常量等价。"""

    def test_legacy_warning_falls_back_to_info(self, tmp_data_dir):
        from maxagent.logger import _normalize_level
        from maxagent.logger import LEVEL_INFO
        assert _normalize_level('WARNING') == LEVEL_INFO
        assert _normalize_level('warning') == LEVEL_INFO
        assert _normalize_level('ERROR') == LEVEL_INFO
        assert _normalize_level('CRITICAL') == LEVEL_INFO

    def test_unknown_string_falls_back_to_info(self):
        from maxagent.logger import _normalize_level
        from maxagent.logger import LEVEL_INFO
        assert _normalize_level('LOUD') == LEVEL_INFO
        assert _normalize_level('') == LEVEL_INFO
        assert _normalize_level(None) == LEVEL_INFO

    def test_int_levels_normalize(self):
        from maxagent.logger import _normalize_level
        from maxagent.logger import LEVEL_DEBUG
        from maxagent.logger import LEVEL_INFO
        assert _normalize_level(logging.DEBUG) == LEVEL_DEBUG
        assert _normalize_level(logging.INFO) == LEVEL_INFO
        assert _normalize_level(logging.WARNING) == LEVEL_INFO

    def test_three_states_pass_through(self):
        from maxagent.logger import _normalize_level
        assert _normalize_level('OFF') == 'OFF'
        assert _normalize_level('off') == 'OFF'
        assert _normalize_level('INFO') == 'INFO'
        assert _normalize_level('DEBUG') == 'DEBUG'


class TestApplyLogLevel:
    """运行期 apply_log_level：不重建 handler，直接切级别。"""

    def test_runtime_switch_off_to_debug(self, tmp_data_dir):
        from maxagent.logger import apply_log_level
        from maxagent.logger import get_logger
        from maxagent.logger import setup_logging
        setup_logging(level='OFF', use_stderr=False)
        log = get_logger('maxagent.test')
        log.info('drop-when-off')
        # 切到 DEBUG
        assert apply_log_level('DEBUG') == 'DEBUG'
        log.debug('keep-after-debug')
        log.info('keep-info-after-debug')
        content = _read_log(tmp_data_dir)
        assert 'drop-when-off' not in content
        assert 'keep-after-debug' in content
        assert 'keep-info-after-debug' in content

    def test_runtime_switch_does_not_duplicate_handlers(self, tmp_data_dir):
        from maxagent.logger import apply_log_level
        from maxagent.logger import ROOT_NAME
        from maxagent.logger import setup_logging
        setup_logging(level='INFO', use_stderr=False)
        root = logging.getLogger(ROOT_NAME)
        before = len(root.handlers)
        apply_log_level('DEBUG')
        apply_log_level('OFF')
        apply_log_level('INFO')
        # handler 数量应保持不变
        assert len(root.handlers) == before

    def test_apply_normalizes_legacy_value(self, tmp_data_dir):
        from maxagent.logger import apply_log_level
        from maxagent.logger import setup_logging
        setup_logging(level='INFO', use_stderr=False)
        # 老配置传 WARNING 应被折算成 INFO
        assert apply_log_level('WARNING') == 'INFO'


class TestNoStderrOutput:
    """关键回归：日志彻底不再输出到 stderr。"""

    def test_no_stream_handler_after_setup(self, tmp_data_dir):
        from maxagent.logger import ROOT_NAME
        from maxagent.logger import setup_logging
        # 哪怕历史代码里 use_stderr=True 也不该产生 StreamHandler——
        # 参数已废弃，新版本永不向控制台输出
        setup_logging(level='DEBUG', use_stderr=True)
        root = logging.getLogger(ROOT_NAME)
        for handler in root.handlers:
            assert not isinstance(handler, logging.StreamHandler) or \
                isinstance(handler, logging.handlers.RotatingFileHandler), \
                'StreamHandler 不应再出现在 maxagent root logger 上'

    def test_does_not_write_to_stderr(self, tmp_data_dir, capsys):
        from maxagent.logger import get_logger
        from maxagent.logger import setup_logging
        setup_logging(level='DEBUG', use_stderr=True)
        log = get_logger('maxagent.test')
        log.info('should-not-appear-on-stderr')
        log.warning('also-not-here')
        captured = capsys.readouterr()
        assert 'should-not-appear-on-stderr' not in captured.err
        assert 'also-not-here' not in captured.err
        # stdout 同样不该有
        assert 'should-not-appear-on-stderr' not in captured.out


class TestIsDebugEnabled:
    def test_off_returns_false(self, tmp_data_dir):
        from maxagent.logger import is_debug_enabled
        from maxagent.logger import setup_logging
        setup_logging(level='OFF', use_stderr=False)
        assert is_debug_enabled() is False

    def test_info_returns_false(self, tmp_data_dir):
        from maxagent.logger import is_debug_enabled
        from maxagent.logger import setup_logging
        setup_logging(level='INFO', use_stderr=False)
        assert is_debug_enabled() is False

    def test_debug_returns_true(self, tmp_data_dir):
        from maxagent.logger import is_debug_enabled
        from maxagent.logger import setup_logging
        setup_logging(level='DEBUG', use_stderr=False)
        assert is_debug_enabled() is True


class TestConfigCompat:
    """config.AppConfig.from_dict 对老配置的三态归一化。"""

    def test_legacy_warning_in_config_becomes_info(self):
        from maxagent.config import AppConfig
        cfg = AppConfig.from_dict({'log_level': 'WARNING'})
        assert cfg.log_level == 'INFO'

    def test_legacy_error_in_config_becomes_info(self):
        from maxagent.config import AppConfig
        cfg = AppConfig.from_dict({'log_level': 'ERROR'})
        assert cfg.log_level == 'INFO'

    def test_off_passes_through(self):
        from maxagent.config import AppConfig
        cfg = AppConfig.from_dict({'log_level': 'OFF'})
        assert cfg.log_level == 'OFF'

    def test_debug_passes_through(self):
        from maxagent.config import AppConfig
        cfg = AppConfig.from_dict({'log_level': 'DEBUG'})
        assert cfg.log_level == 'DEBUG'

    def test_unknown_falls_back_to_info(self):
        from maxagent.config import AppConfig
        cfg = AppConfig.from_dict({'log_level': 'GARBAGE'})
        assert cfg.log_level == 'INFO'