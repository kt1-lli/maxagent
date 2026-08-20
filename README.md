# MaxAgent · 3ds Max 内嵌 AI 助手

> 用自然语言操作 3ds Max。建模、加修改器、调材质灯光、批量处理、写脚本、查官方手册、管理个人知识库。

![max](https://img.shields.io/badge/3ds_Max-2022~2027-orange)
![python](https://img.shields.io/badge/Python-3.7~3.13-blue)
![qt](https://img.shields.io/badge/Qt-PySide2_|_PySide6-green)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

---

**一句话定位**

MaxAgent 是运行在 3ds Max 内部的 AI Agent 插件。它通过 Function Calling 让大模型直接调用 Max 原生 API，同时提供 Autodesk 官方文档检索、本地 BM25 知识库、Skills 扩展、Todo + Verify 自愈、项目记忆等能力，让 AI 不仅能"操作 Max"，还能"学会你的流程"。

---

**核心特性**

- **近 100 个内置工具**：覆盖场景查询、几何创建、变换、修改器、材质、灯光相机、渲染、场景 IO、知识库、Skills、学习、反思、记忆等
- **Function Calling 驱动**：LLM 自主选择工具，schema 由参数注解自动推导
- **本地 + 云端模型**：支持 Ollama / LM Studio / OpenAI / DeepSeek / 任意 OpenAI 兼容协议
- **Autodesk 官方 MCP 接入**：`autodesk_max_docs` 直连 Autodesk Knowledge，答案带官方出处
- **本地 BM25 知识库**：
  - A 类：打包 Max-Python-Help 官方文档（占位文件已含，可替换）
  - C 类：Skills 语义召回，关键词 + BM25 双路匹配
  - D 类：用户可导入 md / txt / 目录作为个人知识库
- **Agent 自愈**：Todo 规划 + Verify 校验 + BudgetGuard 预算保护
- **项目记忆**：跨会话记录项目背景、决策与约束
- **观察式学习**：录制用户手动操作并沉淀为 Skill 或规则
- **团队共享资源目录**：把 Skill / 用户工具 / 规则 / 反思 / 知识源放到一个只读 Git 目录，团队其他成员重启 MaxAgent 即可自动挂载使用
- **IDE Bridge**：HTTP 服务，可与外部 IDE Agent 联动
- **主线程隔离**：工具在 Max 主线程执行并自动 undo，LLM 请求跑在子线程
- **零外部依赖**：LLM 客户端、MCP、知识库均基于 Python stdlib

---

**快速开始**

**1. 启动**

克隆仓库到任意目录，把 `install.ms` 拖进 3ds Max 视口即可。

启动器会把仓库目录注入 `sys.path` 并弹出面板，无需 pip install，也无需拷贝到 Max 启动目录。再次启动 Max 时重新拖入即可，`sys.path` 注入是幂等的。

注册到工具栏 / 快捷键：

```
Customize → Customize User Interface
  Category:  MaxAgent
  Action:    MaxAgent_Show / MaxAgent_Toggle
```

代码中也可主动调用：

```python
import maxagent
maxagent.show()
maxagent.toggle()
maxagent.reload_pkg()  # 开发态热重载
```

MaxScript 全局函数：`g_show_max_agent()` / `g_toggle_max_agent()` / `g_reload_max_agent()`。

**2. 打包 mzp**

```bash
python release/build.py --dry-run
python release/build.py --verbose   # 产出到 release/dist/maxagent-<version>.mzp
```

把 `.mzp` 拖入 Max 视口即可安装。

**3. 配置 LLM**

首次启动后点面板顶部的 **⚙ 设置**。内置 4 个 Profile：

| Profile | Base URL | 默认模型 |
| --- | --- | --- |
| Ollama | `http://localhost:11434/v1` | `qwen2.5:14b` |
| LM Studio | `http://localhost:1234/v1` | `local-model` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-v4-flash` |

也可新建任意 OpenAI 兼容网关。填完点 **🧪 测试连接**。

> 本地模型必须支持 Function Calling。实测可用：`qwen2.5:14b+`、`llama3.1:8b-instruct`、`mistral-nemo`。  
> Key 存储在 `%USERPROFILE%\Documents\3dsMax\maxagent\config.json`，仅 base64 混淆，共享机器请勿保存高权限 Key。

**4. 试一试**

```
👤 你: 创建一个茶壶，加 TurboSmooth 修改器，迭代 2 次
🤖 助手:
   🔧 create_teapot {"radius": 30}              ✓ {"name": "Teapot001"}
   🔧 add_modifier {"node": "Teapot001",
                    "modifier": "TurboSmooth",
                    "params": {"iterations": 2}}  ✓ {"applied": true}
   已为 Teapot001 添加 2 次迭代的 TurboSmooth。

👤 你: 把项目规范.md 加进知识库
🤖 助手:
   🔧 add_knowledge_source {"path": "D:\\项目规范.md"}  ✓ {"ok": true}
   已导入，之后可直接用自然语言查询其中的规范。
```

---

**工具全景**

| 类别 | 数量 | 代表工具 |
| --- | --- | --- |
| 场景查询 | 9 | `get_max_info`、`get_scene_stats`、`list_objects`、`find_objects_by_name` 等 |
| 几何创建 | 7 | `create_box`、`create_sphere`、`create_teapot` 等 |
| 变换 | 5 | `move_object`、`rotate_object`、`scale_object`、`align_to`、`reset_pivot` |
| 修改器 | 4 | `add_modifier`、`remove_modifier`、`list_modifiers`、`collapse_stack` |
| 材质 | 5 | `create_standard_material`、`create_physical_material`、`assign_material` 等 |
| 灯光相机 | 4 | `create_light`、`create_camera`、`set_viewport_camera` 等 |
| 渲染 | 3 | `render_current_frame`、`render_animation`、`set_render_resolution` |
| 场景 IO | 13 | 保存 / 加载 / 导入 / 导出 / 合并 / 删除 / 成组 / 重命名 / 重置场景 |
| 知识库 | 7 | `search_max_docs`、`search_knowledge`、`add_knowledge_source` 等 |
| Skills | 7 | `save_skill`、`list_skills`、`run_skill_code` 等 |
| 学习工具 | 4 | `propose_new_tool`、`list_learned_tools` 等 |
| 学习规则 | 3 | `suggest_rule_addition`、`list_learned_rules` 等 |
| 反思 | 3 | `reflect_on_outcome`、`list_reflections` 等 |
| 记忆 | 4 | `memory_read`、`memory_search`、`memory_write`、`event_search` |
| 联网 | 2 | `web_search`、`web_fetch` |
| Autodesk 官方 | 1 | `autodesk_max_docs` |
| 创意高级 | 4 | `generate_material_variants`、`smart_replace_modifier` 等 |
| 场景感知 | 4 | `capture_viewport`、`check_mesh_quality`、`diff_scene_snapshots` 等 |
| Todo | 3 | `todo_write`、`todo_update_status`、`todo_read` |
| 高级工作流 | 5 | `setup_studio_scene`、`create_three_point_lighting`、`align_along_curve` 等 |
| 批量 | 1 | `batch_execute` |
| 逃生舱 | 2 | `run_maxscript`、`run_python` |

查看全部工具：

```python
from maxagent.tools import load_all_tools, list_tools
load_all_tools()
for t in list_tools():
    print(t.name, '-', t.description)
```

---

**知识库系统**

MaxAgent 内置基于 BM25 的本地检索引擎，零外部依赖，分三场景工作。

**A 类 · 官方 Max-Python-Help**

打包时自带 `maxagent/knowledge/data/max_python_help.md` 占位文档。替换为真实的 `Max-Python-Help_2023.md` 后，下次启动 Max 会自动重建索引。

启用后，LLM 可通过 `search_max_docs(query)` 查询官方 API 手册，回答"如何设置材质颜色"这类问题时不再靠幻觉。

**C 类 · Skills 语义召回**

Skills 不再只靠关键词匹配。保存 Skill 时会自动建立 BM25 索引；当用户输入没有命中关键词时，语义召回会作为兜底返回最相关的 2 个 Skill。

**D 类 · 个人知识库**

用户可把 md / txt 文件或目录导入本地知识库。导入后文件会被复制到 `{config_dir}/knowledge/user_sources/<source_id>/`，原文件可安全删除。之后用 `search_knowledge(query)` 检索。

---

**Agent 能力**

**Todo + Verify 自愈**

复杂任务会自动拆成 Todo 列表。每个子任务执行后会 Verify 校验，失败则触发重试或修正。LLM 可调用 `todo_write`、`todo_update_status`、`todo_read` 查看进度。

**BudgetGuard**

为 LLM 调用次数、Token 消耗、工具执行次数设置预算上限，防止失控循环。

**项目记忆**

跨会话保存项目背景、命名规范、重要决策。LLM 可调用 `memory_read` / `memory_search` / `memory_write` 读写项目记忆。

**观察式学习**

`macro_recorder` 录制用户在 Max 中的手动操作，结合 `reflection_tools` 沉淀为可复用 Skill 或规则。

---

**项目结构**

```
maxagent/
├── install.ms                     # MaxScript 入口，拖入视口启动
├── README.md
├── release/                       # 打包与 CI
│   ├── build.py
│   ├── version.py
│   ├── macros/
│   ├── mzp_install.ms
│   └── ...
├── maxagent/
│   ├── __init__.py                # show / hide / toggle / reload_pkg
│   ├── startup.py
│   ├── config.py                  # Profile 管理
│   ├── llm_client.py              # OpenAI 兼容客户端
│   ├── autodesk_mcp.py            # Autodesk Knowledge MCP
│   ├── runtime_helpers.py         # pymxs 版本探测 + 主线程调度
│   ├── qt_compat.py
│   ├── logger.py
│   ├── shared_resources.py      # 共享只读资源扫描、冲突解决、写保护
│   ├── skills.py                  # Skill 加载与语义召回
│   ├── macro_recorder.py
│   ├── reflections_loader.py
│   ├── session_memory.py          # 会话记忆
│   ├── summarization_checkpoint.py# 长对话摘要
│   ├── ui_state.py
│   ├── reload.py
│   ├── agent/
│   │   ├── worker.py              # LLM + 工具循环
│   │   ├── conversation.py
│   │   ├── approval_queue.py      # 人工审批
│   │   ├── budget_guard.py        # 预算保护
│   │   ├── task_context.py
│   │   ├── scene_snapshot.py
│   │   ├── coding_rules.py
│   │   ├── few_shot_examples.py
│   │   └── max_knowledge.py
│   ├── tools/                     # 全部工具
│   ├── knowledge/                 # BM25 知识库引擎
│   │   ├── bm25.py
│   │   ├── tokenizer.py
│   │   ├── chunker.py
│   │   ├── sources.py
│   │   ├── index.py
│   │   └── data/
│   ├── memory/                    # 项目记忆系统
│   │   ├── project_memory.py
│   │   ├── search.py
│   │   ├── store.py
│   │   └── writer.py
│   ├── learning/                  # Skill 生成
│   │   └── skill_generator.py
│   ├── bridge/                    # IDE Bridge HTTP 服务
│   └── ui/                        # PySide2/6 面板
```

---

**扩展：自定义工具**

参数注解自动转成 OpenAI Tools Schema，最少三行注册：

```python
from maxagent.tools.registry import tool

@tool(description='干一些很酷的事', category='custom')
def my_cool_op(target: str, count: int = 1) -> dict:
    from pymxs import runtime as rt
    # ...
    return {'ok': True}
```

工具默认在 Max 主线程执行并包裹 `pymxs.undo`。纯查询工具加 `wrap_undo=False`。

---

**团队共享资源目录**

把 Skill / 用户工具 / 规则 / 反思 / 知识源作为团队共享资产：

```bash
# 1. 团队 TA/TD 维护一个 Git 仓库
shared-maxagent-assets/
├── skills/
├── user_tools/
├── user_rules/
├── reflections/
└── knowledge/

# 2. 美术在本地 clone 后，通过环境变量或设置面板挂载
set MAXAGENT_SHARED_DIR=C:\\TeamAssets\\shared-maxagent-assets
```

- 共享资源对当前实例**只读**，不会污染本地 `config_dir`
- 同名资产默认**使用共享版本**；首次冲突会弹出对话框，也可在设置页切换默认策略
- 共享的 `user_tool` 会自动加 `shared_` 前缀，避免和本地工具冲突
- 共享工具首次调用前会做语法检查，团队入库前建议先在本地验证

---

**故障排查**

- LLM 连不上 / 401 / 超时：检查 Base URL 与 API Key，本地服务需先启动
- Max 启动白屏 / 卡住：把 `startup` 目录的 `maxagent_startup.py` 改名为 `.bak`
- 工具调用失败：看面板红色 ✗ 后的具体错误，多数是模型给错参数
- 模型不调用工具：检查 Profile 的 `supports_tools`，且模型要支持 tools
- Autodesk MCP 无响应：需要外网可达 `developer.api.autodesk.com`
- 知识库查不到：确认已导入文档或替换 `max_python_help.md` 后重启 Max
- 开发时改了代码不生效：`maxagent.reload_pkg()` 或 `g_reload_max_agent()`

---

**License**

MIT License. 使用 `run_maxscript` / `run_python` 逃生舱时请保留默认的二次确认。

---

**MaxAgent v1.0.1** — Made for 3ds Max users.
