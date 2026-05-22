#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一日志模块（三态版：关闭 / 开启 / DEBUG）。

设计目标
========
1. **三档状态**：``OFF`` / ``INFO`` / ``DEBUG``。一字段（``log_level``）
   表达全部含义——OFF 表示完全关闭，INFO 是默认开启，DEBUG 是详细模式。
2. **只写文件，不写控制台**：日志一律落盘 ``<config_dir>/logs/
   maxagent.log``，按 2 MB × 5 份滚动归档。**不再向 stderr 输出**
   （Max 嵌入环境里也保持安静，避免 MAXScript Listener 被刷屏）。
3. **DEBUG = 全量埋点**：DEBUG 模式下 LLM 请求 / 工具调用 / 会话生命
   周期 / Worker 线程切换 / UI 关键事件全部入档，方便事后定位偶发
   bug；INFO 模式下只记关键节点。
4. **独立命名空间**：所有 logger 都挂在 ``maxagent.*`` 下，
   ``propagate=False``，绝不污染 Max 自身或其它插件的 root logger。
5. **线程安全**：标准库 ``logging`` 内建线程锁，Worker 子线程、
   主线程、QTimer 回调写日志都安全。
6. **零外部依赖**：仅用 ``logging`` + ``logging.handlers``。
7. **幂等初始化**：``setup_logging`` 重复调用不会重复挂 handler。

典型用法
========

启动期（``startup.py`` 或 ``__init__.py``）::

    from maxagent.logger import setup_logging
    setup_logging()    # 自动从 AppConfig 读 log_level

业务模块::

    from maxagent.logger import get_logger
    logger = get_logger(__name__)

    logger.info('启动会话: %s', sid)              # 关键节点
    logger.debug('LLM payload: %s', summary)      # 详细模式
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

向后兼容
========
旧 ``log_level='WARNING' / 'ERROR'`` 配置在 ``apply_log_level`` 里被
归一化成 ``'INFO'``——三态简化后没有必要保留中间档。
``use_stderr`` 形参为兼容旧测试保留，传 ``True`` 也只产生空操作。
"""

from __future__ import absolute_import
from __future__ import print_function

import logging
import logging.handlers
import os


# 所有日志统一挂在这个命名空间下，避免污染外部 root logger
ROOT_NAME = 'maxagent'

# 标记位：避免重复初始化（reload 时多次 import 也只挂一次 handler）
_INIT_SENTINEL_ATTR = '_maxagent_log_initialized'

# 默认参数（可被 setup_logging 覆盖）
DEFAULT_LEVEL = 'INFO'
DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 单个文件 2 MB
DEFAULT_BACKUP_COUNT = 5             # 保留 5 份历史

# ---------- 三态枚举 ---------- #
# 用字符串而非 logging 数字常量，让配置文件人类可读
LEVEL_OFF = 'OFF'        # 完全关闭：不写文件、不输出
LEVEL_INFO = 'INFO'      # 开启：只记关键节点
LEVEL_DEBUG = 'DEBUG'    # 详细：所有埋点全量入档
VALID_LEVELS = (LEVEL_OFF, LEVEL_INFO, LEVEL_DEBUG)

# 文件格式：时间 + 级别 + 模块 + 线程 + 消息
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
        # 目录创建失败（权限/只读盘）：返回 None，调用方降级成"不写文件"
        return None
    return log_dir


def _normalize_level(level):
    """把任意输入归一化成 OFF / INFO / DEBUG 三档之一。

    兼容历史值：``'WARNING'`` / ``'ERROR'`` / ``logging.WARNING`` 等
    一律折算成 ``INFO``（三态后没有中间档）；非法值同样回落 ``INFO``。
    ``None`` 也回落 ``INFO``，避免在 setup 阶段抛异常。
    """
    if isinstance(level, int):
        # logging 数字常量：CRITICAL/ERROR/WARNING → INFO，
        # INFO → INFO，DEBUG/NOTSET → DEBUG
        if level <= logging.DEBUG:
            return LEVEL_DEBUG
        if level <= logging.INFO:
            return LEVEL_INFO
        return LEVEL_INFO
    if isinstance(level, str):
        name = level.strip().upper()
        if name in VALID_LEVELS:
            return name
        # 兼容老的 WARNING/ERROR/CRITICAL 配置
        if name in ('WARNING', 'ERROR', 'CRITICAL', 'WARN'):
            return LEVEL_INFO
        if name == 'NOTSET':
            return LEVEL_DEBUG
    return LEVEL_INFO


def _logging_level_for(state):
    """三态 → ``logging`` 数字级别。

    OFF 用 ``logging.CRITICAL + 1``（高于所有真实级别），保证任何
    ``logger.xxx`` 调用都被过滤掉，等价"什么也不写"，但又不需要拆 handler。
    """
    if state == LEVEL_OFF:
        return logging.CRITICAL + 1
    if state == LEVEL_DEBUG:
        return logging.DEBUG
    return logging.INFO


def setup_logging(
    level=None,
    log_dir=None,
    max_bytes=DEFAULT_MAX_BYTES,
    backup_count=DEFAULT_BACKUP_COUNT,
    use_stderr=False,
):
    """初始化 ``maxagent`` 命名空间下的日志系统。

    :param level: 三态字符串 ``'OFF'`` / ``'INFO'`` / ``'DEBUG'``，
                  也接受 ``logging.DEBUG`` 等数字（会被归一化）。
                  ``None`` 时尝试从 ``AppConfig.log_level`` 读取，
                  缺失再回落 ``INFO``。
    :param log_dir: 自定义日志目录；``None`` 时走默认（config_dir/logs）。
    :param max_bytes: 单个日志文件大小上限。
    :param backup_count: 滚动保留的历史文件个数。
    :param use_stderr: **已废弃**——保留参数仅为不破坏旧调用签名，
                       任何取值都不会再向 stderr 输出。
    :returns: 已配置好的 root logger（``maxagent``）。
    """
    # use_stderr 仅为兼容签名保留，故意不读，pylint: disable=unused-argument
    del use_stderr

    root = logging.getLogger(ROOT_NAME)

    # 幂等：已初始化过就只更新级别，不再叠加 handler
    if getattr(root, _INIT_SENTINEL_ATTR, False):
        if level is not None:
            apply_log_level(level)
        return root

    # 决定级别：显式传入 > AppConfig.log_level > 默认 INFO
    if level is None:
        try:
            from .config import load_config
            cfg = load_config()
            level = getattr(cfg, 'log_level', None)
        except Exception:  # pylint: disable=broad-except
            level = None
    state = _normalize_level(level)
    root.setLevel(_logging_level_for(state))

    # 关键：不要把日志冒泡到外部 root logger，避免污染 Max
    root.propagate = False

    formatter = logging.Formatter(_LOG_FORMAT, _DATE_FORMAT)

    # ---- 文件 handler（滚动）：唯一输出通道 ---- #
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
            # 文件 handler 自身设 DEBUG，真正过滤靠 root logger 级别。
            # 这样 INFO/DEBUG 切换只需调 root.setLevel，不需要重建 handler。
            file_handler.setLevel(logging.DEBUG)
            root.addHandler(file_handler)
        except (OSError, IOError):
            # 文件不可写也不致命，降级成"完全静音"
            pass

    # 注意：故意不再添加 StreamHandler。日志彻底不输出到控制台。

    setattr(root, _INIT_SENTINEL_ATTR, True)
    if state != LEVEL_OFF:
        # OFF 时连"系统已初始化"也不写，保持完全静默
        root.info('日志系统已初始化，level=%s, dir=%s', state, log_dir)
    return root


def apply_log_level(level):
    """运行期切换日志级别（三态），不重建 handler。

    :param level: ``'OFF'`` / ``'INFO'`` / ``'DEBUG'``，或可被
                  ``_normalize_level`` 识别的等价值。
    :returns: 实际生效的归一化状态字符串。
    """
    state = _normalize_level(level)
    root = logging.getLogger(ROOT_NAME)
    root.setLevel(_logging_level_for(state))
    if state != LEVEL_OFF:
        # 切到非 OFF 时打一条 info 用作切换审计
        root.info('日志级别已切换为 %s', state)
    return state


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


def is_debug_enabled():
    """快捷判断 root logger 当前是否处于 DEBUG 级别。

    业务侧在做"价高的 DEBUG 摘要构造"前可先用这个短路判断，避免
    构造开销（虽然 ``logger.debug`` 本身已经按级别延迟，但参数表达
    式仍会先求值）。
    """
    return logging.getLogger(ROOT_NAME).isEnabledFor(logging.DEBUG)


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
