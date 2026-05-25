-- ----------------------------------------------------------------------
-- MaxAgent mzp 安装钩子
--
-- 当用户把 maxagent-X.Y.Z.mzp 拖入 3ds Max 视口时，Max 会解压 mzp 并
-- 自动执行本脚本（mzp 协议规定）。本脚本任务：
--   1. 探测当前 Python 版本，从 runtime/cpXX/ 选择对应产物
--   2. 拷贝到用户脚本目录 %LOCALAPPDATA%\Autodesk\3dsMax\<ver>\ENU\scripts\maxagent\
--   3. 注入 sys.path 并立即可用
--   4. 注册菜单/工具栏宏 MaxAgent_Show / MaxAgent_Toggle / MaxAgent_Uninstall
--   5. 询问用户是否要把"MaxAgent"菜单注册到主菜单栏末尾
--
-- 兼容范围: 3ds Max 2022 / 2023 / 2024 / 2025 / 2025.1 / 2026 / 2027
--           Python 3.7 / 3.9 / 3.10 / 3.11 / 3.13
--
-- 与 MAXAGENT_INSTALL.ms（开发态启动器）相互独立，互不冲突。
-- ----------------------------------------------------------------------

global g_maxagent_mzp_root
global g_maxagent_install_dir
global g_maxagent_supported_abis = #("cp37", "cp39", "cp310", "cp311", "cp313")
global g_maxagent_menu_name = "MaxAgent"
global g_maxagent_macro_category = "MaxAgent"

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

-- 注册三个 macroScript（不论用户选择哪种安装方式都会注册）
fn _maxagent_register_macros = (
    macroScript MaxAgent_Show category:"MaxAgent" tooltip:"显示 MaxAgent 面板" buttontext:"MaxAgent" (
        try (python.execute "import maxagent.startup as _s; _s.show_panel(force=True)")
        catch (messagebox ("启动失败: " + getCurrentException()) title:"MaxAgent")
    )
    macroScript MaxAgent_Toggle category:"MaxAgent" tooltip:"切换 MaxAgent 面板" buttontext:"切换面板" (
        try (python.execute "import maxagent; maxagent.toggle()")
        catch (messagebox ("切换失败: " + getCurrentException()) title:"MaxAgent")
    )
    macroScript MaxAgent_OpenInstallDir category:"MaxAgent" tooltip:"打开 MaxAgent 安装目录" buttontext:"打开安装目录" (
        local _userScripts = getDir #userScripts
        _userScripts = trimRight _userScripts "\\"
        local _dir = _userScripts + "\\maxagent"
        if doesDirectoryExist _dir then (
            shellLaunch "explorer.exe" _dir
        ) else (
            messagebox ("未找到安装目录: " + _dir) title:"MaxAgent"
        )
    )
    macroScript MaxAgent_About category:"MaxAgent" tooltip:"关于 MaxAgent" buttontext:"关于" (
        local _ver = "?"
        try (
            _ver = python.execute "import maxagent; maxagent.__version__"
        ) catch ()
        messagebox ("MaxAgent " + (_ver as string) + "\n\n3ds Max AI 助手插件\nFunction Calling 驱动 · 零外部依赖\n\nhttps://gitee.com/cmqll/max_agent") title:"关于 MaxAgent"
    )
    macroScript MaxAgent_Uninstall category:"MaxAgent" tooltip:"卸载 MaxAgent" buttontext:"卸载" (
        -- 注意：不能依赖 g_maxagent_install_dir 全局变量，
        -- MaxScript global 不跨 Max 会话持久化，重启后即为 undefined。
        -- 这里实时重算路径：%LOCALAPPDATA%\Autodesk\3dsMax\<ver>\<lang>\scripts\maxagent
        local _userScripts = getDir #userScripts
        _userScripts = trimRight _userScripts "\\"
        local _dir = _userScripts + "\\maxagent"

        if not (doesDirectoryExist _dir) then (
            messagebox ("未找到 MaxAgent 安装目录:\n" + _dir + "\n\n可能已卸载或安装在其它位置。") title:"MaxAgent 卸载"
        ) else (
            if (queryBox ("确认卸载 MaxAgent？\n\n安装目录:\n" + _dir + "\n\n（仅删除程序文件，保留用户数据 _userdata/）") title:"MaxAgent 卸载") then (
                -- 1) 优先保留 _userdata 子目录（用户对话历史/学到的工具/配置）
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

                -- 2) 同步移除 MaxAgent 主菜单（如果存在）
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

                try (
                    python.execute _py
                    messagebox ("✅ MaxAgent 已卸载\n\n保留位置: " + _dir + "\\_userdata\n（如不需要可手动删除）\n\n请重启 3ds Max") title:"MaxAgent"
                ) catch (
                    messagebox ("卸载失败: " + getCurrentException()) title:"MaxAgent"
                )
            )
        )
    )
    true
)

-- 在主菜单栏末尾注册 MaxAgent 菜单
-- 兼容策略：
--   * Max 2022~2024：menuMan API 直接持久化到 MaxStartUI.cui
--   * Max 2025+：menuMan 仍可用（向后兼容），但新 CUI 系统会接管，
--     Autodesk 建议 2025+ 用 callbacks.notificationParam #cuiRegisterMenus，
--     这里继续用 menuMan 以减少分支，2025+ 实测可工作（Autodesk 保留兼容层）。
fn _maxagent_register_menu = (
    try (
        local _mainMenu = menuMan.getMainMenuBar()
        if _mainMenu == undefined do (
            messagebox "无法获取主菜单栏，菜单注册跳过" title:"MaxAgent"
            return false
        )

        -- 若已存在同名菜单，先移除（避免重复）
        local _exist = -1
        for i = 1 to _mainMenu.numItems() while _exist == -1 do (
            local _it = _mainMenu.getItem i
            if _it != undefined and _it.getTitle() == "MaxAgent" do _exist = i
        )
        if _exist > 0 do (
            _mainMenu.removeItemByPosition _exist
        )

        -- 创建 MaxAgent 菜单
        local _menu = menuMan.createMenu "MaxAgent"

        -- 添加菜单项（每个 macroScript 用 createActionItem 包装）
        local _show = menuMan.createActionItem "MaxAgent_Show" "MaxAgent"
        _show.setUseCustomTitle true
        _show.setTitle "显示面板"
        _menu.addItem _show -1

        local _toggle = menuMan.createActionItem "MaxAgent_Toggle" "MaxAgent"
        _toggle.setUseCustomTitle true
        _toggle.setTitle "切换面板"
        _menu.addItem _toggle -1

        _menu.addItem (menuMan.createSeparatorItem()) -1

        local _open = menuMan.createActionItem "MaxAgent_OpenInstallDir" "MaxAgent"
        _open.setUseCustomTitle true
        _open.setTitle "打开安装目录..."
        _menu.addItem _open -1

        local _about = menuMan.createActionItem "MaxAgent_About" "MaxAgent"
        _about.setUseCustomTitle true
        _about.setTitle "关于 MaxAgent..."
        _menu.addItem _about -1

        _menu.addItem (menuMan.createSeparatorItem()) -1

        local _uninst = menuMan.createActionItem "MaxAgent_Uninstall" "MaxAgent"
        _uninst.setUseCustomTitle true
        _uninst.setTitle "卸载 MaxAgent..."
        _menu.addItem _uninst -1

        -- 把菜单挂到主菜单栏的最末尾
        local _sub = menuMan.createSubMenuItem "MaxAgent" _menu
        _mainMenu.addItem _sub -1

        -- 持久化：保存到当前 CUI 文件，重启 Max 自动恢复
        menuMan.updateMenuBar()
        try (menuMan.saveMenuFile (menuMan.getMenuFile())) catch ()

        true
    ) catch (
        local err = getCurrentException()
        messagebox ("菜单注册失败（已自动跳过）:\n" + err + "\n\n你仍可在 Customize > Customize User Interface > Category: MaxAgent 中找到所有命令。") title:"MaxAgent"
        false
    )
)

-- 弹出选择对话框，让用户选择安装方式
-- 返回：1 = 装菜单（默认推荐）, 2 = 仅注册宏, 3 = 取消（什么都不装）
fn _maxagent_ask_install_mode = (
    -- yesNoCancelBox: yes=1, no=2, cancel=3
    local msg = "MaxAgent 已安装到本机。\n\n请选择快捷入口安装方式：\n\n"
    msg += "[是]   在主菜单栏末尾添加 \"MaxAgent\" 菜单（推荐）\n"
    msg += "[否]   仅注册命令到 Customize 类别 \"MaxAgent\"\n"
    msg += "         （需自行去 Customize User Interface 拖按钮 / 加快捷键）\n\n"
    msg += "[取消] 跳过快捷入口注册\n"
    msg += "         （仅本次会话可用，重启 Max 后需手动 Run Script）"
    local r = yesNoCancelBox msg title:"MaxAgent 安装方式"
    case r of (
        #yes:    1
        #no:     2
        #cancel: 3
        default: 3
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

    -- 3) 注册 macroScript（恒定执行，不论用户选择何种入口）
    _maxagent_register_macros()

    -- 4) 询问用户安装方式并按选择执行
    local _mode = _maxagent_ask_install_mode()
    local _entryDesc = ""
    case _mode of (
        1: (
            if _maxagent_register_menu() then
                _entryDesc = "✅ 已添加到主菜单栏（顶部 \"MaxAgent\" 菜单）"
            else
                _entryDesc = "⚠️ 菜单注册失败，请用 Customize User Interface 手动添加"
        )
        2: (
            _entryDesc = "已注册命令到 Customize 类别 \"MaxAgent\"\n请去 Customize > Customize User Interface 添加按钮 / 快捷键"
        )
        3: (
            _entryDesc = "未注册快捷入口（仅本次会话可用）"
        )
    )

    -- 5) 立即显示面板
    try (
        python.execute "import maxagent.startup as _s; _s.show_panel(force=True)"
    ) catch (
        local err = getCurrentException()
        messagebox ("✅ MaxAgent 已安装到:\n" + installDir + "\n\n" + _entryDesc + "\n\n但首次启动失败:\n" + err + "\n\n请重启 3ds Max 后从菜单/快捷方式打开") title:"MaxAgent"
        return true
    )

    messagebox ("✅ MaxAgent 安装成功 (" + abi + ")\n\n安装目录:\n" + installDir + "\n\n" + _entryDesc) title:"MaxAgent"
    true
)

-- mzp 拖入时自动执行
maxagent_mzp_install()
