# MaxAgent · 3ds Max / Maya 内嵌 AI 助手

> 用自然语言操作 3ds Max 或 Maya。建模、加修改器、调材质灯光、批量处理、写脚本、查官方手册、管理个人知识库。

![max](https://img.shields.io/badge/3ds_Max-2022~2027-orange)
![maya](https://img.shields.io/badge/Maya-2024~2027-green)
![python](https://img.shields.io/badge/Python-3.7~3.13-blue)
![qt](https://img.shields.io/badge/Qt-PySide2_|_PySide6-green)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

---

**一句话定位**

MaxAgent 是运行在 3ds Max / Maya 内部的 AI Agent 插件。它通过 Function Calling 让大模型直接调用 DCC 原生 API，同时提供 Autodesk 官方文档检索、本地 BM25 知识库、Skills 扩展、Todo + Verify 自愈、项目记忆等能力，让 AI 不仅能"操作 DCC"，还能"学会你的流程"。同一套面板与交互，按宿主自动切换工具集与领域知识。

---

**核心特性**

- **内置工具**：3ds Max 侧 95 个、Maya 侧 183 个（按宿主加载），覆盖场景查询、几何创建、变换、修改器、材质、灯光相机、渲染、场景 IO、知识库、Skills、学习、反思、记忆等
- **双 DCC 支持**：3ds Max（pymxs）与 Maya（cmds / MEL）按宿主自动切换工具集、领域速查与停靠方式，Agent 层完全共享
- **Function Calling 驱动**：LLM 自主选择工具，schema 由参数注解自动推导
- **本地 + 云端模型**：支持 Ollama / LM Studio / OpenAI / DeepSeek / 任意 OpenAI 兼容协议
- **Autodesk 官方 MCP 接入**：`autodesk_max_docs` / `autodesk_maya_docs` 直连 Autodesk Knowledge，按宿主限定产品作用域，答案带官方出处
- **本地 BM25 知识库**：
  - A 类：打包 Max-Python-Help 官方文档（占位文件已含，可替换）
  - C 类：Skills 语义召回，关键词 + BM25 双路匹配
  - D 类：用户可导入 md / txt / 目录作为个人知识库
- **Agent 自愈**：Todo 规划 + Verify 校验 + BudgetGuard 预算保护
- **项目记忆**：跨会话记录项目背景、决策与约束
- **助手形象**：给助手起名字、换头像，纯 UI 换皮，LLM 行为不变
- **观察式学习**：录制用户手动操作并沉淀为 Skill 或规则
- **团队共享资源目录**：把 Skill / 用户工具 / 规则 / 反思 / 知识源放到一个只读 Git 目录，团队其他成员重启 MaxAgent 即可自动挂载使用
- **IDE Bridge**：HTTP 服务，可与外部 IDE Agent 联动（3ds Max / Maya 双端支持，设置 → IDE 接口）
- **主线程隔离**：工具在 DCC 主线程执行并自动 undo（Max 为 pymxs.undo，Maya 为每工具独立回滚），LLM 请求跑在子线程
- **零外部依赖**：LLM 客户端、MCP、知识库均基于 Python stdlib

---

**快速开始**

**1. 启动**

**3ds Max**：克隆仓库到任意目录，把 `install.ms` 拖进 3ds Max 视口即可。

启动器会把仓库目录注入 `sys.path` 并弹出面板，无需 pip install，也无需拷贝到 Max 启动目录。再次启动 Max 时重新拖入即可，`sys.path` 注入是幂等的。

**Maya**：把仓库根目录的 `maya_entry.py` 拖进 Maya 视口（或打开 Maya 的 Script Editor 执行 `import maya_entry; maya_entry.launch()`）。Maya 会通过原生 workspaceControl 停靠面板，重启后自动恢复停靠状态。

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

**独立运行 / 测试时的 DCC 强制指定**

在非 DCC 环境独立运行（`python -m maxagent.startup`）或跑测试时，自动探测可能拿不到宿主，可用环境变量强制指定：

```bash
set MAXAGENT_FORCE_DCC=3dsmax   # 或 maya
python -m maxagent.startup
```

`maxagent/dcc/runtime.py` 的 `_detect_dcc()` 会优先读取该变量并锁定 `current_dcc()`，工具加载、领域知识、system prompt 均按指定宿主切换。

**2. 打包**

```bash
python release/build.py --dry-run
python release/build.py --target max --verbose    # 产出到 release/dist/maxagent-<version>.mzp
python release/build.py --target maya --verbose   # 产出到 release/dist/maxagent-<version>.zip
```

`--target max` 生成 3ds Max 安装包（`.mzp`，拖入视口安装）；`--target maya` 生成 Maya 安装包（`.zip`，解压后把 `maya_entry.py` 拖入视口或放入 userSetup）；`--target full` 同时包含两侧源码（开发用）。

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

Maya 侧同理：说"创建一个 polyCube，开 3 段细分"即可，助手会调用 Maya 工具集（`create_cube` 等 cmds 封装），坐标系 / 单位 / 历史节点习惯由内置 Maya 世界观速查兜底，不会把 Max 习惯带进 Maya。

---

**3ds Max 与 Maya 的差异**

同一套面板、设置与 Agent 引擎，宿主不同时自动切换以下内容：

| 维度 | 3ds Max | Maya |
| --- | --- | --- |
| 启动入口 | `install.ms` 拖入视口 / MacroScript | `maya_entry.py` 拖入视口 / Script Editor |
| 面板停靠 | `QDockWidget` | Maya 原生 `workspaceControl` |
| API 底座 | `pymxs.runtime` | `maya.cmds` / MEL |
| 脚本逃生舱 | `run_maxscript` + `run_python` | `run_mel` + `run_python` |
| 领域速查 | `lookup_max_knowledge` | `lookup_maya_knowledge` |
| Autodesk 官方文档 | `autodesk_max_docs`（官方 MCP） | `autodesk_maya_docs`（官方 MCP） |
| IDE Bridge | 支持（设置 → IDE 接口） | 支持（设置 → IDE 接口） |
| 打包产物 | `.mzp` | `.zip` |

两侧工具集分属 `maxagent/tools/max/` 与 `maxagent/tools/maya/`，共享层（LLM、知识库、Skills、记忆、UI）完全复用。详细差异表见 `docs/dual_dcc_diff.md`。

---

**工具全景**（3ds Max 侧，Maya 侧按宿主自动加载对应实现；"Autodesk 官方"行为双端各 1 个工具）

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
| Autodesk 官方 | 按宿主 1 | `autodesk_max_docs` / `autodesk_maya_docs` |
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

MaxAgent 内置基于 BM25 的本地检索引擎，零外部依赖，分场景工作。

**A 类 · 官方 Max-Python-Help**

打包时自带 `maxagent/knowledge/data/max_python_help.md` 占位文档。替换为真实的 `Max-Python-Help_2023.md` 后，下次启动 Max 会自动重建索引。

启用后，LLM 可通过 `search_max_docs(query)` 查询官方 API 手册（3ds Max / Maya 均可调用），回答"如何设置材质颜色"这类问题时不再靠幻觉。

**B 类 · DCC 领域速查（按宿主切换）**

内置"世界观 + 话题速查"两层知识，随宿主自动切换：Max 侧覆盖 pymxs 节点 / 修改器 / 材质习惯，Maya 侧覆盖 Y-up 坐标系、transform + shape 层级、construction history 等 cmds 习惯，避免把 Max 习惯带进 Maya（反之亦然）。LLM 通过 `lookup_max_knowledge` / `lookup_maya_knowledge` 按需查询，高频常识则直接注入 system prompt。

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

`macro_recorder` 录制用户在 DCC 中的手动操作，结合 `reflection_tools` 沉淀为可复用 Skill 或规则。

---

**助手形象**

设置 → **👤 助手形象**：给 MaxAgent 这个"岗位"选一位"上任的员工"——起名字 + 换头像。

- **纯 UI 换皮**：名字与头像只影响对话气泡的视觉显示，LLM 对此一无所知；岗位职责与身份铁律不变
- **头像两种模式**：Emoji（固定为默认形象，内置 SVG 图标渲染，不依赖系统字体）或自定义图片（自动缩放为 64×64 PNG 存储于 `{config_dir}/avatar.png`）
- **图片头像渲染**：以 base64 data URI 内嵌气泡，PySide2/6 双版本一致；图片文件丢失时自动回落默认形象
- **实时预览**：保存前可在预览区确认效果，保存后下一条新消息开始生效

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

工具默认在 DCC 主线程执行（Max 包裹 `pymxs.undo`）。纯查询工具加 `wrap_undo=False`。用 `dcc=['maya']` 或 `dcc=['3dsmax']` 可把工具限定到特定宿主，不写则双端可见。

Maya 侧工具请从 `maxagent/tools/maya/_common.py` 复用 `_ensure_in_maya` / `_normalize_names` / `_rollback_on_error` 等校验与回滚助手，不要在业务文件里复制。

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
- 设置面板提供「拉取最新」按钮：先 `git fetch` 检测更新，再让用户确认后执行 `git pull --ff-only`
- 设置面板提供「克隆仓库」按钮：输入 Git URL 即可把团队仓库克隆到本地并自动挂载

---

**故障排查**

- LLM 连不上 / 401 / 超时：检查 Base URL 与 API Key，本地服务需先启动
- 3ds Max 启动白屏 / 卡住：把 `startup` 目录的 `maxagent_startup.py` 改名为 `.bak`
- Maya 面板不出现：确认 `maya_entry.py` 拖入后 Script Editor 无报错；重启 Maya 会自动恢复 workspaceControl
- 工具调用失败：看面板红色 ✗ 后的具体错误，多数是模型给错参数
- 模型不调用工具：检查 Profile 的 `supports_tools`，且模型要支持 tools
- Autodesk MCP 无响应：需要外网可达 `developer.api.autodesk.com`
- 知识库查不到：确认已导入文档或替换 `max_python_help.md` 后重启 DCC
- 开发时改了代码不生效：`maxagent.reload_pkg()`（Max）或重开 Maya 面板

---

**License**

MIT License. 使用 `run_maxscript`（Max）/ `run_mel`、`run_python`（Maya）逃生舱时请保留默认的二次确认。

---

**MaxAgent v1.0.1** — Made for 3ds Max & Maya users.