# MaxScript 编码规范 (LLM Coding Rules)

> **用途**: 供 LLM 生成 MaxScript 代码时遵守的强制规则  
> **强制要求**: 所有注释必须使用中文

---

## 目录

- [核心强制规则](#核心强制规则)
- [命名规范](#命名规范)
- [语法规则（重点）](#语法规则重点)
- [数据类型语法](#数据类型语法)
- [运算符](#运算符)
- [字符串操作](#字符串操作)
- [数组操作](#数组操作)
- [异常处理](#异常处理)
- [UI 开发规范](#ui-开发规范)
- [.NET 交互规范](#net-交互规范)
- [性能优化](#性能优化)
- [禁止事项（强制）](#禁止事项强制)
- [生成代码检查清单](#生成代码检查清单)
- [语法速查表](#语法速查表)

---

## 核心强制规则

### C001 [MUST] 所有注释必须使用中文

```maxscript
-- ✓ 正确：使用中文注释
-- 计算骨骼偏移量
local offset = 0

-- ✗ 错误：使用英文注释
-- Calculate bone offset
local offset = 0
```

---

### C002 [MUST] 所有变量必须使用 local 或 global 显式声明

```maxscript
-- ✓ 正确：显式声明
local offset = 0
global g_version = 1.0

-- ✗ 错误：隐式全局变量
offset = 0  -- 禁止！
```

---

### C003 [MUST_NOT] 禁止使用 MaxScript 保留关键字作为标识符

**保留关键字列表**：

| 类别 | 关键字 |
|------|--------|
| 控制流 | `if`, `then`, `else`, `do`, `while`, `for`, `to`, `by`, `where`, `collect`, `exit`, `continue`, `with` |
| 函数/结构体 | `fn`, `function`, `mapped`, `struct`, `return`, `throw` |
| 异常处理 | `try`, `catch` |
| 逻辑运算 | `and`, `or`, `not` |
| 类型转换 | `as` |
| 布尔值 | `true`, `false`, `on`, `off`, `undefined`, `unsupplied`, `ok`, `dontCollect` |
| 时间上下文 | `animate`, `at`, `time` |
| 分支 | `case`, `of`, `default` |
| .NET | `dotNet`, `dotNetObject`, `dotNetClass`, `dotNetAssembly` |
| UI | `rollout`, `utility`, `plugin`, `macroscript`, `rcmenu` |

**错误示例**：

```maxscript
-- ✗ 错误：使用关键字作为变量名
local if = 10
local for = #()
local return = true
local time = 0

-- ✓ 正确：使用替代命名
local ifFlag = 10
local forItems = #()
local returnValue = true
local timeValue = 0
```

---

### C004 [MUST_NOT] 禁止使用不存在的 MaxScript API（防 LLM 幻觉）

**常见幻觉 API 对比**：

| 幻觉 API | 正确写法 |
|---------|----------|
| `getBoneRotation(bone)` | `bone.transform.rotation` |
| `setLayerWeight(layer, weight)` | 需自行实现，MaxScript 无此函数 |
| `getSelectedBones()` | `selection as array` |
| `exportFBX(filePath)` | `FBXExporter.Export filePath` |

**验证流程**：
1. 查阅 Autodesk 官方文档
2. 在 MaxScript Listener 中执行 `help "functionName"` 验证
3. 使用 `exists functionName` 检查函数是否存在

---

### C005 [MUST] 函数必须使用 return 显式返回

```maxscript
-- ✓ 正确：显式返回
fn add a b =
(
    return a + b
)

-- ✗ 错误：隐式返回
fn add a b =
(
    a + b  -- 禁止！
)
```

---

## 命名规范

### 变量命名

- **规则**: MUST use camelCase
- **有效**: `targetRootBone`, `layerWeightData`
- **无效**: `target_root_bone`, `layerweightdata`

### 函数命名

- **规则**: MUST use camelCase，动词开头
- **有效**: `calculateOffset()`, `applyWeight()`
- **无效**: `Calculate_Offset()`, `计算偏移()`

### 全局变量命名

- **规则**: MUST use `g_` prefix + camelCase
- **有效**: `g_currentSelection`, `g_pluginVersion`
- **无效**: `globalSelection`

### 常量命名

- **规则**: MUST use `UPPER_SNAKE_CASE`
- **有效**: `MAX_BONE_COUNT`, `DEFAULT_WEIGHT`
- **无效**: `maxBoneCount`

### UI 控件命名

- **规则**: MUST use 类型前缀 + PascalCase
- **有效**: `btnExecute`, `spnWeight`, `txtName`, `lblStatus`
- **无效**: `executeButton`

---

## 语法规则（重点）

### 变量声明

| 语法 | 格式 |
|------|------|
| local | `local varName [= initialValue]` |
| global | `global varName [= initialValue]` |
| persistent global | `persistent global varName [= initialValue]` |

```maxscript
-- ✓ 正确示例
local offset = 0
global g_version = 1.0
persistent global g_userSettings = #()

-- ✗ 错误示例
offset = 0  -- 缺少 local/global
g_version = 1.0  -- 缺少 global 关键字
```

---

### IF 语句（重点）

| 语法 | 格式 |
|------|------|
| if-then | `if condition then statement` |
| if-then-else | `if condition then statement1 else statement2` |
| if-do | `if condition do statement` （无 else 时使用） |

```maxscript
-- ✓ 正确示例
if a > 0 then print "正数"
if a > 0 then (print "正数") else (print "非正数")
if (selection.count > 0) do print "有选中"

-- ✗ 错误示例
if a > 0 (print "a")  -- 缺少 then
if a > 0 do print "正" else print "非正"  -- if-do 不能配 else
```

---

### FOR 循环（重点）

| 语法 | 格式 |
|------|------|
| for-in | `for var in collection do statement` |
| for-in-where | `for var in collection where condition do statement` |
| for-to | `for var = start to end [by step] do statement` |
| for-collect | `result = for var in collection collect expression` |

```maxscript
-- ✓ 正确示例
for obj in selection do print obj.name
for i = 1 to 10 do print i
for i = 10 to 1 by -1 do print i  -- 倒序
local names = for obj in selection collect obj.name

-- ✗ 错误示例
for obj selection do ...  -- 缺少 in
for i 1 10 do ...  -- 缺少 = 和 to
result = for i = 1 to 10 (i * i)  -- 缺少 collect
```

---

### WHILE 循环

| 语法 | 格式 |
|------|------|
| while-do | `while condition do statement` |
| do-while | `do (statements) while condition` |

```maxscript
-- ✓ 正确示例
local i = 0
while (i < 10) do
(
    print i
    i += 1
)

-- ✗ 错误示例
while (i < 10) (print i)  -- 缺少 do
```

---

### CASE 语句

**语法**: `case [expression] of (cases)`

```maxscript
-- ✓ 正确示例
case i of
(
    1: print "一"
    2: print "二"
    default: print "其他"
)

case of
(
    (i > 0): print "正数"
    (i < 0): print "负数"
    default: print "零"
)

-- ✗ 错误示例
case i (1: print "一")  -- 缺少 of
```

---

### 函数定义

| 语法 | 格式 |
|------|------|
| fn | `fn functionName [params] = (statements)` |
| function | `function functionName [params] = (statements)` |
| mapped | `mapped fn functionName params = (statements)` （自动遍历数组） |

```maxscript
-- ✓ 正确示例
fn add a b =
(
    return a + b
)

fn setupUI width:200 height:300 title:"工具" =
(
    format "宽度: %, 高度: %\n" width height
)

-- ✗ 错误示例
fn add a b (return a + b)  -- 缺少 =
```

---

### 结构体定义

**语法**: `struct structName (members_and_methods)`

```maxscript
-- ✓ 正确示例
struct Point3D
(
    x, y, z,
    fn init px py pz = (x = px; y = py; z = pz),
    fn distanceTo other = (sqrt ((x-other.x)^2 + (y-other.y)^2 + (z-other.z)^2))
)
```

> **注意**: 成员之间必须用逗号分隔

---

## 数据类型语法

### 基础类型

| 类型 | 语法 | 示例 |
|------|------|------|
| 整数 | 数字（无小数点） | `10`, `-5`, `0xFF` |
| 浮点数 | 数字（含小数点） | `3.14`, `-0.5`, `1.0e-3` |
| 字符串 | `"字符串"` 或 `@"逐字字符串"` | `"Hello"`, `@"C:\Path"` |
| 布尔值 | `true` / `false` / `on` / `off` | `true`, `on` |

### 复合类型

| 类型 | 语法 | 示例 |
|------|------|------|
| Point2 | `[x, y]` | `[10, 20]` |
| Point3 | `[x, y, z]` | `[10, 20, 30]` |
| Color | `(color r g b [a])` | `(color 255 128 0)` |
| Quat | `(quat x y z w)` | `(quat 0 0 0 1)` |

### 集合类型

| 类型 | 语法 | 示例 |
|------|------|------|
| 数组 | `#(elem1, elem2, ...)` | `#(1, 2, 3)` |
| 位阵列 | `#{start..end}` 或 `#{index1, index2, ...}` | `#{1..10}`, `#{1, 3, 5}` |
| 字典 | `dataPair #key1:value1 #key2:value2 ...` | `dataPair #name:"John" #age:30` |

### 特殊值

| 值 | 说明 |
|-----|------|
| `undefined` | 未定义值 |
| `unsupplied` | 未提供的参数 |
| `ok` | 成功返回值 |
| `dontCollect` | 禁止垃圾回收 |

### 类型转换

**语法**: `value as type`

```maxscript
10 as float  -- 返回 10.0
"10" as integer  -- 返回 10
[10, 20] as point3  -- 返回 [10, 20, 0]
```

---

## 运算符

### 运算符列表

| 类别 | 运算符 |
|------|--------|
| 算术 | `+`, `-`, `*`, `/`, `^` |
| 比较 | `==`, `!=`, `>`, `<`, `>=`, `<=` |
| 逻辑 | `and`, `or`, `not` |
| 赋值 | `=`, `+=`, `-=`, `*=`, `/=` |
| 字符串 | `+` (拼接), `*` (重复) |
| 点运算 | `+` (点相加), `-` (点相减), `*` (点乘标量) |
| 数组 | `[index]` (索引，从 1 开始), `[start..end]` (切片) |

### 运算符优先级

1. `^` (指数)
2. `*`, `/`
3. `+`, `-`
4. `==`, `!=`, `>`, `<`, `>=`, `<=`
5. `not`
6. `and`
7. `or`
8. `=`, `+=`, `-=`, `*=`, `/=`

---

## 字符串操作

### 字符串字面量

```maxscript
-- 标准字符串（支持转义）
"字符串"  -- 支持 \n \t \" \\

-- 逐字字符串（反斜杠不转义）
@"C:\Users\Username\Documents"
```

### 字符串拼接与重复

```maxscript
-- 拼接
"Hello" + ", " + "World"  -- 返回 "Hello, World"

-- 重复
"Ha" * 3  -- 返回 "HaHaHa"
```

### 字符串函数

| 函数 | 说明 |
|------|------|
| `string.count` | 字符串长度 |
| `substring string start length` | 子字符串 |
| `findString string substring` | 查找子串，返回索引（从 1 开始） |
| `replace string start length newString` | 替换 |
| `toUpper string` / `toLower string` | 大小写转换 |
| `trimL string` / `trimR string` / `trim string` | 去空格 |

### 格式化输出

```maxscript
-- 格式化输出到 Listener
format "格式化字符串%" arg1 arg2 ...

-- 打印表达式到 Listener
print expression
```

---

## 数组操作

### 创建数组

```maxscript
-- 字面量
local arr = #(1, 2, 3, 4, 5)

-- collect
local result = for i = 1 to 10 collect i
```

### 访问数组

```maxscript
-- 索引（从 1 开始！）
arr[1]  -- 第一个元素

-- 切片
arr[1..5]  -- 前五个元素
```

### 修改数组

```maxscript
append array element  -- 追加元素
insertItem element array index  -- 插入元素
deleteItem array index  -- 删除元素
arr = #()  -- 清空数组
```

### 查询数组

```maxscript
array.count  -- 数组长度
findItem array element  -- 查找元素，返回索引（0 表示未找到）
```

### 遍历数组

```maxscript
for item in array do ...
for i = 1 to array.count do ...
for item in array collect ...  -- 收集结果
```

---

## 异常处理

### try-catch 语法

```maxscript
try
(
    -- 可能失败的代码
    local result = 10 / 0
)
catch
(
    -- 异常处理
    format "错误：%\n" (getCurrentException())
)
```

### throw 语法

```maxscript
throw()  -- 重新抛出当前异常
throw "error message"  -- 抛出自定义异常

-- 示例
fn processBone bone =
(
    if (not isValidNode bone) then
    (
        throw "无效的骨骼节点"
    )
)
```

---

## UI 开发规范

### Rollout 结构模板

```maxscript
rollout rolloutName "标题" width:200
(
    -- 控件定义
    button btnExecute "执行" pos:[10, 10] width:180
    
    -- 事件处理
    on btnExecute pressed do
    (
        -- 逻辑
    )
)

createDialog rolloutName
```

### 控件命名前缀

| 控件类型 | 前缀 |
|---------|------|
| button | `btn` |
| spinner | `spn` |
| editText | `txt` |
| label | `lbl` |
| checkBox | `chk` |
| dropDownList | `ddl` |
| listBox | `lst` |
| slider | `sld` |

> **规则**: UI 更新必须使用 try-catch 包裹，防止控件不存在导致脚本崩溃

---

## .NET 交互规范

### DLL 加载模板

```maxscript
fn loadDLL =
(
    local scriptPath = getSourceFileName()
    local scriptDir = getFilenamePath scriptPath
    local dllPath = scriptDir + "core.dll"
    
    if (doesFileExist dllPath) then
    (
        dotNet.loadAssembly dllPath
        return true
    )
    else
    (
        messageBox ("无法找到 DLL: " + dllPath)
        return false
    )
)
```

> **规则**: DLL 路径必须使用绝对路径，并先检查文件是否存在

### 数据类型转换

```maxscript
-- MaxScript 数组 -> .NET List<string>
local dotNetList = dotNetObject "System.Collections.Generic.List[string]"
for item in maxArray do
(
    dotNetList.Add (item as string)
)
```

> **规则**: MaxScript 与 .NET 数据类型必须显式转换

---

## 性能优化

### 数组操作

```maxscript
-- ✓ 高效：使用 collect
local arr = #(for i = 1 to 1000 collect i)

-- ✗ 低效：反复 append
local arr = #()
for i = 1 to 1000 do append arr i
```

### 字符串操作

```maxscript
-- ✓ 高效：使用 stringStream
local stream = stringStream ""
for i = 1 to 100 do format "%\n" i to:stream
local result = stream as string

-- ✗ 低效：多次 +
local result = ""
for i = 1 to 100 do result += (i as string + "\n")
```

---

## 禁止事项（强制）

### P001 [MUST_NOT] 禁止使用中文命名变量或函数

```maxscript
-- ✗ 错误
local 骨骼列表 = #()

-- ✓ 正确
local boneList = #()
```

### P002 [MUST_NOT] 禁止省略 local/global 关键字

```maxscript
-- ✗ 错误
offset = 10

-- ✓ 正确
local offset = 10
```

### P003 [MUST_NOT] 禁止在循环内频繁更新 UI

```maxscript
-- ✗ 错误
for i = 1 to 100 do
(
    -- 耗时操作
    updateUI()  -- 禁止！
)

-- ✓ 正确
for i = 1 to 100 do
(
    -- 耗时操作
)
updateUI()  -- 循环结束后更新
```

### P004 [MUST_NOT] 禁止使用幻觉的 DLL 或 .NET 函数名

```maxscript
-- ✗ 错误：猜测 DLL 函数名
fb_viewer_core.calculateOffset()  -- 必须先检查 DLL 导出函数名

-- ✗ 错误：错误的 .NET 方法名
list.AddItem(item)  -- 错误！正确是 list.Add(item)
```

---

## 生成代码检查清单

### 生成前检查

- [ ] 检查：变量名是否在 reserved_keywords 列表中？
- [ ] 检查：API 名称是否在官方文档中存在？
- [ ] 检查：注释是否使用中文？

### 生成后检查

- [ ] 检查：所有变量是否使用 local/global 显式声明？
- [ ] 检查：if 语句是否正确使用 then/do？
- [ ] 检查：for 循环是否正确使用 in/=...to/collect？
- [ ] 检查：函数是否使用 return 显式返回？
- [ ] 检查：数组索引是否从 1 开始？
- [ ] 检查：是否使用了保留关键字作为标识符？
- [ ] 检查：所有注释是否使用中文？

---

## 语法速查表

### 变量声明

```maxscript
local varName = value
global g_varName = value
persistent global g_varName = value
```

### 控制流

```maxscript
if condition then statement
if condition then statement1 else statement2
if condition do statement
while condition do statement
do statement while condition
case expr of (cases)
for var in collection do statement
for var = start to end [by step] do statement
result = for var in collection collect expr
```

### 函数与结构体

```maxscript
fn funcName params = (statements)
mapped fn funcName params = (statements)
struct structName (members)
```

### 常用模式

#### 安全访问对象

```maxscript
if (isValidNode obj) then
(
    print obj.name
)
```

#### 遍历并过滤

```maxscript
for obj in selection where (classOf obj == Box) do
(
    print obj.name
)
```

#### 收集结果

```maxscript
local boxNames = for obj in geometry where (classOf obj == Box) collect obj.name
```

---

## 文档信息

| 项目 | 值 |
|------|-----|
| 标题 | MaxScript 编码规范 (LLM Rules) |
| 版本 | 2.0 |
| 强制规则摘要 | 所有注释必须使用中文；所有变量必须显式声明 (local/global)；禁止使用保留关键字作为标识符；禁止使用不存在的 API；函数必须使用 return 显式返回 |
