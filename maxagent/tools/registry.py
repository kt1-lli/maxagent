#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具注册表与 JSON Schema 自动生成。

设计目标：
1. 用 @tool 装饰器声明工具，自动收集到全局注册表。
2. 自动从函数签名 + docstring + 显式 schema 字段生成 OpenAI tools JSON Schema。
3. 支持工具分组、启用/禁用、危险标记。
4. 工具 docstring 同时是给 LLM 的说明，要写清楚每个参数的含义。
"""

from __future__ import absolute_import
from __future__ import print_function

import inspect
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional


# 全局注册表：tool_name -> ToolSpec
_REGISTRY = {}  # type: Dict[str, "ToolSpec"]


class ToolSpec(object):
    """单个工具的元数据与执行入口。"""

    def __init__(
        self,
        name,                       # type: str
        func,                       # type: Callable
        description,                # type: str
        parameters,                 # type: Dict[str, Any]
        category="misc",            # type: str
        dangerous=False,            # type: bool
        wrap_undo=True,             # type: bool
        run_on_main_thread=True,    # type: bool
    ):
        self.name = name
        self.func = func
        self.description = description
        self.parameters = parameters
        self.category = category
        self.dangerous = dangerous
        self.wrap_undo = wrap_undo
        self.run_on_main_thread = run_on_main_thread

    def to_openai_schema(self):
        """生成 OpenAI tools 协议的单条 schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def tool(
    name=None,                  # type: Optional[str]
    description="",             # type: str
    parameters=None,            # type: Optional[Dict[str, Any]]
    category="misc",            # type: str
    dangerous=False,            # type: bool
    wrap_undo=True,             # type: bool
    run_on_main_thread=True,    # type: bool
):
    """工具注册装饰器。

    :param name: 工具名（不传则用函数名）
    :param description: 给 LLM 看的功能描述
    :param parameters: OpenAI 风格的 JSON Schema；不传则按函数签名自动推导
    :param category: 工具分组，便于 UI 展示
    :param dangerous: 是否危险（如删除场景、执行任意代码），UI 会高亮提醒
    :param wrap_undo: 执行时是否包 undo 上下文
    :param run_on_main_thread: 是否需要 marshal 回主线程执行（pymxs 调用必须 True）
    """

    def _decorator(func):
        tool_name = name or func.__name__
        if tool_name in _REGISTRY:
            raise ValueError("工具名重复: {}".format(tool_name))

        desc = description or (inspect.getdoc(func) or "").split("\n\n")[0].strip()
        params = parameters if parameters is not None else _infer_parameters(func)

        spec = ToolSpec(
            name=tool_name,
            func=func,
            description=desc,
            parameters=params,
            category=category,
            dangerous=dangerous,
            wrap_undo=wrap_undo,
            run_on_main_thread=run_on_main_thread,
        )
        _REGISTRY[tool_name] = spec
        # 让被装饰函数仍可直接调用
        func.__tool_spec__ = spec
        return func

    return _decorator


# ---------------------------------------------------------------------- #
# 自动推导 JSON Schema
# ---------------------------------------------------------------------- #

_PY_TO_JSON = {
    int: "integer",
    float: "number",
    str: "string",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _annotation_to_schema(anno):
    """把 Python type annotation 简单映射到 JSON Schema 类型。"""
    if anno is inspect.Parameter.empty:
        return {"type": "string"}
    # typing.Optional[X] / Union[X, None]
    origin = getattr(anno, "__origin__", None)
    args = getattr(anno, "__args__", ())
    if origin is not None:
        # Optional[X] = Union[X, None]
        if args and type(None) in args:
            non_none = [a for a in args if a is not type(None)]  # noqa: E721
            if len(non_none) == 1:
                schema = _annotation_to_schema(non_none[0])
                schema["nullable"] = True
                return schema
        # List[X]
        if origin in (list,) or getattr(origin, "__name__", "") == "list":
            item_schema = (
                _annotation_to_schema(args[0]) if args else {"type": "string"}
            )
            return {"type": "array", "items": item_schema}
        # Dict[str, X]
        if origin in (dict,) or getattr(origin, "__name__", "") == "dict":
            return {"type": "object"}
    if anno in _PY_TO_JSON:
        return {"type": _PY_TO_JSON[anno]}
    return {"type": "string"}


def _default_to_schema(default):
    """从默认值倒推 JSON Schema 类型（type hint 缺失时的兜底）。"""
    if default is inspect.Parameter.empty or default is None:
        return None
    # bool 必须先于 int 判断（bool 是 int 子类）
    if isinstance(default, bool):
        return {"type": "boolean"}
    if isinstance(default, int):
        return {"type": "integer"}
    if isinstance(default, float):
        return {"type": "number"}
    if isinstance(default, str):
        return {"type": "string"}
    if isinstance(default, (list, tuple)):
        return {"type": "array"}
    if isinstance(default, dict):
        return {"type": "object"}
    return None


def _infer_parameters(func):
    """从函数签名自动构造 JSON Schema parameters 对象。

    类型推导优先级:
    1) 函数签名中的 type annotation
    2) 默认值的 Python 类型（如 default=10.0 -> number）
    3) 兜底 string
    """
    sig = inspect.signature(func)
    properties = {}
    required = []

    # 解析 docstring 中的 :param name: desc 为参数描述
    doc = inspect.getdoc(func) or ""
    param_docs = {}
    for line in doc.splitlines():
        line = line.strip()
        if line.startswith(":param "):
            try:
                head, desc = line.split(":", 2)[1:]
                pname = head.split()[-1]
                param_docs[pname] = desc.strip()
            except (ValueError, IndexError):
                continue

    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        # 1) 优先使用 annotation
        if param.annotation is not inspect.Parameter.empty:
            schema = _annotation_to_schema(param.annotation)
        else:
            # 2) 用默认值兜底
            from_default = _default_to_schema(param.default)
            schema = from_default if from_default is not None else {"type": "string"}
        if pname in param_docs:
            schema["description"] = param_docs[pname]
        properties[pname] = schema
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    out = {
        "type": "object",
        "properties": properties,
    }
    if required:
        out["required"] = required
    return out


# ---------------------------------------------------------------------- #
# 注册表查询
# ---------------------------------------------------------------------- #

def get_tool(name):
    """按名称查询工具。"""
    return _REGISTRY.get(name)


def list_tools(category=None, include_dangerous=True):
    """列出已注册工具。

    :param category: 仅列出指定分组（None 表示全部）
    :param include_dangerous: 是否包含 dangerous=True 的工具
    """
    out = []
    for spec in _REGISTRY.values():
        if category is not None and spec.category != category:
            continue
        if not include_dangerous and spec.dangerous:
            continue
        out.append(spec)
    return out


def build_openai_tools_schema(category=None, include_dangerous=True):
    """生成 OpenAI tools 数组，可直接塞给 chat.completions。

    会过滤掉「我的资源 → 工具」里被用户禁用的项，让 LLM 完全感知不到
    禁用工具的存在（既不会在 schema 中出现，也无法被自然语言触发）。
    禁用名单读取失败时按"无禁用项"处理，避免循环依赖在启动期阻塞。
    """
    # 延迟 import 防止 ``maxagent.disabled_registry`` <-> ``maxagent.tools``
    # 形成启动期循环依赖（registry 在 tools 包内，被 tools/__init__ 引用）。
    try:
        from ..disabled_registry import get_disabled_tools_set
        disabled = get_disabled_tools_set()
    except Exception:  # pylint: disable=broad-except
        disabled = set()
    return [
        spec.to_openai_schema()
        for spec in list_tools(
            category=category, include_dangerous=include_dangerous,
        )
        if spec.name not in disabled
    ]


def clear_registry():
    """清空注册表（仅测试用）。"""
    _REGISTRY.clear()
