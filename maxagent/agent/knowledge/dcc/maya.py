#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 领域知识库（LLM 上下文注入 + 按需查询）。

设计目标：
1. 给 LLM 提供"Maya 世界长什么样"的基础常识，避免把 Max 习惯带到 Maya。
2. 与 Max 知识库结构保持一致，方便后续统一维护。
"""

from __future__ import absolute_import
from __future__ import print_function

from .base import DCCKnowledge


MAYA_BASIC_KNOWLEDGE = """\
==============================================================
🌍 Maya 世界观速查（每次对话必读，避免把 Max 习惯带进来）
==============================================================
🧭 坐标系：Y 轴向上（Y-up）右手系；默认线性单位 cm，角度单位度。

📐 节点与 transform / shape：
   - 场景中可见节点通常是 transform + shape 的层级：
     transform 负责位移/旋转/缩放；shape 负责几何数据。
   - 创建几何体返回的是 transform 名（如 "pCube1"），其 shape 子节点是
     "pCubeShape1"。
   - 改 transform 会整体移动对象；改 shape 的 CV/顶点才改变几何。
   - 不要直接把 shape 的 parent 当作世界坐标原点的对象。

📦 Primitive 关键参数：
   - polyCube: width(X) / height(Y) / depth(Z) / subdivisionsWidth /
     subdivisionsHeight / subdivisionsDepth。
   - polySphere: radius / subdivisionsAxis / subdivisionsHeight。
   - polyCylinder: radius / height / subdivisionsAxis / subdivisionsCaps。
   - Maya 的 pivot 默认在对象中心；吸附到地面可移动 pivot 到 minY。

🌳 场景图：DagPath / DAG 层级；用 cmds.ls() / cmds.listRelatives() 遍历。
   cmds.select('obj') 会替换选择；追加选择用 cmds.select('obj', add=True)。

🧱 历史与变形器：
   - 所有创建/修改默认带 constructionHistory；需要完全独立几何时先
     cmds.delete(ch=True) 删除历史。
   - 常用变形器：bend、twist、taper、noise、ffd、wrap、skinCluster。
   - 添加变形器：cmds.deformer(obj, type='bend')。

💡 灯光关键属性：intensity / color / useRayTraceShadows / emitDiffuse /
   emitSpecular。常用类型：ambientLight、directionalLight、pointLight、
   spotLight、areaLight。

🎬 相机关键属性：focalLength(mm) / horizontalFilmAperture /
   verticalFilmAperture / nearClipPlane / farClipPlane。

🎨 材质：
   - 默认材质节点是 lambert / phong / blinn / standardSurface。
   - 赋材质：cmds.sets(obj, edit=True, forceElement=shading_group)。
   - 文件贴图用 file 节点，连到 baseColor 等属性。

⚠️ 操作前必查（防止幻觉 API）：
   cmds.objExists(name) → 对象是否存在
   cmds.objectType(obj) → 节点类型
   cmds.nodeType(node) → DG 节点类型
   cmds.pluginInfo(plugin, query=True, loaded=True) → 插件是否加载

🪤 高频陷阱：
   1. Maya API 中 angles 默认是度，不是弧度（OpenMaya 的 MAngle 除外）。
   2. cmds.move(x, y, z, obj) 默认是**相对位移**；绝对定位用
      cmds.xform(obj, translation=[x, y, z], worldSpace=True)。
   3. 选择集是全局状态，很多命令依赖当前选择；推荐显式传对象名。
   4. 删除历史前注意：history 里可能有 skinCluster，删掉就丢权重。
   5. 对 shape 操作顶点时，要确认对象处于正确的 component 模式或
      使用 cmds.xform(obj + '.vtx[0]', ...)。

🎬 动画基础：
   - 时间单位：帧；cmds.currentTime(frame) 设置当前帧。
   - 关键帧：cmds.setKeyframe(obj, attribute='translateX')。
   - 播放范围：cmds.playbackOptions(minTime=..., maxTime=...)。
   - 常用曲线类型：animCurveTL / animCurveTA / animCurveTU。

🌐 全局层级操作：
   - cmds.ls() 列出对象；cmds.ls(type='mesh') 列出所有 mesh shape。
   - 显示/隐藏：cmds.setAttr(obj + '.visibility', 0/1)。
   - 冻结变换：cmds.makeIdentity(obj, apply=True, t=1, r=1, s=1)。
   - 打组：cmds.group(objects, name='group1')；解组 cmds.ungroup。
==============================================================
"""


# L2 知识库主题：占位，后续按 Maya 高频需求扩展
MAYA_KNOWLEDGE_TOPICS = {
    'primitive': {
        '_summary': 'Maya 常用 primitive 创建命令与关键参数。',
        'cube': (
            'cmds.polyCube(w=1, h=1, d=1, sx=1, sy=1, sz=1) 返回 transform。'
            'shape 名为 transform + "Shape"。默认 pivot 在中心。'
        ),
        'sphere': (
            'cmds.polySphere(r=1, sx=20, sy=20) 返回 transform。'
        ),
        'cylinder': (
            'cmds.polyCylinder(r=1, h=2, sx=20, sy=1, sz=1) 返回 transform。'
        ),
    },
    'transform': {
        '_summary': 'Maya transform 操作要点。',
        'absolute_move': (
            'cmds.xform(obj, translation=[x, y, z], worldSpace=True) 设置世界坐标。'
        ),
        'pivot': (
            'cmds.xform(obj, pivots=[x, y, z], worldSpace=True) 设置 pivot。'
        ),
        'freeze': (
            'cmds.makeIdentity(obj, apply=True, t=1, r=1, s=1) 冻结变换。'
        ),
    },
    'deformer': {
        '_summary': 'Maya 常用变形器。',
        'bend': (
            'cmds.deformer(obj, type="bend")；属性: curvature / lowBound / highBound。'
        ),
        'skincluster': (
            'cmds.skinCluster(joints, mesh, bindMethod=0, normalizeWeights=1)。'
        ),
    },
}


MAYA_KNOWLEDGE = DCCKnowledge(
    dcc_name='maya',
    basic_knowledge=MAYA_BASIC_KNOWLEDGE,
    topics=MAYA_KNOWLEDGE_TOPICS,
)

__all__ = ['MAYA_KNOWLEDGE']
