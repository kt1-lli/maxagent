# MaxAgent · 3ds Max 内嵌 AI 助手

> 用自然语言操作 3ds Max —— 建模、加修改器、调材质灯光、批量处理、写脚本、查官方手册……
> 支持 **本地模型**（Ollama / LM Studio）和 **云端 API**（OpenAI / DeepSeek / 任意 OpenAI 兼容协议），
> 内置 **Autodesk 官方 Knowledge MCP**，让 AI 直接引用官方文档回答问题。

![max](https://img.shields.io/badge/3ds_Max-2022~2027-orange)
![python](https://img.shields.io/badge/Python-3.7~3.13-blue)
![qt](https://img.shields.io/badge/Qt-PySide2_|_PySide6-green)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

---

## ✨ 特性

- **50+ 内置工具**，覆盖场景查询 / 几何 / 变换 / 修改器 / 材质 / 灯光相机 / 渲染 / 场景 IO
- **Function Calling 驱动**：LLM 自主选择并调用工具，无需硬编码 prompt 模板
- **Autodesk 官方 MCP 接入**：`autodesk_max_docs` 工具直连
  `https://developer.api.autodesk.com/knowledge/public/v1/mcp`，
  查询自动限定在 3ds Max 范围，答案有官方文档支撑
- **IDE Bridge 双 Agent 协作**：Max 内 Agent 与 IDE 端 Agent 通过 BridgeServer 联动
- **Reflections / MacroRecorder 记录学习**：观察用户手动操作，沉淀成可复用工具
- **多 Profile 配置**：一键切换 Ollama / DeepSeek / GPT-4o / 自建 LLM 网关
- **撤销支持**：每个工具调用包一层 `pymxs.undo`，可 Ctrl+Z 回滚
- **逃生舱**：`run_maxscript` / `run_python` 让 AI 写自定义脚本（默认二次确认）
- **跨版本兼容**：Max 2022 ~ 2027 / Python 3.7 ~ 3.13 / PySide2 + PySide6 全覆盖
- **零外部依赖**：LLM、MCP 客户端均基于 stdlib（`urllib` + `json`）实现，
  规避 Max 环境中 pip 装包的各种坑

---

## 🚀 5 分钟上手

### 1. 启动（推荐：免安装方式）⭐

克隆本仓库到任意目录，**把 `install.ms` 拖进 3ds Max 视口** —— 完成。

启动器会把仓库根目录注入 `sys.path` 并弹出面板，
**无需事先 pip install，也不用拷贝文件到 Max 启动目录**。

注册到工具栏 / 快捷键：

```
Customize → Customize User Interface
  Category:  MaxAgent
  Action:    MaxAgent_Show / MaxAgent_Toggle
```

> 💡 适合开发期与绿色分发。删除仓库目录即等同完全卸载。

### 1.b 持久安装（可选）

想让 Max 每次启动就自动加载 MaxAgent，用 `install.py`：

```powershell
cd path\to\maxagent
python install.py                  # 装到所有检测到的 Max 版本
python install.py --version 2024   # 只装 Max 2024
python install.py --uninstall      # 卸载
```

### 1.c 打包 mzp（分发用）

```bash
python release/build.py --verbose  # 产出 release/dist/maxagent-X.Y.Z.mzp
```

分发时把 mzp 拖入 Max 视口即可自动安装。

### 2. 配置 LLM

首次启动后，点面板顶部的 **⚙ 设置**，选择或新建 Profile：

| 场景 | Base URL | API Key | 推荐模型 |
|------|----------|---------|----------|
| Ollama 本地 | `http://localhost:11434/v1` | `ollama`（占位） | `qwen2.5:14b` / `qwen2.5:32b` |
| LM Studio | `http://localhost:1234/v1` | `lmstudio` | 本地加载的模型名 |
| DeepSeek | `https://api.deepseek.com/v1` | `sk-xxx` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `sk-xxx` | `gpt-4o-mini` / `gpt-4o` |

> ⚠️ **本地模型必须支持 Function Calling**。实测可用：`qwen2.5:14b+`、`llama3.1:8b-instruct`、`mistral-nemo`。
> 纯 chat 模型（无 tools 支持）无法驱动工具调用。

> 🔐 **Key 存储**：位于 `%USERPROFILE%\Documents\3dsMax\maxagent\config.json`，仅 base64 混淆，**不是加密**。

填完点 **🧪 测试连接** 验证。绿色 ✓ 即可使用。

### 3. 试一试

```
👤 你: 创建一个茶壶，加 TurboSmooth 修改器，迭代 2 次
🤖 助手:
   🔧 create_teapot {"radius": 30}          ✓ {"name": "Teapot001"}
   🔧 add_modifier {"node": "Teapot001",
                    "modifier": "TurboSmooth",
                    "params": {"iterations": 2}}
                                             ✓ {"applied": true}
   已为 Teapot001 添加 2 次迭代的 TurboSmooth。

👤 你: TurboSmooth 的 Isoline Display 是什么意思？
🤖 助手:
   🔧 autodesk_max_docs {"query": "TurboSmooth Isoline Display"}
                                             ✓（返回官方文档摘要）
   Isoline Display 会隐藏细分后新增的边线……（引用自 Autodesk 官方手册）
```

---

## 📦 项目结构

```
maxagent-project/
├── install.ms               # 拖入 Max 视口即可启动的 MaxScript 入口
├── install.py               # 持久安装脚本（可选）
├── setup.py                 # 可选 pip 安装
├── docs/                    # 架构、工具开发、故障排查文档
├── release/
│   ├── build.py             # 打包脚本（源码 → mzp）
│   ├── version.py
│   ├── pyproject.toml
│   └── ci/                  # GitHub Actions / 工蜂蓝盾流水线
├── .github/workflows/       # 主 CI 流水线
└── maxagent/
    ├── __init__.py          # 公开 API：show / hide / toggle
    ├── startup.py           # Max 启动入口
    ├── config.py            # Profile 配置管理
    ├── llm_client.py        # OpenAI 兼容客户端（流式 + tool calls）
    ├── autodesk_mcp.py      # Autodesk Knowledge MCP 客户端（stdlib 实现）
    ├── qt_compat.py         # PySide2 / PySide6 适配
    ├── runtime_helpers.py   # pymxs 版本探测 + 主线程调度
    ├── session_memory.py    # 会话记忆
    ├── summarization_checkpoint.py  # 长对话摘要压缩
    ├── macro_recorder.py    # 用户操作录制
    ├── reflections_loader.py # 反射式规则/工具学习
    ├── skills.py            # Skill 加载与调度
    ├── web_search.py        # 联网搜索
    ├── bridge/              # IDE Bridge 双 Agent 协作
    ├── agent/
    │   ├── conversation.py  # 多轮对话状态机
    │   ├── worker.py        # 子线程 LLM + 工具循环
    │   ├── task_context.py
    │   ├── scene_snapshot.py
    │   └── coding_rules.py
    ├── tools/
    │   ├── registry.py      # 工具注册表 + Schema 自动推导
    │   ├── dispatcher.py    # 主线程调度器 + undo 包装
    │   ├── escape_hatch.py  # run_maxscript / run_python
    │   ├── scene_query.py / geometry.py / transform.py
    │   ├── modifier.py / material.py / light_camera.py
    │   ├── render.py / scene_io.py
    │   ├── autodesk_docs.py # Autodesk 官方文档查询工具
    │   ├── web_tools.py     # 联网搜索工具
    │   ├── knowledge_tools.py
    │   ├── skills_tools.py
    │   ├── learn_tools.py / learn_rules.py
    │   └── reflection_tools.py
    └── ui/
        ├── dock_widget.py
        ├── settings_dialog.py
        ├── bubbles.py / tool_block.py
        └── ...
```

---

## 🧠 工作原理（极简版）

```
你 ──[文字]──▶ DockWidget (主线程)
                  │
                  ▼
              Worker (子线程)
                  │ HTTP/SSE
                  ▼
              LLM API
                  │ tool_calls
                  ▼
              Worker
                  ├──▶ 官方文档查询（autodesk_max_docs → MCP）
                  └──▶ 主线程 Dispatcher (持 pymxs)
                             │
                             ▼
                        Max 场景 ✓
```

详细架构见 [docs/architecture.md](docs/architecture.md)。

---

## 🔍 Autodesk 官方 MCP 集成

MaxAgent 内置了对 Autodesk **Knowledge MCP** 服务的支持：

- 端点：`https://developer.api.autodesk.com/knowledge/public/v1/mcp`
- 协议：MCP over Streamable HTTP（`initialize` / `tools/list` / `tools/call`，SSE 帧解析）
- 强制作用域：所有查询会自动加 `3ds Max: ` 前缀，并按远端 schema 填充 `product` / `filter` 等字段
- LLM 侧暴露为工具 `autodesk_max_docs`，工具描述明确标注「权威、限定 3ds Max」，
  优先级高于通用 `web_search`

由此，模型对手册细节、参数含义、API 名称等问题会优先查官方文档，而不是靠训练语料脑补。

---

## 🛠️ 扩展：自定义工具

三行代码就能新增一个 LLM 可调用的工具：

```python
from maxagent.tools.registry import tool

@tool('my_cool_op', description='干一些很酷的事')
def my_cool_op(target: str, count: int = 1) -> dict:
    from pymxs import runtime as rt
    # ... 你的 pymxs 代码
    return {'ok': True}
```

参数注解会被自动转成 OpenAI Tools JSON Schema。详见 [docs/tool_development.md](docs/tool_development.md)。

---

## 🐛 故障排查

- LLM 连不上 / 401 / 超时 → 见 [docs/troubleshooting.md](docs/troubleshooting.md)
- Max 启动白屏 / 卡住 → 立即在 startup 目录把 `maxagent_startup.py` 改名为 `.bak`
- 工具调用失败 → 看面板红色 ✗ 后的具体错误，多数是模型给错参数
- 模型不调用工具 → 检查 Profile「启用 Function Calling」是否打勾，且模型本身要支持 tools
- Autodesk MCP 无响应 → 需要外网可达 `developer.api.autodesk.com`

---

## 🤝 贡献

欢迎提 Issue / PR：

- 工蜂：<https://git.woa.com/cmqli/max_agent>
- Gitee：<https://gitee.com/cmqll/max_agent>

主分支为 `master`，提交信息用中文亦可。改代码前请确保 `flake8 maxagent/ --max-line-length=120` 通过。

---

## 📜 License

MIT License. 使用 `run_maxscript` / `run_python` 逃生舱时请保留默认的二次确认，
避免恶意 prompt 触发破坏性操作。

---

**MaxAgent** — Made with ❤️ for 3ds Max users.
