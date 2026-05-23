#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bridge TCP 服务器。

启动 / 停止幂等，可被设置面板的开关安全反复触发。

线程模型::

    [外部 IDE] --tcp--> [BridgeServer 后台 accept 线程]
                            ↓ 每连接一线程（短连接）
                      [_handle_connection]
                            ↓ 读一行 JSON
                            ↓ dispatch by method
                      [handlers/*.py]
                            ↓ 必要时 invoke 回主线程
                      [pymxs / worker 等]

handlers 内部按需走 ``main_thread_runner`` 把代码扔回 Qt 主线程跑（保证
pymxs 安全）；返回值再写回 socket。

对外暴露三个全局函数 ``start_global_server`` / ``stop_global_server`` /
``get_global_server``，供 ``startup.py`` 和设置面板调用。
"""

from __future__ import absolute_import
from __future__ import print_function

import socket
import threading
import traceback
from typing import Any
from typing import Callable
from typing import Optional

from ..logger import get_logger
from .protocol import BridgeErrorCode
from .protocol import BridgeMethod
from .protocol import BRIDGE_PROTOCOL_VERSION
from .protocol import encode_frame
from .protocol import make_response
from .protocol import read_frame


logger = get_logger(__name__)


# accept 阻塞超时（秒），保证 stop() 后线程能及时退出
_ACCEPT_TIMEOUT = 1.0


class BridgeServer(object):
    """Bridge TCP 服务器。

    :param host: 监听地址，默认 ``127.0.0.1``
    :param port: 监听端口，默认 ``7003``
    :param token: 可选访问令牌，非空时请求必须带 ``"token"`` 字段
    :param main_thread_runner: 把 callable 调度回 Qt 主线程执行的钩子，
        签名 ``(fn, *args, timeout=...) -> Any``。``None`` 时所有工作
        都在后台线程跑（仅用于无 Max 环境的单元测试）。
    :param config_manager: 用于 dispatch_task 复用 LLM/会话；测试时可 None
    :param dispatch_max_rounds: dispatch 单任务最大工具循环轮数
    :param dispatch_timeout_sec: dispatch 单任务总超时
    :param dispatch_enabled: dispatch_task 是否对外暴露
    """

    def __init__(self, host='127.0.0.1', port=7003, token='',
                 main_thread_runner=None, config_manager=None,
                 dispatch_max_rounds=20, dispatch_timeout_sec=300,
                 dispatch_enabled=True):
        # type: (...) -> None
        self.host = str(host or '127.0.0.1')
        self.port = int(port or 7003)
        self.token = str(token or '')
        self._main_thread_runner = main_thread_runner
        self._config_manager = config_manager
        self._dispatch_max_rounds = int(dispatch_max_rounds or 20)
        self._dispatch_timeout_sec = float(dispatch_timeout_sec or 300)
        self._dispatch_enabled = bool(dispatch_enabled)

        self._sock = None  # type: Optional[socket.socket]
        self._accept_thread = None  # type: Optional[threading.Thread]
        self._running = False
        # 保护 start/stop 与状态读取
        self._lock = threading.Lock()
        # dispatch_task 单实例锁：同时只能跑一个 dispatch，
        # 避免多 IDE 客户端并发打架抢 worker / 主线程
        self._dispatch_lock = threading.Lock()
        # 实时连接计数（仅供 UI 状态显示）
        self._active_connections = 0
        self._stats_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def is_running(self):
        # type: () -> bool
        with self._lock:
            return self._running

    def active_connections(self):
        # type: () -> int
        with self._stats_lock:
            return self._active_connections

    def start(self):
        # type: () -> None
        """启动监听；已运行时是 no-op。"""
        with self._lock:
            if self._running:
                logger.debug('bridge already running, skip start')
                return
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_REUSEADDR, 1,
                )
                sock.bind((self.host, self.port))
                sock.listen(8)
                sock.settimeout(_ACCEPT_TIMEOUT)
            except OSError as exc:
                logger.warning(
                    'bridge bind failed %s:%d: %s',
                    self.host, self.port, exc,
                )
                raise
            self._sock = sock
            self._running = True
            t = threading.Thread(
                target=self._accept_loop,
                name='maxagent-bridge-accept',
            )
            t.daemon = True
            t.start()
            self._accept_thread = t
            logger.info(
                'bridge started on %s:%d (token=%s, dispatch=%s)',
                self.host, self.port,
                'yes' if self.token else 'no',
                'on' if self._dispatch_enabled else 'off',
            )

    def stop(self, join_timeout=3.0):
        # type: (float) -> None
        """停止监听；未运行时是 no-op。"""
        with self._lock:
            if not self._running:
                return
            self._running = False
            sock = self._sock
            self._sock = None
        # close socket 让 accept 抛错跳出
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._accept_thread
        self._accept_thread = None
        if thread is not None:
            thread.join(timeout=float(join_timeout or 3.0))
        logger.info('bridge stopped')

    # ------------------------------------------------------------------ #
    # 主接受循环
    # ------------------------------------------------------------------ #
    def _accept_loop(self):
        # type: () -> None
        sock = self._sock
        while self._running and sock is not None:
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                # stop() 关掉了 socket
                break
            t = threading.Thread(
                target=self._handle_connection,
                args=(conn, addr),
                name='maxagent-bridge-conn',
            )
            t.daemon = True
            t.start()

    # ------------------------------------------------------------------ #
    # 单连接处理
    # ------------------------------------------------------------------ #
    def _handle_connection(self, conn, addr):
        # type: (socket.socket, Any) -> None
        with self._stats_lock:
            self._active_connections += 1
        try:
            self._handle_connection_inner(conn, addr)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('bridge connection error from %s: %s', addr, exc)
        finally:
            try:
                conn.close()
            except OSError:
                pass
            with self._stats_lock:
                self._active_connections -= 1

    def _handle_connection_inner(self, conn, addr):
        # type: (socket.socket, Any) -> None
        # 读一帧 JSON 请求
        try:
            raw = read_frame(conn)
        except ValueError as exc:
            self._send_error(
                conn, 'unknown',
                BridgeErrorCode.INVALID_RESPONSE,
                'read frame failed: {}'.format(exc),
            )
            return

        # 解析 JSON
        try:
            from .protocol import decode_frame
            req = decode_frame(raw)
        except (ValueError, TypeError) as exc:
            self._send_error(
                conn, 'unknown',
                BridgeErrorCode.INVALID_RESPONSE,
                'json decode failed: {}'.format(exc),
            )
            return

        if not isinstance(req, dict):
            self._send_error(
                conn, 'unknown',
                BridgeErrorCode.INVALID_RESPONSE,
                'request must be a json object',
            )
            return

        request_id = str(req.get('request_id') or 'unknown')
        method = str(req.get('method') or '')
        payload = req.get('payload') or {}
        if not isinstance(payload, dict):
            payload = {}

        logger.debug(
            'bridge req from %s: method=%s rid=%s',
            addr, method, request_id,
        )

        # 鉴权（可选 token）
        if self.token:
            req_token = str(req.get('token') or payload.get('token') or '')
            if req_token != self.token:
                self._send_error(
                    conn, request_id,
                    BridgeErrorCode.UNAUTHORIZED,
                    'invalid or missing token',
                )
                return

        # 路由
        try:
            resp = self._dispatch_method(method, payload, request_id)
        except Exception as exc:  # pylint: disable=broad-except
            tb = traceback.format_exc()
            logger.exception('bridge handler crashed: %s', exc)
            resp = make_response(
                request_id, False,
                error_code=BridgeErrorCode.INTERNAL_ERROR,
                error_message='handler crashed: {}'.format(exc),
                error_details={'traceback': tb},
            )

        try:
            conn.sendall(encode_frame(resp))
        except OSError as exc:
            logger.warning('bridge send response failed: %s', exc)

    # ------------------------------------------------------------------ #
    # 方法路由
    # ------------------------------------------------------------------ #
    def _dispatch_method(self, method, payload, request_id):
        # type: (str, dict, str) -> dict
        if method == BridgeMethod.CAPABILITIES:
            return self._handle_capabilities(request_id)

        if method == BridgeMethod.EXECUTE_PYTHON:
            from .handlers.execute_python import handle_execute_python
            return handle_execute_python(
                payload=payload,
                request_id=request_id,
                main_thread_runner=self._main_thread_runner,
            )

        if method == BridgeMethod.DISPATCH_TASK:
            if not self._dispatch_enabled:
                return make_response(
                    request_id, False,
                    error_code=BridgeErrorCode.UNSUPPORTED_METHOD,
                    error_message='dispatch_task is disabled in settings',
                )
            # 单实例锁：同时只能跑一个 dispatch_task
            if not self._dispatch_lock.acquire(blocking=False):
                return make_response(
                    request_id, False,
                    error_code=BridgeErrorCode.BUSY,
                    error_message=(
                        'another dispatch_task is in progress, '
                        'please retry later'
                    ),
                )
            try:
                from .handlers.dispatch_task import handle_dispatch_task
                return handle_dispatch_task(
                    payload=payload,
                    request_id=request_id,
                    main_thread_runner=self._main_thread_runner,
                    config_manager=self._config_manager,
                    default_max_rounds=self._dispatch_max_rounds,
                    default_timeout_sec=self._dispatch_timeout_sec,
                )
            finally:
                self._dispatch_lock.release()

        return make_response(
            request_id, False,
            error_code=BridgeErrorCode.UNSUPPORTED_METHOD,
            error_message='unsupported method: {}'.format(method),
        )

    def _handle_capabilities(self, request_id):
        # type: (str) -> dict
        """返回本 bridge 支持的方法集，供 dcc-mcp 端按能力注册工具。"""
        methods = [BridgeMethod.EXECUTE_PYTHON, BridgeMethod.CAPABILITIES]
        if self._dispatch_enabled:
            methods.append(BridgeMethod.DISPATCH_TASK)
        return make_response(request_id, True, data={
            'protocol_version': BRIDGE_PROTOCOL_VERSION,
            'dcc': '3dsMax',
            'methods': methods,
            'dispatch_max_rounds': self._dispatch_max_rounds,
            'dispatch_timeout_sec': self._dispatch_timeout_sec,
        })

    # ------------------------------------------------------------------ #
    # 错误响应快捷
    # ------------------------------------------------------------------ #
    def _send_error(self, conn, request_id, code, message, details=None):
        # type: (socket.socket, str, str, str, Optional[dict]) -> None
        try:
            resp = make_response(
                request_id, False,
                error_code=code, error_message=message,
                error_details=details,
            )
            conn.sendall(encode_frame(resp))
        except OSError as exc:
            logger.debug('bridge send error frame failed: %s', exc)


# ---------------------------------------------------------------------- #
# 全局单例（供 startup / 设置面板使用）
# ---------------------------------------------------------------------- #
_GLOBAL_SERVER = None  # type: Optional[BridgeServer]
_GLOBAL_LOCK = threading.Lock()


def get_global_server():
    # type: () -> Optional[BridgeServer]
    with _GLOBAL_LOCK:
        return _GLOBAL_SERVER


def start_global_server(host='127.0.0.1', port=7003, token='',
                        main_thread_runner=None, config_manager=None,
                        dispatch_max_rounds=20, dispatch_timeout_sec=300,
                        dispatch_enabled=True):
    # type: (...) -> BridgeServer
    """启动 / 重启全局 bridge。

    若已有运行实例且参数完全一致，直接返回；否则先停旧再起新。
    """
    global _GLOBAL_SERVER  # pylint: disable=global-statement
    with _GLOBAL_LOCK:
        old = _GLOBAL_SERVER
        if old is not None and old.is_running() \
                and old.host == host and int(old.port) == int(port) \
                and old.token == token \
                and bool(old._dispatch_enabled) == bool(dispatch_enabled):
            return old
        if old is not None:
            try:
                old.stop()
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning('stop old bridge failed: %s', exc)
        srv = BridgeServer(
            host=host, port=port, token=token,
            main_thread_runner=main_thread_runner,
            config_manager=config_manager,
            dispatch_max_rounds=dispatch_max_rounds,
            dispatch_timeout_sec=dispatch_timeout_sec,
            dispatch_enabled=dispatch_enabled,
        )
        srv.start()
        _GLOBAL_SERVER = srv
        return srv


def stop_global_server():
    # type: () -> None
    """停止全局 bridge；不存在则 no-op。"""
    global _GLOBAL_SERVER  # pylint: disable=global-statement
    with _GLOBAL_LOCK:
        srv = _GLOBAL_SERVER
        _GLOBAL_SERVER = None
    if srv is not None:
        try:
            srv.stop()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('stop global bridge failed: %s', exc)
