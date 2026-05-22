# Python + pymxs 编码规范 (LLM Coding Rules)

> **用途**: 供 LLM 生成 Python + pymxs 代码时遵守的强制规则  
> **强制要求**: 所有注释必须使用中文

---

## 目录

- [核心强制规则](#核心强制规则)
- [导入规范](#导入规范)
- [命名规范](#命名规范)
- [大小写敏感规则（重点）](#大小写敏感规则重点)
- [索引规则（重点）](#索引规则重点)
- [相等性测试规则（重点）](#相等性测试规则重点)
- [pymxs.runtime 使用规范](#pymxsruntime-使用规范)
- [pymxs Name 字面量规范](#pymxs-name-字面量规范)
- [上下文表达式规范](#上下文表达式规范)
- [撤销/重做规范](#撤销重做规范)
- [类型转换规范](#类型转换规范)
- [场景对象操作规范](#场景对象操作规范)
- [异常处理规范](#异常处理规范)
- [禁止事项（重点）](#禁止事项重点)
- [最佳实践](#最佳实践)
- [检查清单](#检查清单)
- [语法速查表](#语法速查表)

---

## 核心强制规则

### C001 [MUST] 所有注释必须使用中文

```python
# ✓ 正确：使用中文注释
# 计算骨骼偏移量
offset = 0

# ✗ 错误：使用英文注释
# Calculate bone offset
offset = 0
```

---

### C002 [MUST] pymxs.runtime 统一使用别名 rt

```python
# ✓ 正确
from pymxs import runtime as rt

# ✗ 错误
from pymxs import runtime  # 缺少别名
```

---

### C003 [MUST_NOT] 禁止使用 is 测试 pymxs 对象相等性

> **原因**: pymxs 对象是包装对象，is 比较会返回 False

```python
# ✓ 正确
if t == t2:
    pass

# ✗ 错误
if t is t2:  # 永远为 False
    pass
```

---

### C004 [MUST] 访问 pymxs 数组使用 0-based 索引

> **原因**: pymxs 自动转换 MaxScript 1-based 到 Python 0-based

```python
# ✓ 正确
mats = rt.meditMaterials
first = mats[0]  # 正确

# ✗ 错误
first = mats[1]  # 错误！应该是 mats[0]
```

---

### C005 [MUST] 传递索引给 MaxScript 函数使用 1-based

```python
# ✓ 正确
rt.setMeditMaterial(1, mat)  # 正确

# ✗ 错误
rt.setMeditMaterial(0, mat)  # 错误！槽位 0 不存在
```

---

### C006 [MUST_NOT] 禁止使用不存在的 pymxs/MaxScript API

**常见幻觉 API 对比**：

| 幻觉 API | 正确写法 |
|---------|----------|
| `rt.getBoneRotation(bone)` | `bone.rotation` 或 `bone.transform.rotationPart` |
| `rt.setLayerWeight(layer, weight)` | LayerSkin 需自行实现 |
| `rt.exportFBX(filePath)` | `rt.FBXExporter.Export(filePath)` |
| `rt.getSelectedBones()` | `list(rt.selection)` |

**验证流程**：
1. 查阅 Autodesk 官方文档
2. 确认 API 在官方文档中存在

---

### C007 [MUST] Python 代码标识符使用英文，注释使用中文

```python
# ✓ 正确
boneList = []  # 骨骼列表

# ✗ 错误
骨骼列表 = []  # 禁止使用中文命名
```

---

### C008 [MUST_NOT] 禁止使用 Python 关键字作为变量名

**保留关键字列表**：

| 类别 | 关键字 |
|------|--------|
| 常量 | `False`, `None`, `True` |
| 逻辑/运算 | `and`, `or`, `not`, `is`, `in` |
| 控制流 | `if`, `else`, `elif`, `for`, `while`, `break`, `continue`, `pass`, `return`, `yield` |
| 函数/类 | `def`, `class`, `lambda` |
| 异常 | `try`, `except`, `finally`, `raise`, `assert` |
| 导入 | `import`, `from`, `as` |
| 其他 | `async`, `await`, `del`, `global`, `nonlocal`, `with` |

```python
# ✓ 正确
ifFlag = True

# ✗ 错误
if = True  # if 是关键字
```

---

## 导入规范

**规则**: MUST use standard import aliases

```python
# ✓ 正确
from pymxs import runtime as rt
import pymxs as pmx

# ✗ 错误
from pymxs import runtime  # 缺少别名，调用繁琐
```

> **注意**: `rt` 是社区约定俗成的别名

---

## 命名规范

### 变量命名

- **规则**: MUST use camelCase
- **有效**: `targetRootBone`, `layerWeightData`
- **无效**: `target_root_bone`, `layerweightdata`

### 函数命名

- **规则**: MUST use camelCase，动词开头
- **有效**: `calculateOffset()`, `applyWeight()`, `getBoneByName(name)`
- **无效**: `Calculate_Offset()`, `计算偏移()`

### 常量命名

- **规则**: MUST use `UPPER_SNAKE_CASE`
- **有效**: `MAX_BONE_COUNT = 100`, `DEFAULT_WEIGHT = 0.5`
- **无效**: `maxBoneCount`

### 私有成员命名

- **规则**: SHOULD use single underscore prefix
- **有效**: `_internalCache`, `_validateInput()`

---

## 大小写敏感规则（重点）

> **【重点】Python 大小写敏感，但 pymxs.runtime 不敏感**  
> **重要性**: CRITICAL - 极易出错

### Python 是大小写敏感的

```python
# ✓ 正确
print()

# ✗ 错误
Print()  # NameError
```

### pymxs.runtime 是大小写不敏感的

```python
# 以下三种写法等价
rt.converttomesh()
rt.convertToMesh()
rt.CONVERTTOMESH()
```

> **建议**: 统一使用 MaxScript 文档中的 camelCase 大小写格式

---

## 索引规则（重点）

> **【重点】Python 0-based，MaxScript 1-based，pymxs 自动转换**  
> **重要性**: CRITICAL - 最常见的 Bug 来源

### 规则总结

| 场景 | 索引规则 |
|------|---------|
| 场景对象访问（pymxs 数组） | 0-based |
| MaxScript 函数索引参数 | 1-based |
| meditMaterials 访问 | 0-based |
| setMeditMaterial 参数 | 1-based |
| selection 访问 | 0-based（`rt.selection[i]`） |
| `for i = 1 to N do` 的 Python 等价 | `for i in range(N):` |

### 示例

```python
# ✓ 正确：访问 pymxs 数组（使用 0-based）
mats = rt.meditMaterials
first_mat = mats[0]  # 第一个槽位

# ✓ 正确：传递索引给 MaxScript 函数（使用 1-based）
mat = rt.PhysicalMaterial()
rt.setMeditMaterial(1, mat)  # 设置第一个槽位

# ✗ 错误
mats = rt.meditMaterials
first = mats[1]  # 错误！应该是 mats[0]

rt.setMeditMaterial(0, mat)  # 错误！槽位 0 不存在
```

---

## 相等性测试规则（重点）

> **【重点】pymxs 对象必须使用 == 而非 is**  
> **重要性**: CRITICAL - is 会返回 False 即使引用同一场景对象

### 规则

> **原因**: pymxs 对象是包装对象，每次访问会创建新包装，is 比较身份会失败

```python
from pymxs import runtime as rt

t = rt.Teapot(name='myTeapot')
t2 = rt.getNodeByName('myTeapot')

# ✓ 正确
if t == t2:
    print("是同一个对象")

# ✗ 错误
if t is t2:  # 永远为 False
    print("这行永远不会执行")
```

> **注意**: 对于普通 Python 对象，is 仍然适用；仅 pymxs 包装对象需要用 ==

---

## pymxs.runtime 使用规范

### 基本用法

```python
from pymxs import runtime as rt

t = rt.Teapot()  # 创建茶壶
box = rt.Box()  # 创建盒子
print(rt.selection)  # 获取当前选择
```

### 属性访问

```python
# MaxScript 属性通过 . 访问，不区分大小写
t.pos  # 等价
t.POS  # 等价
t.Pos  # 等价
```

### 方法调用

```python
rt.convertToMesh(box)
rt.move(t, rt.Point3(10, 0, 0))
```

### 关键字参数

```python
# MaxScript 的 key: value 语法转换为 Python 的 key=value

# MaxScript 语法
# bm = Bitmap 320 240 color:white
# render to:bm

# pymxs 等价写法
bm = rt.Bitmap(320, 240, color=rt.White)
rt.render(to=bm)
```

---

## pymxs Name 字面量规范

> MaxScript 使用 Name 字面量（如 `#world`），pymxs 中需用 `rt.Name()` 创建

### 规则

```python
# MaxScript 语法
# toolMode.coordsys #world

# pymxs 等价写法
rt.toolMode.coordsys(rt.Name('world'))
```

### 常用 Name 字面量

| 类别 | 值 |
|------|-----|
| 坐标系 | `rt.Name('world')`, `rt.Name('local')`, `rt.Name('screen')` |
| 变换中心 | `rt.Name('pivot')`, `rt.Name('center')` |

---

## 上下文表达式规范

> MaxScript 上下文表达式（`animate on`、`at time` 等）在 pymxs 中使用 `with` 语句

### 动画上下文示例

```python
# MaxScript 语法
# t = Teapot()
# with animate on (
#     at time 0 t.position = [-100,0,0]
#     at time 100 t.position = [100,0,0]
# )

# pymxs 等价写法
import pymxs
from pymxs import runtime as rt

t = rt.Teapot()
with pymxs.animate(True):
    with pymxs.atime(0):
        t.pos = rt.Point3(-100, 0, 0)
    with pymxs.atime(100):
        t.pos = rt.Point3(100, 0, 0)
```

### 可用的上下文表达式

| 上下文 | 说明 |
|--------|------|
| `pymxs.animate(True\|False)` | 开启动画录制 |
| `pymxs.atime(time)` | 在指定时间执行 |
| `pymxs.atlevel(True\|False)` | 在指定层级 |
| `pymxs.quiet(True\|False)` | 静默模式 |
| `pymxs.redraw(True\|False)` | 重绘控制 |
| `pymxs.undo(True\|False)` | 撤销控制 |

### 不可用的上下文表达式

以下 MaxScript 上下文表达式在 pymxs 中不可用：

- `coordsys` → 需用 `rt.setRefCoordSys(name)`
- `about`
- `printAllElements`
- `defaultAction`
- `MXSCallStackCaptureEnabled`
- `dontRepeatMessages`
- `macroRecorderEmitterEnabled`

---

## 撤销/重做规范

> pymxs 通过 `pymxs.run_undo()` 和 `pymxs.run_redo()` 访问撤销/重做

### 基本用法

```python
import pymxs
from pymxs import runtime as rt

t = rt.Teapot()

# 启用撤销
with pymxs.undo(True):
    t.pos = rt.Point3(20, 20, 20)

# 撤销
pymxs.run_undo()

# 重做
pymxs.run_redo()
```

> **注意**: 撤销块中发生异常时，已执行的代码会被撤销，异常不会传播到撤销块外。这与 MaxScript 行为不同，需注意。

---

## 类型转换规范

### 简单类型

> 简单类型（int、float、str）自动转换，可直接使用

| Python 类型 | MaxScript 类型 | 转换 |
|-------------|---------------|------|
| int | integer | 自动 |
| float | float | 自动 |
| str | string | 自动 |

### 复杂类型

> 复杂类型（Array、Dictionary）被包装为 MXSWrapper 对象，需使用对应方法访问，或转换为 Python 原生类型

### 数组转换

```python
# MaxScript
# arr = #(1, 2, 3)

# pymxs
arr = rt.array(1, 2, 3)
print(arr[0])  # 0-based 访问
print(len(arr))  # 获取长度

# 转换为 Python list
py_list = list(arr)
print(py_list)  # [1, 2, 3]
```

### 字典转换

```python
# MaxScript
# d = Dictionary #(#one, 1) #(#two, 2)

# pymxs
d = rt.Dictionary(("one", 1), ("two", 2))

# 获取键（返回 MaxScript Array of Name）
keys = d.keys

# 转换为 Python dict
py_dict = {str(k): d[k] for k in d.keys}
print(py_dict)  # {'one': 1, 'two': 2}
```

### Point3 转换

```python
# MaxScript: [x,y,z]
# pymxs: rt.Point3(x,y,z)
pos = rt.Point3(10, 20, 30)
rt.move(obj, rt.Point3(0, 100, 0))
```

### Color 转换

```python
# MaxScript: (color r g b)
# pymxs: rt.Color(r,g,b)
c = rt.Color(255, 128, 0)
obj.wireColor = rt.Color(255, 0, 0)  # 设置为红色
```

#### ⚠️ 颜色相关高频踩坑（项目实战教训）

**坑 1：`rt.Color` 必须大写 C，写成 `rt.color` 会得到完全错误的颜色**

pymxs 在 attribute lookup 时**区分大小写**。小写 `rt.color(255, 0, 0)` 不会报错（pymxs 会模糊匹配到一个旧式 MaxScript 函数），但返回的对象赋给 `mat.diffuse` 后通道顺序会错乱，
表现为"红色变墨绿、白色变暗红"。**唯一正确写法：大写首字母 `rt.Color`**。

```python
# ✅ 正确
mat.diffuse = rt.Color(255, 0, 0)

# ❌ 错误（曾导致茶壶变墨绿色的真实 bug）
mat.diffuse = rt.color(255, 0, 0)
```

同理 `rt.Point3`、`rt.Box`、`rt.Teapot`、`rt.Standardmaterial` 等所有"类型构造器"
均遵循 MaxScript 类名 pascal-case，**禁止全小写**。

**坑 2：通过 `rt.execute()` 拼接 MaxScript 时，颜色字面量不能写成 Python 列表**

MaxScript 中颜色字面量是 `(color 255 0 0)`（**空格分隔，不是逗号**），与 Python 的
`[255, 0, 0]` 完全不兼容。把 Python 列表 `str()` 后直接拼进 MaxScript 字符串会得到
`(color 2 5 5)`（解析成单字符 `'2'`/`'5'`/`'5'`），渲染为深青色。

```python
# ❌ 错误：把 Python 列表当成 MaxScript 颜色字面量
rgb = [255, 0, 0]
rt.execute('mat.diffuse = (color {})'.format(rgb))
# 实际生成: mat.diffuse = (color [255, 0, 0])  ← MaxScript 解析失败 / 错乱

# ✅ 推荐 1：根本不用 execute，直接 pymxs 对象操作
mat.diffuse = rt.Color(255, 0, 0)

# ✅ 推荐 2：必须用 execute 时，手动展开为空格分隔
r, g, b = 255, 0, 0
rt.execute('mat.diffuse = (color {} {} {})'.format(r, g, b))
```

**坑 3：分量范围 0~255 vs 0~1 不要混用**

3ds Max 内部 `rt.Color()` 使用 **0~255** 整数分量（与 MaxScript 一致）。LLM 容易按
其它 DCC 软件（Maya / Blender）习惯传 `[1.0, 0.0, 0.0]` 当成红色——pymxs **不会自动归一化**，
分量 1.0 几乎等于黑色。本项目 `tools/material.py::_to_color` 做了自动判断：
``max(r,g,b) <= 1.0`` 时按 0~1 处理并放大 255 倍，否则按 0~255 直接使用。
**对外暴露的工具签名应保持这一约定**。

```python
# 0~255 范围（推荐）
mat.diffuse = rt.Color(255, 0, 0)

# 0~1 范围（需先放大）
r, g, b = 1.0, 0.0, 0.0
mat.diffuse = rt.Color(r * 255, g * 255, b * 255)
```

**坑 4：`mat.diffuse` 与 `mat.diffuseMap` 不是同一个东西**

- `mat.diffuse`：纯色 `rt.Color` 对象
- `mat.diffuseMap`：贴图节点（`rt.Bitmaptexture` 等）
- PhysicalMaterial 用 `mat.base_color` / `mat.base_color_map`

赋值时不要混用，否则会静默失败（材质看起来"没变化"）。

---

## 场景对象操作规范

### 创建对象

```python
from pymxs import runtime as rt

# 创建茶壶（使用默认值）
t = rt.Teapot()

# 创建茶壶（指定参数）
t = rt.Teapot(radius=50, pos=rt.Point3(100, 20, 10), segments=8)
```

### 访问对象

```python
from pymxs import runtime as rt

# 获取对象
obj = rt.getNodeByName("Box001")
if obj is not None:
    print(obj.name)

# 遍历选中对象
for item in rt.selection:
    print(item.name)
```

### 修改对象

```python
from pymxs import runtime as rt

# 修改位置
obj = rt.getNodeByName("Box001")
obj.pos = rt.Point3(100, 200, 300)

# 修改参数
obj.height = 50
obj.width = 100
```

### 添加修改器

```python
from pymxs import runtime as rt

obj = rt.Teapot()
taper_mod = rt.Taper(amount=2.0, curve=1.5)
rt.addModifier(obj, taper_mod)
```

---

## 异常处理规范

### try-except 包裹 pymxs 调用

```python
try:
    obj = rt.getNodeByName("Nonexistent")
    if obj is None:
        raise ValueError("对象不存在")
    print(obj.name)
except ValueError as e:
    # 注意：本项目规则禁止 f-string，统一使用 .format() 字符串模板
    print('错误: {}'.format(e))
```

> **注意**: 撤销块中的异常处理需特别注意。不同 3ds Max 版本的撤销块异常处理行为可能不同，建议充分测试。

---

## 禁止事项（重点）

### P001 [MUST_NOT] 禁止使用不存在的 pymxs / MaxScript API

```python
# ✗ 错误：以下 API 不存在
rt.getBoneRotation(bone)  # 不存在
rt.setLayerWeight(layer, weight)  # 不存在
rt.exportFBX(filePath)  # 不存在
rt.getSelectedBones()  # 不存在
```

---

### P002 [MUST_NOT] 禁止使用 is 测试 pymxs 对象相等性

```python
# ✓ 正确
if t == t2:
    pass

# ✗ 错误
if t is t2:
    pass
```

---

### P003 [MUST_NOT] 禁止混淆 0-based 和 1-based 索引

> **注意**: 访问 pymxs 数组用 0-based；传递索引给 MaxScript 函数用 1-based

---

### P004 [MUST_NOT] 禁止使用中文命名 Python 变量或函数

```python
# ✓ 正确
boneList = []

# ✗ 错误
骨骼列表 = []
```

---

### P005 [MUST_NOT] 禁止使用 Python 关键字作为变量名

```python
# ✗ 错误
if = 10
for = []
return = True
```

---

### P006 [MUST_NOT] 禁止使用小写 `rt.color`，颜色构造器必须 `rt.Color`

pymxs attribute lookup 区分大小写。小写 `rt.color()` 不会立刻报错，但返回的对象通道
顺序错乱，赋给 `mat.diffuse` 会出现"红变绿、白变暗红"等诡异颜色 bug。**所有类型构造器
统一遵循 MaxScript pascal-case**：`rt.Color`、`rt.Point3`、`rt.Box`、`rt.Teapot`、
`rt.Standardmaterial` 等。

```python
# ✗ 错误（曾导致茶壶变墨绿色的真实 bug）
mat.diffuse = rt.color(255, 0, 0)

# ✓ 正确
mat.diffuse = rt.Color(255, 0, 0)
```

---

### P007 [MUST_NOT] 禁止把 Python 列表当成 MaxScript 颜色字面量拼进 `rt.execute`

MaxScript 的颜色字面量是 `(color 255 0 0)`（**空格分隔**），与 Python 列表
`[255, 0, 0]` 完全不兼容。`str([255, 0, 0])` 拼进 MaxScript 字符串后会被解析成
`(color 2 5 5)`（按字符切），渲染为深青色而非红色。

```python
# ✗ 错误
rgb = [255, 0, 0]
rt.execute('mat.diffuse = (color {})'.format(rgb))

# ✓ 推荐：直接用 pymxs 对象，避免 execute
mat.diffuse = rt.Color(255, 0, 0)

# ✓ 必须用 execute 时，手动展开为空格分隔
r, g, b = 255, 0, 0
rt.execute('mat.diffuse = (color {} {} {})'.format(r, g, b))
```

---

## 最佳实践

1. 所有注释必须使用中文
2. pymxs.runtime 统一使用别名 rt
3. 访问 pymxs 数组使用 0-based 索引
4. 传递索引给 MaxScript 函数使用 1-based
5. pymxs 对象相等性测试使用 ==，禁用 is
6. MaxScript 关键字参数转换为 Python keyword argument 语法
7. MaxScript Name 字面量（`#name`）使用 `rt.Name('name')` 创建
8. 上下文表达式（`animate/on`、`at time`）使用 `with` 语句
9. 撤销操作使用 `with pymxs.undo(True):` 包裹
10. 复杂类型（Array、Dictionary）优先转换为 Python 原生类型再操作
11. 函数必须有中文注释说明功能、参数、返回值
12. 使用 try-except 包裹可能失败的 pymxs 调用

---

## 检查清单

### 生成前检查

- [ ] 检查：是否所有注释使用中文？
- [ ] 检查：是否使用了不存在的 pymxs/MaxScript API？
- [ ] 检查：变量名是否使用了 Python 关键字？

### 生成后检查

- [ ] **【索引检查】** 访问 pymxs 数组是否使用了 0-based？
- [ ] **【索引检查】** 传递索引给 MaxScript 函数是否使用了 1-based？
- [ ] **【相等性检查】** 是否使用 == 而非 is 测试 pymxs 对象？
- [ ] **【API 检查】** 是否使用了不存在的 pymxs/MaxScript API？
- [ ] **【大小写检查】** pymxs.runtime 调用是否使用了标准 camelCase 大小写？
- [ ] **【撤销检查】** 需要撤销的操作是否用 `with pymxs.undo(True):` 包裹？
- [ ] 所有注释是否使用中文？
- [ ] 函数是否有中文注释说明？
- [ ] 是否处理了可能的异常情况 (try-except)？

---

## 语法速查表

### 导入与初始化

```python
# 标准导入方式
import pymxs
from pymxs import runtime as rt

# 验证 pymxs 可用
print(rt.maxVersion())  # 打印 3ds Max 版本
```

### 创建与修改对象

```python
# 创建对象
box = rt.Box(pos=rt.Point3(0, 0, 0), width=50, height=50, depth=50)

# 修改属性
box.pos = rt.Point3(100, 200, 0)
box.wireColor = rt.Color(255, 0, 0)  # 红色
```

### 动画关键帧

```python
t = rt.Teapot()
with pymxs.animate(True):
    with pymxs.atime(0):
        t.pos = rt.Point3(-100, 0, 0)
    with pymxs.atime(100):
        t.pos = rt.Point3(100, 0, 0)
```

### 选择操作

```python
# 获取选中对象
sel = list(rt.selection)
for obj in sel:
    print(obj.name)

# 清空选择
rt.select(None)

# 选择对象
rt.select(box)
```

### 材质操作

```python
# 获取材质编辑器槽位（0-based 访问）
mats = rt.meditMaterials
first_mat = mats[0]

# 设置材质（1-based 参数）
mat = rt.PhysicalMaterial()
rt.setMeditMaterial(1, mat)
```

---

## 文档信息

| 项目 | 值 |
|------|-----|
| 标题 | Python + pymxs 编码规范 (LLM Rules) |
| 版本 | 2.1 |
| 强制规则摘要 | 所有注释必须使用中文；pymxs.runtime 统一使用别名 rt；访问 pymxs 数组使用 0-based 索引；传递索引给 MaxScript 函数使用 1-based；pymxs 对象相等性测试使用 ==，禁用 is；禁止使用不存在的 API；禁止使用 Python 关键字作为变量名；类型构造器必须 pascal-case（rt.Color / rt.Point3 / rt.Box，禁止 rt.color 等小写形式）；rt.execute 拼接 MaxScript 时颜色字面量是空格分隔 `(color r g b)`，禁止把 Python 列表 str() 后直接拼入 |
