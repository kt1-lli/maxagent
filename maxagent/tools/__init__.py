#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maxagent.tools 包入口。

统一暴露：
- load_all_tools(): 一次性导入所有工具模块，触发 @tool 装饰器注册
- get_tool / list_tools / build_openai_tools_schema：来自 registry
- ToolDispatcher：来自 dispatcher

工具按 DCC 分层存放：
- tools/shared/：跨 DCC 通用工具
- tools/max/：3ds Max 专用工具（声明 dcc=['3dsmax']）
- tools/maya/：Maya 专用工具（声明 dcc=['maya']，后续 Phase 添加）
"""

from __future__ import absolute_import
from __future__ import print_function

from ..logger import get_logger
from .dispatcher import ToolDispatcher
from .dispatcher import ToolExecutionError
from .shared.batch import batch_execute
from .shared.batch import set_global_dispatcher
from .registry import build_openai_tools_schema
from .registry import get_tool
from .registry import list_tools
from .registry import tool


_LOADED = False
logger = get_logger(__name__)


# 通用工具模块（所有 DCC 都加载）
_SHARED_MODULES = {
    'maxagent.tools.shared.batch',
    'maxagent.tools.shared.knowledge_tools',
    'maxagent.tools.shared.learn_rules',
    'maxagent.tools.shared.learn_tools',
    'maxagent.tools.shared.memory_tools',
    'maxagent.tools.shared.reflection_tools',
    'maxagent.tools.shared.skills_tools',
    'maxagent.tools.shared.todo_tools',
    'maxagent.tools.shared.web_tools',
}

# 3ds Max 专用工具模块
_MAX_MODULES = [
    'maxagent.tools.max.animation',
    'maxagent.tools.max.autodesk_docs',
    'maxagent.tools.max.class_tree',
    'maxagent.tools.max.creative',
    'maxagent.tools.max.geometry',
    'maxagent.tools.max.high_level',
    'maxagent.tools.max.light_camera',
    'maxagent.tools.max.material',
    'maxagent.tools.max.modifier',
    'maxagent.tools.max.render',
    'maxagent.tools.max.scene_awareness',
    'maxagent.tools.max.scene_io',
    'maxagent.tools.max.scene_query',
    'maxagent.tools.max.scripting',
    'maxagent.tools.max.transform',
    'maxagent.tools.max.viewport_capture',
]

# Maya 专用工具模块（占位，后续 Phase 实现）
_MAYA_MODULES = [
]


def _discover_modules(force_dcc=None):
    """根据当前 DCC 环境返回要加载的模块列表。

    :param force_dcc: 强制指定 DCC 环境，主要用于测试；None 时使用
        ``current_dcc()`` 自动探测。
    """
    from ..dcc.runtime import current_dcc
    dcc = force_dcc if force_dcc is not None else current_dcc()
    modules = list(_SHARED_MODULES)
    if dcc == '3dsmax':
        modules.extend(_MAX_MODULES)
    elif dcc == 'maya':
        modules.extend(_MAYA_MODULES)
    else:
        logger.info(
            '当前 DCC 为 %s，仅加载通用工具模块', dcc,
        )
    return modules


def load_all_tools(include_escape_hatch=True, load_user_tools=True, force_dcc=None):
    """导入并注册所有内置工具模块。

    :param include_escape_hatch: 已废弃，保留仅用于兼容；脚本工具
        ``run_maxscript`` / ``run_python`` 现为标准工具，默认始终注册。
    :param load_user_tools: 是否扫描并加载 ``user_tools/`` 下用户学习到的工具
    :param force_dcc: 强制指定 DCC 环境，主要用于测试
    :returns: 已注册工具的总数
    """
    global _LOADED  # pylint: disable=global-statement

    import importlib
    import sys

    for name in _discover_modules(force_dcc=force_dcc):
        try:
            mod = sys.modules.get(name)
            if mod is not None:
                importlib.reload(mod)
            else:
                importlib.import_module(name)
        except Exception:  # pylint: disable=broad-except
            logger.exception('加载工具模块失败: %s', name)

    # 脚本工具（run_maxscript / run_python）已作为标准工具始终加载。
    if not include_escape_hatch:
        logger.debug(
            'include_escape_hatch=False 已忽略，脚本工具现为标准工具'
        )

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

    # 扫描 description 质量
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
