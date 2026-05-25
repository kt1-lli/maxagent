# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/) 规范。

## [0.4.0] - 2026-05-25

首版正式发布版（mzp 形式）。

### Added
- 多 ABI 单包发布：mzp 内含 5 套 Python 字节码（cp37 / cp39 / cp310 / cp311 / cp313），
  覆盖 3ds Max 2022~2027 全部版本
- Cython + PyArmor 混合保护：核心 8 个文件（系统 prompt / LLM 调用 / 对话编排
  / 工具调度 / 配置管理）以 Cython .pyd 形式分发，其余文件 PyArmor 加密
- mzp 拖入即装，自动探测 Python 版本并选择对应产物
- macroScript 自动注册：MaxAgent_Show / MaxAgent_Toggle / MaxAgent_Uninstall

### Changed
- 安装目录从仓库源码切换到 `%LOCALAPPDATA%\Autodesk\3dsMax\<版本>\ENU\scripts\maxagent\`
- 用户数据 `_userdata/` 保持原位置，安装/升级/卸载不会触碰

### Notes
- 0.4.0 之前的内部源码版本不再追溯进 changelog
- 升级用户：从源码态切到 mzp 态时，建议先卸载源码版（移除工程根并清理 sys.path 注入），
  再安装 mzp 版，避免双重 import
