# MaxAgent · 3ds Max 内嵌 AI 助手

> 用自然语言操作 3ds Max——创建几何体、加修改器、设置材质灯光、批量重命名、写脚本……
> 支持 **本地模型**（Ollama / LM Studio）和 **云端 API**（OpenAI / DeepSeek / 任意 OpenAI 兼容协议）。

![banner](https://img.shields.io/badge/3ds_Max-2022~2027-orange) ![python](https://img.shields.io/badge/Python-3.7+-blue) ![qt](https://img.shields.io/badge/Qt-PySide2_|_PySide6-green) ![license](https://img.shields.io/badge/license-MIT-lightgrey)

---

## ✨ 特性

- **51 个内置工具**，覆盖 8 大类 Max 操作（场景查询/几何/变换/修改器/材质/灯光相机/渲染/IO）
- **自然语言驱动**：通过 LLM Function Calling 让 AI 自主选择工具并执行
- **流式回复**：模型边想边说，体验丝滑
- **多 Profile 配置**：一键切换 Ollama / DeepSeek / GPT-4o / 自建 LLM 网关
- **撤销支持**：每个工具调用包一层 `pymxs.undo`，可 Ctrl+Z 回滚
- **逃生舱**：`run_maxscript` / `run_python` 工具让 AI 写自定义脚本（默认需弹窗确认）
- **跨版本兼容**：Max 2022 ~ 2027 / PySide2 / PySide6 全覆盖
- **零外部依赖**：LLM 客户端纯 stdlib（urllib + json）实现

---

## 🚀 5 分钟上手

### 1. 启动（推荐：免安装方式）⭐

下载本仓库到任意目录，**直接把 `MAXAGENT_INSTALL.ms` 拖到 3ds Max 视口** —— 完成。

启动器会自动把仓库目录注入 `sys.path` 并显示面板，**无需事先 `pip install`、无需拷贝文件到 Max 启动目录**。
重启 Max 后再次拖入即可，每次拖入都是幂等的，不会污染 `sys.path`。

注册到工具栏 / 快捷键（拖入一次后即可）：

```
Customize → Customize User Interface
  Category:  MaxAgent
  Action:    MaxAgent_Show / MaxAgent_Toggle
```

> 💡 适合开发期、多版本切换、绿色分发。删除整个仓库目录就等于完全卸载。

### 1.b 持久安装（可选：每次启动 Max 自动加载）

如果你想 **每次启动 Max 都自动加载 MaxAgent**（不用每次拖 ms 文件），用 `install.py`：

```powershell
cd path\to\maxagent-project
python install.py            # 装到所有检测到的 Max 版本
python install.py --version 2024   # 只装 Max 2024
python install.py --uninstall      # 卸载
```

安装后启动 Max 会看到 MaxScript Listener 打印 `[MaxAgent] 已加载`，
此后通过 MaxScript `g_show_max_agent()` 或 Python `import maxagent; maxagent.show()` 调出面板。

### 2. 配置 LLM

第一次启动后，点面板顶部的 **⚙ 设置**：

#### A. 本地模型 (Ollama)

```
Profile 名称: ollama-qwen
Base URL:    http://localhost:11434/v1
API Key:     ollama         (占位符，Ollama 不校验)
模型:        qwen2.5:14b    (推荐 14B 起，建模指令需要工具调用能力)
温度:        0.3
```

> 💡 **本地模型选型建议**：必须支持 Function Calling。
> 实测可用：`qwen2.5:14b` / `qwen2.5:32b` / `llama3.1:8b-instruct` / `mistral-nemo`。
> 不可用：纯 chat 模型如 `llama3:8b`（无 tools 支持）。

#### B. LM Studio

```
Base URL: http://localhost:1234/v1
API Key:  lmstudio
模型:     <你在 LM Studio 加载的模型名>
```

#### C. DeepSeek

```
Base URL: https://api.deepseek.com/v1
API Key:  sk-xxx
模型:     deepseek-chat
```

#### D. OpenAI / 兼容协议

```
Base URL: https://api.openai.com/v1
API Key:  sk-xxx
模型:     gpt-4o-mini   (推荐) 或 gpt-4o
```

> 🔐 **API Key 存储**：保存在 `%USERPROFILE%\Documents\3dsMax\maxagent\config.json`，
> 用 base64 简单混淆，**不是加密**——不要在共享机器上保存高权限 Key。

填完后点 **🧪 测试连接** 验证。绿色 ✓ 即可使用。

### 3. 试一试

```
👤 你: 创建一个茶壶，加 TurboSmooth 修改器，迭代 2 次
🤖 助手: 好的，我来创建...
   🔧 调用工具: create_teapot {"radius": 30}
     ✓ {"name": "Teapot001"}
   🔧 调用工具: add_modifier {"node": "Teapot001", "modifier": "TurboSmooth", "params": {"iterations": 2}}
     ✓ {"applied": true}
   已为 Teapot001 添加 2 次迭代的 TurboSmooth。
```

更多示例见 [docs/examples.md](docs/examples.md)。

---

## 📦 项目结构

```
maxagent-project/
├── install.py               # 持久安装脚本（可选）
├── setup.py                 # 可选 pip 安装
├── requirements.txt
├── MAXAGENT_INSTALL.ms      # MaxScript 启动器（免安装入口，拖入 Max 视口即可）
├── README.md                # 本文档
├── docs/
│   ├── architecture.md      # 架构 + 线程模型
│   ├── tool_development.md  # 自定义工具开发指南
│   └── troubleshooting.md   # 常见问题
└── maxagent/
    ├── __init__.py          # 公开 API: show/hide/toggle
    ├── config.py            # Profile 配置管理（4 个内置 Profile）
    ├── llm_client.py        # OpenAI 兼容客户端（流式 + tool calls）
    ├── qt_compat.py         # PySide2/PySide6 适配层
    ├── runtime_helpers.py   # pymxs 版本探测 + 主线程调度
    ├── startup.py           # Max 启动入口
    ├── agent/
    │   ├── conversation.py  # 多轮对话状态机
    │   └── worker.py        # 子线程 LLM + 工具循环
    ├── tools/               # 51 个工具
    │   ├── registry.py      # 工具注册表 + Schema 自动推导
    │   ├── dispatcher.py    # 主线程调度器 + undo 包装
    │   ├── escape_hatch.py  # run_maxscript / run_python
    │   ├── scene_query.py   # 场景查询 (8)
    │   ├── geometry.py      # 几何创建 (10)
    │   ├── transform.py     # 变换 (6)
    │   ├── modifier.py      # 修改器 (5)
    │   ├── material.py      # 材质 (8)
    │   ├── light_camera.py  # 灯光相机 (6)
    │   ├── render.py        # 渲染 (4)
    │   └── scene_io.py      # 场景IO (4)
    └── ui/
        ├── dock_widget.py   # 主聊天面板
        └── settings_dialog.py # 设置对话框
```

---

## 🛠️ 工具速查表

| 类别 | 数量 | 代表工具 |
|------|------|---------|
| 场景查询 | 8 | `list_scene_objects`, `get_object_info`, `find_objects_by_class`, `get_selection`, `get_scene_stats` |
| 几何创建 | 10 | `create_box`, `create_sphere`, `create_teapot`, `create_cylinder`, `create_plane`, `create_torus`, ... |
| 变换 | 6 | `set_position`, `set_rotation`, `set_scale`, `move_object`, `align_to`, `mirror_object` |
| 修改器 | 5 | `add_modifier`, `remove_modifier`, `list_modifiers`, `set_modifier_param`, `collapse_stack` |
| 材质 | 8 | `create_standard_material`, `create_pbr_material`, `assign_material`, `set_material_color`, ... |
| 灯光/相机 | 6 | `create_light`, `set_light_color`, `set_light_intensity`, `create_camera`, `look_at` |
| 渲染 | 4 | `render_frame`, `set_render_resolution`, `set_active_camera`, `save_render` |
| 场景 IO | 4 | `save_scene`, `load_scene`, `import_file`, `export_selected` |
| 逃生舱 | 2 | `run_maxscript` ⚠️, `run_python` ⚠️ |

完整清单：在面板里问 **"列出所有可用工具"**，或在 Python 中：

```python
from maxagent.tools import list_tools
for t in list_tools():
    print(t.name, '-', t.description)
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
                   │ 切回主线程
                   ▼
                Dispatcher (主线程, 持 pymxs)
                   │
                   ▼
                Max 场景 ✓
```

详细架构见 [docs/architecture.md](docs/architecture.md)。

---

## 🐛 遇到问题？

- LLM 连不上 / 401 / 超时 → [docs/troubleshooting.md#llm-连不上](docs/troubleshooting.md)
- Max 启动时白屏 / 卡住 → 立即在 startup 目录重命名 `maxagent_startup.py` 为 `.bak`
- 工具调用失败 → 看面板里红色 ✗ 后面的具体错误信息，多数是模型给错了参数
- 模型不调用工具 → 检查 Profile 的「启用 Function Calling」是否打勾，模型本身要支持 tools

---

## 🤝 贡献 / 扩展

想加新工具？三行代码即可：

```python
from maxagent.tools.registry import tool

@tool('my_cool_op', description='干一些很酷的事')
def my_cool_op(target: str, count: int = 1) -> dict:
    from pymxs import runtime as rt
    # ... 你的 pymxs 代码
    return {'ok': True}
```

详细指南见 [docs/tool_development.md](docs/tool_development.md)。

---

## 📜 License

MIT License. 请勿在生产环境的高安全场景中使用 `run_maxscript` / `run_python` 逃生舱
而不开启二次确认（默认是开的）。

---

**MaxAgent v0.1.0** — Made with ❤️ for 3ds Max users.
