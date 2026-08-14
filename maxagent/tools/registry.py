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


# ---------------------------------------------------------------------- #
# 参数校验
# ---------------------------------------------------------------------- #

try:
    string_types = (str, unicode)  # type: ignore[name-defined]
except NameError:
    string_types = (str,)


def validate_tool_args(name, args):
    # type: (str, Dict[str, Any]) -> "tuple[bool, str]"
    """基于 JSON Schema 校验工具参数。

    :param name: 工具名
    :param args: 参数字典
    :returns: (is_valid: bool, error_message: str)
    """
    spec = get_tool(name)
    if spec is None:
        return (False, "未知工具: {}".format(name))
    if not isinstance(args, dict):
        return (False, "参数必须是对象")

    schema = spec.parameters
    if not isinstance(schema, dict):
        return (True, "")

    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    errors = []

    # 1. 必填字段缺失
    for key in required:
        if key not in args:
            errors.append("参数 '{}' 必填".format(key))
            continue
        value = args[key]
        if value is None:
            prop_schema = properties.get(key, {})
            if not prop_schema.get("nullable"):
                errors.append("参数 '{}' 必填".format(key))

    if errors:
        return (False, "; ".join(errors))

    # 2. 逐个参数按 schema 校验，必要时做安全类型转换
    for key, value in args.items():
        prop_schema = properties.get(key, {})
        if not prop_schema:
            continue

        # 尝试把字符串形式的数字/bool 转换为真实类型
        coerced, value = _coerce_value(value, prop_schema)
        if coerced:
            args[key] = value

        is_valid, error = _validate_value(value, prop_schema, key)
        if not is_valid:
            errors.append(error)

    if errors:
        return (False, "; ".join(errors))
    return (True, "")


def _coerce_value(value, schema):
    # type: (Any, Dict[str, Any]) -> "tuple[bool, Any]"
    """尝试把字符串值安全转换为 schema 期望的类型。

    只处理简单标量：integer / number / boolean。
    数组/对象元素由调用方递归处理。

    :returns: (是否发生转换, 转换后的值)。未转换时返回原值。
    """
    if not isinstance(value, string_types):
        return (False, value)

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        # 多类型时，按 integer -> number -> boolean 顺序尝试
        for t in ("integer", "number", "boolean"):
            if t in expected_type:
                coerced, new_value = _coerce_value_to_type(value, t)
                if coerced:
                    return (True, new_value)
        return (False, value)

    coerced, new_value = _coerce_value_to_type(value, expected_type)
    return (coerced, new_value)


def _coerce_value_to_type(value, type_name):
    # type: (str, str) -> "tuple[bool, Any]"
    """把字符串按单一类型转换。"""
    text = value.strip()
    if type_name == "integer":
        try:
            return (True, int(text))
        except ValueError:
            return (False, value)
    if type_name == "number":
        try:
            return (True, float(text))
        except ValueError:
            return (False, value)
    if type_name == "boolean":
        lower = text.lower()
        if lower in ("true", "1", "yes", "on"):
            return (True, True)
        if lower in ("false", "0", "no", "off"):
            return (True, False)
        return (False, value)
    return (False, value)


def _validate_value(value, schema, path):
    # type: (Any, Dict[str, Any], str) -> "tuple[bool, str]"
    """校验单个值是否符合 schema 定义。"""
    if not isinstance(schema, dict):
        return (True, "")

    errors = []
    expected_type = schema.get("type")

    # 值为 None 时，仅当声明 nullable 或通过 type 允许 null 才通过
    if value is None:
        if expected_type is None:
            return (True, "")
        if isinstance(expected_type, list) and "null" in expected_type:
            return (True, "")
        if expected_type == "null":
            return (True, "")
        if schema.get("nullable"):
            return (True, "")
        return (False, "参数 '{}' 不能为空".format(path))

    # schema 中缺少 type 时按通过处理，不阻塞自定义 schema
    if expected_type is None:
        return (True, "")

    # enum 校验优先于 type（枚举值本身可能跨多种类型）
    enum_values = schema.get("enum")
    if enum_values is not None:
        if value not in enum_values:
            errors.append("参数 '{}' 必须是 {} 之一".format(path, enum_values))

    # type 校验
    type_ok = True
    if isinstance(expected_type, list):
        type_ok = _type_matches_any(value, expected_type)
    else:
        type_ok = _type_matches(value, expected_type)
    if not type_ok:
        errors.append(_type_error(path, value, expected_type))
    else:
        # 数字范围
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = schema.get("minimum")
            if minimum is not None and value < minimum:
                errors.append("参数 '{}' 不能小于 {}".format(path, minimum))
            maximum = schema.get("maximum")
            if maximum is not None and value > maximum:
                errors.append("参数 '{}' 不能大于 {}".format(path, maximum))

        # 字符串长度
        if isinstance(value, string_types):
            min_length = schema.get("minLength")
            if min_length is not None and len(value) < min_length:
                errors.append("参数 '{}' 长度不能小于 {}".format(path, min_length))
            max_length = schema.get("maxLength")
            if max_length is not None and len(value) > max_length:
                errors.append("参数 '{}' 长度不能大于 {}".format(path, max_length))

        # 数组长度及元素类型
        if isinstance(value, list):
            min_items = schema.get("minItems")
            if min_items is not None and len(value) < min_items:
                errors.append("参数 '{}' 元素个数不能小于 {}".format(path, min_items))
            max_items = schema.get("maxItems")
            if max_items is not None and len(value) > max_items:
                errors.append("参数 '{}' 元素个数不能大于 {}".format(path, max_items))
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for idx, item in enumerate(value):
                    is_valid, error = _validate_value(
                        item, item_schema, "{}[{}]".format(path, idx),
                    )
                    if not is_valid:
                        errors.append(error)

    if errors:
        return (False, "; ".join(errors))
    return (True, "")


def _type_matches(value, type_name):
    # type: (Any, str) -> bool
    """判断 value 是否匹配单一 JSON Schema 类型。"""
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "string":
        return isinstance(value, string_types)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "null":
        return value is None
    return False


def _type_matches_any(value, type_names):
    # type: (Any, List[str]) -> bool
    """判断 value 是否匹配多种 JSON Schema 类型之一。"""
    return any(_type_matches(value, t) for t in type_names)


def _type_error(path, value, expected):
    # type: (str, Any, "str | List[str]") -> str
    """构造类型不匹配的中文错误信息。"""
    if isinstance(expected, list):
        expected_str = " 或 ".join(expected)
    else:
        expected_str = expected
    return "参数 '{}' 期望 {}，收到 {}".format(
        path, expected_str, _json_type_name(value),
    )


def _json_type_name(value):
    # type: (Any) -> str
    """把 Python 值映射到 JSON Schema 类型名（用于错误信息）。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, string_types):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__