#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
svg2spec.py - Reverse-engineer a figure_spec skeleton from an existing SVG.

This supports the "edit an existing figure" workflow: a layered SVG produced
by this builder (or a structurally similar one) is parsed deterministically
into entities, groups, relations and mappings, so an Agent can edit the spec
and re-render rather than redrawing from scratch.

Geometry cannot uniquely recover semantics: a dashed T-bar may be degradation
or indirect inhibition, and original source locators are not stored in the
SVG. Such ambiguities are emitted as inferred elements plus explicit
uncertainty entries instead of being guessed silently.

Usage:
    python3 svg2spec.py input.svg [skeleton.json]
"""
from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET

NS = {"svg": "http://www.w3.org/2000/svg"}


def _local(tag):
    return tag.split("}")[-1]


def _source_refs(el):
    return [x for x in (el.get("data-source-refs") or "").split() if x]


def _marker_kind(value):
    """Extract arrow/tbar/open/none from a marker url(...) reference."""
    if not value:
        return "none"
    m = re.search(r"#(arrow|tbar|open)-", value)
    return m.group(1) if m else "none"


def _infer_type(marker, dashed, uncertainty):
    table = {
        ("arrow", False): ("activation", "solid filled arrow read as activation"),
        ("tbar", False): ("inhibition", "solid T-bar read as inhibition"),
        ("none", False): ("association", "plain line read as association/binding"),
        ("open", False): ("production", "open arrow read as production/release"),
        ("arrow", True): ("indirect_activation", "dashed arrow read as indirect activation"),
        ("tbar", True): ("indirect_inhibition",
                          "dashed T-bar may be indirect inhibition or degradation; verify"),
        ("open", True): ("translocation", "dashed open arrow read as translocation"),
        ("none", True): ("association", "dashed plain line; dashed association is unusual"),
    }
    rtype, note = table[(marker, dashed)]
    uncertainty.append(note)
    return rtype


def svg_to_spec(svg_path: str) -> dict:
    tree = ET.parse(svg_path)
    root = tree.getroot()
    uncertainty = []
    source_ids = set()

    # collect every source id referenced anywhere
    for el in root.iter():
        source_ids.update(_source_refs(el))

    # ---- entities --------------------------------------------------------
    entities = []
    label_text = {}
    for g in root.findall(".//svg:g", NS):
        gid = g.get("id") or ""
        if gid.startswith("label-"):
            eid = gid[len("label-"):]
            lines = [t.text or "" for t in g.findall(".//svg:text", NS)]
            label_text[eid] = "\n".join(lines)

    for el in root.iter():
        eid_full = el.get("id") or ""
        if not eid_full.startswith("entity-"):
            continue
        eid = eid_full[len("entity-"):]
        tag = _local(el.tag)
        if tag == "ellipse":
            shape = "ellipse"
        elif tag == "polygon":
            shape = "hex"
        elif tag == "rect":
            try:
                rx = float(el.get("rx", 0)); h = float(el.get("height", 1))
            except ValueError:
                rx, h = 0, 1
            shape = "capsule" if rx >= h / 2 else ("round" if rx > 0 else "rect")
        else:
            shape = "round"
        refs = _source_refs(el)
        ent = {
            "id": eid,
            "label": label_text.get(eid, eid),
            "shape": shape,
            "fill": el.get("fill"),
            "stroke": el.get("stroke"),
            "source_refs": refs,
            "inferred": True,
        }
        if not refs:
            uncertainty.append(f"entity '{eid}': no source reference in SVG; supply one")
        entities.append(ent)

    ent_ids = {e["id"] for e in entities}

    # ---- groups ----------------------------------------------------------
    groups = []
    for el in root.iter():
        gid_full = el.get("id") or ""
        if not gid_full.startswith("group-"):
            continue
        gid = gid_full[len("group-"):]
        # membership cannot be read from the box geometry alone; leave it
        # for the Agent to fill from the entity positions/labels
        groups.append({"id": gid, "contains": [], "source_refs": _source_refs(el),
                       "inferred": True})
        uncertainty.append(f"group '{gid}': contains list must be reconstructed and confirmed")

    # ---- relations -------------------------------------------------------
    relations = []
    for el in root.findall(".//svg:path", NS):
        rid_full = el.get("id") or ""
        if not rid_full.startswith("rel-"):
            continue
        rid = rid_full[len("rel-"):]
        end_marker = _marker_kind(el.get("marker-end"))
        start_marker = _marker_kind(el.get("marker-start"))
        dashed = bool(el.get("stroke-dasharray"))
        rtype = _infer_type(end_marker, dashed, uncertainty)
        refs = _source_refs(el)
        rel = {"id": rid, "from": None, "to": None, "type": rtype,
               "source_refs": refs, "inferred": True}
        if start_marker != "none":
            rel["direction"] = "bidirectional"
        relations.append(rel)
        uncertainty.append(
            f"relation '{rid}': from/to endpoints cannot be recovered from path geometry; "
            f"attach them by matching the original node anchors")

    # the SVG stores node ids, not edge endpoints, so wire endpoints need the
    # human/agent; surface that honestly rather than inventing them
    spec = {
        "spec_version": "1.0",
        "title": "Reverse-engineered skeleton (inferred, verify before use)",
        "sources": [{"id": sid, "locator": "unknown - reconstruct from original materials",
                     "type": "unknown"} for sid in sorted(source_ids)],
        "entities": entities,
        "groups": groups,
        "relations": relations,
        "data_mappings": [],
        "legend": {"visible": True},
        "uncertainty": [{"note": u} for u in uncertainty],
        "change_log": [],
    }
    # entities not assigned to a group keep group absent (null)
    for e in spec["entities"]:
        e.setdefault("group", None)
    return spec


def main():
    if len(sys.argv) not in (2, 3):
        print("usage: python3 svg2spec.py input.svg [skeleton.json]")
        return 2
    svg_path = sys.argv[1]
    spec = svg_to_spec(svg_path)
    out = sys.argv[2] if len(sys.argv) == 3 else None
    text = json.dumps(spec, ensure_ascii=False, indent=2)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print("wrote skeleton:", out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
