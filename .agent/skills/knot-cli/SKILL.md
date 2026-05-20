---
name: knot-cli
description: 通过 knot-cli 命令行与 Knot 平台智能体对话。支持直接提问（chat -p）和指定智能体（--agentId）两种模式，可按需切换模型。适用于日常问答、代码分析、自动化脚本，新建智能体对话。
---

# Knot CLI Skill

## 1. 普通对话（直接 chat -p 模式）

无需指定智能体，直接使用 `-p` 参数发送问题：

```bash
knot-cli chat -p "你的问题"
```

**示例：**

```bash
# 最简单的对话
knot-cli chat -p "帮我分析这个项目的代码结构"

# 指定工作区
knot-cli chat -w /path/to/project -p "帮我分析这个项目的代码结构"

```

---

## 2. 指定智能体对话

### 第一步：获取智能体 ID

```bash
knot-cli list-agents
```

该命令会列出当前可用的智能体及其 ID。

### 第二步：指定智能体发起对话

使用 `--agentId`（或 `-a`）参数指定智能体 ID：

```bash
knot-cli chat -a <agent_id> -p "你的问题"
```

**示例：**

```bash
# 指定智能体对话
knot-cli chat -a abc123 -p "帮我做代码审查"
```

---

## 其他：指定模型

使用 `--model`（或 `-m`）参数指定对话模型：

```bash
knot-cli chat -p "你的问题" --model "deepseek-v3.1"
```

**可用模型：**

| 模型名称 | 说明 |
|----------|------|
| `deepseek-v3.2` | DeepSeek V3.2 |
| `glm-4.7` | GLM-4.7 |
| `Claude-4.6-Sonnet` | Claude-4.6-Sonnet |

**示例：**

```bash
# 使用 deepseek-v3.2 模型
knot-cli chat -p "帮我优化这段代码" --model "deepseek-v3.2"

# 指定智能体 + 指定模型
knot-cli chat -a abc123 -p "帮我做代码审查" --model "glm-4.7"
```

---

## 其他：异步调用（nohup 后台运行）

若不需要等待结果，可使用 `nohup` 将 knot-cli 放到后台执行，输出重定向到文件，稍后再读取结果。

**基本用法：**

```bash
# 后台执行，输出写入 output.log
nohup knot-cli chat -p "你的问题" > output.log 2>&1 &

# 记录后台进程 PID，方便后续追踪
echo $! > knot.pid
```

**等待完成后读取结果：**

```bash
# 等待后台进程结束
wait $(cat knot.pid)

# 读取输出结果
cat output.log
```

**完整示例（脚本中使用）：**

```bash
#!/bin/bash

# 发起异步对话
nohup knot-cli chat -p "帮我分析这段日志中的异常" > /tmp/knot_result.log 2>&1 &
KNOT_PID=$!

# 执行其他任务...
echo "knot-cli 正在后台运行（PID: $KNOT_PID），继续执行其他任务..."

# 等待 knot-cli 完成
wait $KNOT_PID

# 输出结果
echo "=== knot-cli 返回结果 ==="
cat /tmp/knot_result.log
```

**指定智能体的异步调用：**

```bash
nohup knot-cli chat -a abc123 -p "帮我做代码审查" > /tmp/review.log 2>&1 &
wait $!
cat /tmp/review.log
```

---

## 附录：安装 knot-cli

如果没有安装 `knot-cli`，请引导用户自行安装，禁止自动安装!!!
参考文档：https://iwiki.woa.com/p/4016921090
