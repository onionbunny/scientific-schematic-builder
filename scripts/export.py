#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export.py - Editable SVG -> journal-ready export adapters.

The converter is chosen at runtime from what is actually installed, with a
deterministic fallback chain. Every conversion records the exact method used
(or the failure reason), so result.json never claims an export it did not do.

SVG  (primary editable source, text + layers)
PDF  (vector, fonts embedded by the rendering engine)
PNG  (raster at an explicit DPI)
TIFF (raster, LZW, DPI metadata written)
EPS  (vector via inkscape or poppler)
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
from urllib.request import pathname2url


def stamp_png_dpi(path, dpi):
    """Losslessly write DPI metadata into a PNG (best-effort)."""
    tmp = path + ".dpi.png"
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.load()
            im.save(tmp, format="PNG", dpi=(dpi, dpi))
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)

CSS_DPI = 96.0


def detect_tools() -> dict:
    tools = {
        "cairosvg": None,
        "pillow": None,
        "rsvg-convert": shutil.which("rsvg-convert"),
        "inkscape": shutil.which("inkscape"),
        "chromium": shutil.which("chromium") or shutil.which("chromium-browser")
                    or shutil.which("google-chrome"),
        "pdftocairo": shutil.which("pdftocairo"),
        "convert": shutil.which("convert"),
    }
    try:
        import cairosvg  # noqa
        tools["cairosvg"] = getattr(cairosvg, "__version__", "installed")
    except Exception:
        pass
    try:
        import PIL  # noqa
        tools["pillow"] = PIL.__version__
    except Exception:
        pass
    return tools


def _run(cmd, timeout=120):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout)
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def _svg_size_mm(svg_text):
    m = re.search(r'width="([\d.]+)mm"\s+height="([\d.]+)mm"', svg_text)
    if not m:
        raise ValueError("SVG must declare width/height in mm")
    return float(m.group(1)), float(m.group(2))


def _wrapper_html(svg_text, w_mm, h_mm):
    # work in exact CSS px (fractional allowed) and forbid scrollbars, so
    # rounding can never produce clipping via an overflow scroll
    w_css = w_mm / 25.4 * CSS_DPI
    h_css = h_mm / 25.4 * CSS_DPI
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        f"@page{{size:{w_mm}mm {h_mm}mm;margin:0}}"
        "html,body{margin:0;padding:0;background:white;overflow:hidden}"
        f"svg{{display:block;width:{w_css:.3f}px;height:{h_css:.3f}px}}"
        "</style></head><body>" + svg_text + "</body></html>")


def _chromium(tools, html_path, out_path, mode, w_mm, h_mm, dpi):
    """mode: 'pdf' or 'png'. Returns (ok, detail).

    Headless Chromium's real viewport may differ from --window-size across
    builds, so PNG mode requests an oversized window and the image is then
    cropped deterministically to the exact target pixel dimensions (the SVG
    is anchored at 0,0)."""
    base = [tools["chromium"], "--no-sandbox", "--disable-gpu",
            "--disable-dev-shm-usage", "--hide-scrollbars"]
    target_w = max(1, round(w_mm / 25.4 * dpi))
    target_h = max(1, round(h_mm / 25.4 * dpi))
    page_url = "file://" + pathname2url(os.path.abspath(html_path))

    for headless in ("--headless=new", "--headless"):
        if mode == "pdf":
            cmd = base + [headless, "--no-pdf-header-footer",
                          f"--print-to-pdf={out_path}", page_url]
            try:
                rc, out, err = _run(cmd, timeout=90)
            except subprocess.TimeoutExpired:
                continue
            if rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return True, f"chromium {headless} pdf"
            continue

        # PNG: oversized window, then exact crop + DPI stamp
        scale = dpi / CSS_DPI
        raw = out_path + ".raw.png"
        for extra in (60, 140, 300):
            wpx = max(1, math.ceil(w_mm / 25.4 * CSS_DPI) + extra)
            hpx = max(1, math.ceil(h_mm / 25.4 * CSS_DPI) + extra)
            if os.path.exists(raw):
                os.remove(raw)
            cmd = base + [headless, f"--force-device-scale-factor={scale:.4f}",
                          f"--window-size={wpx},{hpx}",
                          f"--screenshot={raw}", page_url]
            try:
                rc, o, e = _run(cmd, timeout=90)
            except subprocess.TimeoutExpired:
                continue
            if not (rc == 0 and os.path.exists(raw)):
                continue
            try:
                from PIL import Image
                with Image.open(raw) as im:
                    im.load()
                    if im.size[0] < target_w or im.size[1] < target_h:
                        continue  # viewport still too small; retry bigger
                    crop = im.crop((0, 0, target_w, target_h))
                    crop.save(out_path, format="PNG", dpi=(dpi, dpi))
                os.remove(raw)
                return True, f"chromium {headless} png(cropped)"
            except Exception:
                continue
        if os.path.exists(raw):
            os.remove(raw)
    return False, "chromium headless conversion failed"


def export_all(svg_path: str, out_dir: str, basename: str,
               formats, dpi: int, tools=None) -> list:
    tools = tools or detect_tools()
    os.makedirs(out_dir, exist_ok=True)
    with open(svg_path, encoding="utf-8") as f:
        svg_text = f.read()
    w_mm, h_mm = _svg_size_mm(svg_text)
    results = []
    pdf_path = os.path.join(out_dir, basename + ".pdf")
    png_path = os.path.join(out_dir, basename + ".png")

    html_path = os.path.join(out_dir, "." + basename + "_wrapper.html")
    # wrapper is written lazily but cheap to prepare once
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(_wrapper_html(svg_text, w_mm, h_mm))

    # dependency-aware work list: TIFF needs PNG, EPS needs PDF, regardless
    # of the order the user listed the requested formats
    wanted_set = set(formats)
    worklist, seen = [], set()

    def _add(f):
        if f not in seen:
            seen.add(f)
            worklist.append(f)

    for f in formats:
        if f == "tiff":
            _add("png")
        if f == "eps":
            _add("pdf")
        _add(f)

    dep_failure = {}
    for fmt in worklist:
        target = os.path.join(out_dir, f"{basename}.{fmt}")
        is_support = fmt not in wanted_set
        rec = {"format": fmt, "path": target, "status": "failed", "method": None,
               "error": None, "support": is_support}
        try:
            if fmt == "svg":
                # the renderer already wrote the editable source
                rec.update(status="ok", method="native-svg", path=svg_path)

            elif fmt == "pdf":
                if tools["cairosvg"]:
                    import cairosvg
                    cairosvg.svg2pdf(url=svg_path, write_to=target)
                    rec.update(method="cairosvg")
                elif tools["rsvg-convert"]:
                    rc, o, e = _run([tools["rsvg-convert"], "-f", "pdf", "-o", target, svg_path])
                    if rc == 0: rec.update(method="rsvg-convert")
                    else: raise RuntimeError(e.strip() or "rsvg-convert failed")
                elif tools["inkscape"]:
                    rc, o, e = _run([tools["inkscape"], svg_path, "--export-type=pdf",
                                     f"--export-filename={target}"])
                    if rc == 0: rec.update(method="inkscape")
                    else: raise RuntimeError(e.strip() or "inkscape failed")
                elif tools["chromium"]:
                    ok, detail = _chromium(tools, html_path, target, "pdf", w_mm, h_mm, dpi)
                    if ok: rec.update(method=detail)
                    else: raise RuntimeError(detail)
                else:
                    raise RuntimeError("no SVG->PDF converter available")
                rec["status"] = "ok"

            elif fmt == "png":
                target_px = round(w_mm / 25.4 * dpi)
                if tools["cairosvg"]:
                    import cairosvg
                    cairosvg.svg2png(url=svg_path, write_to=target,
                                     output_width=target_px, dpi=dpi)
                    rec.update(method="cairosvg")
                elif tools["rsvg-convert"]:
                    rc, o, e = _run([tools["rsvg-convert"], "-d", str(dpi), "-p", str(dpi),
                                     "-f", "png", "-o", target, svg_path])
                    if rc == 0: rec.update(method="rsvg-convert")
                    else: raise RuntimeError(e.strip())
                elif tools["inkscape"]:
                    rc, o, e = _run([tools["inkscape"], svg_path, "--export-type=png",
                                     f"--export-dpi={dpi}", f"--export-filename={target}"])
                    if rc == 0: rec.update(method="inkscape")
                    else: raise RuntimeError(e.strip())
                elif tools["chromium"]:
                    ok, detail = _chromium(tools, html_path, target, "png", w_mm, h_mm, dpi)
                    if ok: rec.update(method=detail)
                    else: raise RuntimeError(detail)
                elif os.path.exists(pdf_path) and tools["pdftocairo"]:
                    stem = os.path.join(out_dir, "." + basename + "_raster")
                    rc, o, e = _run([tools["pdftocairo"], "-png", "-r", str(dpi),
                                     "-singlefile", pdf_path, stem])
                    if rc == 0 and os.path.exists(stem + ".png"):
                        shutil.move(stem + ".png", target)
                        rec.update(method="pdftocairo")
                    else:
                        raise RuntimeError(e.strip() or "pdftocairo failed")
                elif tools["convert"]:
                    rc, o, e = _run([tools["convert"], "-density", str(dpi),
                                     "-background", "white", svg_path, target])
                    if rc == 0: rec.update(method="imagemagick")
                    else: raise RuntimeError(e.strip())
                else:
                    raise RuntimeError("no SVG->PNG rasterizer available")
                rec["status"] = "ok"
                rec["target_width_px"] = target_px
                stamp_png_dpi(target, dpi)

            elif fmt == "tiff":
                # TIFF is produced from the raster at the same DPI
                if dep_failure.get("png"):
                    raise RuntimeError(f"PNG dependency failed: {dep_failure['png']}")
                if not os.path.exists(png_path):
                    raise RuntimeError("TIFF requires a successful PNG export first")
                if tools["pillow"]:
                    from PIL import Image
                    with Image.open(png_path) as im:
                        rgb = im.convert("RGB")
                        rgb.save(target, format="TIFF", compression="tiff_lzw",
                                 dpi=(dpi, dpi))
                    rec.update(method="pillow(lzw)")
                elif tools["convert"]:
                    rc, o, e = _run([tools["convert"], png_path, "-compress", "LZW",
                                     "-density", str(dpi), target])
                    if rc == 0: rec.update(method="imagemagick(lzw)")
                    else: raise RuntimeError(e.strip())
                else:
                    raise RuntimeError("no TIFF writer available")
                rec["status"] = "ok"

            elif fmt == "eps":
                if dep_failure.get("pdf"):
                    raise RuntimeError(f"PDF dependency failed: {dep_failure['pdf']}")
                if tools["inkscape"]:
                    rc, o, e = _run([tools["inkscape"], svg_path, "--export-type=eps",
                                     f"--export-filename={target}"])
                    if rc == 0: rec.update(method="inkscape")
                    else: raise RuntimeError(e.strip())
                elif os.path.exists(pdf_path) and tools["pdftocairo"]:
                    stem = os.path.join(out_dir, "." + basename + "_eps")
                    # poppler allows -singlefile only with png/jpeg/tiff;
                    # single-page output may be written with or without .eps
                    rc, o, e = _run([tools["pdftocairo"], "-eps",
                                     pdf_path, stem])
                    produced = stem + ".eps" if os.path.exists(stem + ".eps") else (
                        stem if rc == 0 and os.path.exists(stem) else None)
                    if produced:
                        shutil.move(produced, target)
                        rec.update(method="pdftocairo")
                    else:
                        raise RuntimeError(e.strip() or "pdftocairo failed")
                else:
                    raise RuntimeError("no EPS converter available (need inkscape or pdf+pdftocairo)")
                rec["status"] = "ok"

            else:
                rec["error"] = f"unsupported export format '{fmt}'"

        except Exception as exc:  # honest failure record, no silent skip
            rec["error"] = str(exc)[:300]
            if is_support:
                dep_failure[fmt] = rec["error"]
            if os.path.exists(target) and os.path.getsize(target) == 0:
                os.remove(target)
        if not is_support:
            results.append(rec)

    try:
        os.remove(html_path)
    except OSError:
        pass
    return results


if __name__ == "__main__":
    # manual debug entry: python3 export.py figure.svg out_dir 600 png pdf tiff
    svg, outdir, dpi = sys.argv[1], sys.argv[2], int(sys.argv[3])
    fmts = sys.argv[4:] or ["png", "pdf"]
    base = os.path.splitext(os.path.basename(svg))[0]
    for r in export_all(svg, outdir, base, fmts, dpi):
        print(r)
