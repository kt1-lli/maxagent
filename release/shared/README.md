# MaxAgent

3ds Max 内嵌的 AI 助手，支持本地模型（Ollama / LM Studio）与云端 API
（OpenAI / DeepSeek / 兼容协议），通过 Function Calling 操作 Max 场景。

## 安装

1. 下载 `maxagent-X.Y.Z.mzp`
2. 把文件**拖进 3ds Max 视口**（任意视口均可）
3. 弹窗提示"安装成功"后即可使用
4. 后续启动方式（任选其一）：
   - `Customize > Customize User Interface...`，Category 选 **MaxAgent**，添加 `MaxAgent_Show` 到工具栏
   - 直接 MaxScript Listener 运行：`macros.run "MaxAgent" "MaxAgent_Show"`

## 兼容性

| 3ds Max 版本 | Python | 支持状态 |
|--------------|--------|----------|
| 2022 / 2022.1 ~ 2022.3 | 3.7 | ✅ |
| 2023 | 3.9 | ✅ |
| 2024 | 3.10 | ✅ |
| 2025 / 2025.1 / 2026 | 3.11 | ✅ |
| 2027 | 3.13 | ✅ |

mzp 安装时会自动选择匹配版本，**用户无需关心 Python 版本**。

## 卸载

`Customize > Customize User Interface...` 找到 `MaxAgent_Uninstall` 宏点击即可。
卸载只删除程序文件，**保留 `_userdata/` 用户数据**（含 API key 与对话历史）。

如需彻底清理，手动删除：

```
%LOCALAPPDATA%\Autodesk\3dsMax\<版本>\<语言>\scripts\_userdata\
```

## 隐私与安全

- API key 仅存储在本地 `_userdata/config.json`
- 不向任何第三方收集使用数据
- 详见 [LICENSE](LICENSE)
