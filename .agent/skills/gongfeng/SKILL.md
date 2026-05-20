---
name: gongfeng
description: gongfeng(工蜂)，提供代码仓库管理、分支操作、合并请求、议题管理、代码审查、文件操作、提交查询等, 工蜂域名：https://git.woa.com
homepage: https://git.woa.com/api/mcp/mcp
---

## ⚠️ 重要提醒：必须通过 mcporter-internal 调用

```
✅ 正确方式：mcporter-internal call gongfeng.<工具名>
❌ 错误方式：直接调用 MCP 工具（会失败）
```

**调用示例**：
```bash
# Mac/Linux：优先使用 --args JSON 格式
mcporter-internal call gongfeng.search_projects --args '{"search": "关键词"}'
mcporter-internal call gongfeng.create_branch --args '{"project_id": "xxx", "branch_name": "feature/xxx"}'
# Windows：可用 kv 方式（当参数值为数字形式的 ID/编码时加 --raw-strings 防止被转为 number）
mcporter-internal call gongfeng.search_projects search="关键词" --raw-strings
mcporter-internal call gongfeng.create_branch project_id="xxx" branch_name="feature/xxx" --raw-strings
```

> 💡 不熟悉 mcporter-internal 用法？请参考 `mcporter-internal` skill 文档。若当前环境未加载该 skill，可查阅本目录下的 `DEPENDENCIES.md`。

---


## 通用说明

### 通用参数约定

| 参数 | 说明 |
|------|------|
| `project_id` | 项目ID（数字）或完整路径（如 `tai/gongfeng`） |
| `page` | 分页页码，默认 1 |
| `per_page` | 每页数量，默认 10 |
| 时间格式 | ISO 8601，`+` 需转码为 `%2B`，如 `2019-03-25T00:10:19%2B0800` |

### ID vs IID

- **ID**: 全局唯一标识符
- **IID**: 项目内唯一编号（用户可见）

---

## 可用工具参考

`mcporter-internal list gongfeng` 的输出如下：

```
  /**
   * Create or update a single file in a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param file_path 创建/更新文件的路径
   * @param content 文件内容
   * @param commit_message 提交信息
   * @param branch_name 创建/更新文件的分支
   */
  function create_or_update_file(project_id: string, file_path: string, content: string, commit_message: string, branch_name: string);

  /**
   * Search for Gongfeng projects
   *
   * @param page? 分页页码（默认：1）
   * @param per_page? 每页结果数量（默认：10）
   * @param search? 搜索关键词
   * @param type? 项目类型 GIT 或 SVN
   */
  function search_projects(page?: number, per_page?: number, search?: string, type?: "GIT" | "SVN");

  /**
   * Create a new Gongfeng project
   *
   * @param name 项目名称
   * @param description? 项目描述
   */
  function create_repository(name: string, description?: string);

  /**
   * Create a new issue in a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param title 议题标题
   * @param description? 议题描述
   * @param assignee_users? 分配的用户,多个用户名用英文逗号分隔
   */
  function create_issue(project_id: string, title: string, description?: string, assignee_users?: string);

  /**
   * Create a new merge request in a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param title 合并请求标题
   * @param description? 合并请求描述
   * @param source_branch 包含更改的源分支
   * @param target_branch 目标合并分支
   * @param target_project_id? 目标项目的 id
   * @param tapd_info? tapd 源码提交关键字，例如：--story=864823135 wefwf
   * @param reviewers? 评审人名称 (多个评审人请用英文逗号分隔)
   * @param necessary_reviewers? 必要评审人名称 (多个评审人请用英文逗号分隔)
   * @param labels? 合并请求的标签，多个请用英文逗号分隔
   */
  function create_merge_request(project_id: string, title: string, description?: string, source_branch: string, target_branch: string);
  // optional (5): target_project_id, tapd_info, reviewers, necessary_reviewers, labels

  /**
   * Search merge request in a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param iid? 项目中的MR IID
   * @param source_branch? 包含更改的源分支
   * @param target_branch? 目标合并分支
   * @param page? 分页页码（默认：1）
   * @param per_page? 每页结果数量（默认：8）
   * @param state? 合并请求状态 可选值：merged, opened, reopened 或 closed，不填写返回所有的合并请求
   * @param order_by? 排序字段，允许按 created_at, updated_at resolve_at排序（默认 created_at）
   * @param sort? 排序方式，允许 asc or desc（默认 desc）
   * @param created_after? 此日期及之后创建的 MR；例如 2019-03-25T00:10:19+0000 或
   *                       2019-03-25T00:10:19+0800，时间参数中的[+]必须转码为[%2B]，如“2019-03-25T00:10:19%2B0800”
   */
  function search_merge_request(project_id: string, iid?: number, source_branch?: string, target_branch?: string, page?: number);
  // optional (5): per_page, state, order_by, sort, created_after

  /**
   * Create a new branch in a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param branch_name 新分支名称
   * @param ref? 新分支的源分支/提交
   */
  function create_branch(project_id: string, branch_name: string, ref?: string);

  /**
   * Get notes/comments for a merge request
   *
   * @param project_id Project ID or Project full path
   * @param page? 分页页码（默认：1）
   * @param per_page? 每页结果数量（默认：10）
   * @param merge_request_id 合并请求ID 不是iid
   * @param system? 是否仅需要系统评论（全部评论：null，仅系统评论：true，仅非系统评论：false。默认全部评论）
   * @param sort? 结果排序（按创建时间正序：created_asc，按创建时间逆序：created_desc。默认created_desc）
   * @param resolve_states? 解决状态，0 : "default"（默认）1 : "unresolved"（未解决）2 : "resolved"（已解决）。默认为null全部状态。
   */
  function search_merge_request_notes(project_id: string, page?: number, per_page?: number, merge_request_id: number, system?: boolean);
  // optional (2): sort, resolve_states

  /**
   * Search merge requests by user(assignee_user/author_user/reviewer_user)
   *
   * @param page? 分页页码（默认：1）
   * @param per_page? 每页结果数量（默认：10）
   * @param state? Code state, options: opened, merged, closed, reopened, all, default: opened
   * @param sort? Sorting, options: created_desc, created_asc, updated_desc, updated_asc,
   *              milestone_due_asc, milestone_due_desc, default: created_desc
   * @param assignee_user_name? Assignee username (English name)
   * @param author_user_name? Author username (English name)
   * @param reviewer_user_name? Reviewer username (English name)
   */
  function search_merge_request_by_user(page?: number, per_page?: number, state?: "opened" | "merged" | "closed" | "reopened" | "all", sort?: "created_desc" | "created_asc" | "updated_desc" | "updated_asc" | "milestone_due_asc" | "milestone_due_desc", assignee_user_name?: string);
  // optional (2): author_user_name, reviewer_user_name

  /**
   * Get the raw blob info of a file in a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param sha commit hash 值、分支名或 tag
   * @param file_path 文件路径(文件名)
   * @param start_line? 开始行号
   * @param end_line? 结束行号
   */
  function get_blob_content(project_id: string, sha: string, file_path: string, start_line?: number, end_line?: number);

  /**
   * Get the user info of a Gongfeng project
   *
   * @param user_id 用户ID或用户名
   */
  function get_user_info(user_id: string);

  /**
   * Get the current user info
   */
  function get_current_user();

  /**
   * Get the commit info of a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param commit_sha commit sha
   */
  function get_commit_info(project_id: string, commit_sha: string);

  /**
   * Get the commit list of a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param page? 分页页码（默认：1）
   * @param per_page? 每页结果数量（默认：10）
   * @param ref_name? Branch or tag name (default: default branch)
   * @param path? File path to filter commits
   * @param since? 此日期及之后的提交：例如 2019-03-25T00:10:19+0000 或
   *               2019-03-25T00:10:19+0800，时间参数中的[+]必须转码为[%2B]，如“2019-03-25T00:10:19%2B0800”
   * @param until? 此日期及之前的提交；例如 2019-03-25T00:10:19+0000 或
   *               2019-03-25T00:10:19+0800，时间参数中的[+]必须转码为[%2B]，如“2019-03-25T00:10:19%2B0800” ）
   */
  function get_commits_list(project_id: string, page?: number, per_page?: number, ref_name?: string, path?: string);
  // optional (2): since, until

  /**
   * Search issues in a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param page? 分页页码（默认：1）
   * @param per_page? 每页结果数量（默认：10）
   * @param iid? Issue IID
   * @param state? Issue state
   * @param order_by? Sort field
   * @param sort? Sort order
   * @param created_after? 此日期及之后创建的issue：例如 2019-03-25T00:10:19+0000 或
   *                       2019-03-25T00:10:19+0800，时间参数中的[+]必须转码为[%2B]，如“2019-03-25T00:10:19%2B0800”
   */
  function search_project_issues(project_id: string, page?: number, per_page?: number, iid?: number, state?: "opened" | "closed");
  // optional (3): order_by, sort, created_after

  /**
   * Get the issue detail in a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param issue_iid Issue IID
   */
  function get_issue_detail(project_id: string, issue_iid: number);

  /**
   * Create a note for an issue
   *
   * @param project_id Project ID or Project full path
   * @param issue_id Issue ID不是IID
   * @param note_message 评论内容
   */
  function create_issue_note(project_id: string, issue_id: number, note_message: string);

  /**
   * Create a comment for a merge request
   *
   * @param project_id Project ID or Project full path
   * @param merge_request_id 合并请求ID 不是iid
   * @param body 评论的内容
   * @param path? 文件路径
   * @param line? 行号
   * @param line_type? 变更类型（对代码行评论时必填），可选old、new
   * @param risk? 严重程度 可选值 0、1、2、3。0:"default"（默认）1:"slight"（轻微）2:"normal"（一般）3:"serious"（严重）
   * @param resolve_state? 需解决 可选值 0、1、2。0:"default"（默认）1:"unresolved"（未解决）2:"resolved"（已解决）
   * @param labels? 评审问题分类的标签名称，支持添加多个
   * @param is_person_note? 值为 true 时，该评论记录至 comments tab；false（默认值）时，只进入 conversation tab
   * @param notify_enabled? 默认值为 true，发通知给相关用户
   */
  function create_merge_request_note(project_id: string, merge_request_id: number, body: string, path?: string, line?: number);
  // optional (6): line_type, risk, resolve_state, labels, is_person_note, ...

  /**
   * Reply to a comment on a merge request
   *
   * @param project_id Project ID or Project full path
   * @param merge_request_id 合并请求ID 不是iid
   * @param note_id 需要回复的评论 id
   * @param body 评论的内容
   * @param notify_enabled? 默认值为 true，发通知给相关用户
   */
  function reply_merge_request_note(project_id: string, merge_request_id: number, note_id: number, body: string, notify_enabled?: boolean);

  /**
   * Get the code changes of a merge request, including modified files and their changes. Supports
   * filtering files and returning file list only.
   *
   * @param project_id Project ID or Project full path
   * @param merge_request_id 合并请求ID 不是iid
   * @param diff_file_only? 是否只返回文件列表，true时返回files为文件路径数组
   * @param filter_files? 要筛选的文件路径列表，只返回匹配的文件 文件夹路径需要以/结尾
   */
  function get_merge_request_changes(project_id: string, merge_request_id: number, diff_file_only?: boolean, filter_files?: string[]);

  /**
   * Get the repository tree of a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param page? 分页页码（默认：1）
   * @param per_page? 每页结果数量（默认：10）
   * @param ref_name? commit hash 值、分支或 tag，默认：默认分支
   * @param path? 文件路径
   * @param max_depth? 遍历目录最大深度（-1表示不限制，深度从1开始），默认：1
   */
  function get_repository_tree(project_id: string, page?: number, per_page?: number, ref_name?: string, path?: string);
  // optional (1): max_depth

  /**
   * Get the tapd workitems related to Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param type 类型: mr/cr/issue
   * @param iid MR/CR/ISSUE对应的iid
   */
  function get_tapd_workitems(project_id: string, type: "mr" | "cr" | "issue", iid: number);

  /**
   * Get the tags list of a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param search? 对 tagname 进行模糊搜索
   * @param page? 分页（default：1）
   * @param per_page? 默认页面大小（default：20，max：100）
   * @param order_by? tags 返回列表的排序字段，可选字段:name、updated（默认）按照 committed_date 字段排序
   * @param sort? order_by 的排序顺序 ,可选可选字段:asc、desc(默认)
   */
  function get_tag_list(project_id: string, search?: string, page?: number, per_page?: number, order_by?: "name" | "updated");
  // optional (1): sort

  /**
   * Get the svn repository tree of a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param path 目录路径 查询根目录用/表示
   * @param revision 版本号 如HEAD
   */
  function get_svn_repository_tree(project_id: string, path: string, revision: string);

  /**
   * Batch modify files in a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param branch_name 分支名称
   * @param commit_message 提交信息
   * @param add_files? 新增文件：参数说明 [{"path":"","content":""}]
   * @param edit_files? 修改文件：参数说明 [{"path":"","content":""}]
   * @param delete_path? 删除文件路径数组
   * @param encoding? 文件编码
   */
  function batch_modify_files(project_id: string, branch_name: string, commit_message: string, add_files?: string[], edit_files?: string[]);
  // optional (2): delete_path, encoding

  /**
   * Get the commit diff of a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param sha commit hash 值、分支名或 tag
   * @param path? 文件路径
   */
  function get_commit_diff(project_id: string, sha: string, path?: string);

  /**
   * Get file blame/history information for a single file in a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param file_path 文件的完整路径
   * @param ref? commit hash 值、分支名或 tag
   * @param line_number? 指定行号
   * @param start_line? 起始行号，用于筛选指定行号范围的blame内容
   * @param end_line? 结束行号，用于筛选指定行号范围的blame内容
   */
  function get_file_blame(project_id: string, file_path: string, ref?: string, line_number?: number, start_line?: number);
  // optional (1): end_line

  /**
   * Compare differences between two commits, branches, or tags in a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param from 源提交、分支或标签
   * @param to 目标提交、分支或标签
   * @param path? 文件路径，用于筛选特定文件的差异
   * @param straight? 是否直接比较（不包含合并提交）
   * @param only_count? 仅返回统计信息，不返回 diffs 和 commits 详情
   * @param diff_file_only? 是否只返回文件路径列表，true时files字段返回文件路径字符串数组（默认：false）
   * @param filter_files? 要筛选的文件路径列表，支持目录前缀匹配（如 `src/` 匹配src目录下所有文件）和精确文件路径匹配
   */
  function compare(project_id: string, from: string, to: string, path?: string, straight?: boolean);
  // optional (3): only_count, diff_file_only, filter_files

  /**
   * Get the notes/comments list for an issue in a Gongfeng project
   *
   * @param project_id Project ID or Project full path
   * @param page? 分页页码（默认：1）
   * @param per_page? 每页结果数量（默认：10）
   * @param issue_id 议题 ID不是IID
   */
  function get_issue_notes(project_id: string, page?: number, per_page?: number, issue_id: number);
```

---

## ⚠️ JSON 参数跨平台写法

- **macOS / Linux**：`--args "{\"key\": \"value\"}"`
- **Windows PowerShell**：`--args '{\"key\": \"value\"}'`（单引号包裹，内部双引号仍需 `\` 转义）
- **Windows PowerShell 禁止**用双引号包裹 JSON（如 `--args "{"key":"value"}"`），否则内部双引号会被 PowerShell 自身吞掉，导致报错 `Unable to parse --args: Expected property name or '}' in JSON at position 1`
- 详见 `mcporter-internal` skill 文档