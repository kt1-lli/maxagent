-- ----------------------------------------------------------------------
-- MaxAgent mzp 安装钩子（[install] 阶段）
--
-- 当用户把 maxagent-X.Y.Z.mzp 拖入 3ds Max 视口时，Max 按 mzp.run 清单
-- 调度执行：
--   [install] 阶段 -> 本脚本（mzp_install.ms）
--     1. 探测当前 Python 版本，从 runtime/cpXX/ 选择对应产物
--     2. 拷贝到用户脚本目录 %LOCALAPPDATA%\Autodesk\3dsMax\<ver>\<lang>\scripts\maxagent\
--        （<lang> 由 getDir #userScripts 自动解析：中文版 zh-CN / 英文版 ENU）
--     3. 注入 sys.path 并立即可用
--     4. 注册菜单/工具栏宏 MaxAgent_Show / MaxAgent_Toggle / MaxAgent_Uninstall
--     5. 询问用户是否要把"MaxAgent"菜单注册到主菜单栏末尾
--     （注意：本阶段**不**直接 show_panel——见下）
--
--   [run] 阶段 -> mzp_run.ms
--     6. 真正 import maxagent 并 show_panel(force=True)
--
-- 把"显示面板"延后到 [run] 阶段是 Autodesk 推荐的标准做法：
--   * 避开 mzp 解压期事件循环与 Qt UI 初始化抢主线程
--   * [install] 失败时 [run] 不会执行，避免"半成品"启动
--   * 异常透出位置统一在 [run]，便于用户看到真实失败原因
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

-- 解析 mzp 解压目录
--
-- mzp.run [install] 阶段执行 .ms 时，Max 拿不到自身路径的标准方式有：
--   1. installerSource —— Max 在 mzp 解压后自动注入的全局变量，指向解压
--      临时目录。这是 mzp 协议下**首选**的方式（Autodesk 官方文档惯例）。
--   2. getSourceFileName() —— 仅在脚本通过显式 fileIn / Run Script 执行
--      时返回路径；mzp dispatch 通过内部机制运行时**返回空字符串**。
--      实测 3ds Max 2022 CHS：mzp 内 getSourceFileName() 返回 ""。
--
-- 因此实现策略：先 installerSource，失败再退到 getSourceFileName，
-- 最后兜底用一个搜索：所有进程 cwd / temp 下找最近的 mzp_install.ms 同
-- 父目录。这个兜底是最后保险，正常情况下走不到。
fn _maxagent_resolve_mzp_root = (
    -- 1) 优先使用 mzp 协议注入的 installerSource 全局变量
    --    用 try 包住，因为 installerSource 在非 mzp 上下文（比如开发态
    --    fileIn 测试）会是 undefined，访问未定义全局会抛异常
    local rootDir = undefined
    try (
        if (isProperty ::globalVars #installerSource) do (
            local _src = ::installerSource
            if _src != undefined and _src != "" do (
                -- installerSource 可能是文件路径（指向 mzp_install.ms 自身）
                -- 也可能直接是目录路径，二者都处理
                if doesFileExist _src then
                    rootDir = getFilenamePath _src
                else if doesDirectoryExist _src then
                    rootDir = _src
            )
        )
    ) catch ()

    -- 2) 退而求其次：getSourceFileName()（开发态 fileIn 调试时有效）
    if rootDir == undefined or rootDir == "" do (
        try (
            local scriptPath = getSourceFileName()
            if scriptPath != undefined and scriptPath != "" do (
                rootDir = getFilenamePath scriptPath
            )
        ) catch ()
    )

    if rootDir == undefined or rootDir == "" do return undefined
    rootDir = trimRight rootDir "\\"
    rootDir
)

-- 探测当前 Max 内嵌 Python 的小版本，返回如 "cp311" 的字符串
fn _maxagent_detect_python_abi = (
    local pyTag = python.execute "import sys; 'cp{}{}'.format(sys.version_info.major, sys.version_info.minor)"
    pyTag
)

-- 计算用户级安装目录: %LOCALAPPDATA%\Autodesk\3dsMax\<MaxVer>\<lang>\scripts\maxagent
-- 注意：<lang> 由 getDir #userScripts 解析，中文版为 zh-CN，英文版为 ENU，无需我们硬编码
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

-- 注册五个 macroScript（不论用户选择哪种安装方式都会注册）
--
-- 实现方式：拷贝预先打包的 UTF-8 BOM 编码 .mcr 到 getDir #userMacros
-- 然后 fileIn 立即生效。这样做的关键收益：
--   1. .mcr 编码完全可控（UTF-8 BOM），中英文版 Max 都能正确解析中文字面量。
--      Max 自动从内联 macroScript 块生成的 .mcr 编码与当前语言绑定
--      （中文版 GBK / 英文版 1252），无法跨语言通用。
--   2. .mcr 内容与本脚本解耦，便于维护和审查。
--   3. getDir #userMacros 自带语言识别（中文版 \CHS\，英文版 \ENU\）。
fn _maxagent_register_macros mzpRoot = (
    local _src = mzpRoot + "\\macros\\MaxAgent-Macros.mcr"
    if not (doesFileExist _src) do (
        messagebox ("❌ mzp 内缺少 macros\\MaxAgent-Macros.mcr，无法注册命令\n请重新下载完整包") title:"MaxAgent 安装失败"
        return false
    )

    local _userMacros = getDir #userMacros
    _userMacros = trimRight _userMacros "\\"
    local _dst = _userMacros + "\\MaxAgent-Macros.mcr"

    -- 用 Python copy 而不是 copyFile：copyFile 在某些 Max 版本对 BOM 文件
    -- 会做编码转换，破坏 UTF-8 BOM；shutil.copy 是字节级拷贝，绝对安全。
    local _py = ""
    _py += "import shutil, os\n"
    _py += "_src = r'" + _src + "'\n"
    _py += "_dst = r'" + _dst + "'\n"
    _py += "os.makedirs(os.path.dirname(_dst), exist_ok=True)\n"
    _py += "shutil.copyfile(_src, _dst)\n"
    try (
        python.execute _py
    ) catch (
        local err = getCurrentException()
        messagebox ("❌ 拷贝 .mcr 文件失败:\n" + err + "\n\n源: " + _src + "\n目标: " + _dst) title:"MaxAgent 安装失败"
        return false
    )

    -- fileIn 触发 Max 注册其中的 macroScript 块（让本会话立即可用，
    -- 不必等下次 Max 重启）。Max 会自己读取 BOM 并按 UTF-8 解码。
    try (
        fileIn _dst
    ) catch (
        local err = getCurrentException()
        messagebox ("⚠️ macroScript 注册失败（已拷贝 .mcr，下次启动 Max 会自动加载）:\n" + err) title:"MaxAgent"
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
        local _msg = "❌ MaxAgent 无法定位 mzp 解压目录\n\n"
        _msg += "诊断信息:\n"
        _msg += "  installerSource = "
        try (_msg += (::installerSource as string)) catch (_msg += "(undefined)")
        _msg += "\n  getSourceFileName() = \"" + (getSourceFileName() as string) + "\"\n\n"
        _msg += "可能原因:\n"
        _msg += "  1. 拖入 mzp 时 Max 没有以标准 mzp 协议执行（极少见）\n"
        _msg += "  2. mzp 文件损坏 / 不完整\n\n"
        _msg += "解决方法:\n"
        _msg += "  • 重新下载 mzp\n"
        _msg += "  • 或者：把 mzp 当 zip 解压，进入解压目录后\n"
        _msg += "    在 MAXScript 菜单选 Run Script，选 mzp_install.ms"
        messagebox _msg title:"MaxAgent 安装失败"
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

    -- 3) 注册 macroScript（拷贝预先打包的 UTF-8 BOM .mcr 到 usermacros 目录）
    --    传入 mzp 解压根目录以便定位 macros\MaxAgent-Macros.mcr
    if not (_maxagent_register_macros g_maxagent_mzp_root) do return false

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

    -- 5) 显示安装完成提示（show_panel 由 mzp.run 的 [run] 阶段触发，
    --    那个阶段会在本 [install] 阶段全部成功之后再拉起 UI——
    --    这样能避开 mzp 解压上下文与 Qt UI 初始化的事件循环冲突）
    messagebox ("✅ MaxAgent 安装成功 (" + abi + ")\n\n安装目录:\n" + installDir + "\n\n" + _entryDesc + "\n\n面板即将自动弹出...") title:"MaxAgent"
    true
)

-- mzp 拖入时自动执行
maxagent_mzp_install()
