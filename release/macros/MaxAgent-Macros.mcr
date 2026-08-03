-- ----------------------------------------------------------------------
-- MaxAgent macroScript 集合（手写 .mcr，UTF-8 with BOM 编码）
--
-- 设计原因：
--   不让 Max 自动从 mzp_install.ms 内联 macroScript 块生成 .mcr 文件，
--   原因是 Max 自动序列化的 .mcr 编码与当前 Max 语言绑定（中文版 GBK，
--   英文版 1252 / Latin-1），结果是：
--     * 在中文版 Max 注册的 .mcr 拷给英文版用户 → 中文按钮文案乱码报错
--     * 跨 Max 版本升级时 ActionTable 路径错位
--
--   改为我们自己提供一份 UTF-8 BOM 的 .mcr 直接落到 %LOCALAPPDATA%\
--   ...\<lang>\usermacros\，Max 按 BOM 解码，所有语言版本都能正确读取
--   中文字面量。
--
-- 安装位置：
--   getDir #userMacros  （由 Max 解析为当前语言的 usermacros 目录）
--
-- 注：.mcr 内的 macroScript 块由 Max 启动时自动 fileIn，无需手动注册。
--
-- sys.path 兜底：
--   mzp_install.ms 仅在安装那一刻向 sys.path 注入安装目录，
--   Max 重启后该注入丢失，宏内 `import maxagent` 会 ModuleNotFoundError。
--   因此 Show / Toggle 两个入口宏在 import 之前都先重新注入一次。
--   注入逻辑直接 inline 到 macroScript 块内，避免依赖 .mcr 块外
--   fn 定义的跨会话可见性。
-- ----------------------------------------------------------------------

-- 安全执行 Python 代码，并把完整 Python 异常回显到 Max Listener 与弹窗。
-- 返回 #(true, undefined) 或 #(false, tracebackString)
fn _maxagent_safe_python code = (
    local _wrapper = "import sys, traceback\n"
    _wrapper += "_maxagent_err = None\n"
    _wrapper += "try:\n"
    _wrapper += "    " + code + "\n"
    _wrapper += "    _maxagent_err = ['OK', None]\n"
    _wrapper += "except Exception as _e:\n"
    _wrapper += "    _tb = traceback.format_exc()\n"
    _wrapper += "    _msg = str(type(_e).__name__) + ': ' + str(_e)\n"
    _wrapper += "    _maxagent_err = ['ERR', _msg + '\\n' + _tb]\n"
    _wrapper += "_maxagent_err\n"

    local _result = undefined
    try (
        _result = python.execute _wrapper
    ) catch (
        local _msErr = "MaxScript python.execute 失败: " + (getCurrentException() as string)
        format "%\n" _msErr to:listener
        return #(false, _msErr)
    )

    if _result == undefined do return #(false, "python.execute 未返回结果")
    if classOf _result != Array do (
        return #(false, ("python.execute 返回类型异常: " + (classOf _result as string)))
    )
    if _result.count < 1 do return #(false, "python.execute 返回数组为空")

    local _status = _result[1]
    if _status == "OK" then (
        #(true, undefined)
    ) else (
        if _result.count < 2 do return #(false, "python.execute 返回错误但缺少详情")
        #(false, (_result[2] as string))
    )
)

fn _maxagent_report_error title msg = (
    format "%\n" msg to:listener
    messagebox msg title:title
)

macroScript MaxAgent_Show
    category:"MaxAgent"
    tooltip:"显示 MaxAgent 面板"
    buttontext:"MaxAgent"
(
    -- 1) 注入 sys.path（不输出任何内容，避免污染 python.execute 返回值）
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

    -- 2) 调用 Python 显示面板
    local _r = _maxagent_safe_python "import maxagent.startup as _s; _s.show_panel(force=True)"
    if not _r[1] do (
        local _msg = "MaxAgent 启动失败。完整 Python 异常已输出到 Max Listener（F11）。\n\n"
        _msg += "常见原因:\n"
        _msg += "  • 安装不完整（缺少 maxagent 包文件）\n"
        _msg += "  • 上次更新后未重新打包/重新安装 mzp\n"
        _msg += "  • Python 代码存在语法/导入错误\n\n"
        _msg += "异常摘要:\n" + _r[2]
        _maxagent_report_error "MaxAgent 启动失败" _msg
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

    local _r = _maxagent_safe_python "import maxagent; maxagent.toggle()"
    if not _r[1] do (
        local _msg = "MaxAgent 切换失败。完整 Python 异常已输出到 Max Listener（F11）。\n\n"
        _msg += "异常摘要:\n" + _r[2]
        _maxagent_report_error "MaxAgent 切换失败" _msg
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
    -- getDir #userScripts 自带语言识别，中文版返回 \zh-CN\，英文版返回 \ENU\
    local _userScripts = getDir #userScripts
    _userScripts = trimRight _userScripts "\\"
    local _pkgDir = _userScripts + "\\maxagent"
    local _macroDir = getDir #userMacros
    _macroDir = trimRight _macroDir "\\"
    local _mcrFile = _macroDir + "\\MaxAgent-Macros.mcr"

    local _msg = "确定要卸载 MaxAgent 吗？\n\n"
    _msg += "将删除以下目录/文件:\n"
    _msg += _pkgDir + "\n"
    _msg += _mcrFile + "\n\n"
    _msg += "卸载后请重启 3ds Max。"
    local _ans = queryBox _msg title:"卸载 MaxAgent" beep:false
    if _ans == true then (
        local _ok = true
        if doesDirectoryExist _pkgDir do (
            try (
                -- HWnd 占位：removeDir 在较新 Max 中可能不存在，用系统命令兜底
                shellLaunch "cmd.exe" ("/c rmdir /s /q \"" + _pkgDir + "\"")
            ) catch (
                _ok = false
                format "删除目录失败: %\n" (getCurrentException() as string) to:listener
            )
        )
        if doesFileExist _mcrFile do (
            try (
                deleteFile _mcrFile
            ) catch (
                _ok = false
                format "删除宏文件失败: %\n" (getCurrentException() as string) to:listener
            )
        )
        if _ok then (
            messagebox "MaxAgent 已卸载，请重启 3ds Max。" title:"MaxAgent"
        ) else (
            messagebox "卸载过程中发生错误，部分文件可能未删除，请手动清理。" title:"MaxAgent"
        )
    )
)
