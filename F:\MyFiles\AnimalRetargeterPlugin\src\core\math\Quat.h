// -*- coding: utf-8 -*-
// Quat.h
// 轻量四元数工具（与 Max Point3/Matrix3 兼容），零外部依赖
// 用于骨骼旋转重定向求解

#pragma once

#include <max.h>
#include <Quat.h>

#include <cmath>

namespace Retarget {
namespace math {

// 包装 Max 原生 Quat，提供重定向常用工具方法
class QuatUtil {
public:
    // 从两个骨骼的本地轴向构造相对旋转
    static Quat FromToRotation(const Point3& from, const Point3& to) {
        Point3 a = from.Normalize();
        Point3 b = to.Normalize();
        float dot = DotProd(a, b);
        dot = clamp(dot, -1.0f, 1.0f);
        if (dot > 0.9995f) {
            return Quat(0.0f, 0.0f, 0.0f, 1.0f); // 几乎同向
        }
        if (dot < -0.9995f) {
            // 反向：绕任意正交轴旋转 180 度
            Point3 axis = CrossProd(Point3(1, 0, 0), a);
            if (axis.Length() < 0.01f) {
                axis = CrossProd(Point3(0, 1, 0), a);
            }
            axis = axis.Normalize();
            return Quat(axis.x * sinf(PI / 2), axis.y * sinf(PI / 2),
                        axis.z * sinf(PI / 2), cosf(PI / 2));
        }
        Point3 axis = CrossProd(a, b).Normalize();
        float angle = acosf(dot);
        float s = sinf(angle / 2.0f);
        return Quat(axis.x * s, axis.y * s, axis.z * s, cosf(angle / 2.0f));
    }

    // 球面线性插值
    static Quat Slerp(const Quat& q0, const Quat& q1, float t) {
        return ::Slerp(q0, q1, t);
    }

private:
    static float clamp(float v, float lo, float hi) {
        return (v < lo) ? lo : ((v > hi) ? hi : v);
    }
};

} // namespace math
} // namespace Retarget
