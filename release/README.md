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
├── shared/                  ← 不加密的资源（README/CHANGELOG/LICENSE）
├── ci/
│   └── bk-pipelines.yml     ← 工蜂蓝盾 CI 配置
├── build_cache/             ← 中间产物（已 gitignore）
└── dist/                    ← 最终产物（已 gitignore）
    └── maxagent-X.Y.Z.mzp
```

---

## 快速开始

### 本地一键打包（开发期，单 ABI 快速迭代）

```bash
cd <仓库根>
uv run python release/build.py --quick
```

`--quick` 只构建当前本地 Python 对应的 ABI（默认 cp311），约 30 秒出 mzp。

### 完整 5 ABI 构建（发布前）

```bash
uv run python release/build.py
```

需要本地或 CI 有 Python 3.7 / 3.9 / 3.10 / 3.11 / 3.13 五套环境。
推荐用 `pyenv-win`（Windows）或 `uv python install <版本>`（跨平台）准备。

### 指定版本号发布

```bash
uv run python release/build.py --version 0.4.1
```

会同步修改 `version.py` 并更新 mzp 元数据。

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
拷贝到 %LOCALAPPDATA%\Autodesk\3dsMax\<版本>\ENU\scripts\maxagent\
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
