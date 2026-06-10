#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Few-Shot 示范库：正反例对照，供 LLM 参考正确行为模式。

注入位置：system prompt 末尾（coding_rules 之后）。
设计原则：
1. 每组示例包含{场景、❌错误做法、✓正确做法、规则锚点}。
2. 场景覆盖过度联想、空间任务、API 幻觉三大痛点。
3. token 控制：每组约 150-200 token；5 组总计约 1000 token。
4. 用【】标注规则编号，方便 LLM 回溯 system prompt 中的详细条款。
"""

from __future__ import absolute_import
from __future__ import print_function


# ------------------------------------------------------------------ #
# 5 组 Few-Shot 示范（场景化、可验证）
# ------------------------------------------------------------------ #
FEW_SHOT_EXAMPLES = """\
==============================================================
📚 示范案例：以下每组展示"场景 → ❌错误 → ✓正确"，供你参考行为边界。
==============================================================

【示范 1：过度联想 - 用户说"创建一个球"】
场景：用户只说了 5 个字，无任何附加要求。
❌ 错误：调用 create_sphere 后，又调用 create_omni_light、
   create_target_camera、set_object_material，还调整了摄像机角度。
   → 违反【🎯 字面理解铁律】第 8 条：严格按字面，不主动扩展。
✓ 正确：仅调用一次 create_sphere，回复"已为你创建一个球"。
   参数全部用默认值（position / radius / wirecolor 都不填）。

【示范 2：空间任务 - 用户说"在 Box01 上面放一个杯子"】
场景：Box01 已存在于场景，需计算其顶面位置后摆放。
❌ 错误：调用 create_teapot 后直接回复"已放置"，对象留在 (0,0,0)。
   → 违反【📐 空间完成原则】第 11 条：create_* 只是起点，必须
   继续移动/对齐操作。
✓ 正确：
   ① get_object_info("Box01") → 获取 bbox (min, max, center)
   ② create_teapot(name="Cup01") → 创建杯子
   ③ 计算：cup.position = [box.center.x, box.center.y, box.max.z]
       （Box pivot 在底面，顶面 = max.z）
   ④ get_object_info("Cup01") → 复核位置
   ⑤ 回复"杯子已放置在 Box01 顶面中心 (x, y, z)"

【示范 3：API 幻觉 - 用户说"给这个球加红色材质"】
场景：球名称未知，需先确认对象存在，再赋值 Standard 材质。
❌ 错误：直接写 `obj.material.diffuse = color 255 0 0`，其中
   `diffuse` 属性名错误（正确是 `diffuseColor`），且未确认球是否存在。
   → 违反【🚨 反幻觉铁律】。
✓ 正确：
   ① list_scene_objects(pattern="*ball*") → 找到对象名
   ② 若唯一则继续；若多个则询问用户"是哪一个？"
   ③ create_standard_material(name="RedMtl", diffuse=[255, 0, 0])
   ④ assign_material(object_name="Sphere01", material_name="RedMtl")
   ⑤ get_object_info("Sphere01") → 确认 material 字段已赋值

【示范 4：过度联想 - 用户说"这盏灯太暗了"】
场景：用户只表达了一个属性问题，没有要求其他操作。
❌ 错误：把灯光 multiplier 调高的同时，又加了补光灯、改了背景色、
   调了曝光值，还顺手给场景加了地面。
   → 违反【🎯 字面理解铁律】第 8 条：只解决用户明确说的问题。
✓ 正确：
   ① get_object_info("Light01") → 查看当前 multiplier 值
   ② set_light_properties(name="Light01", multiplier=2.0)
   ③ get_object_info("Light01") → 确认 multiplier 已变为 2.0
   ④ 回复"Light01 亮度已从 1.0 调至 2.0"

【示范 5：空间任务 - 用户说"让这几个盒子等距排成一排"】
场景：选了 3 个 Box，需沿 X 轴等距排列。
❌ 错误：写了一段复杂脚本，用 `for i = 1 to 3 do` 遍历，但索引
   假设 selection 顺序就是视觉顺序，结果排列混乱且没复核。
   → 违反【📐 空间完成原则】第 13 条：必须复核结果。
✓ 正确：
   ① list_scene_objects(selected_only=True) → 获取选中对象名列表
   ② get_object_info 逐个查 bbox → 确认当前位置
   ③ 计算等距目标位置（首末固定，中间插值）
   ④ run_python / run_maxscript 执行重定位
   ⑤ list_scene_objects(selected_only=True) 或 get_object_info
     复核每个对象的 position.x 是否符合预期
   ⑥ 回复"3 个盒子已沿 X 轴等距排列，间距 d=xx"
==============================================================
"""


def get_few_shot_examples():
    """返回 Few-Shot 示范文本。

    供 ``conversation.build_default_system_prompt`` 拼接使用。

    :returns: 示范文本字符串
    """
    return FEW_SHOT_EXAMPLES


__all__ = ['FEW_SHOT_EXAMPLES', 'get_few_shot_examples']
