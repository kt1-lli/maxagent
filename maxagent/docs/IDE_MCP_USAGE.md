# IDE 接口（Bridge）使用指南

本指南介绍如何让 IDE（Cursor / Claude Desktop / Cline 等支持 MCP 协议的工具）
通过 [`dcc-mcp`](https://gitee.com/cmqll/dcc-mcp) 连接到 maxagent，实现：

- **execute_python**：在 Max 主线程执行任意 Python 代码（pymxs 安全）
- **dispatch_task**：把整个自然语言任务派给 maxagent 自己跑（IDE Agent ↔
  maxagent Agent 双 Agent 协作）

## 整体架构

```
[IDE: Cursor / Claude Desktop]
        │ MCP stdio
        ▼
[dcc-mcp 子进程]   ← uvx / pipx 拉起
        │ TCP 7003 + JSON
        ▼
[3ds Max + maxagent (Bridge)]
```

- **dcc-mcp**：跑在你的开发机上的独立 Python 子进程，由 IDE 启停；负责
  把 IDE LLM 的 MCP tool call 转换成 TCP JSON 请求发给 maxagent
- **maxagent Bridge**：嵌在 3ds Max 进程内的 TCP 服务器，监听 127.0.0.1，
  默认 7003 端口；处理请求并返回结果

只暴露在 127.0.0.1 上，不会被外网访问。

## 快速开始

### 1. 在 maxagent 中开启 Bridge

打开 maxagent 设置面板 → 「IDE 接口」Tab：

- 勾选「启用 IDE Bridge」
- 端口默认 `7003`，与 dcc-mcp 3dsMax 预设一致，无需修改
- 「访问令牌」留空（本机回环免鉴权）
- 「允许 IDE 派发自然语言任务」推荐勾选（启用 dispatch_task）
- 点「应用并重启 Bridge」
- 状态指示灯应变为绿色「● 运行中 127.0.0.1:7003」

### 2. 安装 dcc-mcp

任选其一：

```bash
# 推荐：用 uvx（启动时按需自动拉取）
# 无需提前安装，直接配 mcp.json 即可

# 也可以全局安装
pipx install dcc-mcp
# 或
pip install dcc-mcp
```

### 3. 配置 IDE

在 maxagent 设置面板的「IDE 接口」Tab 点「复制 dcc-mcp / Cursor 配置示例」，
然后粘贴到 IDE 的 MCP 配置文件：

#### Cursor

`~/.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "maxagent": {
      "command": "uvx",
      "args": ["dcc-mcp"],
      "env": {
        "DCC_MCP_NAME": "3dsMax",
        "DCC_MCP_BRIDGE_HOST": "127.0.0.1",
        "DCC_MCP_BRIDGE_PORT": "7003"
      }
    }
  }
}
```

#### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）
或 `%APPDATA%/Claude/claude_desktop_config.json`（Windows）。
结构与 Cursor 完全一致。

### 4. 重启 IDE

IDE 会启动 dcc-mcp 子进程并自动连接到 maxagent。在 Cursor 中你应该能看到
`maxagent` 工具集出现，包含 `execute_python` 和 `dispatch_task`。

## 两种调用方式

### A. execute_python（IDE LLM 自己写代码）

适合你已经清楚要做什么、希望 IDE LLM 直接生成 pymxs 代码的场景。

举例：在 Cursor 里说

> 帮我写一段 pymxs 代码：在场景中创建 5 个红色 Box，沿 X 轴排列。
> 用 execute_python 工具直接跑在我的 Max 里。

IDE LLM 会生成代码并通过 `execute_python` 提交，maxagent 在 Max 主线程执行
后返回 stdout / stderr / 异常 / 你赋给 `result` 变量的返回值。

### B. dispatch_task（把任务派给 maxagent 这个 Agent）⭐推荐

适合"测试我的工具/插件"这类自然语言任务。**IDE LLM 不写代码**，只把任务
描述派给 maxagent，maxagent 用自己的 LLM + 工具循环自主完成，最后回报
结构化报告。

举例：在 Cursor 里说

> 我刚写了一个工具 `auto_pivot_align(target='center'|'bottom'|'top')`。
> 请用 dispatch_task 让 maxagent 自己跑测：
>
> 1. 新建一个 100x100x100 的 Box
> 2. 调用 `auto_pivot_align(target='bottom')`
> 3. 验证 pivot 是否落在 Box 底面中心
>
> 然后把测试报告告诉我。

IDE LLM 调用 `dispatch_task`，传入这段 prompt。maxagent 在内部跑自己的
LLM + 工具（execute_python 等）完成测试，返回：

- `final_message`: maxagent 的最终回复
- `tool_calls`: 中间所有工具调用的 trace（name / arguments / ok / result / 耗时）
- `rounds`: LLM ↔ 工具循环的轮数
- `elapsed_ms`: 总耗时

## 工具属性对比

| 维度          | execute_python                | dispatch_task              |
|---------------|-------------------------------|----------------------------|
| 输入          | Python 代码字符串             | 自然语言任务 + 可选 profile |
| 谁出大脑      | IDE LLM（写代码）             | maxagent 内部 LLM          |
| 单次耗时      | 通常 < 1s                     | 几秒~几十秒                 |
| 适用场景      | 精确控制 / 简单查询             | 测试工具 / 复杂多步任务      |
| 典型轮数      | 1                             | 1~20（受配置约束）          |

## 配置项说明

| 字段                          | 默认值      | 说明                                          |
|-------------------------------|-------------|-----------------------------------------------|
| `bridge_enabled`              | `false`     | 主开关                                         |
| `bridge_host`                 | `127.0.0.1` | 监听地址（不要改成 `0.0.0.0`，会暴露到外网）       |
| `bridge_port`                 | `7003`      | 监听端口                                       |
| `bridge_token`                | 空          | 可选访问令牌                                    |
| `bridge_dispatch_enabled`     | `true`      | 是否暴露 dispatch_task                         |
| `bridge_dispatch_max_rounds`  | `20`        | 单次 dispatch 最大 LLM ↔ 工具循环轮数             |
| `bridge_dispatch_timeout_sec` | `300`       | 单次 dispatch 总超时（秒）                       |

## 安全提示

1. **默认关闭**：必须主动开启才会监听端口
2. **仅监听 127.0.0.1**：外网无法访问；不要手动改成 `0.0.0.0`
3. **可选 token 鉴权**：多人共用一台机器担心误连时建议设置
4. **dispatch_task 单实例锁**：同时最多一个 dispatch 在跑，避免并发抢主线程
5. **超时与轮数硬上限**：防 LLM 死循环
6. **execute_python 完全开放**：你能写什么，IDE LLM 就能让 Max 跑什么；
   仅限本机使用，不要把端口暴露到外网

## 故障排查

### 状态指示灯一直是「● 未启动」

- 检查端口是否被其他程序占用：`netstat -ano | findstr :7003`
- 改用其他端口（如 17003），同步更新 dcc-mcp 配置中的 `DCC_MCP_BRIDGE_PORT`

### IDE 里看不到 maxagent 工具

- IDE 重启后再试
- `mcp.json` 路径是否正确（IDE 文档说明）
- `uvx dcc-mcp` 是否能在终端独立运行

### `dispatch_task` 返回错误「no LLM profile available」

- maxagent 设置面板「模型」Tab 至少配置一个可用 Profile
- 把它设为「激活」

### `execute_python` 卡住

- maxagent 的 `dispatch_task` 单实例锁正在被占用，等当前任务完成
- 检查代码本身是否含死循环

## 协议参考

请求 / 响应均为单行 JSON，`\n` 结束：

```text
请求: {"request_id":"...","method":"execute_python|dispatch_task|capabilities",
      "payload":{...},"protocol_version":"2.0","token":"可选"}\n
响应: {"request_id":"...","ok":true|false,"data":{...}|null,
      "error":null|{"code":"...","message":"...","details":{...}}}\n
```

具体字段定义见 `maxagent/bridge/protocol.py` 与 `dcc_mcp/bridge/protocol.py`。

## 进阶：直接用 curl 调试

```bash
# 发一个 capabilities 请求
echo '{"request_id":"t1","method":"capabilities","payload":{}}' \
  | nc 127.0.0.1 7003

# 期望响应
# {"request_id":"t1","ok":true,"data":{"protocol_version":"2.0",
#  "dcc":"3dsMax","methods":["execute_python","dispatch_task","capabilities"],
#  ...},"error":null}
```
