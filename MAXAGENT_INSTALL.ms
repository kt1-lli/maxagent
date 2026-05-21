-- ----------------------------------------------------------------------
-- MaxAgent 启动器（MaxScript 版） - 免安装版
--
-- 用法（任选其一）:
--   1. 把本文件拖到 Max 视口 → 自动注入 sys.path 并显示面板
--   2. MaxScript > Run Script... 选中本文件
--   3. 创建工具栏按钮: Customize > Customize User Interface...
--        Category: MaxAgent
--        Action:   MaxAgent_Show / MaxAgent_Toggle
--
-- 工作目录约定:
--   <仓库根>/MAXAGENT_INSTALL.ms     ← 本文件
--   <仓库根>/maxagent/               ← Python 包
--   即本 ms 文件所在目录的同级必须存在 maxagent 包目录。
--
-- 无需 install.py，运行时动态把仓库根加入 sys.path。
-- ----------------------------------------------------------------------

global g_max_agent_root
global g_show_max_agent
global g_hide_max_agent
global g_toggle_max_agent

-- 把路径里的 \ 转义成 \\，防止写入 Python 字符串字面量时被吃掉
fn _maxagent_escape_path p = (
    substituteString p "\\" "\\\\"
)

-- 以本 ms 脚本所在目录作为 maxagent 包的父目录
-- maxagent 包在 <ms 同级目录>/maxagent/，所以要把 <ms 同级目录> 加到 sys.path
fn _maxagent_resolve_root = (
    local scriptPath = getSourceFileName()
    if scriptPath == undefined or scriptPath == "" do return undefined
    -- getFilenamePath 返回带尾部反斜杠的目录名
    local rootDir = getFilenamePath scriptPath
    rootDir = trimRight rootDir "\\"
    rootDir
)

-- 安全调用 Python：捕获 MaxScript 与 Python 端异常并弹窗
fn _maxagent_safe_call_python pycode = (
    try (
        python.execute pycode
    ) catch (
        local err = getCurrentException()
        messagebox ("MaxAgent 调用 Python 失败:\n\n" + err) title:"MaxAgent"
    )
)

-- 确保 maxagent 包路径已加入 sys.path（重复注入是无害的，幂等）
fn _maxagent_ensure_path = (
    if g_max_agent_root == undefined do (
        g_max_agent_root = _maxagent_resolve_root()
    )
    if g_max_agent_root == undefined or g_max_agent_root == "" do (
        messagebox "无法定位 MaxAgent 安装目录\n（getSourceFileName 返回空，请用 Run Script 方式而不是粘贴到 Listener）" \
            title:"MaxAgent"
        return false
    )
    local escaped = _maxagent_escape_path g_max_agent_root
    local pycode = ""
    pycode += "import sys, os\n"
    pycode += "_root = r'" + g_max_agent_root + "'\n"
    pycode += "if _root not in sys.path:\n"
    pycode += "    sys.path.insert(0, _root)\n"
    _maxagent_safe_call_python pycode
    true
)

fn g_show_max_agent = (
    if not _maxagent_ensure_path() do return false
    -- 用户主动触发（拖入、菜单、宏脚本），强制显示
    _maxagent_safe_call_python "import maxagent.startup as _s; _s.show_panel(force=True)"
)

fn g_hide_max_agent = (
    if not _maxagent_ensure_path() do return false
    _maxagent_safe_call_python "import maxagent; maxagent.hide()"
)

fn g_toggle_max_agent = (
    if not _maxagent_ensure_path() do return false
    _maxagent_safe_call_python "import maxagent; maxagent.toggle()"
)

-- 自动启动入口（脚本被拖到视口或 Run Script 加载时执行）
-- 注意：这里走 force=False，让 AppConfig.auto_show_on_startup 真正生效。
-- 用户在设置里关闭"启动自动显示"后，重新拖动 ms 不会再强制弹窗，
-- 仍然可以通过菜单 / g_show_max_agent() 手动显示。
fn g_autostart_max_agent = (
    if not _maxagent_ensure_path() do return false
    _maxagent_safe_call_python "import maxagent.startup as _s; _s.show_panel(force=False)"
)

-- 注册菜单项 / 宏脚本（出现在 Customize > Action 列表中）
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

-- 拖到视口或 Run Script 时按配置决定是否显示（非 force）
g_autostart_max_agent()
