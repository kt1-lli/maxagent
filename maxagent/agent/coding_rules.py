#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 代码生成硬性规则。

这些规则在每次创建 Conversation 时被拼接到系统提示词末尾，
作为 LLM 通过 ``run_maxscript`` / ``run_python`` 工具生成代码时
必须遵守的强制约束。

规则来源：
- maxagent/docs/maxscript_rules.md
- maxagent/docs/python_pymxs_rules.md

设计原则：
1. 只放对 LLM 推理结果有约束力的"硬规则"，不放教程/解释/速查表。
2. 写成简短列表，每条一行，方便 LLM 在 system prompt 中快速扫读。
3. 完整规范保留在 docs 目录，供人类查阅与扩展。
"""

from __future__ import absolute_import
from __future__ import print_function


# 注：以下规则被嵌入到 system prompt，**禁止**随意扩写。
# 每加一条都意味着每轮 LLM 调用都会多消耗 tokens。
# 真正面向 LLM 推理结果的"硬约束"才放到这里；
# 教程性、解释性内容请放到 docs/ 下的完整规范文件。

CODING_RULES = """\
==============================================================
代码生成硬性规则（使用 run_maxscript / run_python 时必须遵守）
==============================================================

【通用 - 适用所有代码】
- 所有注释必须使用中文。
- 标识符（变量名 / 函数名）必须使用英文 camelCase；禁止中文命名。
- 不要使用语言保留关键字作为标识符。
- 不要捏造 API；不确定 API 是否存在时，先用查询工具或最小验证脚本探测。

【MaxScript 专用规则】
- 所有变量必须用 local / global / persistent global 显式声明，禁止隐式全局。
- 函数必须使用 return 显式返回值，不依赖最后一行隐式返回。
- if 控制流：有 else 用 `if cond then ... else ...`；无 else 用 `if cond do ...`。
- for 循环：遍历用 `for x in coll do ...`；计数用 `for i = 1 to N do ...`；
  收集用 `result = for x in coll collect expr`。
- 数组索引从 1 开始（`arr[1]` 是第一个元素）。
- 字符串拼接频繁时用 stringStream，避免在循环里 `+= str`。
- 大批量元素构造数组用 `for ... collect`，禁止循环内反复 append。
- 全局变量命名加 `g_` 前缀；常量用 UPPER_SNAKE_CASE；UI 控件用类型前缀
  （btn / spn / txt / lbl / chk / ddl / lst / sld）。
- 危险操作或 UI 更新用 try-catch 包裹；不要在循环体内频繁刷新 UI。

【Python + pymxs 专用规则】
- 必须 `from pymxs import runtime as rt`，统一别名 rt。
- 比较 pymxs 包装对象身份：必须用 `==`，禁止用 `is`（is 永远返回 False）。
- 索引规则（最易错）：
  * 访问 pymxs 数组（如 `rt.meditMaterials`、`rt.selection`）用 0-based。
  * 把索引作为参数传给 MaxScript 函数（如 `rt.setMeditMaterial(i, m)`）用 1-based。
- pymxs.runtime 的属性/方法名大小写不敏感，但**统一写成 MaxScript 文档里的 camelCase**。
- MaxScript Name 字面量（如 `#world`）在 Python 里写成 `rt.Name('world')`。
- MaxScript 上下文表达式用 with 语法对应：
  * `animate on` → `with pymxs.animate(True):`
  * `at time T`  → `with pymxs.atime(T):`
  * `undo on`    → `with pymxs.undo(True):`
  * 其他不可用的上下文（`coordsys`、`about` 等）需改用具体函数（如
    `rt.setRefCoordSys(rt.Name('world'))`）。
- 复杂返回值（Array / Dictionary）建议先 `list(...)` / `dict(...)` 转 Python 原生。
- 关键字参数：MaxScript 的 `key:value` 在 Python 里写成 `key=value`。
- 可能失败的 pymxs 调用用 try/except 包裹，并对 None 返回值显式判空
  （如 `obj = rt.getNodeByName(name); if obj is None: ...`）。

【两端共同的禁止项】
- 禁止使用中文标识符。
- 禁止使用幻觉 API（不存在的函数 / 方法 / 属性）。
- 禁止把 0-based / 1-based 索引混用。
- 禁止用 `is` 比较 pymxs 对象。
- 禁止在不确认 DLL 是否加载、文件是否存在的情况下直接调用外部资源。
==============================================================
"""


def get_coding_rules():
    """返回硬规则字符串。

    供 ``conversation.DEFAULT_SYSTEM_PROMPT`` 拼接使用。
    单独函数化是为方便单元测试和后续动态扩展。

    :returns: 规则文本（多行字符串）
    """
    return CODING_RULES
