#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run.py - End-to-end pipeline: figure_spec.json -> editable SVG -> exports -> QC.

    python3 scripts/run.py --spec work/figure_spec.json \
        --outdir /app/output --workdir /app/work \
        --formats svg pdf png tiff --dpi 600 --preset nature \
        --case-id case-01 --contract /input/case.json

Artifacts and result.json land in --outdir. The run is honest: validation
errors, failed exports, inferred elements and uncertainties are all recorded
rather than papered over.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from figsvg import render_svg, RELATION_STYLE, FONT_STACK  # noqa
from validate import validate_spec, summarize  # noqa
from export import export_all, detect_tools  # noqa
from qc import run_qc  # noqa


def load_presets(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--formats", default="svg,pdf,png")
    ap.add_argument("--dpi", type=int, default=600)
    ap.add_argument("--preset", default=None)
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--contract", default=None)
    ap.add_argument("--basename", default=None)
    args = ap.parse_args()

    if args.dpi <= 0:
        print(json.dumps({"status": "failed",
                          "error": f"--dpi must be a positive integer, got {args.dpi}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    dpi_note = None
    if args.dpi > 2400:
        dpi_note = f"DPI {args.dpi} is unusually high; raster files may be very large"

    os.makedirs(args.outdir, exist_ok=True)
    if args.workdir:
        os.makedirs(args.workdir, exist_ok=True)

    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)

    case_id = args.case_id or spec.get("case_id") or "unknown-case"
    basename = args.basename or "figure_" + str(case_id).replace("/", "_")
    formats = [x.strip() for x in args.formats.split(",") if x.strip()]
    if "svg" not in formats:
        formats.insert(0, "svg")

    # 1. validation gate ---------------------------------------------------
    validation = summarize(validate_spec(spec))
    hard_errors = validation["errors"]

    # 2. render editable source --------------------------------------------
    svg_path = os.path.join(args.outdir, basename + ".svg")
    render_status, render_error = "ok", None
    render_warnings = []
    try:
        from figsvg import SvgRenderer
        renderer = SvgRenderer(spec)
        svg = renderer.render()
        render_warnings = list(renderer.warnings)
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(svg)
    except Exception as exc:
        render_status, render_error = "failed", str(exc)

    # 3. exports ------------------------------------------------------------
    tools = detect_tools()
    export_records = []
    if render_status == "ok":
        export_records = export_all(svg_path, args.outdir, basename, formats, args.dpi, tools)

    # 4. preset + QC --------------------------------------------------------
    preset = None
    unknown_preset = False
    if args.preset:
        preset_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "resources", "presets.json")
        presets = load_presets(preset_path)
        preset = presets.get(args.preset)
        if preset:
            preset = dict(preset)
            preset["name"] = args.preset
        else:
            unknown_preset = True
    qc = (run_qc(spec, svg_path, export_records, args.dpi, preset)
          if render_status == "ok" else {"status": "fail", "items": []})

    # 5. manifest -----------------------------------------------------------
    manifest = {
        "sources": spec.get("sources", []),
        "entities": [{"id": e.get("id"), "label": e.get("label"),
                      "kind": e.get("kind"), "group": e.get("group"),
                      "source_refs": e.get("source_refs", []),
                      "inferred": e.get("inferred", False)}
                     for e in spec.get("entities", [])],
        "groups": [{"id": g.get("id"), "label": g.get("label"),
                    "contains": g.get("contains", []),
                    "source_refs": g.get("source_refs", [])}
                   for g in spec.get("groups", [])],
        "relations": [{"id": r.get("id"), "from": r.get("from"), "to": r.get("to"),
                       "type": r.get("type"), "direction": r.get("direction", "forward"),
                       "label": r.get("label"), "source_refs": r.get("source_refs", []),
                       "inferred": r.get("inferred", False)}
                      for r in spec.get("relations", [])],
        "data_mappings": spec.get("data_mappings", []),
    }

    uncertainty = list(spec.get("uncertainty", []))
    for e in spec.get("entities", []):
        if e.get("inferred"):
            uncertainty.append({"element": e["id"],
                                "note": e.get("uncertainty", "inferred entity, not directly stated in sources")})
    for r in spec.get("relations", []):
        if r.get("inferred") or r.get("type") == "unknown":
            uncertainty.append({"element": r.get("id"),
                                "note": r.get("uncertainty", "relation type not explicitly stated in sources")})

    artifacts = []
    if render_status == "ok":
        artifacts.append({"role": "editable_source", "path": svg_path, "format": "svg",
                          "layered": True, "editable_text": True,
                          "font_family": spec.get("canvas", {}).get("font_family", FONT_STACK)})
    for rec in export_records:
        if rec["format"] == "svg":
            continue
        artifacts.append({
            "role": "export", "path": rec["path"], "format": rec["format"],
            "status": rec["status"], "method": rec["method"], "error": rec["error"],
            "dpi": args.dpi if rec["format"] in ("png", "tiff") else None,
        })

    failed_exports = [r["format"] for r in export_records if r["status"] != "ok"]
    if render_status != "ok":
        status = "failed"
    elif hard_errors or failed_exports or qc["status"] == "fail":
        status = "partial"
    else:
        status = "complete"

    contract_echo = None
    if args.contract and os.path.exists(args.contract):
        try:
            with open(args.contract, encoding="utf-8") as f:
                contract_echo = json.load(f)
        except Exception:
            contract_echo = None

    result = {
        "case_id": case_id,
        "status": status,
        "summary": (f"Rendered {len(spec.get('entities', []))} entities and "
                    f"{len(spec.get('relations', []))} relations; "
                    f"exports: {', '.join(r['format'] + ':' + r['status'] for r in export_records) or 'none'}"),
        "artifacts": artifacts,
        "manifest": manifest,
        "validation": validation,
        "render_warnings": render_warnings,
        "qc": qc,
        "uncertainty": uncertainty,
        "change_log": spec.get("change_log", []),
        "toolchain": tools,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "dpi": args.dpi,
            "dpi_note": dpi_note,
            "formats": formats,
            "preset": args.preset,
            "unknown_preset": unknown_preset,
            "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    }
    if unknown_preset:
        render_warnings.append(
            f"preset '{args.preset}' is not defined in presets.json "
            f"(nature/science/cell/plos/generic); no preset checks applied")
    if contract_echo is not None:
        # keep the original contract visible so the harness can cross-check
        oc = contract_echo.get("output_contract", contract_echo)
        result["output_contract_input"] = oc
        # mechanical coverage check: which contract fields have a matching
        # top-level result key (nested fields are mapped where known)
        if isinstance(oc, dict):
            required = list(oc.keys())
        elif isinstance(oc, list):
            required = [str(x) for x in oc]
        else:
            required = []
        nested = {"sources": "manifest", "entities": "manifest", "groups": "manifest",
                  "relations": "manifest", "data_mappings": "manifest",
                  "editable_source": "artifacts", "exports": "artifacts"}
        covered, missing = [], []
        for key in required:
            if key in result:
                covered.append(key)
            elif nested.get(key) in result:
                covered.append(f"{key} (in {nested[key]})")
            else:
                missing.append(key)
        result["contract_coverage"] = {"required": required, "covered": covered,
                                       "missing": missing}

    result_path = os.path.join(args.outdir, "result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps({"case_id": case_id, "status": status,
                      "validation_errors": hard_errors,
                      "failed_exports": failed_exports,
                      "qc_status": qc["status"],
                      "result": result_path,
                      "render_error": render_error}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
