# Maya 工具行为回归测试说明

本文档描述 `tests/test_maya_tools_behavior.py` 里的覆盖点。测试文件本身
不随开源仓库分发 (`.gitignore` 排除 `tests/`)，本 md 用于团队内部对齐
测试口径与新增测试时的参照。

## 覆盖清单

### `list_maya_objects`
- `detail=True` 时对无 `visibility` 属性的 DG 节点跳过, 不再抛
  `time1.visibility 不存在`
- `limit>0` 时返回 `{"items": [...], "total": N}`, `limit<=0` 或省略
  时返回 `List[str]`

### `set_maya_attr` (nodes.py)
- 字符串 `"2.5"` -> `2.5` (float)
- 字符串 `"false"` -> `False` (bool)
- 字符串 `"[1, 2, 3]"` -> 三分量向量 `setAttr(name, 1.0, 2.0, 3.0, type='double3')`

### `delete_maya_nodes` (nodes.py)
- 逗号分隔字符串 `"a, b, c"` -> `cmds.delete(['a','b','c'])`
- 分号分隔字符串 `"a; b"` -> `cmds.delete(['a','b'])`
- 列表 `['a', 'b']` -> `cmds.delete(['a','b'])`
- 有节点不存在时: `deleted` 只计算成功数, `missing` 报告缺失名

### `get_object_materials` (material.py)
- 通过 shape 反查 shadingEngine, 再解 `sg.surfaceShader` 拿材质名
- transform (`pCube1`) -> shape (`pCubeShape1`) -> SG (`initialShadingGroup`) -> mat (`lambert1`)

## 运行

```bash
cd /path/to/max_agent
python -m pytest tests/test_maya_tools_behavior.py -v
```

## 添加新测试的注意

- 全部通过 `mock.MagicMock()` 桩 `maya.cmds`, 不真的启动 Maya
- 使用 `BaseMayaToolTest` 基类, 自动做:
  - `set_current_dcc('maya')` + tearDown 复位
  - `sys.modules['maya.cmds']` 注入
  - `run_on_main` 直通调用, 绕开主线程调度
- 用真实的 API 调用签名 mock, 不要凭想象设 `side_effect`

## 已知盲区

- `_apply_transform` 里的 `makeIdentity` / `xform` 组合未做行为断言
- `create_maya_node` 的白名单越界只覆盖了 raise, 未测正常路径
- `add_maya_deformer` 的 nonLinear / lattice 分支未测
- 绑定链 `create_ik_handle` / `create_constraint` 未测

未来完善优先级: 绑定链 > deformer > 创建路径。
