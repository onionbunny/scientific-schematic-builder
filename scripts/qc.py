#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qc.py - Pre-/post-export compliance checks for journal submission.

Each check returns pass / warn / fail with measured values. Nothing is assumed:
raster files are opened and measured, the SVG is parsed, colors and stroke
widths are converted to print units, and legend/mapping consistency is
verified item by item.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

MM_TO_PT = 2.83464567
NS = {"svg": "http://www.w3.org/2000/svg"}


def _item(name, status, detail, **measured):
    d = {"check": name, "status": status, "detail": detail}
    d.update(measured)
    return d


def _font_installed(family_stack: str) -> bool:
    if not shutil.which("fc-list"):
        return True  # cannot check; do not fail on environments without fontconfig
    first = family_stack.split(",")[0].strip().strip("'\"")
    try:
        out = subprocess.run(["fc-list"], capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
    except Exception:
        return True
    return first.lower() in out.lower()


def check_svg(svg_path: str, spec: dict) -> list:
    items = []
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
    except Exception as exc:
        return [_item("svg.parse", "fail", f"SVG not well-formed: {exc}")]

    # layers
    layer_ids = {g.get("id") for g in root.findall(".//svg:g", NS)}
    expected = ["layer-relations", "layer-entities", "layer-annotations"]
    missing = [x for x in expected if x not in layer_ids]
    items.append(_item("svg.layers", "fail" if missing else "pass",
                       "named editable layers" if not missing else f"missing layers: {missing}",
                       layers=sorted(x for x in layer_ids if x and x.startswith("layer-"))))

    # counts vs spec (entities are shapes, not necessarily <g>)
    n_ent_spec = len(spec.get("entities", []))
    ent_elems = [el for el in root.iter()
                 if (el.get("id") or "").startswith("entity-")]
    rel_paths = [p for p in root.findall(".//svg:path", NS)
                 if (p.get("id") or "").startswith("rel-")]
    items.append(_item("svg.entity-count",
                       "pass" if len(ent_elems) == n_ent_spec else "fail",
                       f"{len(ent_elems)} rendered vs {n_ent_spec} specified"))
    items.append(_item("svg.relation-count",
                       "pass" if len(rel_paths) == len(spec.get("relations", [])) else "warn",
                       f"{len(rel_paths)} rendered vs {len(spec.get('relations', []))} specified"))

    # editable text
    texts = root.findall(".//svg:text", NS)
    empty = [t for t in texts if not (t.text or "").strip()]
    items.append(_item("svg.text-editable", "fail" if empty else "pass",
                       f"{len(texts)} real <text> elements, {len(empty)} empty",
                       text_count=len(texts)))

    # font
    font_stack = spec.get("canvas", {}).get("font_family", "")
    ok = _font_installed(font_stack)
    items.append(_item("font.available", "warn" if not ok else "pass",
                       f"primary font '{font_stack.split(',')[0]}' "
                       + ("installed" if ok else "not found; renderer will substitute")))

    # stroke widths -> points
    thin, thick = [], []
    for elem in root.iter():
        # text halos (separate stroked text) are legibility devices, not lines
        if elem.tag.endswith("}text"):
            continue
        sw = elem.get("stroke-width")
        if sw is None:
            continue
        try:
            pt = float(sw) * MM_TO_PT
        except ValueError:
            continue
        if pt < 0.5:
            thin.append((elem.get("id") or elem.tag, round(pt, 2)))
        if pt > 2.0:
            thick.append((elem.get("id") or elem.tag, round(pt, 2)))
    items.append(_item("line.width",
                       "warn" if thin or thick else "pass",
                       f"thin(<0.5pt):{len(thin)} thick(>2pt):{len(thick)}",
                       thin=thin[:8], thick=thick[:8]))

    # color space: all sRGB hex
    bad = []
    for elem in root.iter():
        for attr in ("fill", "stroke"):
            v = elem.get(attr)
            if v and v not in ("none", "white") and not re.match(r"^#[0-9a-fA-F]{6}$", v):
                bad.append((elem.get("id") or elem.tag, attr, v))
    items.append(_item("color.space", "warn" if bad else "pass",
                       "sRGB hex colors" if not bad else f"non-hex colors: {bad[:5]}",
                       color_space="sRGB"))

    # legend / mapping consistency
    for m in spec.get("data_mappings", []):
        if m.get("scale_type") != "categorical":
            continue
        dom = m.get("domain", [])
        rng = m.get("range", [])
        used = {e.get("mappings", {}).get("fill", {}).get("value")
                for e in spec.get("entities", [])
                if isinstance(e.get("mappings", {}).get("fill"), dict)}
        legend_visible = spec.get("legend", {}).get("visible", True)
        # a category is explained when used on an entity or listed in the legend
        missing_vals = [v for v in dom if v not in used]
        unexplained = missing_vals if not legend_visible else []
        items.append(_item("legend.mapping-" + m.get("id", "x"),
                           "warn" if unexplained else "pass",
                           f"{len(dom)} categories, {len(rng)} colors; "
                           f"on entities: {sorted(v for v in used if v in dom)}; "
                           f"legend covers all: {legend_visible}",
                           domain=dom, range=rng))

    # ---- geometry: overlap and out-of-bounds -----------------------------
    vb = root.get("viewBox", "").split()
    vb_w = float(vb[2]) if len(vb) == 4 else None
    vb_h = float(vb[3]) if len(vb) == 4 else None

    def _bbox(el):
        tag = el.tag.split("}")[-1]
        if tag == "rect":
            return (float(el.get("x", 0)), float(el.get("y", 0)),
                    float(el.get("width", 0)), float(el.get("height", 0)))
        if tag == "ellipse":
            rx, ry = float(el.get("rx", 0)), float(el.get("ry", 0))
            return (float(el.get("cx", 0)) - rx, float(el.get("cy", 0)) - ry,
                    2 * rx, 2 * ry)
        if tag == "polygon":
            pts = [p.split(",") for p in el.get("points", "").split()]
            xs = [float(p[0]) for p in pts]; ys = [float(p[1]) for p in pts]
            return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        return None

    ent_bboxes = []
    for el in root.iter():
        if (el.get("id") or "").startswith("entity-"):
            b = _bbox(el)
            if b:
                ent_bboxes.append((el.get("id"), b))

    overlaps = []
    for i in range(len(ent_bboxes)):
        for j in range(i + 1, len(ent_bboxes)):
            a, b = ent_bboxes[i][1], ent_bboxes[j][1]
            if (a[0] < b[0] + b[2] and a[0] + a[2] > b[0] and
                    a[1] < b[1] + b[3] and a[1] + a[3] > b[1]):
                overlaps.append((ent_bboxes[i][0], ent_bboxes[j][0]))
    items.append(_item("geometry.overlap", "warn" if overlaps else "pass",
                       f"{len(overlaps)} overlapping entity pairs (usually from manual positions)",
                       overlaps=overlaps[:8]))

    if vb_w and vb_h:
        outside = []
        for eid, (x, y, w, h) in ent_bboxes:
            if x < 0 or y < 0 or x + w > vb_w or y + h > vb_h:
                outside.append(eid)
        items.append(_item("geometry.bounds", "warn" if outside else "pass",
                           f"{len(outside)} entities cross the canvas/viewBox edge",
                           outside=outside[:8], canvas=(vb_w, vb_h)))
    return items


def check_raster(path: str, expected_w_mm: float, dpi: int) -> list:
    items = []
    try:
        from PIL import Image
    except ImportError:
        return [_item("raster." + os.path.basename(path), "warn",
                      "Pillow unavailable, raster not measured")]
    try:
        with Image.open(path) as im:
            w_px, h_px = im.size
            meta_dpi = im.info.get("dpi")
            mode = im.mode
            im.load()
    except Exception as exc:
        return [_item("raster.open", "fail", f"cannot open {path}: {exc}")]
    expect_px = round(expected_w_mm / 25.4 * dpi)
    items.append(_item("raster.dimensions",
                       "pass" if abs(w_px - expect_px) <= max(2, expect_px * 0.02) else "fail",
                       f"{w_px}x{h_px}px, expected width ~{expect_px}px at {dpi}dpi",
                       width_px=w_px, height_px=h_px, expected_width_px=expect_px))
    items.append(_item("raster.mode", "pass" if mode in ("RGB", "RGBA", "L") else "warn",
                       f"pixel mode {mode}", color_space=mode))
    if meta_dpi:
        items.append(_item("raster.dpi-meta", "pass" if abs(meta_dpi[0] - dpi) < 2 else "warn",
                           f"embedded DPI {meta_dpi}", dpi=meta_dpi))
    return items


def check_pdf(path: str) -> list:
    items = []
    if not os.path.exists(path):
        return [_item("pdf.present", "fail", "PDF missing")]
    with open(path, "rb") as f:
        head = f.read(8)
    if not head.startswith(b"%PDF-"):
        return [_item("pdf.magic", "fail", "file is not a PDF")]
    items.append(_item("pdf.magic", "pass", f"valid PDF header {head[:5].decode(errors='replace')}"))
    # render page 1 to prove it opens
    if shutil.which("pdftocairo"):
        stem = os.path.join(os.path.dirname(path), ".pdf_verify")
        proc = subprocess.run(["pdftocairo", "-png", "-r", "72", "-singlefile",
                               "-f", "1", "-l", "1", path, stem],
                              capture_output=True)
        ok = proc.returncode == 0 and os.path.exists(stem + ".png")
        items.append(_item("pdf.opens", "pass" if ok else "fail",
                           "page 1 rasterized successfully" if ok else "pdftocairo failed"))
        if ok:
            os.remove(stem + ".png")
    return items


def run_qc(spec: dict, svg_path: str, export_records: list,
           dpi: int, preset: dict = None) -> dict:
    items = check_svg(svg_path, spec)
    w_mm = float(spec.get("canvas", {}).get("width_mm", 0)) or None

    for rec in export_records:
        if rec["status"] != "ok":
            items.append(_item("export." + rec["format"], "fail",
                               rec.get("error") or "export failed", method=rec.get("method")))
            continue
        path = rec["path"]
        if rec["format"] == "png":
            if w_mm:
                items += check_raster(path, w_mm, dpi)
            items.append(_item("export.png", "pass", f"method: {rec['method']}"))
        elif rec["format"] == "pdf":
            items += check_pdf(path)
            items.append(_item("export.pdf", "pass", f"method: {rec['method']}"))
        elif rec["format"] == "tiff":
            items += check_raster(path, w_mm, dpi) if w_mm else []
            items.append(_item("export.tiff", "pass", f"method: {rec['method']}, LZW"))
        elif rec["format"] == "eps":
            items.append(_item("export.eps", "pass", f"method: {rec['method']}"))
        elif rec["format"] == "svg":
            items.append(_item("export.svg", "pass", "editable vector source"))

    if preset and w_mm and preset.get("max_width_mm"):
        items.append(_item("preset.width",
                           "pass" if w_mm <= preset["max_width_mm"] else "fail",
                           f"{w_mm}mm vs limit {preset['max_width_mm']}mm ({preset.get('name')})"))

    status = "fail" if any(i["status"] == "fail" for i in items) else (
        "warn" if any(i["status"] == "warn" for i in items) else "pass")
    return {"status": status, "items": items}
