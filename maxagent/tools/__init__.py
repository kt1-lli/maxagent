#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maxagent.tools 包入口。

统一暴露：
- load_all_tools(): 一次性导入所有工具模块，触发 @tool 装饰器注册
- get_tool / list_tools / build_openai_tools_schema：来自 registry
- ToolDispatcher：来自 dispatcher
"""

from __future__ import absolute_import
from __future__ import print_function

from .dispatcher import ToolDispatcher
from .dispatcher import ToolExecutionError
from .registry import build_openai_tools_schema
from .registry import get_tool
from .registry import list_tools
from .registry import tool


_LOADED = False


def load_all_tools(include_escape_hatch=True, load_user_tools=True):
    """导入并注册所有内置工具模块。

    :param include_escape_hatch: 是否注册 run_maxscript / run_python 逃生舱
    :param load_user_tools: 是否扫描并加载 ``user_tools/`` 下用户学习到的工具
    :returns: 已注册工具的总数
    """
    global _LOADED  # pylint: disable=global-statement

    # pylint: disable=import-outside-toplevel,unused-import
    from . import scene_query  # noqa: F401
    from . import geometry     # noqa: F401
    from . import transform    # noqa: F401
    from . import modifier     # noqa: F401
    from . import material     # noqa: F401
    from . import light_camera # noqa: F401
    from . import render       # noqa: F401
    from . import scene_io     # noqa: F401
    from . import skills_tools # noqa: F401
    from . import learn_tools  # noqa: F401

    if include_escape_hatch:
        from . import escape_hatch  # noqa: F401
    else:
        # 已加载过，再安全卸载一次
        from .escape_hatch import unregister_escape_hatch
        unregister_escape_hatch()

    # 加载用户学习的工具（失败不影响主流程）
    if load_user_tools:
        try:
            from ..user_tools_loader import load_user_tools as _lu
            r = _lu()
            if r.get('errors'):
                for k, v in r['errors'].items():
                    print('[maxagent] user tool {} 加载失败: {}'.format(k, v))
        except Exception as exc:  # pylint: disable=broad-except
            print('[maxagent] 用户工具扫描异常: {}'.format(exc))

    _LOADED = True
    return len(list_tools())


__all__ = [
    'tool',
    'get_tool',
    'list_tools',
    'build_openai_tools_schema',
    'ToolDispatcher',
    'ToolExecutionError',
    'load_all_tools',
]
