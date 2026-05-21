#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一日志模块。

设计目标
========
1. **持久化**：日志写入 ``<config_dir>/logs/maxagent.log``，滚动归档
   （5 个文件 × 2 MB），重启 Max 后仍可回溯崩溃前几小时的现场。
2. **双通道**：同时写文件 + ``stderr``。文件用于事后追溯，stderr 用
   于开发期在 Max MAXScript Listener 里看实时输出。
3. **独立命名空间**：所有 logger 都挂在 ``maxagent.*`` 下，
   ``propagate=False``，绝不污染 Max 自身或其它插件的 root logger。
4. **线程安全**：标准库 ``logging`` 内建线程锁，Worker 子线程、
   主线程、QTimer 回调写日志都安全。
5. **零外部依赖**：仅用 ``logging`` + ``logging.handlers``，避免
   pip 安装第三方包（Max 内常受限）。
6. **幂等初始化**：``setup_logging`` 重复调用不会重复挂 handler，
   方便 ``reload.py`` 热重载。

典型用法
========

启动期（``startup.py`` 或 ``__init__.py``）::

    from maxagent.logger import setup_logging
    setup_logging()    # 自动从 AppConfig 读 log_level

业务模块::

    from maxagent.logger import get_logger
    logger = get_logger(__name__)

    logger.info('启动会话: %s', sid)
    logger.warning('配置加载失败，回退默认: %s', exc)
    try:
        ...
    except Exception:        # pylint: disable=broad-except
        logger.exception('工具 %s 执行异常', tool_name)

注意事项
========
- 直接用 ``%`` 风格占位（``logger.info('foo: %s', x)``），不要在调用
  端先 ``format``，让 logging 内部按级别延迟拼接，未启用 DEBUG 时
  能省下大段字符串构造开销。
- 异常请用 ``logger.exception``，自动带 traceback。
- ``ROOT_NAME`` 是 ``maxagent``，所以 ``get_logger('maxagent.worker')``
  会继承 root 的 handler，单独 ``get_logger('worker')`` 不会。
"""

from __future__ import absolute_import
from __future__ import print_function

import logging
import logging.handlers
import os
import sys


# 所有日志统一挂在这个命名空间下，避免污染外部 root logger
ROOT_NAME = 'maxagent'

# 标记位：避免重复初始化（reload 时多次 import 也只挂一次 handler）
_INIT_SENTINEL_ATTR = '_maxagent_log_initialized'

# 默认参数（可被 setup_logging 覆盖）
DEFAULT_LEVEL = logging.INFO
DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 单个文件 2 MB
DEFAULT_BACKUP_COUNT = 5             # 保留 5 份历史

# 控制台与文件共用的格式：时间 + 级别 + 模块 + 线程 + 消息
# 线程名能在排查"主线程 vs Worker 子线程"问题时直接看出来
_LOG_FORMAT = (
    '%(asctime)s [%(levelname)s] %(name)s '
    '(%(threadName)s) %(message)s'
)
_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'


def _resolve_log_dir():
    """计算日志目录：``<config_dir>/logs``，目录不存在则创建。

    复用 ``config.get_config_dir`` 拿到和 ``config.json`` 同级的位置，
    保证用户能在同一处找到所有持久化数据（配置/会话/技能/日志）。
    """
    try:
        from .config import get_config_dir
        base = get_config_dir()
    except Exception:  # pylint: disable=broad-except
        # config 模块自身出问题时退回到 ``~/.maxagent``，保证日志能落盘
        base = os.path.join(os.path.expanduser('~'), '.maxagent')
    log_dir = os.path.join(base, 'logs')
    try:
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
    except OSError:
        # 目录创建失败（权限/只读盘）也不抛——降级成只用 stderr
        return None
    return log_dir


def _coerce_level(level):
    """把 'INFO' / logging.INFO / 'debug' 等都归一化成数字级别。"""
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        name = level.strip().upper()
        if hasattr(logging, name):
            value = getattr(logging, name)
            if isinstance(value, int):
                return value
    return DEFAULT_LEVEL


def setup_logging(
    level=None,
    log_dir=None,
    max_bytes=DEFAULT_MAX_BYTES,
    backup_count=DEFAULT_BACKUP_COUNT,
    use_stderr=True,
):
    """初始化 ``maxagent`` 命名空间下的日志系统。

    :param level: 日志级别，可传 ``logging.DEBUG`` / ``'INFO'`` 等。
                  ``None`` 时尝试从 ``AppConfig.log_level`` 读取。
    :param log_dir: 自定义日志目录；``None`` 时走默认（config_dir/logs）。
    :param max_bytes: 单个日志文件大小上限。
    :param backup_count: 滚动保留的历史文件个数。
    :param use_stderr: 是否同时输出到 ``stderr``。Max 嵌入环境保持
                       True 能看到实时日志，自动化跑批可关掉。
    :returns: 已配置好的 root logger（``maxagent``）。
    """
    root = logging.getLogger(ROOT_NAME)

    # 幂等：已初始化过就只更新级别，不再叠加 handler
    if getattr(root, _INIT_SENTINEL_ATTR, False):
        if level is not None:
            root.setLevel(_coerce_level(level))
        return root

    # 决定级别：显式传入 > AppConfig.log_level > DEFAULT_LEVEL
    if level is None:
        try:
            from .config import load_config
            cfg = load_config()
            level = getattr(cfg, 'log_level', None)
        except Exception:  # pylint: disable=broad-except
            level = None
    root.setLevel(_coerce_level(level))

    # 关键：不要把日志冒泡到外部 root logger，避免污染 Max
    root.propagate = False

    formatter = logging.Formatter(_LOG_FORMAT, _DATE_FORMAT)

    # ---- 文件 handler（滚动） ----
    if log_dir is None:
        log_dir = _resolve_log_dir()
    if log_dir:
        log_path = os.path.join(log_dir, 'maxagent.log')
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8',
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(logging.DEBUG)  # 文件抓最详细
            root.addHandler(file_handler)
        except (OSError, IOError):
            # 文件不可写也不致命，继续走 stderr
            pass

    # ---- 控制台 handler ----
    if use_stderr:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        # 控制台跟随 root 级别，避免 INFO 之外的内容刷屏
        root.addHandler(stream_handler)

    setattr(root, _INIT_SENTINEL_ATTR, True)
    root.info('日志系统已初始化，level=%s, dir=%s',
              logging.getLevelName(root.level), log_dir)
    return root


def get_logger(name):
    """获取 ``maxagent`` 子 logger。

    :param name: 通常传 ``__name__``，例如 ``maxagent.agent.worker``。
                 如果传入的不在 ``maxagent.*`` 命名空间下，会自动加前缀，
                 保证日志最终都流向 ``setup_logging`` 配置的 handler。
    """
    if not name:
        return logging.getLogger(ROOT_NAME)
    if name == ROOT_NAME or name.startswith(ROOT_NAME + '.'):
        return logging.getLogger(name)
    return logging.getLogger(ROOT_NAME + '.' + name)


def shutdown_logging():
    """关闭并移除所有 handler，配合 ``reload`` 时使用。

    单纯重 import 不会重置 handler，热重载场景下需要显式 shutdown，
    再 ``setup_logging`` 一次。
    """
    root = logging.getLogger(ROOT_NAME)
    for handler in list(root.handlers):
        try:
            handler.close()
        except Exception:  # pylint: disable=broad-except
            pass
        root.removeHandler(handler)
    if hasattr(root, _INIT_SENTINEL_ATTR):
        delattr(root, _INIT_SENTINEL_ATTR)
