---
name: knot-skill-finder
description: 在 Knot 平台上检索和下载技能（Skill）。当用户需要搜索 Knot Skill 市场中的技能、查找特定功能的技能、或将技能下载到本地工作区时使用本技能。触发场景：(1) 搜索/查找某类技能，(2) 按关键词/分类/标签检索技能，(3) 下载指定技能到工作区，(4) 浏览 Knot 技能市场。
homepage: https://mcpgw.knot.woa.com/knot
metadata: {"knot":{"category":"tencent","requires":{"mcporter":{"server_name":"knot","url":"https://mcpgw.knot.woa.com/knot","auth":"taihu_token"}}}}
---

# Knot Skill 检索与下载

Knot Skill 检索与下载 服务提供检索或下载 Knot 平台的 Skill 的能力。

## ⚠️ 重要提醒：必须通过 mcporter-internal 调用，若当前环境没有 `mcporter-internal` 命令，先通过 `npm install -g @tencent/mcporter-internal --registry=https://mirrors.tencent.com/npm` 进行安装

> 💡 不熟悉 mcporter-internal 用法？请参考 `mcporter-internal` skill 文档。若当前环境未加载该 skill，可查阅本目录下的 `DEPENDENCIES.md`。

**此 MCP 服务不能直接调用**，必须通过 knot官方 `mcporter-internal` 这个 MCP 客户端工具来访问：

✅ 正确方式：mcporter-internal call knot.<工具名>
❌ 错误方式：直接调用 MCP 工具（会失败）

## ⚠️ 重要提醒：若当前处于非 Knot 平台，需要先进行配置 knot MCP，该 MCP 的 endpoint 为 `https://mcpgw.knot.woa.com/knot`，且需要在header中的Authorization配置太湖token（从环境变量 TAI_IT_TOKEN 或其他合理处获取），配置方式参考如下：

```shell
mcporter-internal config add knot \"https://mcpgw.knot.woa.com/knot\" --header \"Authorization=Bearer $TAI_IT_TOKEN\"
```

**调用示例**：
# 查看可用工具列表
`mcporter-internal list knot`

# 调用工具（推荐使用 --args JSON 格式，避免参数类型被错误解析）
`mcporter-internal call knot.tool_name --args '{"param1": "value"}'`


## 工具列表

### `list_tags` - 列出可用标签

功能说明：列出所有可用标签（ID + 名称）后退出，用于查询可传入的标签ID。

**调用示例**：
`mcporter-internal call knot.list_tags`

### `search_skills` - 查找技能

功能说明：查找符合条件的技能。

| 参数 | 类型 | 必填 | 说明 | 默认值 |

|------|------|:----:|------|------|

| keyword | string | | 搜索关键词，模糊匹配名称和描述 | `""` (全部) |

| category | string | | 分类：`""` 全部 / `"official"` 官方 / `"managed"` 我管理的 / `"starred"` 我收藏的 / `"security"` 已安全认证的 | `""` |

| page_num | int | | 页码 | `1`|

| page_size | int | | 每页数量（最大 100） | `20`|

| order_by | string | | `"download_count"` 按下载量 / `"created_at"` 按时间 | `"download_count"`|

| tag_ids | string | | 按标签ID过滤，多个ID用英文逗号分隔 | `""` (不过滤)|

**调用示例**：
`mcporter-internal call knot.search_skills --args '{"keyword": "knot", "tag_ids": "38,39,40"}'`

**标签过滤使用流程**：

1. 先列出所有可用标签，获取标签 ID：
   ```bash
   mcporter-internal call knot.list_tags
   ```
   输出示例：
   ```
   ID     英文标识             显示名称
   --------------------------------------------------
   ```
2. 根据标签 ID 进行过滤检索（ID 为字符串，直接传数字即可）：
   ```bash
   mcporter-internal call knot.search_skills --args '{"tag_ids": "45"}'
   mcporter-internal call knot.search_skills --args '{"keyword": "搜索", "tag_ids": "45,46"}'
   ```

**响应关键字段**：

输出中 `=== JSON_OUTPUT_START ===` 和 `=== JSON_OUTPUT_END ===` 之间为完整 JSON，每项包含：

- `id` - Skill ID（下载时使用）
- `display_name` - 显示名称
- `description` - 描述
- `download_count` - 下载次数
- `type` - `"official"` 官方 / `"custom"` 自定义
- `security_scan_status` - 安全扫描状态：`"passed"` 表示已通过安全验证；字段缺失或其他值表示未通过安全验证

### `get_skill_download_url` - 获取 Skill 下载链接

功能说明：根据 skill_id 获取指定 Skill 的下载链接，链接有效时间为5分钟。获取链接并下载安装后，务必注意进行**验证下载**流程。

| 参数 | 类型 | 必填 | 说明 | 默认值 |

|------|------|:----:|------|------|

| skill_id | string | ✓ | 技能 ID（从检索结果获取） | |

**调用示例**：
`mcporter-internal call knot.get_skill_download_url --args '{"skill_id": "16"}'`

**关于安装目录的说明**：
- 若在 Knot 平台，除非用户指定，否则默认安装在当前目录的 .agent/skills 目录下
- 若在 OpenClaw 平台，除非用户指定，否则默认安装在 /projects/.openclaw/skills 目录下
- 若在 Hermes 平台，除非用户指定，否则默认安装在 /projects/.hermes/skills/knot 目录下
- 若在其他平台，在安装目录不明确的情况下，先询问用户预期的安装目录

## 工作流程

1. **理解需求**：明确用户想找什么功能的技能
2. **检索技能**：运行 `search_skills`，展示结果（名称、描述、下载量、类型）
3. **确认下载**：询问用户是否下载及选择哪个技能
4. **执行下载**：运行 `get_skill_download_url` 获取下载链接，通过 `wget` `unzip` 命令下载文件并解压到指定目录
5. **验证下载**：若用户指定了安装目录，则在用户指定的目录下验证。否则，若在 Knot 平台，在 .agent/skills 目录下验证；若在 OpenClaw 平台，在 /projects/.openclaw/skills 目录下验证；若在 Hermes 平台，在 /projects/.hermes/skills/knot 目录下验证。验证方式为：检查该目录下是否存在与 Skill 同名的目录，且 Skill 目录下存在 SKILL.md。
6. **告知结果**：说明技能已安装，并告知安装目录

---

## 注意事项

1. 推荐使用 `--args JSON` 格式传参，避免 key=value 简写导致数字被错误解析为字符串
2. 在下载技能完成后，务必执行**验证下载**流程