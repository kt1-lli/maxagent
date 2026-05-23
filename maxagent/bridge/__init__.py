#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IDE 桥接模块。

在 3ds Max 进程内开启一个本地 TCP 端口，让外部 IDE（通过 ``dcc-mcp``
等 MCP Server）能调用 maxagent 的能力：

- ``execute_python``: 在 Max 主线程执行任意 Python 代码（pymxs 安全）
- ``dispatch_task``: 把整个自然语言任务派发给 maxagent 自己去跑
  （内部调用 LLM + 工具循环），形成 IDE Agent ↔ maxagent Agent 协作

协议格式与 ``dcc-mcp/dcc_mcp/bridge/protocol.py`` 严格对齐：
JSON 一行一帧，``\\n`` 结尾。

启停入口由外部（``startup.py`` / ``dock_widget`` / 设置面板）按 config
中的 ``bridge_*`` 字段控制。
"""

from __future__ import absolute_import
from __future__ import print_function

from .protocol import BRIDGE_PROTOCOL_VERSION
from .protocol import BridgeMethod
from .server import BridgeServer
from .server import get_global_server
from .server import start_global_server
from .server import stop_global_server


__all__ = [
    'BRIDGE_PROTOCOL_VERSION',
    'BridgeMethod',
    'BridgeServer',
    'get_global_server',
    'start_global_server',
    'stop_global_server',
]
