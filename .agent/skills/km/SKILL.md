---
name: km
description: 搜索km文章时使用
---


## 热门文章

使用mcporter-internal调用km mcp服务
mcporter-internal call "km.hot-articles(limit:10)"

> 💡 不熟悉 mcporter-internal 用法？请参考 `mcporter-internal` skill 文档。若当前环境未加载该 skill，可查阅本目录下的 `DEPENDENCIES.md`。

## 关键字搜索

使用mcporter-internal调用km mcp服务
mcporter-internal call "km.search-articles-visited(keywords:['key'], max_results: 5)" , key为用户输入的关键词

## 注意事项

- 要把输出的标题及链接变成企微的超链接，方便直接点击打开文章
- 在输出的结尾附带上执行时间及数据来源KM MCP等信息
- **JSON 参数跨平台写法**：
  - **macOS / Linux**：`--args "{\"key\": \"value\"}"`
  - **Windows PowerShell**：`--args '{\"key\": \"value\"}'`（单引号包裹，内部双引号仍需 `\` 转义）
  - **Windows PowerShell 禁止**用双引号包裹 JSON（如 `--args "{"key":"value"}"`），否则内部双引号会被 PowerShell 自身吞掉，导致报错 `Unable to parse --args: Expected property name or '}' in JSON at position 1`
  - 详见 `mcporter-internal` skill 文档