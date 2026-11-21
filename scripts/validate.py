#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate.py - Source-traceability gate for figure specs.

Hard rule enforced here: every scientific entity, relation and mapped value
must carry a source_ref that resolves to a declared source, or be explicitly
marked inferred with an uncertainty note. This is the main anti-hallucination
barrier: a rendered figure can never contain an untraceable scientific claim.

Usage:
    from validate import validate_spec
    issues = validate_spec(spec)           # list of {severity, code, message}
    errors = [i for i in issues if i["severity"] == "error"]
"""
from __future__ import annotations

from collections import defaultdict

try:
    from figsvg import RELATION_STYLE  # when run as a script from scripts/
except ImportError:  # package-style import fallback
    from .figsvg import RELATION_STYLE


def _issue(sev, code, msg):
    return {"severity": sev, "code": code, "message": msg}


def validate_spec(spec: dict) -> list:
    issues = []

    # ---- top level ------------------------------------------------------
    if not isinstance(spec, dict):
        return [_issue("error", "spec.not-dict", "figure spec must be a JSON object")]

    sources = {s["id"]: s for s in spec.get("sources", []) if isinstance(s, dict) and "id" in s}
    if not sources:
        issues.append(_issue("warning", "sources.empty",
                             "no source list declared; traceability cannot be independently checked"))

    def check_refs(owner, refs, inferred=False):
        if not refs:
            if inferred:
                issues.append(_issue("warning", "source.inferred",
                                     f"{owner} is inferred rather than source-backed"))
            else:
                issues.append(_issue("error", "source.missing",
                                     f"{owner} has no source_refs; add a source or mark it inferred"))
        else:
            for r in refs:
                if r not in sources:
                    issues.append(_issue("error", "source.unresolved",
                                         f"{owner} references undeclared source '{r}'"))

    # ---- entities -------------------------------------------------------
    entities = spec.get("entities", [])
    ent_ids = set()
    for e in entities:
        eid = e.get("id")
        if not eid:
            issues.append(_issue("error", "entity.no-id", f"entity without id: {e}"))
            continue
        if eid in ent_ids:
            issues.append(_issue("error", "entity.duplicate", f"duplicate entity id '{eid}'"))
        ent_ids.add(eid)
        if not e.get("label"):
            issues.append(_issue("warning", "entity.no-label", f"entity '{eid}' has no label"))
        check_refs(f"entity '{eid}'", e.get("source_refs", []), e.get("inferred", False))
        for key in ("fill", "stroke"):
            val = e.get(key)
            if val and not str(val).startswith("#"):
                issues.append(_issue("error", "entity.color-format",
                                     f"entity '{eid}' {key} must be a #rrggbb color, got {val}"))
        # mapping references attached to an entity
        for visual, rule in e.get("mappings", {}).items():
            if isinstance(rule, dict):
                mid = rule.get("mapping")
                if mid not in {m.get("id") for m in spec.get("data_mappings", [])}:
                    issues.append(_issue("error", "mapping.unresolved",
                                         f"entity '{eid}' {visual} uses unknown mapping '{mid}'"))

    # ---- groups ---------------------------------------------------------
    group_ids = set()
    for g in spec.get("groups", []):
        gid = g.get("id")
        if not gid:
            issues.append(_issue("error", "group.no-id", f"group without id: {g}"))
            continue
        if gid in group_ids:
            issues.append(_issue("error", "group.duplicate", f"duplicate group id '{gid}'"))
        group_ids.add(gid)
        check_refs(f"group '{gid}'", g.get("source_refs", []), g.get("inferred", False))
        for m in g.get("contains", []):
            if m not in ent_ids:
                issues.append(_issue("error", "group.member",
                                     f"group '{gid}' contains unknown entity '{m}'"))

    for e in entities:
        g = e.get("group")
        if g and g not in group_ids:
            issues.append(_issue("error", "entity.group",
                                 f"entity '{e.get('id')}' points to unknown group '{g}'"))

    # two-way consistency between entity.group and group.contains ----------
    membership = defaultdict(list)
    for g in spec.get("groups", []):
        for m in g.get("contains", []):
            membership[m].append(g.get("id"))
    for e in entities:
        eid, declared = e.get("id"), e.get("group")
        containers = membership.get(eid, [])
        if len(containers) > 1:
            issues.append(_issue("error", "group.multi-membership",
                                 f"entity '{eid}' is contained by multiple groups {containers}; "
                                 f"an entity belongs to exactly one group"))
        if declared and containers and declared not in containers:
            issues.append(_issue("error", "group.consistency",
                                 f"entity '{eid}' declares group '{declared}' but is listed in "
                                 f"contains of {containers}"))
        elif declared and not containers:
            issues.append(_issue("error", "group.consistency",
                                 f"entity '{eid}' declares group '{declared}' but is absent from "
                                 f"that group's contains list"))
        elif not declared and containers:
            issues.append(_issue("error", "group.consistency",
                                 f"entity '{eid}' has no group but appears in contains of "
                                 f"{containers}"))

    # ---- relations ------------------------------------------------------
    rel_ids = set()
    for r in spec.get("relations", []):
        rid = r.get("id")
        if not rid:
            issues.append(_issue("error", "relation.no-id", f"relation without id: {r}"))
        elif rid in rel_ids:
            issues.append(_issue("error", "relation.duplicate", f"duplicate relation id '{rid}'"))
        rel_ids.add(rid)
        s, t = r.get("from"), r.get("to")
        if s not in ent_ids:
            issues.append(_issue("error", "relation.endpoint",
                                 f"relation '{rid}' starts at unknown entity '{s}'"))
        if t not in ent_ids:
            issues.append(_issue("error", "relation.endpoint",
                                 f"relation '{rid}' ends at unknown entity '{t}'"))
        rtype = r.get("type")
        if rtype not in RELATION_STYLE:
            if not r.get("marker"):
                issues.append(_issue("error", "relation.type",
                                     f"relation '{rid}' has unknown type '{rtype}' and no explicit marker"))
            else:
                issues.append(_issue("warning", "relation.custom",
                                     f"relation '{rid}' uses custom type '{rtype}'"))
        if r.get("direction") not in (None, "forward", "bidirectional"):
            issues.append(_issue("error", "relation.direction",
                                 f"relation '{rid}' direction must be forward or bidirectional"))
        check_refs(f"relation '{rid}'", r.get("source_refs", []), r.get("inferred", False))

    # parallel edges between the same pair overlap visually
    pair_count = defaultdict(list)
    for r in spec.get("relations", []):
        pair_count[(r.get("from"), r.get("to"))].append(r.get("id"))
    for pair, ids in pair_count.items():
        if len(ids) > 1:
            issues.append(_issue("warning", "relation.parallel",
                                 f"multiple relations {ids} share endpoints {pair}; they render "
                                 f"on top of each other, merge or distinguish them"))

    # containment must be expressed by groups, never as an edge
    for r in spec.get("relations", []):
        if r.get("type") == "contains":
            issues.append(_issue("error", "relation.contains",
                                 f"relation '{r.get('id')}': containment belongs in groups, not as an edge"))

    # ---- data mappings --------------------------------------------------
    mapping_ids = set()
    for m in spec.get("data_mappings", []):
        mid = m.get("id")
        mapping_ids.add(mid)
        check_refs(f"data_mapping '{mid}'", m.get("source_refs", []), m.get("inferred", False))
        stype = m.get("scale_type")
        if stype == "categorical":
            dom, rng = m.get("domain", []), m.get("range", [])
            if len(dom) != len(rng):
                issues.append(_issue("error", "mapping.length",
                                     f"mapping '{mid}': domain has {len(dom)} items but range has {len(rng)}"))
            if len(set(dom)) != len(dom):
                issues.append(_issue("error", "mapping.duplicate-domain",
                                     f"mapping '{mid}': duplicate domain values"))
            for c in rng:
                if not str(c).startswith("#"):
                    issues.append(_issue("error", "mapping.color-format",
                                         f"mapping '{mid}' range color {c} must be #rrggbb"))
        elif stype == "continuous":
            stops = m.get("stops", [])
            if len(stops) < 2:
                issues.append(_issue("error", "mapping.stops",
                                     f"mapping '{mid}' needs at least 2 gradient stops"))
            for s in stops:
                if not str(s.get("color", "")).startswith("#"):
                    issues.append(_issue("error", "mapping.color-format",
                                         f"mapping '{mid}' stop color must be #rrggbb"))
        else:
            issues.append(_issue("error", "mapping.type",
                                 f"mapping '{mid}' scale_type must be categorical or continuous"))

    # every mapped value used on an entity must exist in the domain
    for e in entities:
        for visual, rule in e.get("mappings", {}).items():
            if not isinstance(rule, dict):
                continue
            m = next((x for x in spec.get("data_mappings", []) if x.get("id") == rule.get("mapping")), None)
            if m and m.get("scale_type") == "categorical":
                if rule.get("value") not in m.get("domain", []):
                    issues.append(_issue("error", "mapping.value",
                                         f"entity '{e.get('id')}' maps to value "
                                         f"'{rule.get('value')}' outside domain of '{m.get('id')}'"))

    # ---- legend consistency ---------------------------------------------
    legend = spec.get("legend", {})
    for it in legend.get("items", []):
        if not it.get("label"):
            issues.append(_issue("error", "legend.item", f"legend item without label: {it}"))
        if it.get("color") and not str(it.get("color")).startswith("#"):
            issues.append(_issue("error", "legend.color",
                                 f"legend item '{it.get('label')}' color must be #rrggbb"))
        check_refs(f"legend item '{it.get('label')}'", it.get("source_refs", []), True)

    # ---- charts ---------------------------------------------------------
    for ch in spec.get("charts", []):
        cid = ch.get("id")
        check_refs(f"chart '{cid}'", ch.get("source_refs", []), ch.get("inferred", False))
        cats = ch.get("categories", [])
        for s in ch.get("series", []):
            check_refs(f"series '{s.get('name')}' in chart '{cid}'",
                       s.get("source_refs", []), ch.get("inferred", False))
            if len(s.get("values", [])) != len(cats):
                issues.append(_issue("error", "chart.length",
                                     f"chart '{cid}' series '{s.get('name')}' has "
                                     f"{len(s.get('values', []))} values for {len(cats)} categories"))
            for v in s.get("values", []):
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    issues.append(_issue("error", "chart.value-type",
                                         f"chart '{cid}' series '{s.get('name')}' values must be "
                                         f"numeric, got {v!r}"))
                    break
            if s.get("color") and not str(s.get("color")).startswith("#"):
                issues.append(_issue("error", "chart.color",
                                     f"chart '{cid}' series color must be #rrggbb"))
            if s.get("mapping") and s.get("mapping") not in mapping_ids:
                issues.append(_issue("error", "chart.mapping",
                                     f"chart '{cid}' series references unknown mapping"))

    # ---- canvas / layout ------------------------------------------------
    canvas = spec.get("canvas", {})
    for dim in ("width_mm", "height_mm"):
        if dim in canvas:
            try:
                v = float(canvas[dim])
            except (TypeError, ValueError):
                issues.append(_issue("error", "canvas.dim", f"canvas.{dim} must be numeric"))
            else:
                if v <= 0:
                    issues.append(_issue("error", "canvas.dim-positive",
                                         f"canvas.{dim} must be greater than 0, got {v}"))
    if "font_size_pt" in canvas:
        try:
            if float(canvas["font_size_pt"]) <= 0:
                issues.append(_issue("error", "canvas.font-positive",
                                     "canvas.font_size_pt must be greater than 0"))
        except (TypeError, ValueError):
            issues.append(_issue("error", "canvas.dim", "canvas.font_size_pt must be numeric"))
    for eid in spec.get("layout", {}).get("positions", {}):
        if eid not in ent_ids:
            issues.append(_issue("error", "layout.position",
                                 f"manual position for unknown entity '{eid}'"))

    return issues


def summarize(issues: list) -> dict:
    return {
        "errors": sum(1 for i in issues if i["severity"] == "error"),
        "warnings": sum(1 for i in issues if i["severity"] == "warning"),
        "items": issues,
    }


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) != 2:
        print("usage: python3 validate.py figure_spec.json")
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        spec = json.load(f)
    report = summarize(validate_spec(spec))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report["errors"] else 0)
