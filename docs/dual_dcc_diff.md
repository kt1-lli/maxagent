# 双 DCC 差异表（3ds Max / Maya）

> MaxAgent 同时支持 3ds Max 与 Maya。绝大多数逻辑走共享代码；本表列出实际存在差异的点位，方便修 bug 或加功能时直接对齐。
> 更新原则：只记录**结构性差异**，一次性 API 名字差异不进本表（去 `tools/{max,maya}/` 看源码即可）。

## 1. 宿主与入口

| 维度 | 3ds Max | Maya |
|---|---|---|
| DCC 探测 | `sys.modules` 含 `pymxs` | `sys.modules` 含 `maya.cmds` |
| 启动入口 | `maxagent/startup.py::show_panel` | `maya_entry.py` + `maxagent/ui/maya_startup.py` |
| DCC 标记 | `ensure_current_dcc('3dsmax')` | `ensure_current_dcc('maya')` |
| 打包产物 | `.mzp`（含 mzp.run + macros/*.mcr） | `.zip`（拖拽脚本 + 源码） |
| 用户可见触发 | MaxScript `g_show_max_agent()` | Shelf/Python 命令 |

## 2. Dock/窗口

| 维度 | 3ds Max | Maya |
|---|---|---|
| Dock 容器 | `QDockWidget` + `QMainWindow.addDockWidget` | `workspaceControl` |
| 状态恢复 | `_restore_main_window_state` | Maya 原生 workspaceControl 状态 |
| 窗口标题 | "MaxAgent · 3ds Max AI 助手" | "MaxAgent · Maya AI 助手" |
| 分派入口 | `_create_max_dock(config)` | `_create_maya_dock(config)` |

分支收敛：`maxagent/ui/_dock_dcc.py`（`dock_window_title()` + `dispatch_dock_creation()`）。任何新增的"按 DCC 分派"应加到这里而不是散落到 `dock_widget.py`。

## 3. 工具层

| 维度 | 3ds Max | Maya |
|---|---|---|
| 目录 | `maxagent/tools/max/` | `maxagent/tools/maya/` |
| API 底座 | `pymxs.runtime` | `maya.cmds` |
| 参数校验/回滚 | 各文件自带（历史遗留） | 集中在 `tools/maya/_common.py` |
| 主线程调度 | `run_on_main`（同一实现） | `run_on_main`（同一实现） |
| 失败回滚 | 未统一（跟 Max SDK 弱事务性有关） | `rollback_on_error` context manager（`geometry.py`、`light_camera.py`） |
| info 工具 | `get_object_info` | `get_object_info`（Maya 版） |

关键约定：
- Maya 侧新增工具时**必须**从 `_common.py` 引 `_ensure_in_maya` / `_normalize_names` / `_to_xyz_list` / `_to_color` / `_parse_scalar`，禁止在业务文件复制这些校验函数。
- 创建型工具（geometry/light/camera 等）后续如设置 xform 失败，必须用 `rollback_on_error([transform])` 包裹，避免留下半成品节点。

## 4. Agent/Worker 层（无差异）

`agent/worker.py`、`agent/verify.py`、`llm_client.py` 完全共享，不做 DCC 分支。工具复核的实体存在性检查（`exists=False`/`found=False`）在两侧口径一致。

## 5. 打包过滤（release/build.py）

按 `--target` 参数过滤，避免 Max 用户拿到 Maya 代码：

| target | tools/ 子包 | dcc/*_adapter.py | ui 专属 | 根入口 | 产物 |
|---|---|---|---|---|---|
| `max` | `shared`, `max` | `max_adapter.py` | `dock_widget.py` | — | `.mzp` |
| `maya` | `shared`, `maya` | `maya_adapter.py` | `maya_startup.py` | `maya_entry.py` | `.zip` |
| `full` | `shared`, `max`, `maya` | 两者 | 两者 | `maya_entry.py` | `.mzp` |

过滤规则集中在 `release/build.py` 的 `_TARGET_TOOLS_SUBDIRS` / `_TARGET_DCC_DIRS` / `_TARGET_ROOT_FILES`。加新 DCC 专属文件时，**必须**同步更新这三个字典，否则会跑进错的产物。

## 6. 已知非对称点（暂不修）

- Max 工具没有像 Maya `_common.py` 那样的公共校验/回滚层，属于历史结构。等 Max 侧引入 `tools/max/_common.py` 前不做统一。
- Max 侧 `.mzp` 安装脚本 `mzp_install.ms` 强依赖 Max 的 MacroScript 体系；Maya 侧走 `userSetup` 或拖拽脚本，没有等价物。
- Bridge（HTTP 端口）目前只有 Max 侧启用（`startup.py::_start_bridge`），Maya 侧尚未提供。
