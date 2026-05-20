// ----------------------------------------------------------------------
// MaxAgent 启动器（MaxScript 版）
//
// 用法（任选其一）:
//   1. 拖到 Max 视口 → 自动执行
//   2. MaxScript > Run Script... 选中本文件
//   3. 创建工具栏按钮: Customize > Customize User Interface...
//        Category: MaxAgent
//        Action:   MaxAgent_Show
//
// 前置条件: 已通过 install.py 安装过 maxagent 包
// ----------------------------------------------------------------------

global g_show_max_agent
global g_hide_max_agent
global g_toggle_max_agent

fn _maxagent_safe_call_python pycode = (
    try (
        python.execute pycode
    ) catch (
        local err = getCurrentException()
        messagebox ("MaxAgent 启动失败:\n\n" + err) title:"MaxAgent"
    )
)

fn g_show_max_agent = (
    _maxagent_safe_call_python "import maxagent; maxagent.show()"
)

fn g_hide_max_agent = (
    _maxagent_safe_call_python "import maxagent; maxagent.hide()"
)

fn g_toggle_max_agent = (
    _maxagent_safe_call_python "import maxagent; maxagent.toggle()"
)

// 注册菜单项 / 宏脚本（出现在 Customize 里）
macroScript MaxAgent_Show
    category:"MaxAgent"
    toolTip:"显示 MaxAgent AI 助手面板"
    buttonText:"MaxAgent"
(
    g_show_max_agent()
)

macroScript MaxAgent_Toggle
    category:"MaxAgent"
    toolTip:"切换 MaxAgent 面板显示/隐藏"
(
    g_toggle_max_agent()
)

// 拖到视口时立即显示
g_show_max_agent()
