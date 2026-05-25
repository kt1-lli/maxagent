-- ----------------------------------------------------------------------
-- MaxAgent mzp 运行钩子（[run] 阶段）
--
-- 该脚本由 mzp.run 清单的 [run] 段触发，时机为 mzp_install.ms 全部执行
-- 完毕之后。此时：
--   * 用户脚本目录里的 maxagent/ 包已就位
--   * sys.path 已注入
--   * 三个 macroScript（Show / Toggle / Uninstall …）已注册
--   * 主菜单（如用户选了菜单方式）已挂载
--
-- 我们在这里才真正去 import maxagent 并 show_panel。把 UI 创建从
-- mzp 解压上下文里搬出来，避开 mzp 解压期事件循环与 Qt 主循环抢线程。
--
-- 设计要点：
--   1. 失败时**不再静默吞**——把真实异常 messagebox 出来，便于诊断
--   2. 用 g_show_max_agent() 全局函数 而非 import startup，二者等价
--      但前者由 maxagent.__init__ 暴露，跟菜单"显示面板"项走同一条
--      代码路径，行为一致
--   3. 整个过程包在 try 里，即使面板拉不起来也不影响 Max 自身
-- ----------------------------------------------------------------------

(
    -- 用 quietBox 风格：失败也不打断 Max，只汇报
    local _err = undefined
    try (
        -- 优先走 startup.show_panel(force=True)，与菜单 / 快捷键 入口共用
        -- 同一条路径。force=True 表示哪怕配置里 auto_show_on_startup=False
        -- 也强制弹出（mzp 安装完成后用户预期看到面板）。
        python.execute "import maxagent.startup as _s; _s.show_panel(force=True)"
    ) catch (
        _err = getCurrentException()
    )

    if _err != undefined do (
        local _msg = "MaxAgent 已安装，但首次启动面板时出错：\n\n"
        _msg += (_err as string)
        _msg += "\n\n你仍可通过以下方式手动启动：\n"
        _msg += "  • 主菜单 \"MaxAgent\" → \"显示面板\"\n"
        _msg += "  • 在 MAXScript Listener 输入 g_show_max_agent()\n"
        _msg += "  • Customize User Interface → Category: MaxAgent\n\n"
        _msg += "如问题持续，请重启 3ds Max 后再试。"
        messagebox _msg title:"MaxAgent 启动失败"
    )
)
