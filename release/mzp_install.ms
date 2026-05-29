-- ----------------------------------------------------------------------
-- MaxAgent mzp 安装钩子（dropScript）
--
-- 拖入 mzp 后由 Max 调用本脚本，完成：
--   1. 探测当前 Max 内嵌 Python 版本，从 runtime\cpXX\ 选择对应产物
--   2. 拷贝到用户脚本目录 %LOCALAPPDATA%\Autodesk\3dsMax\<ver>\<lang>\scripts\maxagent\
--   3. 注入 sys.path 并立即可用
--   4. 拷贝 UTF-8 BOM .mcr 到 usermacros，fileIn 注册 macroScript
--   5. 询问用户安装方式：菜单 / 仅宏 / 跳过
--   6. import maxagent 并 show_panel(force=True)
--
-- 兼容范围: 3ds Max 2022 / 2023 / 2024 / 2025 / 2025.1 / 2026 / 2027
--           Python 3.7 / 3.9 / 3.10 / 3.11 / 3.13
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
-- 主入口：mzp.run 里 `extract to "$temp\\maxagent_install"` 把内容
-- 解压到固定目录 (getDir #temp)\maxagent_install\，本函数据此定位。
-- 其他三个分支为兜底，覆盖 extract 指令未生效或非 drop 上下文场景。
fn _maxagent_resolve_mzp_root = (
    local rootDir = undefined

    -- 1) 首选：固定解压目录
    try (
        local _temp = getDir #temp
        if _temp != undefined and _temp != "" do (
            local _candidate = (trimRight _temp "\\") + "\\maxagent_install"
            if doesDirectoryExist _candidate do rootDir = _candidate
        )
    ) catch ()

    -- 2) 兜底：扫 #temp 下含 runtime\ 的子目录
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

    -- 4) getSourceFileName() 兜底
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
--   _sys = Python.Import "sys"
--   _vi  = _sys.version_info
--   pyTag = "cp" + (_vi.major as integer as string) + (_vi.minor as integer as string)
--
-- 失败时返回 undefined，调用方负责给出诊断弹窗。
fn _maxagent_detect_python_abi = (
    local pyTag = undefined
    try (
        local _sys = Python.Import "sys"
        local _vi = _sys.version_info
        local _major = _vi.major as integer
        local _minor = _vi.minor as integer
        pyTag = "cp" + (_major as string) + (_minor as string)
    ) catch (
        pyTag = undefined
    )
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

-- 取当前 Max 主版本号（如 2024 返回 26000，2025 返回 27000，2026 返回 28000）
-- 2025 是菜单系统分水岭：>= 27000 走新 CUI Menu System（cuiRegisterMenus 回调）
fn _maxagent_max_version = (
    local _v = 0
    try (_v = (maxVersion())[1] as integer) catch ()
    _v
)

-- 旧版菜单注册（Max 2022~2024）：menuMan API 直接挂主菜单栏
fn _maxagent_register_menu_legacy = (
    local _mainMenu = menuMan.getMainMenuBar()
    if _mainMenu == undefined do return false

    -- 若已存在同名菜单，先移除（避免重复）
    local _exist = -1
    for i = 1 to _mainMenu.numItems() while _exist == -1 do (
        local _it = _mainMenu.getItem i
        if _it != undefined and _it.getTitle() == "MaxAgent" do _exist = i
    )
    if _exist > 0 do (
        _mainMenu.removeItemByPosition _exist
    )

    local _menu = menuMan.createMenu "MaxAgent"

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

    _menu.addItem (menuMan.createSeparatorItem()) -1

    local _uninst = menuMan.createActionItem "MaxAgent_Uninstall" "MaxAgent"
    _uninst.setUseCustomTitle true
    _uninst.setTitle "卸载 MaxAgent..."
    _menu.addItem _uninst -1

    local _sub = menuMan.createSubMenuItem "MaxAgent" _menu
    _mainMenu.addItem _sub -1

    -- 持久化：保存到当前 CUI 文件，重启 Max 自动恢复
    menuMan.updateMenuBar()
    try (menuMan.saveMenuFile (menuMan.getMenuFile())) catch ()
    true
)

-- 新版菜单回调脚本（Max 2025+）
-- 关键差异（与旧 menuMan API 对照）：
--   * 通过 callbacks.notificationParam() 拿 CuiMenuManager（不能再用 menuMan.getMainMenuBar）
--   * mainMenuBar.CreateSubMenu <GUID> <title>
--   * subMenu.CreateAction <GUID> <actionTableId> "<macroName>`<category>"
--     actionTableId 固定 647394 = 用户 macroScript 默认 ActionTable
--   * subMenu.CreateSeparator <GUID>
--   * 回调返回后系统自动重建菜单，不要再调 menuMan.updateMenuBar / saveMenuFile
--
-- 5 个固定 GUID：菜单本身 + 4 个菜单项 + 2 个分隔符（共 7 个）
-- 硬编码而非 genGUID()，保证菜单系统跨会话能正确去重
fn _maxagent_modern_menu_script = (
    local s = ""
    s += "fn _maxagent_cui_menu_cb = (\n"
    s += "  try (\n"
    s += "    local _mgr = callbacks.notificationParam()\n"
    s += "    if _mgr == undefined do return false\n"
    s += "    local _bar = _mgr.mainMenuBar\n"
    s += "    if _bar == undefined do return false\n"
    s += "    -- 用户 macroScript 默认注册的 ActionTable ID\n"
    s += "    local _tableId = 647394\n"
    s += "    -- 菜单及子项的稳定 GUID（任意 16 字节，保证唯一即可）\n"
    s += "    local _gMenu  = \"6F2A1E4D-7C3B-4A91-B5E2-1D8F5A0C9E11\"\n"
    s += "    local _gShow  = \"6F2A1E4D-7C3B-4A91-B5E2-1D8F5A0C9E12\"\n"
    s += "    local _gTog   = \"6F2A1E4D-7C3B-4A91-B5E2-1D8F5A0C9E13\"\n"
    s += "    local _gSep1  = \"6F2A1E4D-7C3B-4A91-B5E2-1D8F5A0C9E14\"\n"
    s += "    local _gOpen  = \"6F2A1E4D-7C3B-4A91-B5E2-1D8F5A0C9E15\"\n"
    s += "    local _gSep2  = \"6F2A1E4D-7C3B-4A91-B5E2-1D8F5A0C9E16\"\n"
    s += "    local _gUnin  = \"6F2A1E4D-7C3B-4A91-B5E2-1D8F5A0C9E17\"\n"
    s += "\n"
    s += "    local _menu = _bar.CreateSubMenu _gMenu \"MaxAgent\"\n"
    s += "    if _menu == undefined do return false\n"
    s += "    _menu.CreateAction _gShow _tableId \"MaxAgent_Show`MaxAgent\" title:\"显示面板\"\n"
    s += "    _menu.CreateAction _gTog  _tableId \"MaxAgent_Toggle`MaxAgent\" title:\"切换面板\"\n"
    s += "    _menu.CreateSeparator _gSep1\n"
    s += "    _menu.CreateAction _gOpen _tableId \"MaxAgent_OpenInstallDir`MaxAgent\" title:\"打开安装目录...\"\n"
    s += "    _menu.CreateSeparator _gSep2\n"
    s += "    _menu.CreateAction _gUnin _tableId \"MaxAgent_Uninstall`MaxAgent\" title:\"卸载 MaxAgent...\"\n"
    s += "    true\n"
    s += "  ) catch (false)\n"
    s += ")\n"
    s += "callbacks.removeScripts id:#MaxAgentMenu\n"
    s += "callbacks.addScript #cuiRegisterMenus \"_maxagent_cui_menu_cb()\" id:#MaxAgentMenu\n"
    -- 关键：主动触发一次 LoadConfiguration，让 #cuiRegisterMenus 信号重发
    --
    -- 为什么必需：
    --   Max 启动时序：CUI 初始化 → #cuiRegisterMenus 触发（T2）→ 主菜单渲染 →
    --   startup 脚本 fileIn（T4）→ 用户可交互。
    --   本脚本是在 T4 才被 fileIn 的，此刻才 addScript 已经错过了 T2 那一波
    --   #cuiRegisterMenus 信号，导致菜单不会自动注册到主菜单栏。后续其他
    --   触发（如停靠改变）虽然会重新触发回调，但菜单系统会把这种"菜单已渲染
    --   完成后才创建"的子菜单渲染为悬浮容器——这就是用户看到的"悬浮工具条"
    --   的真正根因。
    --
    -- 修复策略：
    --   addScript 之后立即调 LoadConfiguration，重新加载当前 cui 配置，
    --   菜单系统会重发一次 #cuiRegisterMenus，此时回调已注册，CreateSubMenu
    --   挂到主菜单栏，渲染正确。
    --
    -- 用 try/catch 包起来：cui 初始化未完成时调用会异常，下一次菜单系统
    -- 自然事件仍会兜底（虽然渲染位置可能偏，但不至于崩溃）。
    s += "try (\n"
    s += "  local _cuiMgr = maxOps.GetICuiMenuMgr()\n"
    s += "  if _cuiMgr != undefined do (\n"
    s += "    local _curCfg = _cuiMgr.GetCurrentConfiguration()\n"
    s += "    if _curCfg != undefined and _curCfg != \"\" do _cuiMgr.LoadConfiguration _curCfg\n"
    s += "  )\n"
    s += ") catch ()\n"
    s
)

-- 新版菜单注册（Max 2025+）：注册 cuiRegisterMenus 回调 + 持久化到 startup 脚本
-- 实现要点：
--   * 回调内部用 callbacks.notificationParam() 拿 CuiMenuManager，再走新 API
--   * 当前会话立即可见菜单：执行回调注册脚本 + LoadConfiguration 触发回调
--   * 重启后菜单仍在主菜单栏（而不是退化成悬浮工具条）的关键：
--     1) 把回调注册脚本物理落盘到 getDir #userStartupScripts\MaxAgent_Menu.ms
--     2) 该 startup 脚本内部除了 addScript 外，必须**主动调一次 LoadConfiguration**
--        重新发出 #cuiRegisterMenus 信号；这是修复"悬浮工具条"的关键。
--     3) 仅靠 addScript 不够：Max 启动时 #cuiRegisterMenus 信号在 CUI 子系统
--        初始化阶段（T2）就发完了，而 startup 脚本是在主菜单渲染完成后（T4）
--        才被 fileIn 的——错过了 T2，仅注册回调而不重发信号，子菜单要么不出现，
--        要么被后续偶发事件触发后渲染为"悬浮容器"——这就是用户看到的工具条。
--   * 不调 SaveConfiguration：cuiRegisterMenus 回调创建的菜单是"动态菜单"，
--     SaveConfiguration 反而会把它写成 schema 节点 → 下次启动菜单系统按
--     schema 还原时找不到回调对应的容器路径，节点退化为悬浮工具条。
--     这是更早一版方案的根因（与上面 T2/T4 时序根因不同，是另一种坑）。
fn _maxagent_register_menu_modern = (
    local _script = _maxagent_modern_menu_script()

    -- 1) 当前会话立即注册回调，让本会话立即出菜单
    --    脚本内部已包含 removeScripts，避免回调重复注册
    --    （如果上一版残留过 #MaxAgentMenu 回调，这里的 removeScripts 会清掉）
    execute _script

    -- 2) 物理落盘：把回调注册脚本写到 startup 目录，重启 Max 自动 fileIn
    --    getDir #userStartupScripts 解析为 %LOCALAPPDATA%\...\<lang>\scripts\Startup\
    --    Max 启动时会按字典序自动 fileIn 该目录下所有 .ms / .mcr，
    --    从而每次启动都重新注册 cuiRegisterMenus 回调，菜单永久可用。
    --
    --    编码：必须 UTF-8 BOM（与 .mcr 编码策略一致），原因是 _script 内含
    --    中文菜单标题（"显示面板"等）。MaxScript 内置 createFile 用系统
    --    codepage（中文版 GBK / 英文版 1252），跨语言会乱码。改用 Python
    --    open('w', encoding='utf-8-sig') 字节级落盘，Max fileIn 时按 BOM
    --    自动识别 UTF-8，所有语言版本都能正确读取中文字面量。
    --
    --    传值策略：先 createFile 写一个临时源文件（系统 codepage），再用
    --    Python 以 mbcs 解码、UTF-8 BOM 重新写入目标 startup 文件。这样
    --    既绕过 MaxScript→Python 大字符串变量传递的转义难题，又能保证
    --    最终 startup 脚本是纯净的 UTF-8 BOM 编码。
    local _persistOk = false
    try (
        local _startup = getDir #userStartupScripts
        _startup = trimRight _startup "\\"
        local _msPath = _startup + "\\MaxAgent_Menu.ms"

        -- 2a) 先写一个临时 .ms（系统 codepage 编码，本地可读即可）
        local _tmpPath = _startup + "\\MaxAgent_Menu.ms.tmp"
        local _f = createFile _tmpPath
        if _f == undefined then
            throw "createFile 失败"
        format "-- MaxAgent 菜单回调注册（由 mzp 安装时自动生成，卸载时自动删除）\n" to:_f
        format "-- Max 启动时自动 fileIn，重新注册 cuiRegisterMenus 回调\n" to:_f
        format "-- 不要手动编辑：内容与 mzp_install.ms::_maxagent_modern_menu_script() 同步\n\n" to:_f
        format "%" _script to:_f
        close _f

        -- 2b) 用 Python 把临时文件按 mbcs 解码、UTF-8 BOM 重新落盘
        local _py = ""
        _py += "import os\n"
        _py += "_tmp = r'" + _tmpPath + "'\n"
        _py += "_dst = r'" + _msPath + "'\n"
        _py += "with open(_tmp, 'rb') as _fh:\n"
        _py += "    _raw = _fh.read()\n"
        _py += "try:\n"
        _py += "    _txt = _raw.decode('mbcs')\n"
        _py += "except Exception:\n"
        _py += "    _txt = _raw.decode('utf-8', errors='replace')\n"
        _py += "with open(_dst, 'w', encoding='utf-8-sig', newline='\\n') as _fh:\n"
        _py += "    _fh.write(_txt)\n"
        _py += "try: os.remove(_tmp)\n"
        _py += "except Exception: pass\n"
        python.execute _py

        if doesFileExist _msPath do _persistOk = true
    ) catch (
        format "[MaxAgent] 写入 startup 脚本失败: %\n" (getCurrentException()) to:listener
    )

    -- 3) 触发一次菜单重建，让本会话立即出菜单
    --    脚本内已包含同样的 LoadConfiguration 调用（在 addScript 之后），
    --    这里再调一次纯属保险冗余——确保即使 execute _script 时机异常，
    --    本会话也能立即看到菜单。两次调用幂等（GUID 固定，菜单系统按 GUID 去重）。
    try (
        local _mgr = maxOps.GetICuiMenuMgr()
        if _mgr != undefined do (
            local _curCfg = _mgr.GetCurrentConfiguration()
            if _curCfg != undefined and _curCfg != "" do (
                _mgr.LoadConfiguration _curCfg
            )
        )
    ) catch (
        format "[MaxAgent] LoadConfiguration 异常: %\n" (getCurrentException()) to:listener
    )

    -- 写 startup 失败时打日志提醒，但不弹窗——本会话仍可用，下次启动菜单丢失
    if not _persistOk do (
        format "[MaxAgent] ⚠️ 持久化失败，重启 Max 后菜单不会自动恢复\n" to:listener
    )

    true
)

-- 在主菜单栏末尾注册 MaxAgent 菜单（按 Max 版本分发）
fn _maxagent_register_menu = (
    try (
        local _ver = _maxagent_max_version()
        local _ok = false
        if _ver >= 27000 then (
            -- Max 2025+ 走新 CUI Menu System
            _ok = _maxagent_register_menu_modern()
        ) else (
            -- Max 2022~2024 走旧 menuMan API
            _ok = _maxagent_register_menu_legacy()
        )
        if not _ok do (
            messagebox "无法获取主菜单栏，菜单注册跳过" title:"MaxAgent"
            return false
        )
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
        _msg += "诊断信息（四层 fallback 全失败）:\n"
        local _tempDir = ""
        try (_tempDir = getDir #temp) catch ()
        _msg += "  1) " + _tempDir + "\\maxagent_install 不存在\n"
        _msg += "     （mzp.run 的 extract to 指令未生效）\n"
        _msg += "  2) #temp 下未找到含 runtime\\ 的子目录\n"
        -- 列出 #temp 实际能看到的前 8 个子目录，便于排查
        try (
            local _subs = getDirectories ((trimRight _tempDir "\\") + "\\*\\")
            if _subs != undefined and _subs.count > 0 then (
                _msg += "     #temp 当前子目录（前 8 个）:\n"
                local _n = if _subs.count < 8 then _subs.count else 8
                for i = 1 to _n do (
                    _msg += "       - " + (filenameFromPath (trimRight _subs[i] "\\")) + "\n"
                )
            ) else (
                _msg += "     #temp 下当前没有任何子目录\n"
            )
        ) catch (_msg += "     （列举 #temp 子目录失败）\n")
        _msg += "  3) installerSource = "
        try (_msg += (::installerSource as string)) catch (_msg += "(undefined)")
        _msg += "\n  4) getSourceFileName() = \"" + (getSourceFileName() as string) + "\"\n\n"
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
    -- 用 MaxScript 原生 doesDirectoryExist 判存在性。
    -- 注意：之前用 getDirectories(srcPkg + "\\") 是错的——getDirectories 的
    -- 参数是 **glob 模式**，传叶子目录路径会因为目录内"没有子目录"而返回
    -- 空数组，触发"缺少产物"误报。实际 runtime\cpXX\maxagent\ 里只有
    -- .py/.pyc/.pyd 文件，没有子目录，必然命中这个坑。
    local _pkgInit = srcPkg + "\\__init__.py"
    if not (doesDirectoryExist srcPkg) or not (doesFileExist _pkgInit) then (
        local _msg = "❌ mzp 内缺少 " + abi + " 产物，请下载完整版本\n\n"
        _msg += "查找路径:\n" + srcPkg + "\n\n"
        _msg += "存在性检查:\n"
        _msg += "  • 目录: " + ((doesDirectoryExist srcPkg) as string) + "\n"
        _msg += "  • __init__.py: " + ((doesFileExist _pkgInit) as string) + "\n"
        _msg += "  • mzp 根: " + g_maxagent_mzp_root
        messagebox _msg title:"MaxAgent 安装失败"
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
