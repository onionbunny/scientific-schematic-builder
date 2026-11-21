# 最小调用示例（合成数据，非官方测试案例）

`example_spec.json` 是一个虚构的微型通路，仅用于演示接口与完整流程：

```bash
# 1. 单独校验
python3 scripts/validate.py examples/example_spec.json

# 2. 端到端运行：可编辑 SVG + 600 DPI 的 PDF/PNG/TIFF
python3 scripts/run.py \
  --spec examples/example_spec.json \
  --workdir /tmp/fig-work \
  --outdir /tmp/fig-output \
  --formats svg,pdf,png,tiff \
  --dpi 600 --preset nature
```

输出目录中：

- `figure_synthetic-example.svg`：分层可编辑源文件，真实 `<text>`，命名图层，元素带 `data-source-refs`；
- `figure_synthetic-example.pdf` / `.png` / `.tiff`：导出件，result.json 记录实际转换方法与测量值；
- `result.json`：完整结构化结果（status、artifacts、manifest、validation、qc、uncertainty、change_log）。

```bash
# 3. 数据图合成示例（chart_spec.json，双 series 柱状图）
python3 scripts/run.py \
  --spec examples/chart_spec.json \
  --workdir /tmp/chart-work --outdir /tmp/chart-output \
  --formats svg,pdf,png,tiff --dpi 600 --preset nature

# 4. 反向工程：把生成的 SVG 还原为可编辑 spec 骨架
python3 scripts/svg2spec.py /tmp/fig-output/figure_synthetic-example.svg /tmp/skeleton.json
```

示意输出以运行时实际生成的文件为准；本目录不预置任何官方案例答案。
反向骨架中标 `inferred` 的元素与 uncertainty 必须对照原始材料核对，不能直接当作确认结果。
