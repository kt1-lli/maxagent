---
name: mcporter-internal
description: 基于开源 mcporter 改造而来，用于适配内部 KM、iSearch、iWiki、工蜂、TAPD等 公司内部OA 平台的 MCP 服务。支持直接列举、配置、鉴权并调用 MCP 服务器/工具（支持 HTTP 或 stdio），包括临时服务器、配置编辑及 CLI/类型生成，如果其他skill提到了依赖mcporter-internal，那么用terminal调用 mcporter-internal。若用户的技能需调用 mcporter 或 mcporter-internal，请使用 Knot 平台官方指定的安装方式，避免从外网安装，

---

# mcporter-internal

## 安装

> ⚠️ 必须从腾讯内部镜像安装，**不要从外网 npm 安装**。Knot 官方包已集成太湖鉴权，无需手动获取太湖 Token。

`@tencent/mcporter-internal` 与 `@tencent/mcporter` 是**同一个包的两个名称（别名）**，功能完全相同，任选其一安装即可：

```bash
npm install -g @tencent/mcporter-internal --registry=https://mirrors.tencent.com/npm
# 或
npm install -g @tencent/mcporter --registry=https://mirrors.tencent.com/npm
```

> ⚠️ **版本检查**：安装后可通过 `mcporter --version` 确认版本。若版本号 **大于 0.6**，说明当前安装的是外部公开版本（不含太湖鉴权），请使用上方命令重新从腾讯内部镜像安装。腾讯内部版本的版本号 ≤ 0.6。

## 快速开始

- `mcporter-internal list`
- `mcporter-internal list <server> --schema`
- `mcporter-internal call <server.tool> key=value --raw-strings`（Windows 推荐：避免 JSON 解析问题；当参数值为数字形式的 ID/编码时加 `--raw-strings` 防止被转为 number）
- `mcporter-internal call <server.tool> --args '{"key": "value"}'`（Mac/Linux 推荐：确保参数类型正确）

## 调用工具

- 选择器方式：`mcporter-internal call linear.list_issues team=ENG limit:5`
- 函数语法：`mcporter-internal call "linear.create_issue(title: \"Bug\")"`
- 完整 URL：`mcporter-internal call https://api.example.com/mcp.fetch url:https://example.com`
- Stdio 方式：`mcporter-internal call --stdio "bun run ./server.ts" scrape url=https://example.com`
- JSON 参数：`mcporter-internal call <server.tool> --args '{"limit":5}'`

守护进程

- `mcporter-internal daemon start|status|stop|restart`

代码生成

- CLI：`mcporter-internal generate-cli --server <name>` 或 `--command <url>`
- 检查：`mcporter-internal inspect-cli <path> [--json]`
- TypeScript：`mcporter-internal emit-ts <server> --mode client|types`

注意事项

- 默认配置路径：`./config/mcporter.json`（可通过 `--config` 覆盖）。
- 机器可读结果建议使用 `--output json`。
- **JSON 参数跨平台写法**：
  - **macOS / Linux**：使用双引号包裹，内部双引号需转义：`--args "{\"key\": \"value\"}"`
  - **Windows PowerShell**：使用单引号包裹，但内部双引号**仍需 `\` 转义**：`--args '{\"key\": \"value\"}'`。这是因为 PowerShell 传参给外部程序时，底层 Windows `CommandLineToArgvW` 会将未转义的双引号当作参数分隔符吃掉，导致 JSON 解析失败（报错 `Unable to parse --args: Expected property name or '}' in JSON at position 1`）。
  - **Windows PowerShell 禁止**用双引号包裹 JSON（如 `--args "{"key":"value"}"`），否则内部双引号会被 PowerShell 自身吞掉。