// -*- coding: utf-8 -*-
// MaxScriptBindings.cpp
// MaxScript 函数注册实现（pymxs 可通过 runtime.animalRetargeter_* 调用）

#include "MaxScriptBindings.h"
#include "../core/RetargetEngine.h"
#include "../core/BoneMapping.h"

#include <max.h>
#include <maxscript/maxscript.h>
#include <inode.h>

#include <string>

namespace AnimalRetargeter {

// ---- 辅助：按名称取场景节点 ----
static INode* findNode(const MCHAR* name) {
    return GetCOREInterface()->GetSceneINode()->GetByName(name);
}

// ============================================================
// 1. animalRetargeter_bindSource <string rootName>
// ============================================================
def_visible_primitive(animalRetargeter_bindSource, "animalRetargeter_bindSource");
class AnimalRetargeterBindSource : public GenFnPublishable {
public:
    Value* operator()(Value** args, int count) {
        if (count < 1 || !is_string(args[0]))
            return Value::undefined;
        INode* root = findNode(args[0]->to_string());
        bool ok = Retarget::RetargetEngine::instance().bindSource(root);
        return ok ? &true_value : &false_value;
    }
};

// ============================================================
// 2. animalRetargeter_bindTarget <string rootName>
// ============================================================
def_visible_primitive(animalRetargeter_bindTarget, "animalRetargeter_bindTarget");
class AnimalRetargeterBindTarget : public GenFnPublishable {
public:
    Value* operator()(Value** args, int count) {
        if (count < 1 || !is_string(args[0]))
            return Value::undefined;
        INode* root = findNode(args[0]->to_string());
        bool ok = Retarget::RetargetEngine::instance().bindTarget(root);
        return ok ? &true_value : &false_value;
    }
};

// ============================================================
// 3. animalRetargeter_solve <int frameTick>
// ============================================================
def_visible_primitive(animalRetargeter_solve, "animalRetargeter_solve");
class AnimalRetargeterSolve : public GenFnPublishable {
public:
    Value* operator()(Value** args, int count) {
        TimeValue t = (count >= 1) ? (TimeValue)args[0]->to_int() : GetCOREInterface()->GetTime();
        bool ok = Retarget::RetargetEngine::instance().solve(t);
        return ok ? &true_value : &false_value;
    }
};

// ============================================================
// 4. animalRetargeter_bake <int start> <int end> <int step>
// ============================================================
def_visible_primitive(animalRetargeter_bake, "animalRetargeter_bake");
class AnimalRetargeterBake : public GenFnPublishable {
public:
    Value* operator()(Value** args, int count) {
        TimeValue start = (count >= 1) ? (TimeValue)args[0]->to_int() : 0;
        TimeValue end = (count >= 2) ? (TimeValue)args[1]->to_int() : GetCOREInterface()->GetAnimEnd();
        int step = (count >= 3) ? args[2]->to_int() : 1;
        bool ok = Retarget::RetargetEngine::instance().bake(start, end, step);
        return ok ? &true_value : &false_value;
    }
};

// ============================================================
// 5. animalRetargeter_status
// ============================================================
def_visible_primitive(animalRetargeter_status, "animalRetargeter_status");
class AnimalRetargeterStatus : public GenFnPublishable {
public:
    Value* operator()(Value** /*args*/, int /*count*/) {
        std::string s = Retarget::RetargetEngine::instance().status();
        return new StringValue(s.c_str());
    }
};

// ============================================================
// 6. animalRetargeter_addBoneMap <string src> <string dst>
//    <float rotW> <float posW> <bool isRoot>
//    供 Python UI 逐条构建映射表
// ============================================================
def_visible_primitive(animalRetargeter_addBoneMap, "animalRetargeter_addBoneMap");
class AnimalRetargeterAddBoneMap : public GenFnPublishable {
public:
    Value* operator()(Value** args, int count) {
        if (count < 5) return Value::undefined;
        Retarget::BoneMap m;
        m.sourceName = args[0]->to_string();
        m.targetName = args[1]->to_string();
        m.rotationWeight = (float)args[2]->to_float();
        m.positionWeight = (float)args[3]->to_float();
        m.isRoot = args[4]->to_bool();
        // TODO(name): 将 m 追加到全局 preset（需暴露 engine 的可变 preset）
        return &true_value;
    }
};

bool RegisterMaxScriptFunctions() {
    // 各 def_visible_primitive 在 DLL 加载时自动注册
    return true;
}

bool UnregisterMaxScriptFunctions() {
    // def_visible_primitive 由 Max 在卸载时自动清理
    return true;
}

} // namespace AnimalRetargeter
