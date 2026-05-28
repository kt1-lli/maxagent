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

macroScript MaxAgent_Show
    category:"MaxAgent"
    tooltip:"显示 MaxAgent 面板"
    buttontext:"MaxAgent"
(
    try (
        -- 兜底注入 sys.path（幂等，重启后必跑）
        local _userScripts = getDir #userScripts
        _userScripts = trimRight _userScripts "\\"
        local _pyInit = ""
        _pyInit += "import sys\n"
        _pyInit += "_root = r'" + _userScripts + "'\n"
        _pyInit += "if _root not in sys.path:\n"
        _pyInit += "    sys.path.insert(0, _root)\n"
        python.execute _pyInit

        python.execute "import maxagent.startup as _s; _s.show_panel(force=True)"
    ) catch (
        messagebox ("启动失败: " + getCurrentException()) title:"MaxAgent"
    )
)

macroScript MaxAgent_Toggle
    category:"MaxAgent"
    tooltip:"切换 MaxAgent 面板"
    buttontext:"切换面板"
(
    try (
        -- 兜底注入 sys.path（幂等，重启后必跑）
        local _userScripts = getDir #userScripts
        _userScripts = trimRight _userScripts "\\"
        local _pyInit = ""
        _pyInit += "import sys\n"
        _pyInit += "_root = r'" + _userScripts + "'\n"
        _pyInit += "if _root not in sys.path:\n"
        _pyInit += "    sys.path.insert(0, _root)\n"
        python.execute _pyInit

        python.execute "import maxagent; maxagent.toggle()"
    ) catch (
        messagebox ("切换失败: " + getCurrentException()) title:"MaxAgent"
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
    local _dir = _userScripts + "\\maxagent"

    if not (doesDirectoryExist _dir) then (
        messagebox ("未找到 MaxAgent 安装目录:\n" + _dir + "\n\n可能已卸载或安装在其它位置。") title:"MaxAgent 卸载"
    ) else (
        if (queryBox ("确认卸载 MaxAgent？\n\n安装目录:\n" + _dir + "\n\n（仅删除程序文件，保留用户数据 _userdata/）") title:"MaxAgent 卸载") then (
            -- 1) 删除 maxagent 包，保留 _userdata
            local _py = ""
            _py += "import os, shutil\n"
            _py += "_dir = r'" + _dir + "'\n"
            _py += "if os.path.isdir(_dir):\n"
            _py += "    for _name in os.listdir(_dir):\n"
            _py += "        if _name == '_userdata':\n"
            _py += "            continue\n"
            _py += "        _p = os.path.join(_dir, _name)\n"
            _py += "        if os.path.isdir(_p):\n"
            _py += "            shutil.rmtree(_p, ignore_errors=True)\n"
            _py += "        else:\n"
            _py += "            try:\n"
            _py += "                os.remove(_p)\n"
            _py += "            except Exception:\n"
            _py += "                pass\n"
            _py += "    if not os.path.isdir(os.path.join(_dir, '_userdata')):\n"
            _py += "        try: os.rmdir(_dir)\n"
            _py += "        except Exception: pass\n"

            -- 2) 同步移除 MaxAgent 主菜单
            -- Max 2025+ 必须先移除 cuiRegisterMenus 回调，否则 Max 重启
            -- 加载菜单系统时仍会重新注册出来。当前会话的菜单条目用
            -- menuMan.removeItemByPosition 移除（这一步在所有版本都管用）。
            try (
                callbacks.removeScripts id:#MaxAgentMenu
            ) catch ()
            try (
                local _mainMenu = menuMan.getMainMenuBar()
                if _mainMenu != undefined do (
                    local _idx = -1
                    for i = 1 to _mainMenu.numItems() while _idx == -1 do (
                        local _item = _mainMenu.getItem i
                        if _item != undefined and _item.getTitle() == "MaxAgent" do _idx = i
                    )
                    if _idx > 0 do (
                        _mainMenu.removeItemByPosition _idx
                        menuMan.updateMenuBar()
                    )
                )
            ) catch ()

            -- 2b) Max 2025+ 还需把"MaxAgent 已从 schema 剔除"的状态写盘
            --     否则即便回调被移除，schema 文件里仍残留 MaxAgent 节点定义，
            --     重启后菜单系统仍会尝试还原它（变成空菜单或悬浮容器）。
            --     调一次 LoadConfiguration（触发回调外的标准重建路径，此时
            --     回调已被 removeScripts 移除，不会再 CreateSubMenu），
            --     然后 SaveConfiguration 把"无 MaxAgent"的状态落盘。
            try (
                local _mgr = maxOps.GetICuiMenuMgr()
                if _mgr != undefined do (
                    local _curCfg = _mgr.GetCurrentConfiguration()
                    if _curCfg != undefined and _curCfg != "" do (
                        _mgr.LoadConfiguration _curCfg
                        _mgr.SaveConfiguration _curCfg
                    )
                )
            ) catch ()

            -- 3) 同步删除自身 .mcr 文件（避免 ActionTable 残留）
            --    Max 会在重启后忽略找不到的 ActionTable 项
            try (
                local _userMacros = getDir #userMacros
                _userMacros = trimRight _userMacros "\\"
                local _mcr = _userMacros + "\\MaxAgent-Macros.mcr"
                if doesFileExist _mcr do deleteFile _mcr
            ) catch ()

            try (
                python.execute _py
                messagebox ("✅ MaxAgent 已卸载\n\n保留位置: " + _dir + "\\_userdata\n（如不需要可手动删除）\n\n请重启 3ds Max") title:"MaxAgent"
            ) catch (
                messagebox ("卸载失败: " + getCurrentException()) title:"MaxAgent"
            )
        )
    )
)
