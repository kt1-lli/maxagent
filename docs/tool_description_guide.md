# MaxAgent 工具 Description 编写规范

本规范用于指导如何为 MaxAgent 编写工具说明，确保 LLM 能正确理解、调用和维护工具。

## 1. 为什么需要规范

- LLM 只能看到工具的 `description` 和 JSON Schema
- 参数默认值、典型用法、常见陷阱必须显式说明
- 缺乏统一格式会导致新工具说明质量参差不齐

## 2. 每个工具必须包含的字段

使用 `@tool` 装饰器时，尽量提供以下字段：

```python
@tool(
    description='简短、清晰、一句话概括功能。',
    category='geometry',  # 或 animation / material / light_camera 等
    examples=[
        {
            'summary': '简述使用场景',
            'args': {'param1': 'value1', 'param2': 'value2'},
        },
    ],
    notes=[
        '参数格式约定，如 "[x,y,z]" 优先用 JSON 字符串。',
        '常见错误提醒。',
    ],
    returns_desc='返回值类型和结构说明',
    prerequisites=['调用前必须满足的条件'],
)
def my_tool(param1, param2):
    ...
```

## 3. 字段说明

### 3.1 `description`

- 一句话说明功能
- 不要写实现细节，写 LLM 能看懂的"做什么"
- 例：`'在场景中创建一个长方体（Box）。'`

### 3.2 `examples`

至少 1 个，建议 2~3 个。每个 example 包含：

- `summary`：使用场景简述
- `args`：完整参数 dict（可直接作为工具调用参数）

例：

```python
examples=[
    {
        'summary': '在原点创建默认大小的 Box',
        'args': {'length': 10, 'width': 10, 'height': 10},
    },
    {
        'summary': '在指定位置创建 Box',
        'args': {
            'name': 'MyBox',
            'length': 20, 'width': 10, 'height': 5,
            'position': '[50, 0, 0]',
        },
    },
]
```

### 3.3 `notes`

列出 LLM 容易出错的地方：

- 参数格式（字符串 vs 数组）
- 坐标系 / 单位约定
- 可选参数的默认值含义
- 与其他工具的配合顺序

### 3.4 `returns_desc`

说明返回值结构，方便 LLM 后续解析：

```python
returns_desc='dict {"name": 实际对象名, "class": "Box"}'
```

### 3.5 `prerequisites`

调用前必须满足的条件，例如：

```python
prerequisites=['对象 name 必须已存在于场景中']
```

## 4. 坐标与数组参数约定

- 所有 `[x, y, z]` 形式的参数，优先使用 JSON 字符串传递
- 在 `notes` 中明确说明 `"[x,y,z]"` 是合法格式
- 如果 schema 显式声明为 `array`，则直接传 list

## 5. 工具分类

按功能选择 category：

| category | 说明 |
|----------|------|
| `geometry` | 创建/修改几何体 |
| `animation` | 关键帧/约束/时间控制 |
| `material` | 材质/贴图 |
| `light_camera` | 灯光/相机/视口 |
| `scene` | 场景查询/统计 |
| `transform` | 移动/旋转/缩放/对齐 |
| `modifier` | 修改器栈 |
| `file_io` | 文件保存/导出/导入 |
| `creative` | 组合式创意工具 |
| `scripting` | 脚本执行/探测 |
| `misc` | 其他 |

## 6. 新增工具 checklist

- [ ] `description` 一句话说明功能
- [ ] `category` 已正确设置
- [ ] 至少 1 个 `examples`
- [ ] 列出至少 2 条 `notes`
- [ ] `returns_desc` 说明返回值结构
- [ ] `prerequisites` 说明前置条件（如有）
- [ ] 参数 docstring 与 JSON Schema 一致
- [ ] 启动时无 description 质量 warning

## 7. 质量校验

项目启动时会自动扫描所有已注册工具，对缺少 `examples` / `notes` / `returns_desc` 的工具打印 warning。

未来规范成熟后可升级为强制校验。

## 8. 示例：完整工具说明

```python
@tool(
    description='在场景中创建一个长方体（Box）。',
    category='geometry',
    examples=[
        {
            'summary': '在原点创建默认大小的 Box',
            'args': {'length': 10, 'width': 10, 'height': 10},
        },
        {
            'summary': '在指定位置创建 Box',
            'args': {
                'name': 'MyBox',
                'length': 20, 'width': 10, 'height': 5,
                'position': '[50, 0, 0]',
            },
        },
    ],
    notes=[
        'position 和 rotation_euler 支持 JSON 字符串 "[x,y,z]" 或 Python list/tuple。',
        'length 对应 Y 方向，width 对应 X 方向，height 对应 Z 方向。',
        '创建后若需精确摆放，可继续使用 move_object / rotate_object。',
    ],
    returns_desc='dict {"name": 实际对象名, "class": "Box"}',
)
def create_box(length=10.0, width=10.0, height=10.0, name='', position=None, rotation_euler=None):
    ...
```
