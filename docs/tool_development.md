# 自定义工具开发指南

> 想让 AI 干 MaxAgent 内置 51 个工具之外的事？这篇就是给你的。

## 🚀 最小示例

新建文件 `maxagent/tools/my_tools.py`：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""我的自定义工具集。"""

from __future__ import absolute_import

from .registry import tool


@tool(
    name='greet_object',
    description='给场景中的某个对象打个招呼（演示用）',
)
def greet_object(target_name: str, message: str = 'Hello!') -> dict:
    """演示工具：在 Listener 中打印问候。

    :param target_name: 目标对象名
    :param message: 问候语，默认 "Hello!"
    :return: 执行结果
    """
    from pymxs import runtime as rt
    obj = rt.getNodeByName(target_name)
    if obj is None:
        return {'ok': False, 'error': '对象不存在: ' + target_name}
    rt.format('% to %\n', message, target_name)
    return {
        'ok': True,
        'target': target_name,
        'said': message,
    }
```

然后在 `maxagent/tools/__init__.py` 的 `_DEFAULT_MODULES` 列表中加一行：

```python
_DEFAULT_MODULES = [
    'scene_query',
    'geometry',
    # ... 已有的
    'my_tools',  # ← 新增
]
```

重启 Max，问 AI："给 Box001 说 hi"——它会自己调你的工具。

---

## 📐 类型注解 → JSON Schema

工具的参数会自动通过 `inspect.signature` 推导成 JSON Schema 给 LLM 看。

| Python 注解 | LLM 看到的 Schema | 备注 |
|------------|------------------|------|
| `name: str` | `{"type": "string"}` | |
| `count: int` | `{"type": "integer"}` | |
| `radius: float` | `{"type": "number"}` | |
| `enabled: bool` | `{"type": "boolean"}` | |
| `tags: list` | `{"type": "array"}` | items 不指定 |
| `params: dict` | `{"type": "object"}` | properties 不指定 |
| `axis: List[float]` | `{"type": "array", "items": {"type": "number"}}` | 需要 `from typing import List` |
| `target: Optional[str]` | `{"type": ["string", "null"]}` | 自动加上 null |
| 无注解 | 看默认值推导 | 无默认值则 `{"type": "string"}` |

### 常用枚举/约束

```python
from typing import Literal

@tool('set_render_engine')
def set_render_engine(
    engine: Literal['scanline', 'arnold', 'vray'] = 'scanline',
) -> dict:
    ...
```

`Literal` 会被识别为 `enum`：

```json
{"type": "string", "enum": ["scanline", "arnold", "vray"]}
```

---

## 📝 写好 description（最重要）

LLM 是通过 `description` 决定**调不调你的工具**和**怎么调**的。

### ❌ 反面教材

```python
@tool('xxx', description='干一些事')
def xxx(a, b): ...
```

LLM：???

### ✅ 正确写法

```python
@tool(
    name='align_to_floor',
    description=(
        '把指定对象沿 Z 轴向下对齐到地面 (Z=0)，对象的 bounding box '
        '最低点会贴到地面。如果对象已经在地面以下，则向上移动。'
        '不会修改 X/Y 坐标。失败时返回 ok=false。'
    ),
)
def align_to_floor(target_name: str) -> dict:
    """详细 docstring 用于人读..."""
    ...
```

要点：
1. **动词开头**："创建"、"设置"、"删除"
2. **明确边界**：什么会做 / 什么不会做
3. **明确返回**：成功是什么样、失败是什么样
4. **写单位**：度还是弧度、cm 还是 m

---

## 🔒 撤销支持（重要！）

`dispatcher` 自动用 `pymxs.undo("工具名")` 包了一层。这意味着：

- 用户可以 Ctrl+Z 撤销 AI 的每一次操作 ✓
- 你**不需要**自己手动加 `with rt.undo(True): ...`

但有几个**例外**情况你要注意：

```python
@tool(
    'expensive_calculation',
    description='只读的复杂计算，不要包 undo',
    readonly=True,   # ← 标记为只读，dispatcher 不包 undo
)
def expensive_calculation(...) -> dict:
    ...
```

```python
@tool('flush_undo_stack', description='清空撤销栈', dangerous=True)
def flush_undo_stack(): ...   # dangerous=True UI 会显示警告图标
```

---

## 🧪 在 Max 之外测试你的工具

工具函数本质就是 Python 函数，可以直接调：

```python
# tests/test_my_tools.py
import sys, types

# Mock pymxs
fake = types.ModuleType('pymxs')
class _RT:
    def getNodeByName(self, n): return types.SimpleNamespace(name=n)
    def format(self, *a, **k): pass
fake.runtime = _RT()
sys.modules['pymxs'] = fake

from maxagent.tools.my_tools import greet_object

def test_greet_ok():
    r = greet_object('Box001', message='Hi')
    assert r['ok'] is True
    assert r['target'] == 'Box001'
```

跑：
```bash
python -m pytest tests/test_my_tools.py
```

---

## 🎯 调试技巧

### 1. 看 LLM 实际收到的 Schema

```python
from maxagent.tools import build_openai_tools_schema, load_all_tools
load_all_tools()
import json
schemas = build_openai_tools_schema()
# 找到你的工具
for s in schemas:
    if s['function']['name'] == 'greet_object':
        print(json.dumps(s, indent=2, ensure_ascii=False))
        break
```

### 2. 手动调用工具（绕过 LLM）

```python
from maxagent.tools import ToolDispatcher
disp = ToolDispatcher()
result = disp.dispatch('greet_object', {
    'target_name': 'Box001',
    'message': '测试',
})
print(result)
```

### 3. 看 LLM 调用的完整 conversation

UI 的 🗑 旁边可以加一个调试按钮：

```python
# dock_widget.py 加一个保存按钮
def _save_conv(self):
    self._conv.save('C:/temp/maxagent_dump.json')
```

然后用任何 JSON 查看器看完整流程。

---

## 📚 进阶：批量工具

如果你的工具有大量相似操作（比如 20 种修改器），可以用工厂模式：

```python
from .registry import tool

_MODIFIER_TYPES = {
    'turbosmooth': 'TurboSmooth',
    'meshsmooth': 'MeshSmooth',
    'shell': 'Shell',
    # ...
}


def _make_modifier_tool(short_name, mxs_class):
    @tool(
        name='add_{}'.format(short_name),
        description='给对象添加 {} 修改器'.format(mxs_class),
    )
    def _add(target_name: str, iterations: int = 1) -> dict:
        from pymxs import runtime as rt
        obj = rt.getNodeByName(target_name)
        if obj is None:
            return {'ok': False, 'error': 'not found'}
        mod = getattr(rt, mxs_class)()
        if hasattr(mod, 'iterations'):
            mod.iterations = iterations
        rt.addModifier(obj, mod)
        return {'ok': True}
    return _add


for short, cls in _MODIFIER_TYPES.items():
    _make_modifier_tool(short, cls)
```

---

## ⚠️ 常见坑

| 坑 | 解决 |
|----|------|
| pymxs 调用在子线程崩溃 | 工具函数永远只在 dispatcher 派回主线程后执行，你不需要管 |
| 中文对象名 LLM 给错 | 在 system prompt 加规则："对象名用拼音/英文" |
| 默认值是可变对象 `params: dict = {}` | 改成 `Optional[dict] = None`，函数内 `params or {}` |
| 函数没返回值导致 LLM 不知道结果 | **永远** 返回 `dict`，至少 `{'ok': True}` |
| 工具名重复注册 | registry 会报错，改名或重启 Max |

---

## 🌟 最佳实践清单

- [ ] description 写得像写给同事看
- [ ] 所有参数有类型注解
- [ ] 返回值始终是 dict，含 `ok` 字段
- [ ] 失败时返回 `{'ok': False, 'error': '具体原因'}`
- [ ] readonly 工具加 `readonly=True`
- [ ] 危险工具加 `dangerous=True`
- [ ] 函数行数 < 40 行（拆成多个工具或私有函数）
- [ ] 没有副作用的 import（pymxs 在函数内 import）
