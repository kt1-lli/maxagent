-- ----------------------------------------------------------------------
-- MaxAgent 启动脚本
--
-- 用法（任选其一）:
--   1. 把本文件拖到 Max 视口
--   2. MaxScript > Run Script... 选中本文件
--   3. 创建工具栏按钮: Customize > Customize User Interface...
--        Category: MaxAgent
--        Action:   MaxAgent_Show / MaxAgent_Toggle
--
-- 要求: 本 ms 文件所在目录的同级必须存在 maxagent 包目录。
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

-- 安全调用 Python：捕获 MaxScript 与 Python 端异常，并把完整 traceback
-- 输出到 Max Listener，方便用户按 F11 查看。
fn _maxagent_safe_call_python pycode titleStr = (
    local _wrapper = ""
    _wrapper += "import sys, traceback\n"
    _wrapper += "_maxagent_err = None\n"
    _wrapper += "try:\n"
    _wrapper += "    " + pycode + "\n"
    _wrapper += "    _maxagent_err = ('OK', None)\n"
    _wrapper += "except Exception as _e:\n"
    _wrapper += "    _tb = traceback.format_exc()\n"
    _wrapper += "    _msg = str(type(_e).__name__) + ': ' + str(_e)\n"
    _wrapper += "    print('[MaxAgent Python Error] ' + _msg)\n"
    _wrapper += "    print(_tb)\n"
    _wrapper += "    _maxagent_err = ('ERR', _msg + '\\n' + _tb)\n"
    _wrapper += "_maxagent_err\n"

    local _result = undefined
    try (
        _result = python.execute _wrapper
    ) catch (
        local _err = "MaxScript python.execute 失败: " + (getCurrentException() as string)
        format "%\n" _err to:listener
        messagebox _err title:titleStr
        return false
    )

    if _result == undefined do (
        messagebox "python.execute 未返回结果" title:titleStr
        return false
    )
    if _result[1] == "OK" do return true

    local _msg = "MaxAgent 调用 Python 失败。完整异常已输出到 Max Listener（F11）。\n\n"
    _msg += "常见原因:\n"
    _msg += "  • 安装不完整或更新后未重新打包/重新安装 mzp\n"
    _msg += "  • Python 代码存在导入/语法错误\n\n"
    _msg += "异常摘要:\n" + _result[2]
    format "%\n" _msg to:listener
    messagebox _msg title:titleStr
    false
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
    pycode += "print('[MaxAgent] python path injected from install.ms: ' + _root)\n"
    try (
        python.execute pycode
    ) catch (
        local err = getCurrentException()
        messagebox ("MaxAgent 注入 sys.path 失败:\n\n" + err) title:"MaxAgent"
        return false
    )
    true
)

fn g_show_max_agent = (
    if not _maxagent_ensure_path() do return false
    -- 用户主动触发（拖入、菜单、宏脚本），强制显示
    _maxagent_safe_call_python "import maxagent.startup as _s; _s.show_panel(force=True)" "MaxAgent 显示失败"
)

fn g_hide_max_agent = (
    if not _maxagent_ensure_path() do return false
    _maxagent_safe_call_python "import maxagent; maxagent.hide()" "MaxAgent 隐藏失败"
)

fn g_toggle_max_agent = (
    if not _maxagent_ensure_path() do return false
    _maxagent_safe_call_python "import maxagent; maxagent.toggle()" "MaxAgent 切换失败"
)

-- 自动启动入口（脚本被拖到视口或 Run Script 加载时执行）
-- 走 force=False，由 AppConfig.auto_show_on_startup 决定是否显示
fn g_autostart_max_agent = (
    if not _maxagent_ensure_path() do return false
    _maxagent_safe_call_python "import maxagent.startup as _s; _s.show_panel(force=False)" "MaxAgent 启动失败"
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
