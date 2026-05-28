# MaxAgent 发布流水线

本目录收敛 MaxAgent 的所有打包/发布相关产物。**修改 `maxagent/` 包源码无需关心本目录**；
本目录只在打包/发布时使用。

---

## 目录结构

```
release/
├── README.md                ← 本文件
├── version.py               ← 单一版本号源
├── pyproject.toml           ← 构建依赖与工具配置
├── cython_modules.txt       ← Cython 白名单（8 个 Tier-1/2 文件）
├── pyarmor_config.toml      ← PyArmor 加密配置
├── mzp_install.ms           ← mzp 内的 MaxScript 启动钩子
├── build.py                 ← 一键打包入口（多 ABI 矩阵）
├── ci/
│   └── bk-pipelines.yml     ← 工蜂蓝盾 CI 配置
├── build_cache/             ← 中间产物（已 gitignore）
└── dist/                    ← 最终产物（已 gitignore）
    └── maxagent-X.Y.Z.mzp
```

---

## 快速开始

最常用：日常开发期增量打包当前本地 ABI（约 30 秒出 mzp）

```bash
cd <仓库根>
uv run python release/build.py --quick
```

完整命令清单见下文 [命令速查](#命令速查)。

---

## 命令速查

> 所有命令统一以 `uv run python release/build.py` 为入口（下表省略前缀），
> 运行目录为仓库根。

### 1. 主命令（决定构建什么 ABI）

| 命令 | 输出 ABI | 适用场景 | 备注 |
|------|---------|---------|------|
| `--quick` | 当前本地 1 个 ABI | 日常开发迭代、快速回归 | 默认 cp311；约 30 秒 |
| *（不传任何参数）* | 全部 5 个 ABI<br/>cp37/cp39/cp310/cp311/cp313 | 单机多 Python 环境下出完整 mzp | 要求本地能解析全部 SUPPORTED_ABIS，否则报 `ABI cpXX unavailable` |
| `--abis cp311 cp313` | 指定子集 | 仅出某几个 Max 版本对应的产物 | 多个 ABI 用空格分隔；非法值会报错 |
| `--all-abis` | 全部 5 个 ABI | 单机自动调度多 Python 子进程出齐全 mzp | 需要本机已装 uv；与 `--quick` / `--abis` 互斥 |

### 2. 辅助参数（与上面主命令组合使用）

| 命令 | 用途 | 适用场景 | 备注 |
|------|------|---------|------|
| `--version 1.0.1` | 同时改写 `release/version.py` 后再打包 | 发版打 tag 前的版本递增 | 必须是 SemVer，如 `1.0.1` 或 `1.0.1-rc1` |
| `--auto-install-pythons` | 缺失的 Python 自动通过 uv 下载 | 全新环境首次构建、CI runner 启动 | 必须配合 `--all-abis`；首次会下数百 MB |
| `--skip-existing` | 已有 `build_cache/cpXX/` 产物的 ABI 直接跳过 | 大矩阵失败重试、增量补出缺失 ABI | 必须配合 `--all-abis` |
| `--skip-pyarmor` | 跳过 PyArmor 加密阶段，仅做 Cython | 调试阶段排查问题 | **发布禁用**，产物中 .py 仍为明文 |
| `--pack-only` | 跳过 Cython/PyArmor，直接用 `build_cache/` 现有产物组装 mzp | CI 矩阵完成后的聚合作业；本地仅修改 `mzp_install.ms` 后重出包 | 不重新编译，速度最快（< 5s） |
| `--allow-cross-abi` | 允许目标 ABI 与当前解释器 ABI 不一致 | 高级用法，跨 ABI 调试 | 默认禁止；启用后可能触发 `SystemError: unknown opcode` |

### 3. 调试 / 信息输出

| 命令 | 用途 | 适用场景 |
|------|------|---------|
| `--dry-run` | 仅打印执行计划，不真正动文件 | 检查参数搭配是否符合预期 |
| `-v` / `--verbose` | 打开详细日志 | 排查构建失败、查看每个文件的处理时延 |

### 4. 典型组合

| 场景 | 命令 |
|------|------|
| 日常单 ABI 快速迭代 | `uv run python release/build.py --quick` |
| 改完 `mzp_install.ms` 想立刻重出包 | `uv run python release/build.py --pack-only` |
| 发版：递增版本 + 出齐全 5 ABI | `uv run python release/build.py --version 1.0.1 --all-abis` |
| 全新机器首次出完整包 | `uv run python release/build.py --all-abis --auto-install-pythons` |
| 矩阵任务挂掉一个 ABI，单独补打 | `uv run python release/build.py --abis cp310` |
| 仅看会做什么，不动文件 | `uv run python release/build.py --all-abis --dry-run -v` |

---

## 产物路径

```
release/build_cache/cpXX/maxagent/   ← 中间产物（每 ABI 一份；gitignore）
release/dist/maxagent-X.Y.Z.mzp      ← 最终产物（gitignore）
```

---

## 保护策略概览

| 文件类别 | 文件数 | 大致行数 | 处理 | 保护强度 |
|----------|--------|----------|------|----------|
| 包入口/热重载/Qt 兼容 | 3 | ~250 | 保留 .py 明文 | - |
| **Tier-1 + Tier-2 高价值文件** | **8** | **~3000** | **Cython → .pyd** | **L3.5** |
| 工具实现 / UI / 其他 | ~50 | ~22500 | PyArmor RFT → .pyc | L2 |

**Tier-1 + Tier-2 涵盖**：系统 prompt、LLM 调用核心、对话编排、API key 处理、工具调度、配置管理。
**剩余 PyArmor 加密**：UI 层、具体工具实现、辅助模块。

---

## mzp 安装流程

```
用户拖 maxagent-X.Y.Z.mzp 进 3ds Max 视口
        ↓
mzp_install.ms 自动执行
        ↓
探测 sys.version_info → 选择 runtime/cpXX/maxagent/
        ↓
拷贝到 %LOCALAPPDATA%\Autodesk\3dsMax\<版本>\<语言>\scripts\maxagent\
        ↓
注册菜单/工具栏 → 用户点击启动
```

**回滚**：用户重新安装 mzp 时会原子替换；卸载执行 `MaxAgent_Uninstall()` 宏。

---

## CI 接入

`ci/bk-pipelines.yml` 是工蜂蓝盾的流水线模板。需要在工蜂项目设置中启用蓝盾后，
该 yaml 会被自动识别。每次 `master` 推送会触发：

1. 5 ABI 矩阵并行构建（cp37 / cp39 / cp310 / cp311 / cp313）
2. 产物归并 → mzp 打包
3. 上传到工蜂制品库
4. 当 git tag `v*.*.*` 推送时同步推送到 Gitee Release

---

## 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| `build.py` 报 "ABI cp313 unavailable" | 本地无 Python 3.13 | `uv python install 3.13.9` |
| Cython 编译失败 | 缺 C 编译器 | Windows 装 VS Build Tools；Linux 装 build-essential |
| PyArmor 报 license 失效 | 试用版到期 | 联系工程负责人申请正式 license |
| mzp 安装后 Max 闪退 | Python 版本不匹配 | 检查 `mzp_install.ms` 探测日志 |
