#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3ds Max 领域知识库（LLM 上下文注入 + 按需查询）。

设计目标：
1. 给 LLM 提供"3ds Max 世界长什么样"的基础常识，避免靠猜写代码。
2. 区分"必塞 system prompt"的 L1 核心常识与"按需查询"的 L2 详细知识，
   控制每轮对话的 token 开销（L1 ≤ 500 token，L2 仅命中查询时计费）。
3. 知识沉淀为可单测的纯数据结构，与 prompt 拼装/工具调用解耦，
   方便后续按需扩展或回归。

约束：
- L1 文本必须凝练，每条 1 行，禁止把教程、长篇解释塞进来——
  那是 docs/ 的事。
- L2 词条按主题分桶（primitive / modifier / light / camera / pivot / ...），
  每桶纯 dict，键名必须是 LLM 容易猜出的英文标识。

来源参考：
- Autodesk 官方 MaxScript Help (3ds Max 2022~2027)
- pymxs 官方文档（python_pymxs_rules.md 已沉淀代码层规则，本文件
  补足"对象/参数/世界观"层语义）
"""

from __future__ import absolute_import
from __future__ import print_function

from typing import Any
from typing import Dict
from typing import List
from typing import Optional


# --------------------------------------------------------------------- #
# Layer 1：基础世界观（必塞 system prompt，目标 ≤ 500 token / 1000 字）
# --------------------------------------------------------------------- #
# 写作要点：
# 1. 每行一条，前缀 emoji 让 LLM 一眼分清类别
# 2. 只放"反直觉/易错/必备"三类知识；通用编程常识不放
# 3. 不重复 coding_rules 里已有的规则（那边管语法，这边管语义）
MAX_BASIC_KNOWLEDGE = """\
==============================================================
🌍 3ds Max 世界观速查（每次对话必读，避免靠猜操作场景）
==============================================================
🧭 坐标系：Z 轴向上（Z-up）右手系；单位由 units.SystemUnitScale 决定
   （可能是 cm/mm/m/inch，不要假设是厘米）。

📐 节点 transform：每个 node 的关键属性，含义不同切勿混用：
   - position / rotation / scale：pivot 的位姿
   - transform：完整 4×3 矩阵；row4 才是位置
   - pivot：pivot 在世界坐标的位置；几何重心 ≠ pivot
   - min / max / center：**世界系下**轴对齐包围盒（旋转后 AABB 会变大）

📦 Primitive 默认 pivot 位置（极易踩坑）：
   - Box / Cylinder / Cone / Pyramid / Tube：pivot 在 **底面中心**
     （想"放到桌面上"用 pivot.z 对齐桌面）
   - Sphere / GeoSphere / Torus / Teapot / Plane：pivot 在 **几何中心**
   - Box: length=Y / width=X / height=Z（与"长宽"直觉相反！）
   - Plane: length 沿 Y，width 沿 X

🌳 场景图：parent / children；selection 是当前选择集；`$` ≡ 当前选择；
   getNodeByName 不区分大小写，返回 wrapper（用 `==` 不是 `is` 比较）。

🧱 修改器栈：LIFO，后 addModifier 的在栈顶；modifiers[1] 是栈顶；
   collapseStack / convertTo 会塌陷栈不可逆。

💡 灯光关键属性：multiplier / rgb / castShadows / enabled。
🎬 相机关键属性：fov(度) / targetDistance / type；
   V-Ray / Corona / Physical 相机参数与 Standard 不同——先 classOf 探测。

🎨 材质：meditMaterials[1..24] 是编辑器槽位，sceneMaterials 是场景实际引用；
   赋材质用 obj.material = mtl；多材质用 Multi/Sub-Object。

⚠️ 操作前必查（防止幻觉 API）：
   isProperty / getPropNames → 属性是否存在
   classOf / superClassOf    → 类型谱系
   pluginManager.pluginDllName → 第三方插件是否加载

🪤 高频陷阱：
   1. `box.height` 改 Z 向尺寸，不是位置（位置改 position.z）
   2. `copy` 独立 / `instance` 共享 / `reference` 单向继承
   3. `move obj [10,0,0]` 是相对位移；绝对定位用 `obj.position = [10,0,0]`
   4. `select obj` 会清旧选择；保留旧选择用 `selectMore obj`
   5. `rotate eulerAngles X Y Z` 中 X/Y/Z 是**度**不是弧度
   6. animate / at time 必须用 with 包裹（pymxs.animate(True)）
==============================================================
"""


# --------------------------------------------------------------------- #
# Layer 2：按需查询的详细知识（lookup_max_knowledge 工具调用时返回）
# --------------------------------------------------------------------- #
# 设计原则：
# - 主题为粒度，单条 ≤ 800 字符，避免把工具响应膨胀成长文
# - 键名是 LLM 能"想到"的英文 slug；查不到时给出近似建议而非空响应
# - 同主题内多条目用 dict 嵌套（如 primitive 下分 box / sphere 等）

KNOWLEDGE_TOPICS = {
    # 创建参数表（覆盖最常用 8 种 primitive，用户大概率创建后忘记设置参数）
    'primitive': {
        '_summary': '常用 primitive 几何体的默认 pivot 与关键参数。',
        'box': (
            'Box 参数: length(Y轴) / width(X轴) / height(Z轴) / '
            'lengthSegs / widthSegs / heightSegs。'
            'Pivot 在底面中心，原点位于 pivot。'
            'pymxs: rt.Box(length=10, width=20, height=5)。'
        ),
        'sphere': (
            'Sphere 参数: radius / segments(纬度段数, 默认32) / '
            'smooth(布尔) / hemisphere(0~1, 1=半球) / chop(切除/squash)。'
            'Pivot 在几何中心。'
        ),
        'cylinder': (
            'Cylinder 参数: radius / height / heightSegs / capSegs / '
            'sides / smooth / sliceOn / sliceFrom / sliceTo。'
            'Pivot 在底面中心，沿 Z 轴向上。'
        ),
        'cone': (
            'Cone 参数: radius1(底) / radius2(顶) / height / '
            'heightSegs / sides / smooth。'
            'Pivot 在底面中心。radius2=0 即标准圆锥。'
        ),
        'plane': (
            'Plane 参数: length(沿Y) / width(沿X) / lengthSegs / '
            'widthSegs / renderMult。Pivot 在几何中心。'
            '注意 length=Y、width=X，与直觉相反易写错。'
        ),
        'torus': (
            'Torus 参数: radius1(主半径) / radius2(管半径) / '
            'segs(主分段) / sides(管分段) / smooth(0~3)。'
            'Pivot 在几何中心，环面位于 XY 平面。'
        ),
        'teapot': (
            'Teapot 参数: radius / segs / body / handle / spout / lid。'
            'Pivot 在几何中心。teapot 是 Max 的 mascot，常用于测试。'
        ),
        'pyramid': (
            'Pyramid 参数: width(X) / depth(Y) / height(Z) / '
            'widthSegs / depthSegs / heightSegs。'
            'Pivot 在底面中心。'
        ),
    },

    # 灯光：Standard / Photometric / V-Ray 概览
    'light': {
        '_summary': '灯光类型与关键属性。'
                    'Max 自带 Standard + Photometric 两套，渲染器各有一套。',
        'standard_omni': (
            'OmniLight 全向点光源。属性: '
            'multiplier(强度) / rgb / castShadows(布尔) / '
            'shadowType(#shadowMap/#rayTraced) / '
            'farAttenStart / farAttenEnd / useFarAtten。'
        ),
        'standard_spot': (
            'TargetSpot/FreeSpot 聚光灯。属性: '
            'multiplier / rgb / falloff(外角度) / hotspot(内角度) / '
            'castShadows / target(目标点)。'
        ),
        'standard_direct': (
            'TargetDirectionallight/FreeDirectionallight 平行光。'
            '常用作太阳光。属性同 spot，多了 overshoot。'
        ),
        'photometric': (
            'Photometric 光源（基于物理单位）: '
            'lightMul(强度) / lightColor / shape(#point/#linear/#area) / '
            'distribution(#isotropic/#spotlight/#webDistribution)。'
            '需配合 mr/Arnold/V-Ray 才能正确渲染。'
        ),
        'tip': (
            '判断渲染器再选灯：classOf renderer 看是 ScanlineRenderer / '
            'V_Ray / Arnold 等；V-Ray 强烈建议用 VRayLight 而非 Standard。'
        ),
    },

    # 相机：Standard / Physical / V-Ray
    'camera': {
        '_summary': '相机类型与关键属性。',
        'free_camera': (
            'FreeCamera 自由相机。属性: '
            'fov(度数, 水平视场) / nearClip / farClip / '
            'targetDistance(用于景深参考)。'
        ),
        'target_camera': (
            'TargetCamera 目标相机。除 fov 外多一个 target 节点，'
            'cam.target.position 控制目标位置；'
            'cam.position 控制机位。'
        ),
        'physical_camera': (
            'Physical Camera (Max 2016+)：基于物理单位。属性: '
            'film_width / focal_length(代替 fov) / '
            'f_number(光圈) / shutter_speed / iso / '
            'use_dof(景深) / focus_distance。'
        ),
        'tip': (
            'fov 单位是度而非弧度；Physical Camera 的 focal_length 单位是 mm；'
            '渲染时 viewport 必须切到该相机视图，否则等于没生效。'
        ),
    },

    # 修改器：高频 10 个
    'modifier': {
        '_summary': '常用修改器及其关键参数（按类别）。'
                    '所有修改器用 `addModifier obj mod` 添加；栈是 LIFO。',
        'bend': (
            'Bend 弯曲。参数: '
            'angle(弯曲角度) / direction(方向角) / axis(#x/#y/#z) / '
            'limit / upperLimit / lowerLimit。'
        ),
        'twist': (
            'Twist 扭曲。参数: angle / bias / axis(#x/#y/#z) / limit。'
        ),
        'taper': (
            'Taper 锥化。参数: amount / curve / axis / effect / '
            'limit / upperLimit / lowerLimit。'
        ),
        'noise': (
            'Noise 噪波。参数: scale / strength(Point3) / '
            'fractal(布尔) / iterations / animate(动画噪波)。'
        ),
        'uvw_map': (
            'UVWMap 贴图坐标。参数: mapType(#planar/#cylindrical/'
            '#spherical/#shrinkWrap/#box/#xyzToUVW) / length / '
            'width / height / utile / vtile / wtile / mapChannel。'
        ),
        'symmetry': (
            'Symmetry 对称。参数: axis / flip / slice / weldThreshold。'
            '常用于左右对称建模。'
        ),
        'turbosmooth': (
            'TurboSmooth 涡轮平滑（细分）。参数: iterations / '
            'renderIterations / useRenderIters / smoothResult。'
        ),
        'shell': (
            'Shell 厚度。参数: innerAmount / outerAmount / '
            'segments / bevelEdges / autoSmooth。给单面赋厚度。'
        ),
        'edit_poly': (
            'Edit_Poly 可编辑多边形。子层级: 1=Vertex, 2=Edge, '
            '3=Border, 4=Polygon, 5=Element。修改器形式不破坏原栈。'
        ),
        'skin': (
            'Skin 蒙皮。绑骨用：addBone / removeBone / '
            'paintWeights。骨骼层级用 bones 接口操作。'
        ),
    },

    # 材质：Standard / Physical / V-Ray
    'material': {
        '_summary': '材质类型与赋值方法。',
        'standard': (
            'Standard 标准材质。属性: '
            'diffuseColor / specularColor / specularLevel / '
            'glossiness / opacity / selfIllumAmount / bumpAmount / '
            'diffuseMap / bumpMap。Max 老牌材质，渲染器通用。'
        ),
        'physical': (
            'PhysicalMaterial (Max 2017+)。属性: '
            'base_color / metalness / roughness / ior / '
            'transparency / emit_color / emit_luminance / '
            'coat / coat_roughness。基于 PBR，推荐新工程使用。'
        ),
        'multi_sub': (
            'Multi/Sub-Object 多维子材质。属性: numsubs / '
            'materialList[i] / nameList[i]。配合 polygon 的 '
            'matID 实现一物体多材质。'
        ),
        'assign': (
            '赋值: obj.material = mtl。检查: obj.material == undefined 表示未赋值。'
            '材质编辑器槽位用 meditMaterials[1..24] 访问。'
        ),
    },

    # 单位与坐标系
    'units': {
        '_summary': 'Max 单位系统易踩坑。',
        'system': (
            'units.SystemUnitScale: 系统单位刻度（影响所有几何体计算）。'
            'units.SystemType: #inches / #feet / #miles / #millimeters / '
            '#centimeters / #meters / #kilometers。'
            '改 SystemUnitScale 会改变现有几何体的真实尺寸！'
        ),
        'display': (
            'units.MetricType / units.USType: 显示单位（仅影响 UI 显示，'
            '不影响计算）。常用:'
            'units.MetricType = #millimeters; units.DisplayType = #metric。'
        ),
        'coordsys': (
            '坐标系参考: #world / #local / #parent / #screen / #grid。'
            'pymxs 用 rt.setRefCoordSys(rt.Name("world"))，'
            'MaxScript 用 in coordsys world ( ... )。'
        ),
    },

    # 渲染
    'render': {
        '_summary': '渲染相关 API。',
        'basic': (
            'render() 函数渲染当前帧。常用参数: '
            'frame / outputFile / outputWidth / outputHeight / '
            'vfb(布尔, 显示虚拟帧缓存) / camera / renderType。'
        ),
        'renderer': (
            'renderers.current = ScanlineRenderer() 切换渲染器。'
            'classOf renderers.current 检测当前。'
            'V-Ray: V_Ray_Adv_5_xx; Arnold: Arnold; Corona: CoronaRenderer。'
        ),
        'animation': (
            'animationRange.start / .end 是动画时间范围。'
            'sliderTime 是当前帧。'
            '渲染序列用 render frames:#all 或 render fromFrame:0 toFrame:100。'
        ),
    },

    # pivot / transform 进阶
    'pivot': {
        '_summary': 'Pivot（轴心点）操作要点。',
        'reset': (
            '重置 pivot 到几何中心: '
            'obj.pivot = obj.center 然后 ResetXForm obj 再 collapseStack。'
        ),
        'move_only': (
            '只移动 pivot 不影响几何: 直接赋值 obj.pivot = newPos，'
            '相当于 Hierarchy 面板里 "Affect Pivot Only" 模式。'
        ),
        'align_to_bottom': (
            '把 pivot 移到包围盒底面中心:'
            'obj.pivot = [obj.center.x, obj.center.y, obj.min.z]。'
        ),
        'reset_xform': (
            'ResetXForm: 把当前 transform 拍平到顶点数据，'
            '让 transform 矩阵归一。塌陷栈后 pivot 就是新原点。'
        ),
    },
}


# --------------------------------------------------------------------- #
# 公共 API
# --------------------------------------------------------------------- #
def get_basic_knowledge():
    """返回 L1 必塞 system prompt 的基础常识文本。

    供 ``conversation.build_default_system_prompt`` 拼接使用。
    单独函数化是为方便单元测试和后续动态扩展。

    :returns: 多行文本（含分隔线 + 9 个主题 + 陷阱清单）
    """
    return MAX_BASIC_KNOWLEDGE


def list_topics():
    """返回 L2 知识库的所有可查主题名。

    :returns: 主题 slug 列表（按字典序）
    """
    return sorted(KNOWLEDGE_TOPICS.keys())


def lookup_topic(topic, sub_key=None):
    # type: (str, Optional[str]) -> Dict[str, Any]
    """按主题查询 L2 知识库。

    :param topic: 主题名，如 'primitive' / 'modifier'。大小写不敏感。
    :param sub_key: 可选子键，如 topic='primitive' 时 sub_key='box'。
        不传则返回主题下全部条目。
    :returns: 字典 {found, topic, items / suggestion / available_topics}
    """
    if not topic:
        return {
            'found': False,
            'error': 'topic 参数不能为空',
            'available_topics': list_topics(),
        }

    norm = topic.strip().lower()
    if norm not in KNOWLEDGE_TOPICS:
        # 给一个相近建议，避免 LLM 看到空响应不知所措
        suggestion = _find_closest_topic(norm)
        return {
            'found': False,
            'topic': topic,
            'available_topics': list_topics(),
            'suggestion': suggestion,
            'message': (
                '未找到主题 "{}"。可用主题: {}{}'
            ).format(
                topic,
                ', '.join(list_topics()),
                ('；最接近: ' + suggestion) if suggestion else '',
            ),
        }

    bucket = KNOWLEDGE_TOPICS[norm]
    if sub_key is None:
        return {
            'found': True,
            'topic': norm,
            'summary': bucket.get('_summary', ''),
            'keys': sorted([k for k in bucket.keys() if k != '_summary']),
            'items': {
                k: v for k, v in bucket.items()
                if k != '_summary'
            },
        }

    # 带 sub_key：精准查
    sub_norm = sub_key.strip().lower()
    if sub_norm in bucket:
        return {
            'found': True,
            'topic': norm,
            'sub_key': sub_norm,
            'content': bucket[sub_norm],
        }

    return {
        'found': False,
        'topic': norm,
        'sub_key': sub_key,
        'available_keys': sorted(
            [k for k in bucket.keys() if k != '_summary'],
        ),
        'message': (
            '主题 "{}" 下没有子键 "{}"。可用子键: {}'
        ).format(
            norm, sub_key,
            ', '.join(k for k in bucket.keys() if k != '_summary'),
        ),
    }


def _find_closest_topic(query):
    # type: (str) -> str
    """简单字符串相似度查找最接近的主题名。

    用 SequenceMatcher 即可，不引入第三方依赖。

    :param query: 用户输入的主题字符串
    :returns: 最相近的主题名；无可用建议时返回空字符串
    """
    from difflib import SequenceMatcher
    best = ''
    best_ratio = 0.0
    for t in KNOWLEDGE_TOPICS:
        ratio = SequenceMatcher(None, query, t).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = t
    # 阈值 0.4：低于这个值就不像了，不要给误导性建议
    return best if best_ratio >= 0.4 else ''


__all__ = [
    'MAX_BASIC_KNOWLEDGE',
    'KNOWLEDGE_TOPICS',
    'get_basic_knowledge',
    'list_topics',
    'lookup_topic',
]
