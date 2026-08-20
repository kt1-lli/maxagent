// -*- coding: utf-8 -*-
// AnimalRetargeter.cpp
// 插件 DLL 入口与 MaxScript 函数注册

#include "AnimalRetargeter.h"
#include "core/RetargetEngine.h"
#include "maxscript/MaxScriptBindings.h"

#include <max.h>
#include <plugapi.h>

HINSTANCE hInstance = nullptr;

namespace AnimalRetargeter {

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID /*reserved*/) {
    if (reason == DLL_PROCESS_ATTACH) {
        hInstance = hinst;
        DisableThreadLibraryCalls(hinst);
    }
    return TRUE;
}

__declspec(dllexport) const TCHAR* LibDescription() {
    return _T("AnimalRetargeter - 类 Human IK 骨骼重定向系统（支持动物骨架）");
}

__declspec(dllexport) int LibNumberClasses() {
    // 当前无独立 ClassDesc 对象（以 MaxScript 函数为主），返回 0
    return 0;
}

__declspec(dllexport) ClassDesc* LibClassDesc(int /*i*/) {
    return nullptr;
}

__declspec(dllexport) int LibVersion() {
    return LIBRARY_VERSION_MAJOR;
}

__declspec(dllexport) const TCHAR* LibName() {
    return _T("AnimalRetargeter");
}

// 由 Max 在加载插件时调用
__declspec(dllexport) int LibInitialize() {
    if (!RegisterMaxScriptFunctions()) {
        return 0;
    }
    // 初始化全局引擎单例
    Retarget::RetargetEngine::instance();
    return 1;
}

// 由 Max 在卸载插件时调用
__declspec(dllexport) int LibShutdown() {
    UnregisterMaxScriptFunctions();
    Retarget::RetargetEngine::destroy();
    return 1;
}

} // namespace AnimalRetargeter
