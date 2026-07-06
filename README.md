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

- **70 个内置工具**（`@tool` 装饰器统一注册），覆盖 8 大类 Max 操作 + Autodesk 文档 / 联网 / 学习 / 反思
- **Function Calling 驱动**：LLM 自主选择并调用工具，schema 由参数注解自动推导，无需手写
- **主线程隔离**：工具在 Max 主线程通过 `pymxs` 执行并自动包 `undo`，LLM 请求跑在子线程，UI 不阻塞
- **Autodesk 官方 MCP 接入**：`autodesk_max_docs` 工具直连
  `https://developer.api.autodesk.com/knowledge/public/v1/mcp`，
  查询自动限定在 3ds Max 范围，答案有官方文档支撑
- **IDE Bridge**：内置 `maxagent.bridge` HTTP 服务，可与外部 IDE Agent 联动
- **观察式学习**：`macro_recorder` + `reflections_loader` 记录用户手动操作，沉淀为可复用工具与经验
- **Skills 机制**：以插件形式扩展能力，可保存 / 列出 / 删除 / 展示技能
- **多 Profile 配置**：内置 Ollama / LM Studio / OpenAI / DeepSeek 4 个 Profile，可自建
- **逃生舱**：`run_maxscript` / `run_python` 让 AI 写自定义脚本（默认二次确认）
- **跨版本兼容**：Max 2022 ~ 2027 / Python 3.7 ~ 3.13 / PySide2 + PySide6
- **零外部依赖**：LLM、MCP 客户端均基于 `urllib` + `json`，规避 Max 环境 pip 装包的坑

---

## 🚀 快速开始

### 1. 启动

克隆仓库到任意目录，**把 `install.ms` 拖进 3ds Max 视口** —— 完成。

启动器会把仓库目录注入 `sys.path` 并弹出面板，无需 pip install，也不用拷贝到 Max 启动目录。
再次启动 Max 时重新拖入即可，`sys.path` 注入是幂等的。

注册到工具栏 / 快捷键：

```
Customize → Customize User Interface
  Category:  MaxAgent
  Action:    MaxAgent_Show / MaxAgent_Toggle
```

启动后在 Max 的 MaxScript / Python 里也可主动调：

```python
import maxagent
maxagent.show()      # 显示面板
maxagent.toggle()    # 切换显示/隐藏
maxagent.reload_pkg()  # 开发态热重载（无需重启 Max）
```

或 MaxScript 侧的全局函数：`g_show_max_agent()` / `g_toggle_max_agent()` / `g_reload_max_agent()`。

### 2. 打包 mzp（分发时用）

```bash
python release/build.py --dry-run   # 只校验，不产出
python release/build.py --verbose   # 产出到 release/dist/maxagent-1.0.0.mzp
```

分发时把 `.mzp` 拖入 Max 视口即可自动安装。

### 3. 配置 LLM

首次启动后，点面板顶部的 **⚙ 设置**。仓库内置 4 个 Profile：

| 内置 Profile          | Base URL                       | 默认模型             |
|----------------------|--------------------------------|-------------------|
| Ollama (本地)         | `http://localhost:11434/v1`   | `qwen2.5:14b`     |
| LM Studio (本地)      | `http://localhost:1234/v1`    | `local-model`     |
| OpenAI               | `https://api.openai.com/v1`   | `gpt-4o`          |
| DeepSeek             | `https://api.deepseek.com`    | `deepseek-v4-flash` |

也可以新建 Profile 指向任意 OpenAI 兼容网关。填完点 **🧪 测试连接** 验证。

> ⚠️ **本地模型必须支持 Function Calling**。实测可用：`qwen2.5:14b+`、`llama3.1:8b-instruct`、`mistral-nemo`。
> 纯 chat 模型（无 tools 支持）无法驱动工具调用；此时 LM Studio Profile 默认关闭 `supports_tools`，走 JSON 模式兜底。

> 🔐 **Key 存储**：位于 `%USERPROFILE%\Documents\3dsMax\maxagent\config.json`，仅 base64 混淆，**不是加密**，共享机器请勿保存高权限 Key。

### 4. 试一试

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

## 🛠️ 工具全景（共 70 个）

Max 场景类（**48 个**，主线程执行 + `pymxs.undo` 包裹）：

| 类别 | 数量 | 代表工具 |
|------|------|---------|
| 场景查询 `scene_query` | 8 | `get_max_info`、`get_scene_stats`、`list_scene_objects`、`get_selection`、`find_object`、`get_object_info`、`get_active_view`、`get_time_range` |
| 几何创建 `geometry`   | 7 | `create_box`、`create_sphere`、`create_cylinder`、`create_cone`、`create_torus`、`create_plane`、`create_teapot` |
| 变换 `transform`      | 5 | 平移 / 旋转 / 缩放 / 对齐 / 镜像 |
| 修改器 `modifier`     | 4 | `add_modifier`、`remove_modifier`、`list_modifiers`、`collapse_stack` |
| 材质 `material`       | 5 | Standard / PBR 材质创建、指定材质、颜色/参数设置 |
| 灯光相机 `light_camera` | 4 | 灯光创建、参数调整、相机创建、`look_at` |
| 渲染 `render`         | 3 | 渲染帧、分辨率、活动相机 |
| 场景 IO `scene_io`    | 13 | 保存 / 加载 / 导入 / 导出 / 合并等 |

外部知识与自动化（**11 个**）：

| 类别 | 数量 | 工具名 |
|------|------|-------|
| Autodesk 官方文档 | 1 | `autodesk_max_docs` |
| 联网 | 2 | `web_search`、`web_fetch` |
| 本地 Max 知识片段 | 2 | `lookup_max_knowledge`、`list_max_knowledge_topics` |
| Skills 管理 | 4 | `save_skill` / `list_skills` / `show_skill` / `delete_skill` |
| 反思记录 | 3 | `reflect_on_outcome` / `list_reflections` / `delete_reflection` |

学习与自演进（**7 个**）：

| 类别 | 数量 | 工具名 |
|------|------|-------|
| 学习新工具 | 4 | `propose_new_tool` / `list_learned_tools` / `patch_learned_tool` / `delete_learned_tool` |
| 学习规则 | 3 | `suggest_rule_addition` / `list_learned_rules` / `delete_learned_rule` |

逃生舱（**2 个**，默认二次确认）：`run_maxscript`、`run_python`。

在面板里问 **"列出所有可用工具"**，或在 Python 中：

```python
from maxagent.tools import load_all_tools, list_tools
load_all_tools()
for t in list_tools():
    print(t.name, '-', t.description)
```

---

## 📦 项目结构

```
maxagent/
├── install.ms                     # 拖入 Max 视口即可启动的 MaxScript 入口
├── README.md
├── release/
│   ├── build.py                   # 打包脚本（源码 → mzp）
│   ├── version.py                 # 版本号唯一来源
│   ├── pyproject.toml
│   ├── mzp_install.ms             # mzp 内的自安装脚本
│   ├── mzp.run                    # mzp 描述
│   ├── macros/                    # 注册到 Max Customize UI 的宏
│   └── ci/                        # 工蜂蓝盾流水线配置
└── maxagent/                      # 主包
    ├── __init__.py                # 公开 API：show / hide / toggle / reload_pkg
    ├── startup.py                 # Max 启动入口
    ├── config.py                  # 4 个内置 Profile + 用户 Profile 管理
    ├── llm_client.py              # OpenAI 兼容客户端（urllib + SSE）
    ├── autodesk_mcp.py            # Autodesk Knowledge MCP 客户端（stdlib）
    ├── qt_compat.py               # PySide2 / PySide6 适配
    ├── runtime_helpers.py         # pymxs 版本探测 + 主线程调度
    ├── logger.py                  # 统一日志
    ├── attachments.py             # 消息附件（图片、文件）
    ├── sessions.py                # 会话持久化
    ├── session_memory.py          # 会话短期记忆
    ├── summarization_checkpoint.py# 长对话滚动摘要
    ├── macro_recorder.py          # 用户操作录制
    ├── reflections_loader.py      # 反思条目加载
    ├── skills.py                  # Skill 加载与调度
    ├── user_rules_loader.py       # 用户规则加载
    ├── user_tools_loader.py       # 用户学习到的工具加载
    ├── web_search.py / web_providers.py  # 联网搜索
    ├── model_capabilities.py      # 模型能力探测
    ├── disabled_registry.py       # 工具禁用清单
    ├── pack.py                    # 打包辅助
    ├── reload.py                  # 开发态热重载
    ├── ui_state.py                # UI 状态持久化
    ├── bridge/                    # IDE Bridge HTTP 服务
    │   ├── server.py
    │   ├── protocol.py
    │   └── handlers/
    ├── agent/
    │   ├── conversation.py        # 多轮对话状态机
    │   ├── worker.py              # 子线程 LLM + 工具循环
    │   ├── task_context.py
    │   ├── scene_snapshot.py
    │   ├── coding_rules.py
    │   ├── few_shot_examples.py
    │   └── max_knowledge.py
    ├── tools/                     # 70 个工具，见上表
    │   ├── registry.py            # 工具注册表 + Schema 自动推导
    │   ├── dispatcher.py          # 主线程调度器 + undo 包装
    │   ├── escape_hatch.py        # run_maxscript / run_python
    │   ├── scene_query.py / geometry.py / transform.py
    │   ├── modifier.py / material.py / light_camera.py
    │   ├── render.py / scene_io.py
    │   ├── autodesk_docs.py       # Autodesk 官方文档查询工具
    │   ├── web_tools.py           # 联网搜索
    │   ├── knowledge_tools.py     # 本地 Max 知识片段
    │   ├── skills_tools.py        # Skills 管理
    │   ├── learn_tools.py         # 让 AI 提出新工具
    │   ├── learn_rules.py         # 让 AI 提出新规则
    │   └── reflection_tools.py    # 反思记录
    └── ui/                        # 面板与对话框（PySide2/6 兼容）
        ├── dock_widget.py
        ├── settings_dialog.py
        ├── bubbles.py / tool_block.py / markdown_render.py
        ├── input_attachments.py / avatar_crop_dialog.py
        ├── screenshot_overlay.py / emoji_compat.py
        ├── provider_editor.py / employee.py / employee_tab.py
        ├── learn_approval_dialog.py / rule_approval_dialog.py
        ├── rule_import_dialog.py / pack_dialog.py
        └── ...
```

---

## 🧠 工作原理（极简版）

```
你 ──[文字]──▶ DockWidget (主线程)
                  │ Signal
                  ▼
              AgentWorker (子线程)
                  │ HTTP + SSE  (llm_client)
                  ▼
              LLM API
                  │ tool_calls
                  ▼
              AgentWorker
                  ├──▶ 官方文档查询（autodesk_max_docs → autodesk_mcp）
                  ├──▶ 联网 / 本地知识 / Skills / 学习工具
                  └──▶ 主线程 ToolDispatcher（持 pymxs + undo）
                             │
                             ▼
                        Max 场景 ✓
```

---

## 🔍 Autodesk 官方 Knowledge MCP

`maxagent/autodesk_mcp.py` 是纯 stdlib 实现的 MCP over Streamable HTTP 客户端：

- 端点：`https://developer.api.autodesk.com/knowledge/public/v1/mcp`
- 协议：`initialize` / `tools/list` / `tools/call`，SSE 帧解析，会话 id 保持
- 强制作用域：查询自动加 `3ds Max: ` 前缀，并按远端 `inputSchema` 填充 `product` / `filter` 等字段
- LLM 侧暴露为工具 `autodesk_max_docs`（在 `maxagent/tools/autodesk_docs.py` 中注册），
  描述明确标注「权威、限定 3ds Max」，优先级高于通用 `web_search`

由此，模型对手册细节、参数含义、API 名称等问题会先查官方文档，而不是靠训练语料脑补。

需要外网可达 `developer.api.autodesk.com`。

---

## 🛠️ 扩展：自定义工具

参数注解会被自动转成 OpenAI Tools JSON Schema，最少三行即可注册一个新工具：

```python
from maxagent.tools.registry import tool

@tool('my_cool_op', description='干一些很酷的事', category='custom')
def my_cool_op(target: str, count: int = 1) -> dict:
    from pymxs import runtime as rt
    # ... 你的 pymxs 代码
    return {'ok': True}
```

工具默认在 Max 主线程执行、并被 `pymxs.undo` 包裹，可 Ctrl+Z 回滚。
若不需要 undo（如纯查询工具），加 `wrap_undo=False`。

---

## 🐛 故障排查

- LLM 连不上 / 401 / 超时 → 检查 Profile 的 Base URL 与 API Key，本地服务需先启动
- Max 启动白屏 / 卡住 → 立即在 startup 目录把 `maxagent_startup.py` 改名为 `.bak`
- 工具调用失败 → 看面板红色 ✗ 后的具体错误，多数是模型给错参数
- 模型不调用工具 → 检查 Profile 的 `supports_tools`，且模型本身要支持 tools
- Autodesk MCP 无响应 → 需要外网可达 `developer.api.autodesk.com`
- 开发时改了代码不生效 → `maxagent.reload_pkg()` 或 `g_reload_max_agent()`

---

## 📜 License

MIT License. 使用 `run_maxscript` / `run_python` 逃生舱时请保留默认的二次确认，
避免恶意 prompt 触发破坏性操作。

---

**MaxAgent v1.0.0** — Made with ❤️ for 3ds Max users.
