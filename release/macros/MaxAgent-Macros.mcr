-- ----------------------------------------------------------------------
-- MaxAgent macroScript 集合（手写 .mcr，UTF-8 with BOM 编码）
--
-- 关键约束：
--   1. macroScript 块内引用的 fn 必须"跨会话可见"才行。Max 重启后
--      文件级 fn 定义不一定能被 macroScript 找到，导致触发按钮时报：
--        Type error: Call needs function or class, got: undefined
--      → 所有辅助逻辑必须内联进 macroScript 块，不依赖顶部 fn。
--
--   2. python.execute 的返回值行为不稳定：Python 端 try/except 完成后
--      写模块变量作为最后一个表达式，理论上 MaxScript 侧能拿到 list，
--      但实测在部分 Max 版本 / 部分 Python 语句序列下会返回 undefined
--      或非 Array 对象。因此**不依赖返回值**判断成功与否——只要
--      python.execute 没抛 MaxScript 异常，就视为 Python 侧运行完成；
--      Python 内部的错误由 try/except 自己打印到 Listener（F11 可见）。
--
--   3. Python 代码自身出错时，把错误信息写到全局文件 %TEMP%\maxagent_last_err.txt
--      MaxScript 事后检查该文件，存在即弹窗展示。
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
    _pyInit += "import sys, os, tempfile\n"
    _pyInit += "_root = r'" + _userScripts + "'\n"
    _pyInit += "if _root not in sys.path:\n"
    _pyInit += "    sys.path.insert(0, _root)\n"
    _pyInit += "_err_flag = os.path.join(tempfile.gettempdir(), 'maxagent_last_err.txt')\n"
    _pyInit += "try:\n"
    _pyInit += "    os.remove(_err_flag)\n"
    _pyInit += "except Exception:\n"
    _pyInit += "    pass\n"
    try (
        python.execute _pyInit
    ) catch (
        local _err = "注入 sys.path 失败: " + (getCurrentException() as string)
        format "%\n" _err to:listener
        messagebox _err title:"MaxAgent 启动失败"
        return false
    )

    -- 2) 执行主入口（Python 内部 try/except 把错误写到临时文件）
    local _wrapper = ""
    _wrapper += "import sys, os, tempfile, traceback\n"
    _wrapper += "try:\n"
    _wrapper += "    import maxagent.startup as _s\n"
    _wrapper += "    _s.show_panel(force=True)\n"
    _wrapper += "except Exception as _e:\n"
    _wrapper += "    _tb = traceback.format_exc()\n"
    _wrapper += "    _msg = str(type(_e).__name__) + ': ' + str(_e)\n"
    _wrapper += "    print('[MaxAgent Python Error] ' + _msg)\n"
    _wrapper += "    print(_tb)\n"
    _wrapper += "    try:\n"
    _wrapper += "        with open(os.path.join(tempfile.gettempdir(), 'maxagent_last_err.txt'), 'w', encoding='utf-8') as _fh:\n"
    _wrapper += "            _fh.write(_msg + '\\n' + _tb)\n"
    _wrapper += "    except Exception:\n"
    _wrapper += "        pass\n"

    try (
        python.execute _wrapper
    ) catch (
        local _msErr = "python.execute 失败: " + (getCurrentException() as string)
        format "%\n" _msErr to:listener
        messagebox _msErr title:"MaxAgent 启动失败"
        return false
    )

    -- 3) 检查 Python 侧是否留下错误标记
    local _tempDir = sysInfo.tempdir
    local _errFile = _tempDir + "maxagent_last_err.txt"
    if doesFileExist _errFile do (
        local _detail = ""
        try (
            local _fs = openFile _errFile
            if _fs != undefined do (
                while not eof _fs do _detail += (readLine _fs) + "\n"
                close _fs
            )
        ) catch ()
        local _msg = "MaxAgent 启动失败。完整异常已输出到 Max Listener（F11）。\n\n"
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
    _pyInit += "import sys, os, tempfile\n"
    _pyInit += "_root = r'" + _userScripts + "'\n"
    _pyInit += "if _root not in sys.path:\n"
    _pyInit += "    sys.path.insert(0, _root)\n"
    _pyInit += "_err_flag = os.path.join(tempfile.gettempdir(), 'maxagent_last_err.txt')\n"
    _pyInit += "try:\n"
    _pyInit += "    os.remove(_err_flag)\n"
    _pyInit += "except Exception:\n"
    _pyInit += "    pass\n"
    try (
        python.execute _pyInit
    ) catch (
        local _err = "注入 sys.path 失败: " + (getCurrentException() as string)
        format "%\n" _err to:listener
        messagebox _err title:"MaxAgent 切换失败"
        return false
    )

    local _wrapper = ""
    _wrapper += "import sys, os, tempfile, traceback\n"
    _wrapper += "try:\n"
    _wrapper += "    import maxagent\n"
    _wrapper += "    maxagent.toggle()\n"
    _wrapper += "except Exception as _e:\n"
    _wrapper += "    _tb = traceback.format_exc()\n"
    _wrapper += "    _msg = str(type(_e).__name__) + ': ' + str(_e)\n"
    _wrapper += "    print('[MaxAgent Python Error] ' + _msg)\n"
    _wrapper += "    print(_tb)\n"
    _wrapper += "    try:\n"
    _wrapper += "        with open(os.path.join(tempfile.gettempdir(), 'maxagent_last_err.txt'), 'w', encoding='utf-8') as _fh:\n"
    _wrapper += "            _fh.write(_msg + '\\n' + _tb)\n"
    _wrapper += "    except Exception:\n"
    _wrapper += "        pass\n"

    try (
        python.execute _wrapper
    ) catch (
        local _msErr = "python.execute 失败: " + (getCurrentException() as string)
        format "%\n" _msErr to:listener
        messagebox _msErr title:"MaxAgent 切换失败"
        return false
    )

    local _errFile = sysInfo.tempdir + "maxagent_last_err.txt"
    if doesFileExist _errFile do (
        local _detail = ""
        try (
            local _fs = openFile _errFile
            if _fs != undefined do (
                while not eof _fs do _detail += (readLine _fs) + "\n"
                close _fs
            )
        ) catch ()
        local _msg = "MaxAgent 切换失败。完整异常已输出到 Max Listener（F11）。\n\n"
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

    -- 1) 清理菜单
    local _ver = 0
    try (_ver = (maxVersion())[1] as integer) catch ()

    if _ver >= 27000 then (
        try (callbacks.removeScripts id:#MaxAgentMenu) catch ()
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

    -- 2) 删除 startup 脚本
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

    -- 4) 删除 .mcr（最后做）
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
