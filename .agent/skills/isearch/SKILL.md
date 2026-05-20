---
name: isearch
description: "当需要内网检索，如提到 km、iwiki、乐享、腾讯学院、8000、hr、bbs、太极 等公司内部平台搜索时使用"
homepage: https://prod.mcp.it.woa.com/app_iwiki_mcp/mcp3
---

# 腾讯公司内部全网信息搜索服务

用于搜文档、查资料、找知识、检索内容的工具。支持iWiki、KM、乐享、腾讯学院、Code、GitCode、Tapd、Bugly等多个内部平台。

## ⚠️ 重要提醒：必须通过 mcporter-internal 调用

**此 MCP 服务不能直接调用**，必须通过 `mcporter-internal` 这个 MCP 客户端工具来访问：

```
✅ 正确方式：mcporter-internal call isearch.<工具名>
❌ 错误方式：直接调用 MCP 工具（会失败）
```

> 💡 不熟悉 mcporter-internal 用法？请参考 `mcporter-internal` skill 文档。若当前环境未加载该 skill，可查阅本目录下的 `DEPENDENCIES.md`。

---

isearch 提供腾讯公司内部全网内容的关键词搜索能力，支持文档检索、知识库查询、技术资料查找等场景

---

## 工具列表

### `searchDocument` - 全网文档搜索/内容检索工具

**功能**: 在腾讯公司内部各平台（iWiki、KM、乐享、8000等）进行全文搜索，查找技术文档、项目资料、知识库内容。

**适用场景**：
- 搜索技术文档、API文档、开发规范
- 查询项目资料、业务知识、产品文档
- 查找团队分享、经验总结、最佳实践
- 搜索内部问答、解决方案、历史记录
- 检索特定作者、标签的内容

| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| query | string | ✓ | 要搜索的关键词内容（支持模糊匹配） |
| from | string | ✓ | 搜索来源平台 iwiki/km/all，`all`（搜索全网） |

**调用示例**：

```bash
# 搜索技术文档
mcporter-internal call isearch.searchDocument from=all query="React最佳实践"
```

---

## 使用建议
**from=all 参数不能省略**

---

## 注意事项
1. 搜索结果数量建议根据实际需求调整，避免返回过多无关内容
2. **JSON 参数跨平台写法**：
   - **macOS / Linux**：`--args "{\"key\": \"value\"}"`
   - **Windows PowerShell**：`--args '{\"key\": \"value\"}'`（单引号包裹，内部双引号仍需 `\` 转义）
   - **Windows PowerShell 禁止**用双引号包裹 JSON（如 `--args "{"key":"value"}"`），否则内部双引号会被 PowerShell 自身吞掉，导致报错 `Unable to parse --args: Expected property name or '}' in JSON at position 1`
   - 详见 `mcporter-internal` skill 文档