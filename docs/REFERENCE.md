# 参考手册（字段 / CLI / 输出契约）

## 1. figure_spec.json 字段

| 字段 | 说明 |
|---|---|
| `spec_version` | 规格版本，当前 "1.0" |
| `case_id` | 案例标识，与输入 case.json 对齐 |
| `sources[]` | 来源清单：id、type、locator（文件+页码/表行）、可选 quote |
| `canvas` | width_mm/height_mm（缺省自动）、margin_mm、font_size_pt、font_family、rankdir（LR/TB）、node_w_mm/node_h_mm、gap_x_mm/gap_y_mm、background |
| `entities[]` | id、label（支持 `\n` 换行）、kind、shape、group、fill/stroke、font_size_pt、source_refs、inferred、uncertainty、mappings |
| `groups[]` | id、label、contains（实体 id 列表）、fill/stroke、source_refs |
| `relations[]` | id、from、to、type、direction（forward/bidirectional）、label、color、dash、marker、source_refs |
| `data_mappings[]` | id、scale_type（categorical/continuous）、domain+range 或 stops、source_refs |
| `charts[]` | id、type（bar/line/scatter）、位置与尺寸 mm、categories、轴标题与单位、series（name/values/color/mapping/source_refs） |
| `legend` | visible、title、width_mm、items[]；缺省自动汇总关系符号与映射色 |
| `annotations[]` | kind（panel-label/note）、text、x_mm、y_mm |
| `layout.positions` | 实体 id → {x_mm, y_mm} 手工坐标，覆盖自动布局 |
| `uncertainty[]` / `change_log[]` | 不确定性与修改记录 |

实体取色优先顺序：显式 fill/stroke → `mappings` 引用 data_mapping → kind 默认样式。

## 2. 关系类型速查

| type | 线型 | 端点符号 |
|---|---|---|
| activation | 实线 | 实心箭头 |
| inhibition | 实线 | T 型杠 |
| indirect_activation / indirect_inhibition | 虚线 | 箭头 / T 杠 |
| association / binding | 实线 | 无 |
| translocation | 虚线 | 空心箭头 |
| process | 实线 | 实心箭头 |
| production | 实线 | 空心箭头 |
| phosphorylation / dephosphorylation | 实线 | 实心箭头 |
| degradation | 虚线 | T 杠 |
| unknown | 实线 | 空心箭头（须记 uncertainty） |

包含关系只能用 groups 分组框表达；反馈环（DFS 回边）自动侧边绕行，不与主链重叠。

## 3. CLI

```bash
# 校验
python3 scripts/validate.py work/figure_spec.json

# 端到端
python3 scripts/run.py --spec work/figure_spec.json \
  --workdir work --outdir output \
  --formats svg,pdf,png,tiff,eps \
  --dpi 600 --preset nature \
  --case-id case-01 --contract /input/case.json

# 反向工程（编辑已有 SVG）
python3 scripts/svg2spec.py input.svg work/skeleton.json
```

退出码：0 = complete；1 = partial/failed；2 = 入口参数非法（如 DPI 非正）。
formats 顺序任意：tiff 自动依赖 png、eps 自动依赖 pdf，无需手工排序。

## 4. result.json 输出契约

- `case_id`、`status`（complete/partial/failed）、`summary`
- `artifacts[]`：editable_source（SVG，layered/editable_text 标志）与各 export（format、method、dpi、status、error）
- `manifest`：sources、entities、groups、relations、data_mappings（含各自 source_refs）
- `validation`：errors/warnings 计数与明细
- `render_warnings[]`：渲染期问题（映射无法解析而回退、未知 preset 等）
- `qc`：pass/warn/fail 及逐项测量值（像素尺寸、DPI、线宽 pt、字体、色彩空间、geometry.overlap/bounds 几何重叠与出界）
- `uncertainty[]`：推断元素与极性不明项
- `change_log[]`：编辑任务的逐条改动
- `toolchain`、`runtime`：实际检测到的工具与运行环境
- `output_contract_input`：回显输入契约（若提供）
- `contract_coverage`：契约要求字段的 covered/missing 机械核对（若提供契约）

## 5. 边界与失败语义

- 不做统计检验与统计方法选择；不做海报/艺术插画；不写正文。
- 材料缺失、矛盾、字体缺失、转换器缺失均如实记录，不补造数据或软件状态。
- 部分交付必须写明已完成范围与剩余动作。
