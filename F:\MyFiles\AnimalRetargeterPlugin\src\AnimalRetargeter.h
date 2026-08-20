// -*- coding: utf-8 -*-
// AnimalRetargeter.h
// 类 Human IK 重定向系统插件入口头文件（支持动物骨骼）
// 定义插件唯一标识、导出宏与全局函数注册声明

#pragma once

#include <max.h>
#include <plugapi.h>

// 插件唯一 Class ID（请使用 gencid 生成并在发布时固定）
#define ANIMALRETARGETER_CLASS_ID Class_ID(0x7a2b3d11, 0x0c5f18e4)

// DLL 导出宏
#ifdef _ANIMALRETARGETER_EXPORTS
    #define ANIMALRETARGETER_API __declspec(dllexport)
#else
    #define ANIMALRETARGETER_API __declspec(dllimport)
#endif

// MaxScript 全局函数前缀，pymxs 可通过 runtime.animalRetargeter_* 调用
namespace AnimalRetargeter {
    // 插件生命周期
    BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved);
    __declspec(dllexport) const TCHAR* LibDescription();
    __declspec(dllexport) int LibNumberClasses();
    __declspec(dllexport) ClassDesc* LibClassDesc(int i);
    __declspec(dllexport) int LibVersion();
    __declspec(dllexport) const TCHAR* LibName();

    // 注册 MaxScript 函数（在 LibInitialize 中调用）
    bool RegisterMaxScriptFunctions();
    bool UnregisterMaxScriptFunctions();
}
