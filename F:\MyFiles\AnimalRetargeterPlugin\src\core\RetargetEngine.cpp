// -*- coding: utf-8 -*-
// RetargetEngine.cpp
// 重定向求解引擎实现

#include "RetargetEngine.h"
#include "math/Quat.h"

#include <max.h>
#include <inode.h>
#include <control.h>

#include <algorithm>
#include <cstdio>

namespace Retarget {

std::unique_ptr<RetargetEngine> RetargetEngine::instance_;

RetargetEngine& RetargetEngine::instance() {
    if (!instance_) {
        instance_ = std::unique_ptr<RetargetEngine>(new RetargetEngine());
    }
    return *instance_;
}

void RetargetEngine::destroy() {
    instance_.reset();
}

RetargetEngine::RetargetEngine() = default;
RetargetEngine::~RetargetEngine() = default;

bool RetargetEngine::loadPresetFromStruct(const CharacterPreset& preset) {
    preset_ = preset;
    return !preset_.empty();
}

bool RetargetEngine::loadPreset(const std::string& /*presetPath*/) {
    // TODO(name): 解析 Python UI 导出的 JSON 预设，填充 preset_
    // 当前先支持结构体直接注入（由 MaxScript 传入）
    return false;
}

bool RetargetEngine::bindSource(INode* root) {
    if (!root) return false;
    sourceRoot_ = root;
    return true;
}

bool RetargetEngine::bindTarget(INode* root) {
    if (!root) return false;
    targetRoot_ = root;
    bound_ = false;
    return true;
}

bool RetargetEngine::resolveBoneNodes() {
    resolvedPairs_.clear();
    if (!sourceRoot_ || !targetRoot_) return false;

    for (const auto& map : preset_.bones) {
        INode* src = sourceRoot_->GetByName(map.sourceName.c_str());
        INode* dst = targetRoot_->GetByName(map.targetName.c_str());
        if (src && dst) {
            resolvedPairs_.emplace_back(src, dst);
        }
    }
    bound_ = !resolvedPairs_.empty();
    return bound_;
}

void RetargetEngine::computeScaleRatios() {
    // TODO(name): 基于骨骼链长度计算源/目标比例，用于位移缩放
}

bool RetargetEngine::solve(TimeValue timeTick) {
    if (!bound_ && !resolveBoneNodes()) return false;

    // 根骨处理
    solveRoot(timeTick);

    // 逐骨骼旋转求解
    size_t idx = 0;
    for (const auto& map : preset_.bones) {
        if (map.isRoot) { ++idx; continue; }
        if (idx < resolvedPairs_.size()) {
            auto& [src, dst] = resolvedPairs_[idx];
            solveBone(map, src, dst, timeTick);
        }
        ++idx;
    }
    return true;
}

void RetargetEngine::solveRoot(TimeValue t) {
    if (preset_.bones.empty()) return;
    const auto& rootMap = preset_.bones.front();
    if (!rootMap.isRoot) return;

    INode* srcRoot = sourceRoot_;
    INode* dstRoot = targetRoot_;
    if (!srcRoot || !dstRoot) return;

    // 全局位移重定向（带 positionWeight）
    Matrix3 srcTM = srcRoot->GetNodeTM(t);
    Matrix3 dstTM = dstRoot->GetNodeTM(t);
    Point3 srcPos = srcTM.GetTrans();
    Point3 dstPos = dstTM.GetTrans();
    Point3 newPos = dstPos + (srcPos - dstPos) * rootMap.positionWeight;
    dstRoot->SetNodeTM(t, Matrix3(1) * newPos);
}

void RetargetEngine::solveBone(const BoneMap& map, INode* srcNode,
                               INode* dstNode, TimeValue t) {
    if (!srcNode || !dstNode) return;

    // 源骨骼本地旋转
    Quat srcRot;
    srcNode->GetLocalRotation(srcRot, t);
    Quat finalRot = srcRot;

    if (useRotationOffset_) {
        finalRot = finalRot * map.rotationOffset;
    }

    // 权重混合到目标当前旋转
    Quat dstCur;
    dstNode->GetLocalRotation(dstCur, t);
    finalRot = math::QuatUtil::Slerp(dstCur, finalRot, map.rotationWeight);

    dstNode->SetLocalRotation(finalRot, t);
}

bool RetargetEngine::bake(TimeValue start, TimeValue end, int frameStep) {
    if (!bound_ && !resolveBoneNodes()) return false;
    for (TimeValue t = start; t <= end; t += GetTicksPerFrame() * frameStep) {
        if (!solve(t)) return false;
    }
    return true;
}

std::string RetargetEngine::status() const {
    char buf[256];
    snprintf(buf, sizeof(buf),
             "bound=%d pairs=%zu preset=%s",
             bound_ ? 1 : 0, resolvedPairs_.size(),
             preset_.name.c_str());
    return std::string(buf);
}

} // namespace Retarget
