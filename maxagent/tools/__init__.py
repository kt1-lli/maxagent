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

from ..logger import get_logger
from .dispatcher import ToolDispatcher
from .dispatcher import ToolExecutionError
from .batch import batch_execute
from .batch import set_global_dispatcher
from .registry import build_openai_tools_schema
from .registry import get_tool
from .registry import list_tools
from .registry import tool


_LOADED = False
logger = get_logger(__name__)


def load_all_tools(include_escape_hatch=True, load_user_tools=True):
    """导入并注册所有内置工具模块。

    :param include_escape_hatch: 已废弃，保留仅用于兼容；脚本工具
        ``run_maxscript`` / ``run_python`` 现为标准工具，默认始终注册。
    :param load_user_tools: 是否扫描并加载 ``user_tools/`` 下用户学习到的工具
    :returns: 已注册工具的总数
    """
    global _LOADED  # pylint: disable=global-statement

    import importlib
    import sys

    # pylint: disable=import-outside-toplevel,unused-import
    modules = [
        'maxagent.tools.scene_query',
        'maxagent.tools.geometry',
        'maxagent.tools.transform',
        'maxagent.tools.modifier',
        'maxagent.tools.material',
        'maxagent.tools.light_camera',
        'maxagent.tools.render',
        'maxagent.tools.scene_io',
        'maxagent.tools.skills_tools',
        'maxagent.tools.learn_tools',
        'maxagent.tools.learn_rules',
        'maxagent.tools.reflection_tools',
        'maxagent.tools.knowledge_tools',
        'maxagent.tools.web_tools',
        'maxagent.tools.autodesk_docs',
        'maxagent.tools.memory_tools',
        'maxagent.tools.creative',
        'maxagent.tools.viewport_capture',
        'maxagent.tools.scene_awareness',
        'maxagent.tools.todo_tools',
        'maxagent.tools.high_level',
        'maxagent.tools.animation',
        'maxagent.tools.class_tree',
    ]
    for name in modules:
        try:
            mod = sys.modules.get(name)
            if mod is not None:
                importlib.reload(mod)
            else:
                importlib.import_module(name)
        except Exception:  # pylint: disable=broad-except
            # 某个模块加载失败不应影响其他模块
            logger.exception('加载工具模块失败: %s', name)

    # 脚本工具（run_maxscript / run_python）已作为标准工具始终加载。
    # include_escape_hatch 保留为兼容参数，不再控制是否卸载。
    from . import scripting  # noqa: F401
    if not include_escape_hatch:
        logger.debug(
            'include_escape_hatch=False 已忽略，脚本工具现为标准工具'
        )

    # batch 模块在包 __init__ 顶层已被导入（导出了 batch_execute / set_global_dispatcher），
    # 这里跳过重复加载，避免 reload 时工具名重复。
    if 'maxagent.tools.batch' not in sys.modules:
        try:
            importlib.import_module('maxagent.tools.batch')
        except Exception:  # pylint: disable=broad-except
            logger.exception('加载工具模块失败: maxagent.tools.batch')

    # 加载用户学习的工具（失败不影响主流程）
    if load_user_tools:
        try:
            from ..user_tools_loader import load_user_tools as _lu
            r = _lu()
            if r.get('errors'):
                for k, v in r['errors'].items():
                    logger.warning('user tool %s 加载失败: %s', k, v)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('用户工具扫描异常: %s', exc)

    # 扫描 description 质量，提醒缺少说明的工具
    try:
        from .registry import scan_tool_description_quality
        warnings = scan_tool_description_quality()
        if warnings:
            logger.warning(
                '以下工具缺少完整说明（详见 docs/tool_description_guide.md）：\n%s',
                '\n'.join('  - ' + w for w in warnings),
            )
    except Exception:  # pylint: disable=broad-except
        pass

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
    'set_global_dispatcher',
    'batch_execute',
]