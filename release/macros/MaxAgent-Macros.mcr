-- ----------------------------------------------------------------------
-- MaxAgent macroScript 集合（手写 .mcr，UTF-8 with BOM 编码）
--
-- 关键约束：macroScript 块内引用的 fn 必须"跨会话可见"才行。
--   .mcr 文件级 fn 定义只在**首次 fileIn 时**被注册到当前会话全局命名空间；
--   Max 重启后 .mcr 自动 fileIn 的时机在 macroScript 之前，但**文件级 fn
--   在 Max ≤ 2024 上不会被自动重新注册**（尤其是 macroScript 触发时另起
--   新 scope 的场景），导致触发按钮时报：
--     Type error: Call needs function or class, got: undefined
--
--   解决办法：所有 macroScript 内使用的辅助逻辑必须**完全内联**，不能
--   依赖 .mcr 顶部 fn 定义的跨会话可见性。
--
-- 编码：UTF-8 with BOM，避免跨 Max 语言版本中文乱码。
-- ----------------------------------------------------------------------

macroScript MaxAgent_Show
    category:"MaxAgent"
    tooltip:"显示 MaxAgent 面板"
    buttontext:"MaxAgent"
(
    -- 1) 注入 sys.path
    local _userScripts = getDir #userScripts
    _userScripts = trimRight _userScripts "\\"
    local _pyInit = ""
    _pyInit += "import sys, os\n"
    _pyInit += "_root = r'" + _userScripts + "'\n"
    _pyInit += "if _root not in sys.path:\n"
    _pyInit += "    sys.path.insert(0, _root)\n"
    try (
        python.execute _pyInit
    ) catch (
        local _err = "注入 sys.path 失败: " + (getCurrentException() as string)
        format "%\n" _err to:listener
        messagebox _err title:"MaxAgent 启动失败"
        return false
    )

    -- 2) 内联的 safe python 执行（重启后跨会话可见性问题的兜底）
    local _wrapper = ""
    _wrapper += "import sys, traceback\n"
    _wrapper += "_maxagent_err = None\n"
    _wrapper += "try:\n"
    _wrapper += "    import maxagent.startup as _s; _s.show_panel(force=True)\n"
    _wrapper += "    _maxagent_err = ['OK', None]\n"
    _wrapper += "except Exception as _e:\n"
    _wrapper += "    _tb = traceback.format_exc()\n"
    _wrapper += "    _msg = str(type(_e).__name__) + ': ' + str(_e)\n"
    _wrapper += "    print('[MaxAgent Python Error] ' + _msg)\n"
    _wrapper += "    print(_tb)\n"
    _wrapper += "    _maxagent_err = ['ERR', _msg + '\\n' + _tb]\n"
    _wrapper += "_maxagent_err\n"

    local _result = undefined
    try (
        _result = python.execute _wrapper
    ) catch (
        local _msErr = "python.execute 失败: " + (getCurrentException() as string)
        format "%\n" _msErr to:listener
        messagebox _msErr title:"MaxAgent 启动失败"
        return false
    )

    if _result == undefined or classOf _result != Array or _result.count < 1 do (
        messagebox "python.execute 返回异常" title:"MaxAgent 启动失败"
        return false
    )

    if _result[1] != "OK" do (
        local _detail = if _result.count >= 2 then (_result[2] as string) else "未知错误"
        local _msg = "MaxAgent 启动失败。完整 Python 异常已输出到 Max Listener（F11）。\n\n"
        _msg += "常见原因:\n"
        _msg += "  • 安装不完整（缺少 maxagent 包文件）\n"
        _msg += "  • 上次更新后未重新打包/重新安装 mzp\n"
        _msg += "  • Python 代码存在语法/导入错误\n\n"
        _msg += "异常摘要:\n" + _detail
        format "%\n" _msg to:listener
        messagebox _msg title:"MaxAgent 启动失败"
    )
)

macroScript MaxAgent_Toggle
    category:"MaxAgent"
    tooltip:"切换 MaxAgent 面板"
    buttontext:"切换面板"
(
    local _userScripts = getDir #userScripts
    _userScripts = trimRight _userScripts "\\"
    local _pyInit = ""
    _pyInit += "import sys, os\n"
    _pyInit += "_root = r'" + _userScripts + "'\n"
    _pyInit += "if _root not in sys.path:\n"
    _pyInit += "    sys.path.insert(0, _root)\n"
    try (
        python.execute _pyInit
    ) catch (
        local _err = "注入 sys.path 失败: " + (getCurrentException() as string)
        format "%\n" _err to:listener
        messagebox _err title:"MaxAgent 切换失败"
        return false
    )

    local _wrapper = ""
    _wrapper += "import sys, traceback\n"
    _wrapper += "_maxagent_err = None\n"
    _wrapper += "try:\n"
    _wrapper += "    import maxagent; maxagent.toggle()\n"
    _wrapper += "    _maxagent_err = ['OK', None]\n"
    _wrapper += "except Exception as _e:\n"
    _wrapper += "    _tb = traceback.format_exc()\n"
    _wrapper += "    _msg = str(type(_e).__name__) + ': ' + str(_e)\n"
    _wrapper += "    print('[MaxAgent Python Error] ' + _msg)\n"
    _wrapper += "    print(_tb)\n"
    _wrapper += "    _maxagent_err = ['ERR', _msg + '\\n' + _tb]\n"
    _wrapper += "_maxagent_err\n"

    local _result = undefined
    try (
        _result = python.execute _wrapper
    ) catch (
        local _msErr = "python.execute 失败: " + (getCurrentException() as string)
        format "%\n" _msErr to:listener
        messagebox _msErr title:"MaxAgent 切换失败"
        return false
    )

    if _result == undefined or classOf _result != Array or _result.count < 1 do return false

    if _result[1] != "OK" do (
        local _detail = if _result.count >= 2 then (_result[2] as string) else "未知错误"
        local _msg = "MaxAgent 切换失败。完整 Python 异常已输出到 Max Listener（F11）。\n\n"
        _msg += "异常摘要:\n" + _detail
        format "%\n" _msg to:listener
        messagebox _msg title:"MaxAgent 切换失败"
    )
)

macroScript MaxAgent_OpenInstallDir
    category:"MaxAgent"
    tooltip:"打开 MaxAgent 安装目录"
    buttontext:"打开安装目录"
(
    local _userScripts = getDir #userScripts
    _userScripts = trimRight _userScripts "\\"
    local _dir = _userScripts + "\\maxagent"
    if doesDirectoryExist _dir then (
        shellLaunch "explorer.exe" _dir
    ) else (
        messagebox ("未找到安装目录: " + _dir) title:"MaxAgent"
    )
)

macroScript MaxAgent_Uninstall
    category:"MaxAgent"
    tooltip:"卸载 MaxAgent"
    buttontext:"卸载"
(
    -- 实时重算安装路径，不依赖全局变量（global 不跨会话持久化）
    local _userScripts = getDir #userScripts
    _userScripts = trimRight _userScripts "\\"
    local _pkgDir = _userScripts + "\\maxagent"

    local _macroDir = getDir #userMacros
    _macroDir = trimRight _macroDir "\\"
    local _mcrFile = _macroDir + "\\MaxAgent-Macros.mcr"

    local _startupDir = getDir #userStartupScripts
    _startupDir = trimRight _startupDir "\\"
    local _startupMs = _startupDir + "\\MaxAgent_Menu.ms"

    local _msg = "确定要卸载 MaxAgent 吗？\n\n"
    _msg += "将删除以下目录/文件并清理菜单:\n"
    _msg += "  • " + _pkgDir + "\n"
    _msg += "  • " + _mcrFile + "\n"
    _msg += "  • " + _startupMs + "\n"
    _msg += "  • 主菜单栏中的 MaxAgent 子菜单\n\n"
    _msg += "卸载后请重启 3ds Max。"
    local _ans = queryBox _msg title:"卸载 MaxAgent" beep:false
    if _ans != true do return false

    local _ok = true

    -- 1) 清理菜单——按 Max 版本分发（2025+ 走 CUI，2022~2024 走 menuMan）
    local _ver = 0
    try (_ver = (maxVersion())[1] as integer) catch ()

    if _ver >= 27000 then (
        -- Max 2025+：注销 cuiRegisterMenus 回调 + 删除 startup 脚本 + 刷新配置
        try (
            callbacks.removeScripts id:#MaxAgentMenu
        ) catch ()
        try (
            local _mgr = maxOps.GetICuiMenuMgr()
            if _mgr != undefined do (
                local _curCfg = _mgr.GetCurrentConfiguration()
                if _curCfg != undefined and _curCfg != "" do (
                    _mgr.LoadConfiguration _curCfg
                )
            )
        ) catch ()
    ) else (
        -- Max 2022~2024：从主菜单栏移除 MaxAgent 子菜单
        try (
            local _mainMenu = menuMan.getMainMenuBar()
            if _mainMenu != undefined do (
                local _found = -1
                for i = 1 to _mainMenu.numItems() while _found == -1 do (
                    local _it = _mainMenu.getItem i
                    if _it != undefined and _it.getTitle() == "MaxAgent" do _found = i
                )
                if _found > 0 do (
                    _mainMenu.removeItemByPosition _found
                    menuMan.updateMenuBar()
                    try (menuMan.saveMenuFile (menuMan.getMenuFile())) catch ()
                )
            )
        ) catch (
            _ok = false
            format "移除 MaxAgent 子菜单失败: %\n" (getCurrentException() as string) to:listener
        )
    )

    -- 2) 删除 startup 脚本（Max 2025+ 才会写，2022~2024 也无妨）
    if doesFileExist _startupMs do (
        try (deleteFile _startupMs) catch (
            _ok = false
            format "删除 startup 脚本失败: %\n" (getCurrentException() as string) to:listener
        )
    )

    -- 3) 删除包目录
    if doesDirectoryExist _pkgDir do (
        try (
            shellLaunch "cmd.exe" ("/c rmdir /s /q \"" + _pkgDir + "\"")
        ) catch (
            _ok = false
            format "删除目录失败: %\n" (getCurrentException() as string) to:listener
        )
    )

    -- 4) 删除本 .mcr 文件（放最后，因为删完这个宏定义就没了）
    if doesFileExist _mcrFile do (
        try (deleteFile _mcrFile) catch (
            _ok = false
            format "删除宏文件失败: %\n" (getCurrentException() as string) to:listener
        )
    )

    if _ok then (
        messagebox "MaxAgent 已卸载，请重启 3ds Max。" title:"MaxAgent"
    ) else (
        messagebox "卸载过程中发生错误，部分文件可能未删除，请查看 Listener 手动清理。" title:"MaxAgent"
    )
)
