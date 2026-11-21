---
name: scientific-schematic-builder
description: 科研示意图/机制图/流程图/概念图的生成与编辑，从论文、数据表、草图中抽取实体与关系，产出分层可编辑 SVG 源文件并导出合规 PDF/PNG/TIFF/EPS。Use when the task asks for scientific schematics, mechanism/pathway diagrams, flowcharts, relationship diagrams, editable figure sources, data-driven legend remapping, or journal export compliance (DPI, fonts, line width, color space). 触发词包括科研绘图、机制图、信号通路、实验流程、示意图、editable SVG、figure editing、export preset。
---

# Scientific Schematic Builder

把来源材料转成可直接进论文、答辩稿、基金本子的示意图。判断标准不是好看，而是三件可检验的事：

1. 每个科学实体、关系、数值与单位都能追溯到来源材料；
2. 图例、配色、标记与数据逐项一致，数据改动后可重绘；
3. 同时交付分层可编辑源文件（SVG）与符合目标要求的导出件。

**边界**：本 Skill 负责流程、结构关系、概念示意图与图例映射；不做统计方法选择与数值检验，不做海报/艺术插画，不写论文正文。

## 资源清单

```
scripts/figsvg.py     纯标准库渲染器：布局、关系符号、分层 SVG、数据驱动图表
scripts/validate.py   来源追溯闸门：无来源的实体/关系直接报错，含分组一致性/几何数值校验
scripts/export.py     导出适配：cairosvg/rsvg/inkscape/chromium/pdftocairo 分级回退，自动解析 tiff/eps 依赖
scripts/qc.py         合规检查：图层、字体、线宽(pt)、DPI、色彩空间、图例一致性、几何重叠/出界
scripts/run.py        端到端流水线，产出 result.json
scripts/svg2spec.py   反向工程：从现有 SVG 提取可编辑 spec 骨架（编辑场景）
resources/spec_schema.json      figure_spec 的 JSON Schema
resources/relation_vocabulary.json  关系符号词表与硬性规则
resources/presets.json          nature/science/cell/plos/generic 导出预设
resources/palettes.json         色盲友好色板与连续色阶
examples/example_spec.json      最小合成示例（机制图，非官方案例）
examples/chart_spec.json        合成数据图示例（柱状图，非官方案例）
```

运行只依赖 Python 3 标准库；Pillow、cairosvg、chromium 等存在时自动启用，缺失时如实降级，绝不虚报导出结果。

## 工作流

### 第 0 步：读取真实输入

- 完整阅读 `/input/case.json` 与 `task.md`（或等价的任务文件），确认任务类型（新建/编辑）、目标格式、尺寸、期刊或模板参数、output_contract 字段。
- 逐一打开实际材料：PDF 用 `pdftotext`/Python 解析真实页面（不要凭文件名猜内容）；CSV/XLSX 用 `csv`/`openpyxl` 读取真实行列；图片与草图实际查看。材料里没有的基因名、浓度、时间点、单位，一律不得出现在图中。
- 若 case.json 声明了 output_contract，记下其全部字段，最终 result.json 必须逐项覆盖。

### 第 1 步：抽取实体—关系清单（正确性的根基）

先在工作区写一份抽取笔记，再落成 `figure_spec.json`，规则如下：

- **实体**：每个实体记录 id、显示名、类别（gene/protein/receptor/enzyme/metabolite/process…）、来源定位（文件+页码/表行/图区）。
- **关系词表**（见 `resources/relation_vocabulary.json`）：
  - 激活/促进 → 实心箭头；抑制/阻遏 → T 型杠；结合/关联 → 无箭头平线；
  - 间接激活/抑制、转位、降解 → 虚线；产生/释放 → 空心箭头；
  - 方向严格按来源中的因果/上下游顺序转写，from/to 颠倒即生物学错误。
- **包含关系**：A 包含 B 用分组框（groups）表达，**禁止**画成箭头；并列、上下游、时序不得混为一谈。
- **极性不明**：来源只说“参与/相关”而无方向或正负时，用 `type: "unknown"` 并写入 uncertainty，禁止猜测激活或抑制。
- **数值与单位**：浓度、时间点、倍数、p 值、单位逐字转录，不做单位换算以外的改动；每个数值标注来源行。
- **矛盾处理**：材料之间或材料与草图冲突时，不要静默选边，在 `uncertainty` 中并列证据并按任务要求处理；无要求时保留来源原话并标记待人工确认。
- 证据强度使用“证据支持/部分支持/证据不一致/待检验”等表述，避免绝对化判定标签。

### 第 2 步：编写 figure_spec.json

按 `resources/spec_schema.json` 组织，关键字段：

- `sources`：全部来源清单（id、locator、必要时 quote）。
- `entities` / `groups` / `relations`：每项必须带非空 `source_refs`；推断项显式 `"inferred": true` 并附 uncertainty。
- `data_mappings`：分类映射 domain↔range 数量必须相等；实体通过 `mappings: {"fill": {"mapping": "DM1", "value": "upregulated"}}` 取色，禁止直接硬编码数据色后又另写图例。
- `charts`（如任务涉及数据图）：series 的 values 长度与 categories 一致，颜色来自显式值或 data_mapping，每条 series 带 source_refs。
- `legend`、`annotations`（子图编号 A/B/C、注释）、`canvas`（尺寸 mm、字号 pt、rankdir LR/TB）、`layout.positions`（手工微调坐标）。

先跑校验闸门：

```bash
python3 scripts/validate.py work/figure_spec.json
```

存在 error 必须先回材料修正，不得带错渲染。

### 第 3 步：渲染与视觉迭代

```bash
python3 scripts/run.py --spec work/figure_spec.json --workdir work --outdir output \
  --case-id <case_id> --contract /input/case.json \
  --formats svg,pdf,png --dpi 600 --preset nature
```

- 打开生成的 PNG 实际查看：重叠、文字溢出、引导线交叉、箭头/T 杠方向、图例与实体颜色是否一致。
- 需要调整时，在 spec 的 `layout.positions` 中写入坐标（或调整分组、rankdir、字号）后重跑；脚本确定性渲染，重复运行结果稳定。
- SVG 中 1 单位=1mm，文字为真实 `<text>`，实体/关系/图例/注释分属命名图层，每个元素带 `data-source-refs`，可在 Illustrator/Inkscape/文本编辑器中继续编辑。

### 第 4 步：按目标导出

- 任务明确给参数时以任务为准；否则用 `--preset`（nature/science/cell/plos/generic）。线稿图默认 600 DPI、含照片/灰度图 300 DPI；线宽不低于 0.5pt；字号不低于预设最小字号。
- 需要 TIFF/EPS 时加入 `--formats`：TIFF 由同位图 PNG 经 LZW 压缩并写入 DPI 元数据；EPS 经 inkscape 或 poppler 生成。
- 导出方法记录在 result.json（如 `chromium --headless=new pdf`）；某格式无可用转换器时该条目标记 failed 并说明缺失工具，整体状态降为 partial，不伪造文件。

### 第 5 步：QC 与 result.json

`run.py` 自动执行 QC 并写出 `result.json`，包含：case_id、status（complete/partial/failed）、artifacts（含 editable_source 与各 export 的方法/DPI）、manifest（实体/关系/分组/映射及来源）、validation、render_warnings（渲染期映射回退等）、qc（含几何重叠/出界）、uncertainty、change_log、toolchain、runtime；提供契约时另含 contract_coverage（要求字段的覆盖/缺失核对）。

- 可编辑 Office/幻灯片/绘图产物同时保留真实渲染的 PDF/PNG 预览，便于复核。
- 部分完成时必须写明已完成范围、失败原因与剩余动作；材料缺失、无法判断、运行失败分别说明，不得编造软件状态或科学数据。

## 编辑已有图稿的附加流程

1. 输入为分层 SVG：先用 `python3 scripts/svg2spec.py input.svg work/skeleton.json` 确定性提取实体、分组、关系骨架；几何无法还原的语义（虚线 T 杠的歧义、关系端点、原始来源）会显式写入 uncertainty，逐项核对补全后再渲染。输入为 PDF：用 `pdftotext`/`pdftocairo`/`pdfimages` 提取文字与结构；输入为位图：实际查看并重建实体—关系清单。
2. 仅改动任务指定的元素，未要求修改的实体、关系、配色、坐标保持不变。
3. 在 `change_log` 逐条记录：元素 id、改动前→改动后、依据来源、确认状态；数据驱动的图例改动通过修改 data_mappings 后重绘，保证图例与实体同步。

## 异常处理

| 情况 | 处理 |
|---|---|
| 来源材料缺失或无法打开 | 状态 partial/failed，记录缺失文件与受影响元素，不补造内容 |
| 描述矛盾、数据与图例不符 | 列入 uncertainty/QC fail，并列证据，待人工确认或按任务指令处理 |
| 字体缺失 | 警告并报告实际替代字体；可在 font_family 中改用已安装字体后重跑 |
| 导出失败/转换器缺失 | 该格式标 failed 与原因，保留 SVG 源文件，尝试回退链中的下一转换器 |
| 关系含环/自指 | 渲染器自动断环并支持自环，自环标签置于环顶，断环边在 manifest 中可识别 |
| 分组声明与 contains 不一致、实体跨多组 | 校验报 group.consistency/multi-membership 硬错误，先回材料修正 |
| 非法 DPI/未知 preset | 非正 DPI 入口直接拒绝；未知 preset 记 warning 且不做预设检查 |
| 手动位置导致重叠/出界 | QC geometry.overlap/bounds 告警，调整 layout.positions 后重跑 |

## 预算纪律

官方运行有模型请求与时间预算。先保存可运行的最小诚实版本（哪怕只覆盖核心实体与 SVG+PNG），再增量补全实体、导出格式与 QC；不要等到最后才写文件。
