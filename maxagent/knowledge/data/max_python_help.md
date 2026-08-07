# 3ds Max Python Help（占位文件）

> **本文档是打包占位文件。** 请将 Autodesk 官方 `Max-Python-Help_2023.md`
> 直接覆盖本文件（保留文件名 `max_python_help.md`），下次启动 Max 时会自动
> 检测到 mtime/size 变化并重建索引。

## 关于本文件

MaxAgent 使用 BM25 全文检索为 LLM 提供 pymxs / MAXScript 官方参考。
索引数据源指向 `maxagent/knowledge/data/max_python_help.md`——即本文件。
未替换前 LLM 仍可用，只是命中率取决于本占位内容的覆盖面。

## 常用查询示例（供索引 smoke test）

### create_box 参数
`Box name:"MyBox" length:100 width:50 height:80 pos:[10,0,0]`
参数：length 沿 Y 轴，width 沿 X 轴，height 沿 Z 轴。pivot 位于底面中心。

### create_teapot 参数
`Teapot name:"MyTeapot" radius:30 pos:[0,0,0]`
参数：radius 控制整体大小。pivot 位于几何中心。

### 修改器（modifier）
- 添加：`addModifier obj (TurboSmooth())`
- 常见类：TurboSmooth / MeshSmooth / Bend / Twist / Taper / Noisemodifier
- 注意：Noise 修改器的类名是 **Noisemodifier**（无下划线）

### pymxs 属性 setter 陷阱
```python
# 不推荐（Max 返回 copy 时静默失败）
node.pos = Point3(10, 0, 0)

# 推荐路径 1：官方 setProperty
rt.setProperty(node, 'pos', Point3(10, 0, 0))

# 推荐路径 2：pymxs setmxsprop
node.setmxsprop('pos', Point3(10, 0, 0))
```

### 灯光
- `omniLight name:"L1" multiplier:1.0 rgb:[255,255,255]`
- `targetSpot name:"S1" multiplier:1.2 falloff:60`

### 相机
- `Freecamera name:"C1" fov:45`
- `Targetcamera name:"C2" fov:35 targetDistance:200`

### 材质
- 物理材质：`PhysicalMaterial name:"Mat1"`
- 标准材质：`Standardmaterial name:"Mat2"`
- 赋材质：`obj.material = mtl`（推荐走 rt.setProperty(obj, 'material', mtl)）

### 变换
```python
# 位置
rt.setProperty(node, 'pos', Point3(x, y, z))
# 旋转（欧拉角转四元数）
rt.setProperty(node, 'rotation', rt.eulerToQuat(rt.eulerAngles(rx, ry, rz)))
# 缩放
rt.setProperty(node, 'scale', Point3(sx, sy, sz))
```

### 场景查询
- `objects` — 所有对象数组
- `selection` — 当前选中集
- `$` — MAXScript 全局选择变量
- `getNodeByName name` — 按名查找（不区分大小写）
- `getObjectClass obj` — 对象类
- `superClassOf obj` / `classOf obj` — 类型谱系

### Undo
```python
with pymxs.undo(True, "MyOperation"):
    # 你的操作
    pass
```
