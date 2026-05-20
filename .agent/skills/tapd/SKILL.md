---
name: tapd
description: "当提及tapd 需求/缺陷/任务/迭代/Wiki等内容或包含tapd.woa.com链接时使用。"
homepage: http://mcp-idc.tapd.woa.com/mcp/
---

## ⚠️ 重要提醒：必须通过mcporter-internal调用


```
✅ 正确方式：mcporter-internal call tapd.<工具名>
❌ 错误方式：直接调用tapd工具（会失败）
```

> 💡 不熟悉 mcporter-internal 用法？请参考 `mcporter-internal` skill 文档。若当前环境未加载该 skill，可查阅本目录下的 `DEPENDENCIES.md`。

## 🔧 调用流程（严格遵循）

### 第一步：检查服务状态

```bash
mcporter-internal list
# 确认tapd服务状态为healthy
```

### 第二步：语义搜索工具

```bash
mcporter-internal call tapd.lookup_tapd_tool task_description="提炼后的任务描述"
```

### 第三步：执行目标工具

```bash
mcporter-internal call tapd.proxy_execute_tool tool_name="找到的工具名" tool_args='{}'
```

## 📋 三步走调用示例

**场景**: 用户说"帮我查一下项目 12345 的 bug 列表"

```bash
# 1. 搜索工具
mcporter-internal call tapd.lookup_tapd_tool task_description="查询缺陷列表"
# → 返回: bugs_get 及其 input_schema

# 2. 执行工具
mcporter-internal call tapd.proxy_execute_tool tool_name="bugs_get" tool_args='{"workspace_id": "12345"}'
# → 返回: 缺陷列表数据
```

## 🛠️ 工具说明

| 工具                       | 功能                   | 何时使用                     |
| -------------------------- | ---------------------- | ---------------------------- |
| `lookup_tapd_tool`         | 语义搜索匹配 TAPD 工具 | 不确定用哪个工具时           |
| `lookup_tool_param_schema` | 查询工具参数 schema    | 已知工具名，需获取参数定义时 |
| `proxy_execute_tool`       | 代理执行任意 TAPD 工具 | 执行具体操作                 |

## 📝 task_description 提炼规范

调用 `lookup_tapd_tool` 前，将用户原话提炼为「动作+对象」格式：

| 用户原话                   | 提炼后         |
| -------------------------- | -------------- |
| "帮我看看项目里有哪些 bug" | "查询缺陷列表" |
| "我想提一个新需求"         | "创建需求"     |
| "把任务状态改成已完成"     | "更新任务状态" |
| "获取当前迭代的所有任务"   | "查询迭代任务" |

**标准动作词**: 查询/获取/创建/新建/更新/修改/删除/统计  
**标准对象词**: 需求/story/缺陷/bug/任务/task/迭代/iteration/Wiki/测试用例

## 🚨 常见错误及解决方案

### 错误1："Not Acceptable: Client must accept application/json"

**原因**：直接调用了TAPD工具而不是通过mcporter-internal  
**解决**：使用`mcporter-internal call tapd.xxx`格式

### 错误2："Invalid request parameters"

**原因**：参数格式不正确或服务端协议问题  
**解决**：先调用`mcporter-internal list`确认服务状态

### 错误3：工具调用失败

**解决流程**：

1. `mcporter-internal list` 检查服务状态
2. `mcporter-internal call tapd.lookup_tapd_tool` 重新搜索工具
3. 使用正确的mcporter-internal调用格式

## 🔍 调试技巧

如果遇到问题，按顺序检查：

1. `mcporter-internal list` - 服务是否健康
2. 调用格式是否正确（必须包含`mcporter-internal call tapd.`前缀）
3. 参数是否符合JSON格式
4. 任务描述是否提炼准确

## 💡 关键记忆点

**记住：TAPD服务 = mcporter-internal + tapd.工具名**

- 没有mcporter-internal前缀的调用都会失败
- 服务本身是健康的，问题在于调用方式
- 通过正确的mcporter-internal调用流程，服务100%可用

## ⚠️ JSON 参数跨平台写法

- **macOS / Linux**：`--args "{\"key\": \"value\"}"`
- **Windows PowerShell**：`--args '{\"key\": \"value\"}'`（单引号包裹，内部双引号仍需 `\` 转义）
- **Windows PowerShell 禁止**用双引号包裹 JSON（如 `--args "{"key":"value"}"`），否则内部双引号会被 PowerShell 自身吞掉，导致报错 `Unable to parse --args: Expected property name or '}' in JSON at position 1`
- 详见 `mcporter-internal` skill 文档
