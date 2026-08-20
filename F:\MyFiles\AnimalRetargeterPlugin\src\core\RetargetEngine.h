// -*- coding: utf-8 -*-
// RetargetEngine.h
// 重定向求解引擎：加载映射、绑定源/目标骨架、逐帧求解并写入目标骨骼
// 单例模式，供 MaxScript 绑定层调用

#pragma once

#include "BoneMapping.h"

#include <max.h>

#include <memory>
#include <string>
#include <vector>

namespace Retarget {

class RetargetEngine {
public:
    // 单例
    static RetargetEngine& instance();
    static void destroy();

    // 加载角色预设（可来自 Python UI 生成的 JSON 文件）
    bool loadPreset(const std::string& presetPath);
    bool loadPresetFromStruct(const CharacterPreset& preset);

    // 绑定场景中的源/目标骨架根节点
    bool bindSource(INode* root);
    bool bindTarget(INode* root);

    // 逐帧求解：将当前源骨架姿态重定向到目标骨架
    // timeTick 为当前帧（MAX 时间单位）
    bool solve(TimeValue timeTick);

    // 批量烘焙到动画范围 [start, end]
    bool bake(TimeValue start, TimeValue end, int frameStep = 1);

    // 设置全局参数
    void setUseRotationOffset(bool on) { useRotationOffset_ = on; }
    void setStrideBlend(float v) { strideBlend_ = v; } // 步幅混合，0~1

    // 获取当前状态（供 UI 查询）
    std::string status() const;

private:
    RetargetEngine();
    ~RetargetEngine();

    // 解析源/目标骨骼 INode 列表（按映射表名称匹配）
    bool resolveBoneNodes();

    // 计算骨架比例（源/目标骨骼长度比），用于非等比重定向
    void computeScaleRatios();

    // 计算根骨全局位移与朝向，写入目标根骨
    void solveRoot(TimeValue t);

    // 计算单根骨骼旋转（含 rotationOffset 与权重混合）
    void solveBone(const BoneMap& map, INode* srcNode, INode* dstNode, TimeValue t);

private:
    static std::unique_ptr<RetargetEngine> instance_;

    INode* sourceRoot_ = nullptr;
    INode* targetRoot_ = nullptr;

    CharacterPreset preset_;
    std::vector<std::pair<INode*, INode*>> resolvedPairs_; // source, target

    bool useRotationOffset_ = true;
    float strideBlend_ = 1.0f;
    bool bound_ = false;
};

} // namespace Retarget
