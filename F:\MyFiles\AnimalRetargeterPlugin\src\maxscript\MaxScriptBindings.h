// -*- coding: utf-8 -*-
// MaxScriptBindings.h
// 将 C++ 引擎能力注册为 MaxScript 全局函数，供 pymxs 透明调用
// 命名约定：animalRetargeter_*

#pragma once

#include <max.h>

namespace AnimalRetargeter {

// 注册/注销所有 MaxScript 函数
bool RegisterMaxScriptFunctions();
bool UnregisterMaxScriptFunctions();

} // namespace AnimalRetargeter
