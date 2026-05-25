-- ----------------------------------------------------------------------
-- MaxAgent mzp 安装钩子（dropScript）
--
-- Autodesk mzp 协议（实测 + 官方文档）摘要：
--   * mzp.run 是**指令序列**（不是 INI；之前实现按 INI 解析，导致 Max
--     完全识别不了，整个 mzp 静默失败 —— "拖入没反应"）。
--   * 拖入时 Max 只执行 `drop` 指定的那 1 个 .ms；`run` 指令在拖入
--     上下文里被无条件忽略。所以本脚本必须当 dropScript 用，文件名
--     必须出现在 mzp.run 的 `drop "..."` 行里。
--
-- mzp.run 让 Max 解压 mzp 内容到 (getDir #temp)\<mzp 文件名>\，然后调用
-- 本脚本（drop 指令指定）。本脚本通过扫 (getDir #temp)\maxagent-*\ 找到
-- 真实解压根目录，再以此为基准定位 sibling 文件（runtime\cpXX\、macros\
-- 、shared\）。不再依赖 installerSource 或 getSourceFileName()——后者实测
-- 在 Max 2022 CHS 的 drop 上下文里返回空，是上一版"完全没反应"的根因。
--
-- 流程（一气呵成，不再拆 install/run 两阶段）：
--   1. 探测当前 Max 内嵌 Python 版本，从 runtime\cpXX\ 选择对应产物
--   2. 拷贝到用户脚本目录 %LOCALAPPDATA%\Autodesk\3dsMax\<ver>\<lang>\scripts\maxagent\
--      （<lang> 由 getDir #userScripts 自动解析：中文版 zh-CN / 英文版 ENU）
--   3. 注入 sys.path 并立即可用
--   4. 拷贝 UTF-8 BOM .mcr 到 usermacros，fileIn 注册 macroScript
--   5. 询问用户安装方式：菜单 / 仅宏 / 跳过
--   6. 最后 import maxagent 并 show_panel(force=True) —— 把面板拉起来
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
-- mzp.run 里写了 `extract to "$temp\\maxagent_install"`，Max 会把整个
-- mzp 解压到 `(getDir #temp) + "\\maxagent_install\\"` 这个固定路径——
-- 这是**首选**，目录确定性最高，不依赖命名规律也不依赖文件时间戳。
--
-- 其他 fallback 只是保险（如 mzp.run 的 extract 指令在某些 Max 版本上
-- 不生效时兜底）：
--   2. 兜底：扫 `getDir #temp` 下所有一级子目录，找含 `runtime\` 的那个
--      （不依赖目录命名规律，只认结构 sentinel）。
--   3. installerSource 全局变量（mzp 协议可选注入，Max 2022 CHS 实测为空）
--   4. getSourceFileName()（开发态 fileIn 调试用；drop 上下文返回空）
fn _maxagent_resolve_mzp_root = (
    local rootDir = undefined

    -- 1) 首选：mzp.run extract to 指定的固定目录 #temp\maxagent_install
    try (
        local _temp = getDir #temp
        if _temp != undefined and _temp != "" do (
            local _candidate = (trimRight _temp "\\") + "\\maxagent_install"
            if doesDirectoryExist _candidate do rootDir = _candidate
        )
    ) catch ()

    -- 2) 兜底：遍历 #temp 下所有一级子目录，找含 runtime\ 的那个
    if rootDir == undefined or rootDir == "" do (
        try (
            local _temp = getDir #temp
            if _temp != undefined and _temp != "" do (
                _temp = trimRight _temp "\\"
                local _allSubs = getDirectories (_temp + "\\*\\")
                if _allSubs != undefined and _allSubs.count > 0 do (
                    for _c in _allSubs while rootDir == undefined do (
                        local _trimmed = trimRight _c "\\"
                        if doesDirectoryExist (_trimmed + "\\runtime") do
                            rootDir = _trimmed
                    )
                )
            )
        ) catch ()
    )

    -- 3) installerSource 兜底
    if rootDir == undefined or rootDir == "" do (
        try (
            if (isProperty ::globalVars #installerSource) do (
                local _src = ::installerSource
                if _src != undefined and _src != "" do (
                    if doesFileExist _src then
                        rootDir = getFilenamePath _src
                    else if doesDirectoryExist _src then
                        rootDir = _src
                )
            )
        ) catch ()
    )

    -- 4) getSourceFileName() 末位兜底（开发态 fileIn 调试用）
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
--
-- 关键实现细节（按 Autodesk 官方文档 "Executing Python from MAXScript"）：
--   * `python.execute "..."` **不返回 Python 表达式的值**——它返回执行
--     状态枚举 `#success` / `#pathError` / `#initError` / `#scriptFileError`
--     / `#executeError`。早期实现直接把它当成 abi 字符串用，导致每次都
--     报"不支持的 Python 版本: success"。
--   * `python.GetVariable` **不存在**——MAXScript Python 接口只有
--     Import / Reload / Init / Execute / ExecuteFile / GetLastError，
--     没有变量回读 API（实测在 Max 2022 抛 "未知属性 GetVariable"）。
--   * **正确做法**（文档明示）：用 `Python.Import "sys"` 拿到模块
--     wrapper，wrapper 的属性可以直接当 MaxScript 值读：
--         _sys = Python.Import "sys"
--         _sys.version_info.major   -- 直接返回 int
--   * 失败时返回 undefined，调用方负责给出含诊断信息的错误提示。
fn _maxagent_detect_python_abi = (
    local pyTag = undefined
    try (
        local _sys = Python.Import "sys"
        local _vi = _sys.version_info
        local _major = _vi.major as integer
        local _minor = _vi.minor as integer
        if _major != undefined and _minor != undefined do
            pyTag = "cp" + (_major as string) + (_minor as string)
    ) catch (
        pyTag = undefined
    )
    if pyTag == undefined or (classOf pyTag) != String do return undefined
    if (matchPattern pyTag pattern:"cp[0-9]*") == false do return undefined
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
        _msg += "诊断信息（三层 fallback 全失败）:\n"
        _msg += "  1) #temp 下未找到含 runtime\\ 的子目录\n"
        local _tempDir = ""
        try (_tempDir = getDir #temp) catch ()
        _msg += "     #temp = " + _tempDir + "\n"
        -- 列出 #temp 实际能看到的前 8 个子目录，便于排查
        try (
            local _subs = getDirectories ((trimRight _tempDir "\\") + "\\*\\")
            if _subs != undefined and _subs.count > 0 then (
                _msg += "     #temp 当前子目录（前 8 个）:\n"
                local _n = amin _subs.count 8
                for i = 1 to _n do (
                    _msg += "       - " + (filenameFromPath (trimRight _subs[i] "\\")) + "\n"
                )
            ) else (
                _msg += "     #temp 下当前没有任何子目录\n"
            )
        ) catch (_msg += "     （列举 #temp 子目录失败）\n")
        _msg += "  2) installerSource = "
        try (_msg += (::installerSource as string)) catch (_msg += "(undefined)")
        _msg += "\n  3) getSourceFileName() = \"" + (getSourceFileName() as string) + "\"\n\n"
        _msg += "可能原因:\n"
        _msg += "  • mzp 文件损坏 / 不完整\n"
        _msg += "  • Max 拒绝了拖入操作（请确认 .mzp 不在压缩包内）\n\n"
        _msg += "解决方法:\n"
        _msg += "  • 重新下载完整 mzp\n"
        _msg += "  • 或者：把 mzp 当 zip 解压，进入解压目录后\n"
        _msg += "    在 MAXScript 菜单选 Run Script，选 mzp_install.ms"
        messagebox _msg title:"MaxAgent 安装失败"
        return false
    )

    local abi = _maxagent_detect_python_abi()
    if abi == undefined then (
        local msg = "❌ 无法探测 Python 版本（Python.Import \"sys\" 调用失败）\n\n"
        msg += "请把以下信息反馈给开发者：\n"
        msg += "  • 3ds Max 版本: " + (maxVersion())[1] as string + "\n"
        msg += "  • 系统语言目录: " + getDir #userScripts
        messagebox msg title:"MaxAgent 安装失败"
        return false
    )
    if findItem g_maxagent_supported_abis abi == 0 then (
        local msg = "❌ 不支持的 Python 版本: " + abi + "\n\n"
        msg += "MaxAgent 0.4 系列支持: 3ds Max 2022~2027\n"
        msg += "对应 Python: 3.7 / 3.9 / 3.10 / 3.11 / 3.13"
        messagebox msg title:"MaxAgent 安装失败"
        return false
    )

    local srcPkg = (g_maxagent_mzp_root + "\\runtime\\" + abi + "\\maxagent")
    -- 用 MaxScript 原生 API 判存在性，避免重蹈 python.execute 不返回
    -- 表达式值的覆辙（getDirectories 在路径存在时返回非空数组）
    local pkgDirs = getDirectories (srcPkg + "\\")
    if pkgDirs == undefined or pkgDirs.count == 0 then (
        messagebox ("❌ mzp 内缺少 " + abi + " 产物，请下载完整版本\n\n查找路径:\n" + srcPkg) title:"MaxAgent 安装失败"
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

    -- 5) 拉起 AI 面板
    --
    -- 之前版本把 show_panel 放在独立的 mzp_run.ms 里，靠 mzp.run 的 [run]
    -- 阶段触发——但 Autodesk mzp 协议**没有 [run] 段**，那是上一版按 INI
    -- 格式臆想出来的（参见本文件头注释）。drop 上下文下 Max 只执行 1 个
    -- dropScript，所以现在合并到这里一气呵成。
    --
    -- 用 try 包住 import + show_panel：万一面板代码出错，至少安装本身
    -- 是成功的，用户能看到下面的成功提示，而不是被 Python 异常打断。
    local _panelOk = true
    local _panelErr = ""
    try (
        -- 走 maxagent.startup.show_panel(force=True)，与菜单"显示面板"
        -- 共用同一条入口，行为一致。force=True 表示哪怕配置里关掉了
        -- auto_show_on_startup 也强制弹出（mzp 安装完成时用户预期看到）。
        python.execute "import maxagent.startup as _s; _s.show_panel(force=True)"
    ) catch (
        _panelOk = false
        _panelErr = getCurrentException()
    )

    -- 6) 显示最终结果
    local _final = ""
    if _panelOk then (
        _final = "✅ MaxAgent 安装成功 (" + abi + ")\n\n"
        _final += "安装目录:\n" + installDir + "\n\n"
        _final += _entryDesc + "\n\n"
        _final += "💡 面板已弹出。下次启动 Max 时，菜单/宏会自动加载。"
    ) else (
        _final = "⚠️ MaxAgent 已安装，但面板自动弹出失败 (" + abi + ")\n\n"
        _final += "安装目录:\n" + installDir + "\n\n"
        _final += _entryDesc + "\n\n"
        _final += "异常信息:\n" + _panelErr + "\n\n"
        _final += "你可以在 MaxAgent 菜单选 \"显示面板\" 手动打开。"
    )
    messagebox _final title:"MaxAgent"
    _panelOk
)

-- mzp 拖入时自动执行
maxagent_mzp_install()
