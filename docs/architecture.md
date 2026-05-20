# MaxAgent 架构与线程模型

## 1. 整体分层

```
┌─────────────────────────────────────────────────────────────────┐
│                        UI 层 (ui/)                               │
│  ┌───────────────────────┐    ┌─────────────────────────────┐  │
│  │  MaxAgentDockWidget   │    │     SettingsDialog          │  │
│  │  - 聊天浏览器          │    │     - Profile CRUD          │  │
│  │  - Profile 切换        │◀──▶│     - 测试连接               │  │
│  │  - 输入框 / 发送按钮    │    │                              │  │
│  └────────┬──────────────┘    └─────────────────────────────┘  │
│           │ Signal/Slot                                         │
└───────────┼─────────────────────────────────────────────────────┘
            │
┌───────────▼─────────────────────────────────────────────────────┐
│                       Agent 层 (agent/)                          │
│  ┌────────────────────┐         ┌────────────────────────────┐  │
│  │   Conversation     │◀────────│      AgentWorker            │  │
│  │   - 消息列表        │  R/W    │      - 子线程跑 LLM         │  │
│  │   - OpenAI 协议     │         │      - 工具调用循环          │  │
│  │   - 序列化          │         │      - Signal 通知 UI       │  │
│  └────────────────────┘         └─────┬───────────────┬───────┘  │
└──────────────────────────────────────┼───────────────┼──────────┘
                                       │               │
                  ┌────────────────────▼─┐         ┌──▼─────────────┐
                  │  LLM 层 (llm_client) │         │  工具层 (tools)│
                  │  - urllib HTTP        │         │  - registry    │
                  │  - SSE 流式解析       │         │  - dispatcher  │
                  │  - tool_calls 解析    │         │  - 51 个工具    │
                  └───────────────────────┘         └──┬─────────────┘
                                                        │
                                       ┌────────────────▼──────────┐
                                       │  pymxs / 3ds Max 主线程    │
                                       └────────────────────────────┘
```

## 2. 线程模型（关键！）

### 2.1 三个线程的角色

| 线程 | 跑什么 | 不能跑什么 |
|------|--------|-----------|
| **主线程**（= Max 主线程 = Qt 主线程） | UI 槽函数、所有 pymxs 调用、`Conversation` 读写 | 长耗时网络请求 |
| **Worker 子线程** | LLM HTTP 调用、流式 SSE 解析、工具调用循环 | **任何 pymxs 调用**（会让 Max 崩溃！） |
| **Qt 内部线程** | 信号派发、定时器 | 业务逻辑 |

### 2.2 跨线程同步执行流程

Worker 子线程需要执行工具时，必须把请求**同步**派回主线程：

```
[Worker 子线程]                         [主线程]
─────────────────                      ──────────
worker._exec_one_tool_call()
    │
    ├─▶ self._sync_tool_runner(name, args)
    │       │
    │       │  调用 dock_widget._run_tool_sync
    │       │
    │       ├─▶ QTimer.singleShot(0, _run_in_main)
    │       │       │
    │       │       │ Qt 事件循环把闭包派发到主线程
    │       │       │
    │       │       └────────────────▶  _run_in_main()
    │       │                                │
    │       │                                ├─▶ dispatcher.dispatch()
    │       │                                │       │
    │       │                                │       └─▶ pymxs.runtime.Box(...)
    │       │                                │             ✓ 在 Max 主线程
    │       │                                │
    │       │                                └─▶ result_box['value'] = ...
    │       │                                    done.set()
    │       │
    │       └─▶ done.wait(timeout=300)  ←── 子线程在这里阻塞等待
    │
    ├─▶ 拿到结果，写回 conversation
    │
    └─▶ 继续下一轮 LLM 调用
```

**为什么不用 `QMetaObject.invokeMethod(BlockingQueuedConnection)`？**

- 它要求目标 slot 是 `QObject` 的方法且要注册 `Q_INVOKABLE`，PySide 上写法繁琐
- `QTimer.singleShot(0)` + `threading.Event` 同样能实现阻塞同步，且更简单可读
- 30 秒级超时保护，万一主线程卡死也能抛错

### 2.3 流式 token 派发

```
[Worker 子线程]                         [主线程]
─────────────────                      ──────────
LLMClient.chat(stream=True)
    │
    ├─ for chunk in sse_stream:
    │      │
    │      └─▶ on_delta(chunk)         ─── 仍在子线程
    │              │
    │              └─▶ self.chunk_received.emit(chunk)
    │                       │
    │                       │  Qt::QueuedConnection (默认)
    │                       │
    │                       └────────────▶  dock._on_chunk(chunk)
    │                                          │
    │                                          └─▶ renderer.add_assistant_chunk()
    │                                              ✓ 在 UI 线程，安全
```

**注意**：Signal 发到主线程是**异步**的（QueuedConnection），Worker 不会等 UI 渲染完成。
但 UI 渲染速度远快于 LLM 流式速度，实际不会堆积。

## 3. OpenAI Function Calling 协议适配

### 3.1 LLMClient 内部用扁平格式

```python
# llm_client.chat() 返回
{
    "content": "我来创建...",
    "tool_calls": [
        {"id": "call_1", "name": "create_teapot", "arguments": {"radius": 10}}
    ],
    "finish_reason": "tool_calls",
}
```

### 3.2 塞回 conversation 时转回 OpenAI 原生格式

```python
# Worker 转换后塞入 conversation:
{
    "role": "assistant",
    "content": null,
    "tool_calls": [{
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "create_teapot",
            "arguments": "{\"radius\": 10}"  # 注意：是 JSON 字符串！
        }
    }]
}
```

**这是 OpenAI 协议的硬性要求**：`arguments` 必须是字符串而非对象，否则下一轮调用时 LLM 会拒绝。

### 3.3 工具结果消息

```python
{
    "role": "tool",
    "tool_call_id": "call_1",
    "name": "create_teapot",
    "content": "{\"ok\": true, \"result\": {\"name\": \"Teapot001\"}}"
}
```

外层 `{ok, result/error}` 由 Worker 添加，内层 `result` 是 dispatcher 返回值。

## 4. 工具注册与调度

### 4.1 注册期（包加载时）

```
load_all_tools()
    │
    ├─▶ import maxagent.tools.geometry
    │      │
    │      └─▶ @tool('create_box') 装饰器执行
    │             │
    │             ├─▶ inspect.signature() 读出参数类型
    │             ├─▶ 推导 JSON Schema
    │             └─▶ registry._TOOLS['create_box'] = ToolSpec(...)
    │
    ├─▶ import maxagent.tools.transform
    └─▶ ... (8 个模块, 51 个工具)
```

### 4.2 Schema 推导规则

| Python 类型 | JSON Schema |
|------------|-------------|
| `str` | `{"type": "string"}` |
| `int` | `{"type": "integer"}` |
| `float` | `{"type": "number"}` |
| `bool` | `{"type": "boolean"}` |
| `list` / `List[T]` | `{"type": "array", "items": ...}` |
| `dict` / `Dict[str, X]` | `{"type": "object"}` |
| `Optional[T]` | `{"type": [..., "null"]}` |

无类型注解时 fallback 到默认值类型（`width=10` → `integer`，`length=10.0` → `number`）。

### 4.3 调用期

```
dispatcher.dispatch('create_box', {'length': 10, 'width': 5})
    │
    ├─▶ 查 registry, 找到 ToolSpec
    ├─▶ jsonschema 验证参数合法性
    ├─▶ 用 pymxs.undo("create_box") 包一层
    │     │
    │     └─▶ 调用真实函数 create_box(length=10, width=5)
    │            │
    │            └─▶ pymxs.runtime.Box(length=10, width=5)
    │                  ✓ 用户可 Ctrl+Z 撤销
    │
    └─▶ 返回 dict
```

### 4.4 危险工具（dangerous flag）

`run_maxscript` / `run_python` 标 `dangerous=True`，UI 渲染时显示橘色 ⚠️ 图标。
未来版本会在执行前弹窗确认（当前默认放行，可在 escape_hatch.py 改 `REQUIRE_CONFIRM=True`）。

## 5. 配置存储

```
%USERPROFILE%\Documents\3dsMax\maxagent\
└── config.json
    {
        "active_profile": "deepseek",
        "profiles": [
            {
                "name": "ollama-local",
                "base_url": "http://localhost:11434/v1",
                "api_key": "<base64 encoded>",
                "model": "qwen2.5:14b",
                "temperature": 0.3,
                "stream": true,
                "supports_tools": true,
                ...
            },
            ...
        ]
    }
```

API Key 用 base64 简单编码，**不是加密**。如果需要更强的密钥保护，建议用：
- Windows Credential Manager（`pywin32`）
- 1Password CLI / Bitwarden CLI 通过自定义 header 注入

## 6. 扩展点

| 扩展什么 | 改哪里 |
|---------|--------|
| 加新工具 | `tools/<新模块>.py` 用 `@tool` 装饰即可 |
| 接新 LLM 协议 | 改 `llm_client.py` 的 `_parse_*` 函数 |
| 改 UI 风格 | `ui/dock_widget.py` 的 `_STYLE` 字符串 |
| 持久化对话历史 | 调 `Conversation.save(path)` / `Conversation.load(path)` |
| 自定义 system prompt | 实例化 `Conversation(system_prompt=...)` |
| 限制工具集 | 改 `tools/__init__.py` 的 `load_all_tools(modules=[...])` |
