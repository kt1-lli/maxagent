# knowledge/data/ 说明

本目录存放随 mzp 打包发布的**内置知识库文档**。

## 替换 Max Python Help

`max_python_help.md` 目前是占位内容。用户需要：

1. 从 Autodesk 官方获取 `Max-Python-Help_YYYY.md`（如 `Max-Python-Help_2023.md`）
2. 直接覆盖本目录下的 `max_python_help.md`（**保留文件名**）
3. 下次启动 Max，`MaxHelpSource` 会检测到 mtime/size 变化并自动重建 BM25 索引

## 索引持久化位置

索引文件不放在这里（不能污染 mzp 只读区），存于用户配置目录：

```
{HOME}/.maxagent/knowledge/maxhelp.idx.json    # BM25 倒排 + 元数据
{HOME}/.maxagent/knowledge/maxhelp.meta.json   # 上次构建时的源指纹
```

删除这两个文件会触发下次启动重建。

## 支持的文件格式

- `.md` / `.markdown`：按 heading 分段切块
- `.txt` / `.text`：按空行分段切块

其它格式（PDF 等）本批次不支持——如需接入，请先在打包机上转 md。
