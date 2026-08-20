// -*- coding: utf-8 -*-
// BoneMapping.h
// 骨骼映射数据结构：源骨骼 -> 目标骨骼的对应关系与重定向参数
// 方案 B（骨骼无关）核心数据结构；方案 C 中对 Biped/CAT 做特例优化时复用此结构

#pragma once

#include <max.h>

#include <string>
#include <vector>

namespace Retarget {

// 单条骨骼映射
struct BoneMap {
    // 源骨骼在源骨架中的名称（或 Biped 逻辑名）
    std::string sourceName;
    // 目标骨骼在目标骨架中的名称
    std::string targetName;

    // 旋转权重（0~1），小于 1 时按权重混合到目标当前旋转
    float rotationWeight = 1.0f;
    // 位置权重（0~1），用于根骨/Hips 的位移重定向
    float positionWeight = 1.0f;

    // 旋转空间修正：源->目标的局部旋转偏移（T-pose/A-pose 差异补偿）
    Quat rotationOffset = Quat(0, 0, 0, 1);

    // 标记该骨骼是否为根（Hips/Root），参与全局位移与朝向重定向
    bool isRoot = false;

    // 标记是否为 Biped/CAT 特例骨骼（方案 C 使用）
    bool isBipedOrCat = false;
};

// 角色预设：一组骨骼映射 + 元数据
struct CharacterPreset {
    std::string name;                       // 预设名（如 "DogA->DogB"）
    std::string sourceRigType = "generic"; // generic | biped | cat | animal
    std::string targetRigType = "generic";
    std::vector<BoneMap> bones;            // 骨骼映射列表

    bool empty() const { return bones.empty(); }
};

} // namespace Retarget
