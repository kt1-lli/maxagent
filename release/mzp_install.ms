-- ----------------------------------------------------------------------
-- MaxAgent mzp 安装钩子
--
-- 当用户把 maxagent-X.Y.Z.mzp 拖入 3ds Max 视口时，Max 会解压 mzp 并
-- 自动执行本脚本（mzp 协议规定）。本脚本任务：
--   1. 探测当前 Python 版本，从 runtime/cpXX/ 选择对应产物
--   2. 拷贝到用户脚本目录 %LOCALAPPDATA%\Autodesk\3dsMax\<ver>\ENU\scripts\maxagent\
--   3. 注入 sys.path 并立即可用
--   4. 注册菜单/工具栏宏 MaxAgent_Show / MaxAgent_Toggle / MaxAgent_Uninstall
--
-- 兼容范围: 3ds Max 2022 / 2023 / 2024 / 2025 / 2025.1 / 2026 / 2027
--           Python 3.7 / 3.9 / 3.10 / 3.11 / 3.13
--
-- 与 MAXAGENT_INSTALL.ms（开发态启动器）相互独立，互不冲突。
-- ----------------------------------------------------------------------

global g_maxagent_mzp_root
global g_maxagent_install_dir
global g_maxagent_supported_abis = #("cp37", "cp39", "cp310", "cp311", "cp313")

-- 把路径中的反斜杠转义为双反斜杠，便于嵌入 Python 字符串字面量
fn _maxagent_escape_path p = (
    substituteString p "\\" "\\\\"
)

-- 解析 mzp 解压目录（mzp 内部脚本通过 getSourceFileName() 拿到自身位置）
fn _maxagent_resolve_mzp_root = (
    local scriptPath = getSourceFileName()
    if scriptPath == undefined or scriptPath == "" do return undefined
    local rootDir = getFilenamePath scriptPath
    rootDir = trimRight rootDir "\\"
    rootDir
)

-- 探测当前 Max 内嵌 Python 的小版本，返回如 "cp311" 的字符串
fn _maxagent_detect_python_abi = (
    local pyTag = python.execute "import sys; 'cp{}{}'.format(sys.version_info.major, sys.version_info.minor)"
    pyTag
)

-- 计算用户级安装目录: %LOCALAPPDATA%\Autodesk\3dsMax\<MaxVer ENU>\scripts\maxagent
fn _maxagent_user_install_dir = (
    local userScriptsDir = getDir #userScripts
    userScriptsDir = trimRight userScriptsDir "\\"
    (userScriptsDir + "\\maxagent")
)

-- 递归拷贝目录（MaxScript 没有内建递归 copy，借助 Python shutil）
fn _maxagent_copy_tree srcDir dstDir = (
    local pycode = ""
    pycode += "import shutil, os\n"
    pycode += "_src = r'" + srcDir + "'\n"
    pycode += "_dst = r'" + dstDir + "'\n"
    pycode += "if os.path.isdir(_dst):\n"
    pycode += "    shutil.rmtree(_dst, ignore_errors=True)\n"
    pycode += "shutil.copytree(_src, _dst)\n"
    try (
        python.execute pycode
        true
    ) catch (
        local err = getCurrentException()
        messagebox ("MaxAgent 拷贝失败:\n" + err) title:"MaxAgent 安装失败"
        false
    )
)

-- 注入 sys.path 让 import maxagent 立即可用
fn _maxagent_inject_sys_path parentDir = (
    local pycode = ""
    pycode += "import sys, os\n"
    pycode += "_root = r'" + parentDir + "'\n"
    pycode += "if _root not in sys.path:\n"
    pycode += "    sys.path.insert(0, _root)\n"
    try (
        python.execute pycode
        true
    ) catch (
        local err = getCurrentException()
        messagebox ("MaxAgent 注入 sys.path 失败:\n" + err) title:"MaxAgent"
        false
    )
)

-- 安装入口
fn maxagent_mzp_install = (
    g_maxagent_mzp_root = _maxagent_resolve_mzp_root()
    if g_maxagent_mzp_root == undefined or g_maxagent_mzp_root == "" do (
        messagebox "无法定位 mzp 解压目录\n（请用 Run Script 运行而不是粘贴到 Listener）" title:"MaxAgent 安装"
        return false
    )

    local abi = _maxagent_detect_python_abi()
    if findItem g_maxagent_supported_abis abi == 0 then (
        local msg = "❌ 不支持的 Python 版本: " + abi + "\n\n"
        msg += "MaxAgent 0.4 系列支持: 3ds Max 2022~2027\n"
        msg += "对应 Python: 3.7 / 3.9 / 3.10 / 3.11 / 3.13"
        messagebox msg title:"MaxAgent 安装失败"
        return false
    )

    local srcPkg = (g_maxagent_mzp_root + "\\runtime\\" + abi + "\\maxagent")
    local pkgExists = python.execute ("import os; os.path.isdir(r'" + srcPkg + "')")
    if pkgExists != true then (
        messagebox ("❌ mzp 内缺少 " + abi + " 产物，请下载完整版本") title:"MaxAgent 安装失败"
        return false
    )

    local installDir = _maxagent_user_install_dir()
    g_maxagent_install_dir = installDir
    local parentDir = getFilenamePath installDir
    parentDir = trimRight parentDir "\\"

    -- 1) 拷贝包
    if not (_maxagent_copy_tree srcPkg installDir) do return false

    -- 2) 注入 sys.path
    if not (_maxagent_inject_sys_path parentDir) do return false

    -- 3) 注册宏
    macroScript MaxAgent_Show category:"MaxAgent" tooltip:"显示 MaxAgent 面板" (
        try (python.execute "import maxagent.startup as _s; _s.show_panel(force=True)")
        catch (messagebox ("启动失败: " + getCurrentException()) title:"MaxAgent")
    )
    macroScript MaxAgent_Toggle category:"MaxAgent" tooltip:"切换 MaxAgent 面板" (
        try (python.execute "import maxagent; maxagent.toggle()")
        catch (messagebox ("切换失败: " + getCurrentException()) title:"MaxAgent")
    )
    macroScript MaxAgent_Uninstall category:"MaxAgent" tooltip:"卸载 MaxAgent" (
        if (queryBox "确认卸载 MaxAgent？\n（仅删除程序文件，保留用户数据 _userdata/）" title:"MaxAgent 卸载") then (
            local _dir = g_maxagent_install_dir
            local _py = "import shutil; shutil.rmtree(r'" + _dir + "', ignore_errors=True)"
            try (python.execute _py; messagebox "✅ MaxAgent 已卸载，请重启 3ds Max" title:"MaxAgent")
            catch (messagebox ("卸载失败: " + getCurrentException()) title:"MaxAgent")
        )
    )

    -- 4) 立即显示面板
    try (
        python.execute "import maxagent.startup as _s; _s.show_panel(force=True)"
    ) catch (
        local err = getCurrentException()
        messagebox ("✅ MaxAgent 已安装到:\n" + installDir + "\n\n但首次启动失败:\n" + err + "\n\n请重启 3ds Max 后再次拖入或点击 MaxAgent_Show 宏") title:"MaxAgent"
        return true
    )

    messagebox ("✅ MaxAgent 安装成功 (" + abi + ")\n\n安装目录:\n" + installDir + "\n\n后续可在 Customize > Customize User Interface > Category: MaxAgent 添加快捷按钮") title:"MaxAgent"
    true
)

-- mzp 拖入时自动执行
maxagent_mzp_install()
