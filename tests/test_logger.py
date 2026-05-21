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
