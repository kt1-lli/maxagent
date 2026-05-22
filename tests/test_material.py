#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""材质工具回归测试。

主要覆盖修复点：``_to_color`` / ``create_standard_material`` 必须调用
**大写** ``rt.Color(...)``。本项目历史上写成小写 ``rt.color(...)``，曾
导致茶壶 diffuse 颜色错乱（图示墨绿+暗红高光），见 commit 修复说明。

测试策略
--------
材质工具直接依赖 ``maxagent.runtime_helpers.rt`` 这个 pymxs 句柄，但
在测试环境（非 3ds Max）下它是 ``None``。我们用一个 fake 对象冒充
``pymxs.runtime``，记录所有被访问到的属性名 + 调用参数，借此断言：

1. 真的有 ``Color`` 大写属性被调用；
2. 一定不会再退化回 ``color`` 小写；
3. 颜色构造参数与传入值（含 0~1 / 0~255 自动归一化）一致。
"""

from __future__ import absolute_import
from __future__ import print_function

import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class _FakeMaterial(object):
    """模拟 rt.Standardmaterial() 实例：记录所有属性赋值。"""

    def __init__(self, type_name='Standardmaterial'):
        # 用 object.__setattr__ 避免触发自定义 __setattr__，否则会无限递归
        object.__setattr__(self, '_type_name', type_name)
        object.__setattr__(self, 'name', type_name)

    def __setattr__(self, key, value):
        object.__setattr__(self, key, value)


class _FakeRt(object):
    """模拟 pymxs.runtime：

    - 大写 ``Color`` / ``Standardmaterial`` 等正常返回；
    - 任何**未显式定义**的属性访问会被记录到 ``unknown_access``，
      用于断言"小写 color 不再被使用"。
    """

    def __init__(self):
        self.color_calls = []           # 大写 Color() 调用参数列表
        self.lowercase_color_calls = [] # 小写 color() 调用参数列表（应为空）
        self.unknown_access = []
        self.medit_slots = [None] * 25  # 1-based: slot[0] 不用

    # -- 颜色构造 -----------------------------------------------------
    def Color(self, r, g, b):                  # noqa: N802 (pymxs 大写约定)
        self.color_calls.append((r, g, b))
        return ('Color', r, g, b)

    def color(self, r, g, b):
        # 记录但不抛异常——让测试用断言失败而不是 AttributeError 失败
        self.lowercase_color_calls.append((r, g, b))
        return ('color', r, g, b)

    # -- 材质构造 -----------------------------------------------------
    def Standardmaterial(self):                # noqa: N802
        return _FakeMaterial('Standardmaterial')

    def PhysicalMaterial(self):                # noqa: N802
        return _FakeMaterial('PhysicalMaterial')

    def classOf(self, obj):                    # noqa: N802
        return getattr(obj, '_type_name', 'unknown')

    # -- medit 槽位 ---------------------------------------------------
    def getMeditMaterial(self, idx):           # noqa: N802
        slot = self.medit_slots[idx] if 0 <= idx < len(self.medit_slots) else None
        if slot is None:
            # 模拟空槽：默认 Standardmaterial 名字以 # 开头
            slot = _FakeMaterial('Standardmaterial')
            slot.name = '#default'
            self.medit_slots[idx] = slot
        return slot

    def setMeditMaterial(self, idx, mat):      # noqa: N802
        if 0 <= idx < len(self.medit_slots):
            self.medit_slots[idx] = mat

    # -- 兜底：捕获任何未知属性访问，方便断言 -------------------------
    def __getattr__(self, name):
        self.unknown_access.append(name)
        raise AttributeError(name)


class CreateStandardMaterialTests(unittest.TestCase):
    """create_standard_material + _to_color 回归测试。"""

    def setUp(self):
        # 给 runtime_helpers 注入 fake rt + 标记 IN_MAX=True；
        # 但 tools.material 在 import 时已经把 rt 绑定为模块级名字了，
        # 所以同时 patch material 模块里的 rt（更靠谱）。
        # 不能 importlib.reload(material) —— @tool 装饰器有全局注册表，重载会抛"工具名重复"。
        from maxagent import runtime_helpers
        from maxagent.tools import material as material_mod

        self._real_rt = runtime_helpers.rt
        self._real_in_max = runtime_helpers.IN_MAX
        self._material_mod = material_mod
        self._real_material_rt = material_mod.rt
        self._real_material_in_max = material_mod.IN_MAX

        self._fake_rt = _FakeRt()
        runtime_helpers.rt = self._fake_rt
        runtime_helpers.IN_MAX = True
        material_mod.rt = self._fake_rt
        material_mod.IN_MAX = True

    def tearDown(self):
        from maxagent import runtime_helpers
        runtime_helpers.rt = self._real_rt
        runtime_helpers.IN_MAX = self._real_in_max
        self._material_mod.rt = self._real_material_rt
        self._material_mod.IN_MAX = self._real_material_in_max

    # -- 正向用例 ------------------------------------------------------
    def test_diffuse_uses_uppercase_color_with_0_255_input(self):
        """0~255 整数输入：不放大，直接传给 Color。"""
        result = self._material_mod.create_standard_material(
            name='红色材质',
            diffuse=[200, 30, 30],
        )
        self.assertEqual(result['name'], '红色材质')
        self.assertEqual(result['type'], 'Standardmaterial')
        # 关键：必须走大写 Color
        self.assertEqual(
            len(self._fake_rt.color_calls), 1,
            'create_standard_material 必须调用大写 rt.Color()',
        )
        r, g, b = self._fake_rt.color_calls[0]
        self.assertAlmostEqual(r, 200.0)
        self.assertAlmostEqual(g, 30.0)
        self.assertAlmostEqual(b, 30.0)

    def test_diffuse_uses_uppercase_color_with_0_1_input(self):
        """0~1 浮点输入：自动放大到 0~255 后再交给 Color。"""
        self._material_mod.create_standard_material(
            name='m',
            diffuse=[1.0, 0.0, 0.0],
        )
        self.assertEqual(len(self._fake_rt.color_calls), 1)
        r, g, b = self._fake_rt.color_calls[0]
        self.assertAlmostEqual(r, 255.0)
        self.assertAlmostEqual(g, 0.0)
        self.assertAlmostEqual(b, 0.0)

    def test_specular_also_uses_uppercase_color(self):
        """高光颜色一并验证不退化。"""
        self._material_mod.create_standard_material(
            name='m',
            diffuse=[1.0, 0.0, 0.0],
            specular=[1.0, 1.0, 1.0],
        )
        # diffuse + specular = 2 次调用
        self.assertEqual(len(self._fake_rt.color_calls), 2)

    def test_default_diffuse_grey(self):
        """不传 diffuse 时使用默认浅灰，仍走大写 Color。"""
        self._material_mod.create_standard_material(name='m')
        self.assertEqual(len(self._fake_rt.color_calls), 1)
        r, g, b = self._fake_rt.color_calls[0]
        self.assertAlmostEqual(r, 200.0)
        self.assertAlmostEqual(g, 200.0)
        self.assertAlmostEqual(b, 200.0)

    # -- 关键回归 ------------------------------------------------------
    def test_no_lowercase_color_used(self):
        """关键回归：曾经的 bug 是用了小写 rt.color()，导致颜色错乱。

        本测试覆盖多种调用形态，确保**任意一种**都不会退化回小写。
        """
        self._material_mod.create_standard_material(name='m1', diffuse=[255, 0, 0])
        self._material_mod.create_standard_material(name='m2', diffuse=[1.0, 0.5, 0.0])
        self._material_mod.create_standard_material(
            name='m3',
            diffuse=[0.2, 0.2, 0.2],
            specular=[0.9, 0.9, 0.9],
        )
        self._material_mod.create_standard_material(name='m4')  # 走默认值
        self.assertEqual(
            self._fake_rt.lowercase_color_calls, [],
            '检测到 rt.color()（小写）调用：{}。'
            'pymxs 中正确的颜色构造器是 rt.Color()（大写）。'
            .format(self._fake_rt.lowercase_color_calls),
        )


if __name__ == '__main__':
    unittest.main()
