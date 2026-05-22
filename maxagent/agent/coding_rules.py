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
🚨 代码生成硬性规则（强制 - 违反将被判定为错误回答）🚨
使用 run_maxscript / run_python 工具时，必须 100% 遵守以下规则。
==============================================================

【🔥🔥🔥 MaxScript if 控制流模板 - 最高优先级，必须按模板填空 🔥🔥🔥】

只允许以下三种模板，任何偏离都会被工具入口直接拒绝执行：

模板 1（有 else，必须 then + else 配对）：
  if <条件> then (
      -- 真分支代码
  ) else (
      -- 假分支代码
  )

模板 2（无 else，用 do）：
  if <条件> do (
      -- 真分支代码
  )

模板 3（单行表达式）：
  if <条件> then <表达式真> else <表达式假>
  if <条件> do <表达式真>

❌ 永远禁止的写法（本工具入口会扫描并拒绝执行，下例完全等价于语法错误）：
  if <条件> do ( ... ) else ( ... )      ← do 配 else 是错的
  if <条件> ( ... ) else ( ... )         ← 缺 then 关键字
  if <条件> then ( ... )                 ← 既无 else 又用了 then（应改 do）

记住口诀：
  "有 else 一定 then；只一支一定 do；do 永不配 else。"


【🔴 反幻觉铁律 - 优先级仅次于 if 模板】
- 严禁捏造任何 API：函数名 / 方法名 / 属性名 / 参数签名 / 修改器名 /
  全局变量名都必须是 3ds Max 官方文档中真实存在的；不确定就不写。
- 不确定 API 是否存在时，必须按以下顺序处理：
  1) 先用 run_maxscript 跑最小验证脚本，例如 `isProperty objects #foo`、
     `getPropNames Box01`、`showProperties $`；
  2) 用 `classOf` / `superClassOf` / `getInterfaces` 反查；
  3) 仍无法确认时，明确告知用户"不确定该 API 是否存在"，请用户确认或换方案，
     绝对禁止写一段"看起来很合理"的代码当成解决方案交付。
- 涉及不熟悉的修改器 / 控制器 / 渲染器（如 V-Ray、Corona、第三方插件）时，
  必须先探测 `pluginManager.pluginDllName` 或 classOf 是否可用，再调用。
- 回答中只要出现"应该是"、"大概"、"我记得"、"通常"等不确定表述时，对应代码
  必须改为先探测后执行，不允许直接返回。

【通用 - 适用所有代码】
- 所有注释必须使用中文。
- 标识符（变量名 / 函数名）必须使用英文 camelCase；禁止中文命名。
- 不要使用语言保留关键字作为标识符。

【MaxScript 专用规则】
- 变量声明：所有变量必须用 local / global / persistent global 显式声明，
  禁止隐式全局。
- ⚠️ local 作用域铁律：local 只在它所属的【一对括号 ( ... )】或函数体
  /控制流块内有效。这意味着：
  * 在 fn 函数体最外层、rollout 事件处理体内、`( ... )` 表达式块内声明的
    local，仅在该块内可见；离开括号即失效。
  * 多个 local 必须写在【同一个】括号块内才能互相访问；不要把 local 写在
    不同括号块里再期望跨块访问。
  * 顶层脚本想要跨块共享变量必须用 global / persistent global，不能用 local。
  * 错误示范：`( local a = 1 ) ( print a )` —— 第二个括号里 a 已经不存在。
  * 正确示范：`( local a = 1; print a )` —— 同一括号块内使用。
- if 控制流：见顶部【if 控制流模板】，必须按模板填空。
- 函数必须使用 return 显式返回值，不依赖最后一行隐式返回。
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
- 禁止 MaxScript 中出现 `if ... do ... else ...`（do 不能配 else）。
- 禁止 MaxScript 中出现 `if ... else` 而缺少 `then`。
- 禁止把 local 声明放在与使用处不同的括号块。
==============================================================
"""


def get_coding_rules():
    """返回硬规则字符串。

    供 ``conversation.DEFAULT_SYSTEM_PROMPT`` 拼接使用。
    单独函数化是为方便单元测试和后续动态扩展。

    :returns: 规则文本（多行字符串）
    """
    return CODING_RULES


# ---------------------------------------------------------------------- #
# 入口端轻量校验：在 run_maxscript 真正执行前，扫描代码里的硬性语法错误。
# 与"硬规则文本"双管齐下：规则文本约束 LLM 自觉，校验器兜底拦截 LLM 偏差。
# ---------------------------------------------------------------------- #

import re  # noqa: E402  本文件其它部分本来就是纯 Python，无 std 依赖

# if-do-else 反模式：do 子句后又出现 else，等价于语法错误
# 用正则匹配 "if ... do ... else"，跨行（DOTALL）。
# 为了避免把 `for ... do ... else` 之类用法误伤，限定 if 起头。
_RE_IF_DO_ELSE = re.compile(
    r"\bif\b[^\n]*?\bdo\b[\s\S]+?\belse\b",
    re.IGNORECASE,
)

# if 缺 then：形如 `if cond ( ... ) else` —— 在 cond 与括号 / else 之间没有 then 关键字
# 该模式较保守，只标志最常见的 `if <表达式> ( ... ) else` 误用
_RE_IF_MISSING_THEN = re.compile(
    r"\bif\b\s+[^\n()]+?\(\s*[\s\S]+?\)\s*else\b",
    re.IGNORECASE,
)


def validate_maxscript_syntax(code):
    """在执行前扫描 MaxScript 代码，拦截已知硬性语法错误。

    设计目标：哪怕 LLM 没遵守 system prompt 里的规则，工具入口也要把
    "肯定跑不通"的代码挡回去，附带改写建议，让 LLM 下一轮自动修正。

    :param code: 待执行的 MaxScript 源码字符串
    :returns: 二元组 ``(ok, error_msg)``：
              - ok=True, error_msg=None  —— 通过校验，可以执行
              - ok=False, error_msg=str  —— 命中硬性错误，给 LLM 的修复建议
    """
    if not isinstance(code, str) or not code.strip():
        return True, None

    # 同时包含 if 与 else 的代码才需要细查；否则直接放行（避免无意义遍历）
    has_if = re.search(r"\bif\b", code, re.IGNORECASE)
    has_else = re.search(r"\belse\b", code, re.IGNORECASE)
    if not (has_if and has_else):
        return True, None

    # ---- 检查 1：if-do-else 反模式 ----
    # 找到第一段疑似命中的子串（DOTALL），用于精准报错
    if _RE_IF_DO_ELSE.search(code):
        # 进一步降误判：确认 do 与 else 之间不存在另一个 if-then-else
        # （多层嵌套时 outer if 用 do、inner if 用 then-else 会误伤）。
        # 实测 MaxScript 中 do 子句已被消费，外层 do 后再接 else 一定是错的；
        # 这里保留简化判定，直接报错。
        return (
            False,
            (
                "MaxScript 语法错误：检测到 `if ... do ... else ...` 写法。\n"
                "在 MaxScript 中 `do` 子句不能配 `else`。请改写为：\n"
                "  if <条件> then (\n"
                "      -- 真分支\n"
                "  ) else (\n"
                "      -- 假分支\n"
                "  )\n"
                "（即把 `do` 替换为 `then`，与下方的 `else` 配对。）\n"
                "请按上述模板重新生成代码后再次调用 run_maxscript。"
            ),
        )

    # ---- 检查 2：if 缺 then 关键字（与 else 同时出现时才查） ----
    if _RE_IF_MISSING_THEN.search(code) and not re.search(
        r"\bif\b[^\n]*?\bthen\b",
        code,
        re.IGNORECASE,
    ):
        return (
            False,
            (
                "MaxScript 语法错误：if/else 之间缺少 `then` 关键字。\n"
                "正确模板：`if <条件> then (...) else (...)`。\n"
                "请补上 `then` 后再次调用 run_maxscript。"
            ),
        )

    return True, None
