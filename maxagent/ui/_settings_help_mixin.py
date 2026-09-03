#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SettingsDialog 的「帮助」子域 mixin。

从 ``settings_dialog.py`` 抽出的第 Page 5 页面：静态 QTextBrowser
渲染 HTML 帮助内容，并附带一个 ``_jump_to_help_tab`` 快捷跳转方法。

该 mixin 依赖主类初始化后暴露的：
- ``self._NAV_ITEMS``（左侧导航条目列表，含 ``('help', ...)``）
- ``self.nav``（QListWidget 导航控件）

除此之外无其它耦合，可通过多继承直接混入 ``SettingsDialog``。
抽取仅按功能拆分文件，行为与原实现完全一致。
"""

from __future__ import absolute_import
from __future__ import print_function

from ..qt_compat import QtWidgets
from .emoji_compat import ee as _ee


def _current_dcc_name():
    """返回当前 DCC 的显示名（Maya 或 3ds Max）。"""
    try:
        from ..dcc.runtime import current_dcc
        dcc = current_dcc()
        if dcc == 'maya':
            return 'Maya'
        if dcc == '3dsmax':
            return '3ds Max'
        return dcc
    except Exception:  # pylint: disable=broad-except
        return '3ds Max'


class _SettingsHelpMixin(object):
    """Page 5: 帮助（静态 HTML 说明 + 跳转入口）。"""

    def _build_page_help(self):
        # type: () -> QtWidgets.QWidget
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        title = QtWidgets.QLabel(_ee('❓') + '  使用帮助')
        title.setStyleSheet('font-size:16px; font-weight:bold;')
        layout.addWidget(title)

        text = QtWidgets.QTextBrowser()
        text.setOpenExternalLinks(True)
        # 直接给 QTextBrowser 设置高对比度的暗色调色板，避免依赖
        # 系统主题——某些 Max 主题下默认正文偏灰，<code> 无背景，
        # 阅读吃力。这里统一固定背景 + 高亮文字色。
        text.setStyleSheet(
            'QTextBrowser {'
            ' background:#1e1e1e;'
            ' color:#e8e8e8;'
            ' border:1px solid #3a3a3a;'
            ' padding:8px;'
            ' font-size:10pt;'
            ' line-height:160%;'
            '}'
        )
        text.setHtml(self._help_html())
        layout.addWidget(text, 1)
        return page

    @staticmethod
    def _help_html():
        # 颜色规范（与界面整体暗色主题对齐，确保 ≥ AA 级对比度）：
        #   正文       #e8e8e8（浅灰，对比 #1e1e1e ≈ 12:1）
        #   小标题     #ffd166（暖黄，吸引眼球）
        #   代码片段   背景 #2a2a2a + 文字 #ffe082
        #   强调      #a8e6a8（浅绿）
        #   警示      #ff9090（浅红）
        dcc_name = _current_dcc_name()
        dcc_mcp_name = 'Maya' if dcc_name == 'Maya' else '3dsMax'
        example_action = (
            '创建 5 个多边形立方体沿 X 排列'
            if dcc_name == 'Maya' else '创建 5 个 Box 沿 X 排列'
        )
        return (
            '<style>'
            'body { color:#e8e8e8; }'
            'h3 { color:#ffd166; margin:6px 0 4px 0; }'
            'h4 { color:#ffd166; margin:10px 0 4px 0;'
            '     border-left:3px solid #ffd166; padding-left:6px; }'
            'p { color:#e8e8e8; line-height:160%; }'
            'b { color:#ffffff; }'
            'code { background:#2a2a2a; color:#ffe082;'
            '       padding:1px 4px; border-radius:2px; }'
            '.tip { color:#a8e6a8; }'
            '.warn { color:#ff9090; }'
            'hr { border:0; border-top:1px solid #3a3a3a; margin:10px 0; }'
            'table { border-collapse:collapse; margin:4px 0; }'
            'td { padding:3px 8px; border:1px solid #3a3a3a;'
            '     color:#e8e8e8; }'
            'th { padding:3px 8px; border:1px solid #3a3a3a;'
            '     background:#2a2a2a; color:#ffd166; text-align:left; }'
            '</style>'
            '<h3>MaxAgent 设置帮助</h3>'

            '<h4>模型 Tab</h4>'
            '<p>管理多套大模型连接（Ollama / LM Studio / OpenAI / '
            'DeepSeek 等），右键 Profile 可<b>重命名 / 复制 / 设为默认</b>。</p>'

            '<p><b>Base URL</b>：OpenAI 兼容 API 的根地址，多数服务'
            '需要带 <code>/v1</code> 后缀（DeepSeek 官方推荐使用根域名）。'
            '<br>· Ollama：<code>http://localhost:11434/v1</code>'
            '<br>· LM Studio：<code>http://localhost:1234/v1</code>'
            '<br>· OpenAI：<code>https://api.openai.com/v1</code>'
            '<br>· DeepSeek：<code>https://api.deepseek.com</code>'
            '（推荐 <code>deepseek-v4-flash</code> / '
            '<code>deepseek-v4-pro</code>，旧模型 '
            '<code>deepseek-chat</code> / <code>deepseek-reasoner</code> '
            '将于 <span class="warn">2026/07/24</span> 弃用）</p>'

            '<p><b>API Key</b>：本地模型可留空或填占位符；商用 API 必填。</p>'
            '<p><b>模型</b>：模型名称需与服务端实际可用模型完全一致。</p>'
            '<p><b>温度</b>：0.0~2.0，越高越发散；建议 <b>0.2 ~ 0.7</b>。</p>'
            '<p><b>请求超时</b>：单次请求等待秒数，长上下文/慢模型可调大。</p>'
            '<p><b>工具调用上限</b>：单轮对话内 LLM 可触发的工具调用次数上限，'
            '防止无限循环。</p>'
            '<p><b>历史 token 预算</b>：发送给 LLM 时携带的对话历史 token 上限，'
            '超出会自动裁剪最早消息。</p>'

            # ---- 自定义 Header ----
            '<h4>自定义 Header（高级）</h4>'
            '<p>在 LLM HTTP 请求中附加自定义请求头，常用于：'
            '<b>企业网关追踪</b> / <b>Beta 通道开关</b> / <b>第三方代理鉴权</b>。'
            '<br><span class="tip">DeepSeek 直连官方 API 通常无需填写</span>。</p>'

            '<p><b>格式</b>：每行一对 <code>KEY=VALUE</code>，'
            '半角等号分隔；空行 / 不含 <code>=</code> 的行会被忽略，'
            '不支持冒号 / YAML / JSON。</p>'

            '<p><b>DeepSeek 场景示例</b>：</p>'
            '<table>'
            '<tr><th>场景</th><th>填写内容</th></tr>'
            '<tr><td>直连官方 API</td>'
            '<td><span class="tip">留空即可</span>'
            '（已自动处理 Bearer 鉴权）</td></tr>'
            '<tr><td>走企业网关</td>'
            '<td><code>X-Org-Id=team-rendering</code><br>'
            '<code>X-Project=maxagent</code></td></tr>'
            '<tr><td>调试追踪</td>'
            '<td><code>X-Trace-Id=maxagent-debug</code></td></tr>'
            '<tr><td>第三方 OpenAI 兼容网关</td>'
            '<td><code>X-API-Token=xxxxxxxxxx</code><br>'
            '<code>X-Tenant-Id=cg-team</code></td></tr>'
            '</table>'

            '<p class="warn"><b>⚠ 注意</b>：自定义 Header 优先级高于默认值，'
            '<b>请勿覆盖</b> <code>Authorization</code> / '
            '<code>Content-Type</code>，否则会破坏鉴权或被服务端拒收。</p>'

            '<hr>'

            # ---- 视觉 / 图片 ----
            '<h4>🖼️ 图片与视觉</h4>'

            '<p><b>① 插入图片</b>（4 种方式任选）：'
            '<br>· 📎 工具栏「选图」按钮 → 文件对话框'
            '<br>· ✂️ 截图工具 → 自动入栏'
            '<br>· <code>Ctrl+V</code> 直接粘贴剪贴板图片'
            '<br>· 直接拖拽图片文件 / 网页缩略图到输入框</p>'

            '<p><b>② 多模态识别 = 三道开关同时打开</b>：'
            '<br>· <b>应用设置 → 启用图片视觉</b>（全局总开关）'
            '<br>· <b>当前 Profile 模型</b>命中下方"视觉模型白名单"任一子串'
            '<br>· <b>模型本身确实支持</b> OpenAI <code>image_url</code> 协议'
            '<br>三者全开 → 图片以原图发送给 LLM；任一不满足 → 自动降级为'
            '<code>[图片] N 张</code> 文本占位，输入框上方显示黄色提示条，'
            '可一键切换到支持视觉的 Profile。</p>'

            '<p><b>③ 视觉模型白名单</b>（应用设置页可编辑）：'
            '<br>· 每行一个<b>子串</b>，子串匹配且不区分大小写'
            '<br>· 内置覆盖 <code>gpt-4o</code> / <code>claude-3+</code>'
            ' / <code>gemini-1.5+</code> / <code>qwen-vl</code> /'
            ' <code>glm-4v</code> / <code>internvl</code> /'
            ' <code>youtu-vita</code> 等主流模型'
            '<br>· 新机型上线？直接添加一行子串即可识别（如新品牌的'
            ' <code>llava-next</code>）'
            '<br>· 修改即时生效，无需重启；一键「🔄 恢复默认」可回到出厂值</p>'

            '<p><b>④ 气泡里的图片操作</b>：'
            '<br>· 用户气泡：右键 → 复制图片 / 复制路径 / 另存为 / 查看大图'
            '<br>· 助手气泡：图片可点击放大查看'
            '<br>· 方便粘贴到 Word / 微信 / PS 工作流</p>'

            '<p><b>⑤ 测试连接对视觉模型的特殊行为</b>：'
            '<br>· 检测到当前 Profile 模型命中视觉白名单时，'
            '「🔌 测试连接」会<b>自动附带一张 8×8 灰色占位 PNG</b>，'
            '让 vita / claude vision 等"必须含 image_url"的网关也能握手成功'
            '<br>· 「✅ 完整测试」对视觉模型会<b>跳过 tools 字段</b>，'
            '避开 tokenhub 系网关对 tools 敏感导致 400 invalid_params 的坑'
            '<br>· 状态文案带<b>"（视觉）"</b>标识，方便区分进入了哪条路径</p>'

            '<hr>'

            # ---- 对话面板使用技巧 ----
            '<h4>对话面板使用技巧</h4>'
            '<p><b>调整对话区/输入区比例</b>：拖动两区之间的横向分隔条。'
            '<br>· <b>向下拖</b>（输入区收缩）：聊天区变大，原可见消息保持可见。'
            '<br>· <b>向上拖</b>（输入区扩张，方便编辑长 prompt）：'
            '如果你拖动前正<b>停在底部</b>看最新消息，'
            '面板会自动滚回底部，<b>不会</b>把最新消息挤出可见区；'
            '如果你正在<b>翻历史</b>，则保持原位置不打扰阅读。</p>'
            '<p><b>气泡操作</b>：'
            '<br>· 用户气泡里的图片右键 → 复制 / 路径 / 另存 / 查看大图'
            '<br>· 助手气泡的工具调用块可<b>展开/折叠</b>查看入参出参'
            '<br>· 顶部「🗜 压缩」按钮：让 LLM 总结早期对话替换为摘要，'
            '保留最近 2 轮，长对话省 token。</p>'
            '<p><b>智能克制</b>：本助手已内置「字面理解铁律」——'
            '你说"创建一个球"就只创建球，<b>不会</b>顺手补灯/相机/材质；'
            '需要完整场景请明确说"完整场景"、"打光"、"加摄像机"等关键词。'
            '工具完成后会立即给出确认，避免越做越多失控。</p>'

            '<hr>'

            # ---- IDE 接口 / Bridge ----
            '<h4>IDE 接口（Bridge）🔌</h4>'
            '<p>在 {dcc_name} 内开启一个本地 TCP 端口，让外部 IDE'
            '（Cursor / Claude Desktop / Cline 等）通过 '
            '<a href="https://gitee.com/cmqll/dcc-mcp" '
            'style="color:#4da6ff;">dcc-mcp</a> 连接到 maxagent，'
            '形成 <b>IDE Agent ↔ maxagent Agent</b> 协作。</p>'

            '<p><b>两种调用方式</b>：</p>'
            '<table>'
            '<tr><th>工具</th><th>谁出大脑</th><th>典型场景</th></tr>'
            '<tr><td><code>execute_python</code></td>'
            '<td>IDE LLM 写代码</td>'
            '<td>"{example_action}"等明确代码动作</td></tr>'
            '<tr><td><code>dispatch_task</code></td>'
            '<td>maxagent 自跑</td>'
            '<td>"测我刚写的工具"等需要规划+执行的任务</td></tr>'
            '</table>'

            '<p><b>快速接入</b>：</p>'
            '<p>① 打开 <b>IDE 接口</b> Tab → 勾选「启用 IDE Bridge」'
            '<br>② 点「<b>复制 dcc-mcp / Cursor 配置示例</b>」按钮'
            '<br>③ 粘贴到 IDE 的 MCP 配置文件（如 '
            '<code>~/.cursor/mcp.json</code>），重启 IDE 即可。</p>'

            '<p><b>关键设置</b>：</p>'
            '<table>'
            '<tr><th>字段</th><th>默认</th><th>说明</th></tr>'
            '<tr><td>监听端口</td><td>7003</td>'
            '<td>与 dcc-mcp {dcc_mcp_name} 预设一致；改端口需同步 mcp.json</td></tr>'
            '<tr><td>访问令牌</td><td>空</td>'
            '<td>多人共用机器担心误连时填；本机回环用通常无需</td></tr>'
            '<tr><td>允许任务派发</td><td>开</td>'
            '<td>关闭后只暴露 execute_python（IDE 自己写代码）</td></tr>'
            '<tr><td>最大轮数</td><td>20</td>'
            '<td>dispatch_task 内 LLM↔工具循环上限，防死循环</td></tr>'
            '<tr><td>超时</td><td>300s</td>'
            '<td>dispatch_task 单任务总超时</td></tr>'
            '</table>'

            '<p class="warn"><b>⚠ 安全</b>：仅监听 <code>127.0.0.1</code>，'
            '外网不可达；<b>不要</b>手动改成 <code>0.0.0.0</code>。'
            'execute_python 完全开放，仅限本机使用。</p>'
            '<p class="tip">完整指南见 '
            '<code>maxagent/docs/IDE_MCP_USAGE.md</code></p>'

            '<hr>'

            # ---- 共享资源目录 ----
            '<h4>共享资源目录 🧰</h4>'
            '<p>把团队共享的 <b>技能 / 用户工具 / 规则 / 反思 / 知识源</b> 放到一个'
            '只读目录，通过 Git 同步后 MaxAgent 会自动挂载。共享资源对当前实例'
            '<b>只读</b>，不会污染你的本地资源。</p>'

            '<p><b>配置方式</b>：'
            '<br>· 在"共享资源"Tab 点击「浏览…」选择目录；或启动 {dcc_name} 前设置环境变量 '
            '<code>MAXAGENT_SHARED_DIR</code>，两者会互相覆盖（以环境变量为优先）。'
            '<br>· 目录结构与本地 <code>{config_dir}</code> 一致，只需保持子目录名 '
            '<code>skills / user_tools / user_rules / reflections / knowledge</code>。'
            '</p>'

            '<p><b>同名资产冲突</b>：当本地与共享目录存在同名资源时，会按策略处理。'
            '默认策略为<b>"使用共享"</b>，也可以在设置页切换为：使用本地、保留两者、'
            '用共享覆盖本地。首次遇到冲突时会弹出对话框让你逐条确认；确认后的决策会'
            '被记住，下次启动直接应用。</p>'

            '<p><b>典型工作流</b>：'
            '<br>① 团队 TA 或 TD 把写好的 Skill / 工具提交到 Git 仓库\n'
            '② 美术在设置页点击「克隆仓库」输入 URL，或 pull 到本地共享目录后点击「浏览…」\n'
            '③ 日常使用点击「拉取最新」：先 <code>git fetch</code> 检测更新，'
            '发现新提交后列出摘要，确认后再 <code>git pull --ff-only</code>\n'
            '④ 重启 {dcc_name} 后这些资源会自动出现在 LLM 的工具列表和技能列表中\n'
            '⑤ 共享工具首次被调用前会经过语法检查，执行时与本地工具一样受脚本确认开关约束'
            '</p>'

            '<p class="warn"><b>⚠ 注意</b>：</p>'
            '<p>· 共享目录对当前实例<b>只读</b>：不能在里面创建、修改、删除资源，'
            '这些操作必须回到本地资源或到共享仓库源端进行。'
            '<br>· 共享的 <b>user_tool</b> 会自动加上 <code>shared_</code> 前缀，'
            '避免和本地工具同名；Skill、规则、反思、知识源则按原名加载。'
            '<br>· 共享 user_tool 在首次调用前会做语法检查，建议团队内部在入库前'
            '先在本机本地资源中验证通过。'
            '<br>· 如果共享目录存在未提交改动，「拉取最新」会被阻止，需先在 Git 客户端处理。'
            '<br>· 如果把共享目录设为自己的本地 <code>config_dir</code>，'
'所有写操作都会被拒绝，请确保该目录是独立的共享资源目录而不是个人配置目录。</p>'

            '<hr>'

            # ---- 我的资源：规则 / 技能 / 工具 / 导入导出 ----
            '<h4>我的资源 📦</h4>'
            '<p>这一个 Tab 集中管理你为 AI 准备的 <b>规则 / 技能 / 工具</b>，'
            '内部用横向子 Tab 切换 4 个视图：</p>'
            '<table>'
            '<tr><th>子 Tab</th><th>用途</th></tr>'
            '<tr><td><b>规则</b></td>'
            '<td>从对话中沉淀的 LLM 行为规则；查看 / 启停 / 删除</td></tr>'
            '<tr><td><b>技能</b></td>'
            '<td>触发关键词命中时注入的流程模板；查看 / 启停 / 删除</td></tr>'
            '<tr><td><b>工具</b></td>'
            '<td>对话中"学习"出来的可执行 Python 工具；'
            '查看源码 / 启停 / 删除</td></tr>'
            '<tr><td><b>导入/导出</b></td>'
            '<td>三类资源<b>统一打包</b>为 <code>.maxagent-pack</code>'
            '，跨设备同步 / 团队分享</td></tr>'
            '</table>'
            '<p>三个管理子页底部按钮已对齐，统一为'
            '<b>查看 / 启用-禁用 / 删除 / 刷新</b>四颗按钮，'
            '所有导入导出能力都收敛到「导入/导出」子 Tab，避免分散。</p>'
            '<p><b>启用 / 禁用语义（关键）</b>：勾选项即"启用"，'
            '取消勾选即"禁用"——也可选中后点底部'
            '<b>启用/禁用</b>按钮翻转。'
            '<b>禁用后 LLM 完全感知不到该资源</b>：'
            '工具不会出现在 schema、技能不会出现在简介或被关键词触发、'
            '规则不会进入 system prompt。但磁盘文件保留，'
            '随时可重新启用。禁用名单存于 '
            '<code>{config_dir}/disabled.json</code>，'
            '删除该文件即可一键恢复全部。</p>'

            '<p><b>跨设备同步 / 团队分享</b>：</p>'
            '<p>① 打开「我的资源 → 导入/导出」子 Tab'
            '<br>② 每栏顶部独立的「<b>全选</b>」复选框可一键勾上该栏所有项；'
            '也可手动勾选个别项目<br>'
            '③ 填写包名 / 作者 / 描述（可选） → 点「<b>导出选中…</b>」'
            '<br>④ 对方点「<b>导入资源包…</b>」→ 在预览对话框勾选要采纳的项目'
            '<br>⑤ 同名冲突时勾选「覆盖」可强制更新，不勾默认跳过</p>'

            '<p class="warn"><b>⚠ 安全提示</b>：</p>'
            '<p>· 自定义工具是<b>可执行 Python 代码</b>，会在 {dcc_name} 内运行——'
            '只导入<b>信任来源</b>的资源包；'
            '<br>· 包<b>不</b>含 API Key / Profile 配置 / 会话历史，避免敏感信息泄露；'
            '<br>· 包<b>不</b>含启用/禁用状态——导入到对方机器后默认全部启用，'
            '让对方自行决定要不要某项；'
            '<br>· 导入对话框对工具会强制二次确认，并按 '
            '<code>new / existing / invalid</code> 颜色标注每条状态。</p>'

            '<hr>'

            # ---- 日志 / 测试 ----
            '<h4>日志与诊断</h4>'
            '<p><b>日志 Tab</b>：三态切换 <b>关闭 / 开启 / DEBUG</b>。'
            'DEBUG 级别下会全链路打印 LLM 请求 / 工具调用 / 截图 / '
            '附件操作 / 线程切换 / UI 信号延迟 / Bridge 连接与方法分发，'
            '方便排查偶发问题。'
            '日志只写文件不进控制台，路径见日志页底部。</p>'
            '<p><b>测试连接</b>：仅 ping，验证 base_url + key 基本可达。'
            '<br><b>完整测试</b>：复刻真实对话请求'
            '（流式 + 全部工具 schema + <b>真实 system prompt</b>），'
            '用于排查"测试连接通过但实际对话失败"类问题。'
            '<br>失败时错误信息可<b>鼠标选中复制</b>（含 HTTP code / body / '
            'request-id / 关键 headers），方便排查"测试通过、对话失败"差异。</p>'

            '<p><b>🔄 恢复默认</b>：把当前 Profile 字段一键重置为 OpenAI '
            '兼容出厂模板。'
            '<br>· 名称 / 模型 → 留空，请你重填'
            '<br>· Base URL → <code>https://api.openai.com/v1</code>'
            '（与 DeepSeek / Moonshot / 智谱 / 自建 vllm 等绝大多数 OpenAI '
            '兼容网关开箱可用）'
            '<br>· API Key → <b>保留不变</b>（避免误清密钥）'
            '<br>· 其他参数（温度 / token / 超时 / 工具上限 / 流式 / Function '
            'Calling / 自定义 Header）→ 全部回到默认值'
            '<br>注：仅修改表单显示，需点击「应用」才会写盘——避免误把'
            '名称为空的 Profile 强行落盘破坏配置。</p>'
        ).replace(
            '{dcc_name}', dcc_name
        ).replace(
            '{dcc_mcp_name}', dcc_mcp_name
        ).replace(
            '{example_action}', example_action
        )

    def _jump_to_help_tab(self):
        """快速切到"帮助"Tab。"""
        for i, (_label, key) in enumerate(self._NAV_ITEMS):
            if key == 'help':
                self.nav.setCurrentRow(i)
                return
