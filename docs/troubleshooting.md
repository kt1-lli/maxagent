# MaxAgent 故障排查

> 按问题类型分组，每个问题都给出**症状 → 原因 → 解决**三段式答案。

---

## 🚨 致命问题（先看这些）

### Max 启动后白屏 / 卡住几分钟

**症状**：装完 MaxAgent 后启动 Max，主界面长时间不响应。

**最可能原因**：startup 引导脚本里加载了某个有问题的模块。

**应急处理**：
1. **强制关闭 Max**（任务管理器）
2. 进入 `%LOCALAPPDATA%\Autodesk\3dsMax\<ver> - 64bit\<lang>\scripts\startup\`（中文版 `<lang>` 为 `zh-CN`，英文版为 `ENU`）
3. 把 `maxagent_startup.py` 重命名为 `maxagent_startup.py.bak`
4. 重新启动 Max（这次不会加载 MaxAgent）
5. 把 `maxagent_startup.py.bak` 内容复制出来贴到这里反馈

### Max 在我点"发送"那一刻直接闪退

**症状**：UI 看着正常，一发消息 Max 直接 crash。

**原因**：99% 是 **pymxs 在子线程被调用了**。

**解决**：
- 检查你是否魔改过 `dispatcher.py`，确保所有 `pymxs.runtime.*` 调用都在主线程
- 检查是否有自定义工具直接 `import threading` 起线程跑 pymxs
- 用 Process Monitor 抓 `3dsmax.exe` 退出前最后的 stack trace 上报

---

## 🌐 LLM 连不上

### 401 Unauthorized

**原因**：API Key 错误或空。

**解决**：
1. 设置 → 选中对应 Profile → 点 👁 显示 Key → 检查是否完整
2. 本地模型（Ollama / LM Studio）API Key 不能空——填占位符如 `ollama`
3. DeepSeek / OpenAI key 形如 `sk-xxx...`，不要带空格或换行

### Connection refused (本地模型)

**原因**：Ollama / LM Studio 没启动或端口不对。

**解决**：
```powershell
# Ollama 默认端口 11434
curl http://localhost:11434/api/tags

# LM Studio 默认端口 1234（在它的 UI 里启动 Local Server）
curl http://localhost:1234/v1/models
```
能打通就说明服务在跑，再去 MaxAgent 里 **测试连接**。

### Timeout / 超时

**原因**：
- 本地模型推理太慢（特别是 7B 以下小模型 + 复杂工具调用）
- 网络代理问题（公司内网走代理才能连 OpenAI）

**解决**：
- 本地模型：设置里把「请求超时」从 120s 调到 300s+
- OpenAI：在 Profile → 自定义 Header 里加：
  ```
  # 不行，因为 stdlib urllib 不直接支持 proxy header
  ```
  ↑ 不行。直接用环境变量更稳：
  ```powershell
  $env:HTTPS_PROXY = "http://proxy.company.com:8080"
  ```
  然后重启 Max（环境变量在进程启动时生效）。

### SSL: CERTIFICATE_VERIFY_FAILED

**原因**：公司根证书没装到系统信任链。

**解决**（不推荐，但应急可用）：
在 `llm_client.py` 顶部临时加：
```python
import ssl
ssl._create_default_https_context = ssl._create_unverified_context
```
**生产环境建议**：找 IT 拿公司根证书装到 Windows 证书存储。

---

## 🤖 LLM 行为问题

### 模型只回答文字，不调用工具

**原因**：
1. 模型不支持 Function Calling（如纯 chat 的 `llama3:8b`）
2. Profile 里「启用 Function Calling」没勾选
3. 模型上下文太长被截断了 tools 描述

**解决**：
- 换支持 tools 的模型：`qwen2.5:14b+` / `gpt-4o-mini` / `deepseek-chat`
- 设置里检查 `supports_tools` 是 ON
- 长对话先 🗑 清空再开新一轮

### 模型反复调用同一个工具死循环

**症状**：日志里看到同一个工具调了 16 次后被强制中止。

**原因**：模型把工具的报错信息当成"重试一下应该就好"，但参数错误是模型自己造成的。

**解决**：
- 直接告诉它："不要重试，先回答我具体哪里错了"
- 或者增大 `MAX_TOOL_LOOPS`（在 `agent/worker.py` 顶部）治标
- 治本：换更聪明的模型（DeepSeek-Chat / GPT-4o）

### 工具执行成功，但模型说"失败了"

**原因**：模型没看懂返回 JSON。

**解决**：检查 `dispatcher.py` 返回的 `result` 字段是不是包含足够清晰的成功标识。
（理论上不应该出现，出现了请提 issue 附完整对话）

---

## 🎨 UI 问题

### 面板布局错乱 / 字看不清

**原因**：高 DPI 缩放问题。

**解决**：
- Max 2024+ 自带高 DPI 支持，应该没事
- 老版本 Max：右键 `3dsmax.exe` → 兼容性 → 替代高 DPI 缩放为「应用程序」

### 中文乱码

**原因**：Max 老版本 Python 默认编码不是 UTF-8。

**解决**：MaxAgent 已经在所有 `open()` 里强制 `encoding='utf-8'`，
如果还乱码请检查你的字体是否支持中文。

### 流式输出卡顿

**症状**：回复要么不出，要么一下子全出来。

**原因**：模型不支持流式，或代理把 SSE 流缓冲了。

**解决**：
- 设置里关闭「启用流式输出」（fallback 到非流式）
- 检查是不是公司代理 buffer 了流

---

## 🛠️ 工具调用错误

### `tool 'xxx' not found`

**原因**：工具未注册。

**解决**：
1. 检查 `tools/__init__.py` 的 `load_all_tools()` 是否被调用
2. 在 Python Listener 里手动验证：
   ```python
   from maxagent.tools import load_all_tools, list_tools
   load_all_tools()
   print(len(list_tools()))  # 应该是 51
   ```

### `pymxs has no attribute 'runtime'` 或 `cannot import pymxs`

**原因**：在 Max 之外（独立 Python）跑了需要 Max 的工具。

**解决**：MaxAgent 设计上**只在 Max 里运行**。如果你想在外部用，可以：
- mock 掉 pymxs 跑单元测试
- 用 `3dsmaxcmd.exe` 无头模式（不在本工具支持范围）

### 工具参数验证失败 `'radius' is not of type 'number'`

**原因**：模型给的是字符串 `"10"` 不是数字 `10`。

**解决**：通常重试一次 LLM 自己就修正了。如果总是这样：
- 在 system prompt 里明确："工具参数必须是正确的 JSON 类型"
- 把模型温度调低（0.1~0.3）

---

## 🔐 安全相关

### 不想让 AI 执行 `run_maxscript` / `run_python`

**解决**（任选其一）：
1. 在 `tools/__init__.py`：
   ```python
   load_all_tools(include_escape_hatch=False)
   ```
2. 改 `tools/escape_hatch.py` 顶部：
   ```python
   REQUIRE_CONFIRM = True   # 执行前弹窗
   ```

### API Key 怎么加密存储？

当前 base64 是**编码不是加密**。如果对密钥安全敏感，方案：

```python
# 集成 Windows DPAPI（推荐）
import win32crypt  # pywin32

def _encrypt(text):
    return win32crypt.CryptProtectData(
        text.encode('utf-8'), 'maxagent', None, None, None, 0,
    )

def _decrypt(blob):
    return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode()
```
改 `config.py` 的 `_encode_key` / `_decode_key` 即可。

---

## 📞 还是搞不定？

按以下信息收集后提 issue：

1. Max 版本（`Help → About 3ds Max`）
2. Python 版本（在 Python Listener: `import sys; print(sys.version)`）
3. PySide 版本：
   ```python
   try:
       from PySide6 import __version__; print('PySide6', __version__)
   except ImportError:
       from PySide2 import __version__; print('PySide2', __version__)
   ```
4. MaxAgent 版本（`import maxagent; print(maxagent.__version__)`）
5. 完整错误 stack（MAXScript Listener 最下方那段）
6. LLM Profile 类型（敏感字段打码）
