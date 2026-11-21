#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
figsvg.py - Deterministic, editable scientific figure renderer (Python stdlib only).

Input : a normalized figure spec dict (see resources/spec_schema.json)
Output: an SVG string where 1 user unit = 1 mm.
        - real <text> elements (selectable / editable)
        - named layers: groups, entities, relations, charts, legend, annotations
        - every element carries id + data-source-refs for traceability
        - relation glyphs follow a fixed vocabulary (activation arrow vs
          inhibition T-bar etc.) so semantics cannot be flipped by accident.

The LLM owns scientific extraction; this module owns geometry and rendering.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict, deque

PT_TO_MM = 0.352777778  # 1 point = 0.3528 mm

# ---------------------------------------------------------------------------
# Relation vocabulary. `marker` is one of: arrow, tbar, open, none
# ---------------------------------------------------------------------------
RELATION_STYLE = {
    "activation":            {"dash": None,  "marker": "arrow", "label": "Activation / promotes"},
    "inhibition":            {"dash": None,  "marker": "tbar",  "label": "Inhibition / suppresses"},
    "indirect_activation":   {"dash": "2.4,1.5", "marker": "arrow", "label": "Indirect activation"},
    "indirect_inhibition":   {"dash": "2.4,1.5", "marker": "tbar",  "label": "Indirect inhibition"},
    "association":           {"dash": None,  "marker": "none",  "label": "Association / binding"},
    "binding":               {"dash": None,  "marker": "none",  "label": "Binding"},
    "translocation":         {"dash": "2.4,1.5", "marker": "open",  "label": "Translocation"},
    "process":               {"dash": None,  "marker": "arrow", "label": "Process flow"},
    "production":            {"dash": None,  "marker": "open",  "label": "Production / release"},
    "phosphorylation":       {"dash": None,  "marker": "arrow", "label": "Phosphorylation"},
    "dephosphorylation":     {"dash": None,  "marker": "arrow", "label": "Dephosphorylation"},
    "degradation":           {"dash": "2.4,1.5", "marker": "tbar",  "label": "Degradation"},
    "unknown":               {"dash": None,  "marker": "open",  "label": "Unspecified relation"},
}

NODE_KIND_STYLE = {
    "gene":           {"shape": "rect",    "fill": "#eef2f7", "stroke": "#34495e"},
    "protein":        {"shape": "round",   "fill": "#f7f9fc", "stroke": "#34495e"},
    "receptor":       {"shape": "capsule", "fill": "#eaf1fb", "stroke": "#2c5fa8"},
    "enzyme":         {"shape": "round",   "fill": "#f3f9f4", "stroke": "#2e7d32"},
    "metabolite":     {"shape": "ellipse", "fill": "#fff8e1", "stroke": "#b8860b"},
    "small_molecule": {"shape": "ellipse", "fill": "#fff8e1", "stroke": "#b8860b"},
    "complex":        {"shape": "round",   "fill": "#f4f0fa", "stroke": "#6a3d9a"},
    "process":        {"shape": "hex",     "fill": "#eafaf1", "stroke": "#1e8449"},
    "event":          {"shape": "hex",     "fill": "#fdeef0", "stroke": "#a93226"},
    "cell":           {"shape": "round",   "fill": "#ffffff", "stroke": "#566573"},
    "organelle":      {"shape": "round",   "fill": "#fbfcfd", "stroke": "#7f8c8d"},
    "generic":        {"shape": "round",   "fill": "#ffffff", "stroke": "#2c3e50"},
}

FONT_STACK = "Arial, Helvetica, 'Noto Sans', 'Liberation Sans', sans-serif"


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def sanitize_id(s) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s))


def text_width_mm(text: str, font_pt: float) -> float:
    """Rough monospace-ish metric for CJK/Latin mixed labels."""
    em = font_pt * PT_TO_MM
    w = 0.0
    for ch in str(text):
        if ch == "\n":
            continue
        code = ord(ch)
        if code > 0x2E80:  # CJK and broad non-latin
            w += 1.02 * em
        elif ch in "iljtfr .,;:'!|":
            w += 0.34 * em
        elif ch.isupper():
            w += 0.66 * em
        elif ch.isdigit():
            w += 0.55 * em
        else:
            w += 0.52 * em
    return w


def nice_ticks(vmin: float, vmax: float, count: int = 5):
    if vmin == vmax:
        vmax = vmin + 1
    span = vmax - vmin
    raw = span / max(1, count)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    start = math.ceil(vmin / step) * step
    out = []
    v = start
    while v <= vmax + step * 1e-9:
        out.append(round(v, 10))
        v += step
    return out, step


# ---------------------------------------------------------------------------
# Graph layout
# ---------------------------------------------------------------------------
def rank_nodes(ids, edges):
    """Longest-path layering with cycle breaking. Returns (rank, back_edges)."""
    adj = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    edge_set = set()
    for s, t in edges:
        if s == t or (s, t) in edge_set or s not in adj or t not in adj:
            continue
        edge_set.add((s, t))
        adj[s].append(t)
        indeg[t] += 1

    rank = {i: 0 for i in ids}
    q = deque(sorted(i for i in ids if indeg[i] == 0))
    done = set()
    while q:
        u = q.popleft()
        done.add(u)
        for v in adj[u]:
            rank[v] = max(rank[v], rank[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)

    back = set()
    remain = [i for i in ids if i not in done]
    base = max(rank.values()) if rank else 0
    while remain:
        base += 1
        wave = []
        for i in remain:
            preds = [s for s, t in edge_set if t == i]
            if all(p in done for p in preds):
                wave.append(i)
        if not wave:  # unresolved cycle: break it
            wave = [remain[0]]
            for s, t in edge_set:
                if s in wave and t in remain:
                    back.add((s, t))
        for i in wave:
            rank[i] = base
        done.update(wave)
        remain = [i for i in remain if i not in done]
    return rank, back


def classify_back_edges(ids, edges):
    """Iterative DFS: an edge to a node on the current DFS path is a back edge.
    Unlike arbitrary cycle breaking, this matches feedback semantics."""
    adj = {i: [] for i in ids}
    seen = set()
    for s, t in edges:
        if s == t or (s, t) in seen or s not in adj or t not in adj:
            continue
        seen.add((s, t))
        adj[s].append(t)
    color = {i: 0 for i in ids}
    back = set()
    for root in ids:
        if color[root] != 0:
            continue
        stack = [(root, iter(adj[root]))]
        color[root] = 1
        while stack:
            u, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                color[u] = 2
                stack.pop()
                continue
            if color[nxt] == 0:
                color[nxt] = 1
                stack.append((nxt, iter(adj[nxt])))
            elif color[nxt] == 1:
                back.add((u, nxt))
    return back


def order_layers(rank, edges, sweeps: int = 4):
    """Barycenter ordering inside each layer."""
    layers = defaultdict(list)
    for n, r in rank.items():
        layers[r].append(n)
    pos = {n: 0.0 for n in rank}
    in_edges = defaultdict(list)
    out_edges = defaultdict(list)
    for s, t in edges:
        in_edges[t].append(s)
        out_edges[s].append(t)

    for sweep in range(sweeps):
        order = sorted(layers) if sweep % 2 == 0 else sorted(layers, reverse=True)
        for r in order:
            members = layers[r]
            if r == min(layers):
                members.sort()
            else:
                def bary(n, neigh):
                    refs = [p for p in neigh if p in pos and rank.get(p) in (r - 1, r + 1)]
                    return sum(pos[p] for p in refs) / len(refs) if refs else len(members) / 2
                members.sort(key=lambda n: bary(n, in_edges[n] or out_edges[n]))
            for i, n in enumerate(members):
                pos[n] = i
    return {r: list(members) for r, members in layers.items()}


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------
class SvgRenderer:
    def __init__(self, spec: dict):
        self.spec = spec
        self.canvas = spec.get("canvas", {})
        self.font_pt = float(self.canvas.get("font_size_pt", 8))
        self.font_family = self.canvas.get("font_family", FONT_STACK)
        self.pad = float(self.canvas.get("margin_mm", 8))
        self.rankdir = self.canvas.get("rankdir", "LR")  # LR | TB
        self.node_w = float(self.canvas.get("node_w_mm", 26))
        self.node_h = float(self.canvas.get("node_h_mm", 9))
        self.gap_x = float(self.canvas.get("gap_x_mm", 8))
        self.gap_y = float(self.canvas.get("gap_y_mm", 6))

        self.entities = {e["id"]: e for e in spec.get("entities", [])}
        self.groups = {g["id"]: g for g in spec.get("groups", [])}
        self.relations = spec.get("relations", [])
        self.mappings = {m["id"]: m for m in spec.get("data_mappings", [])}
        self.charts = spec.get("charts", [])
        self.annotations = spec.get("annotations", [])
        self.legend_spec = spec.get("legend", {})

        self.rects = {}        # entity id -> (x, y, w, h)
        self.group_boxes = {}  # group id -> (x, y, w, h)
        self.marker_colors = set()
        self.warnings = []

    # -- node geometry -----------------------------------------------------
    def node_size(self, ent):
        label = ent.get("label", ent["id"])
        lines = str(label).split("\n")
        fs = float(ent.get("font_size_pt", self.font_pt))
        w = max(self.node_w, max(text_width_mm(ln, fs) for ln in lines) + 4.5)
        h = max(self.node_h, len(lines) * (fs * PT_TO_MM + 1.6) + 3.2)
        if ent.get("shape"):
            shape = ent["shape"]
        else:
            shape = NODE_KIND_STYLE.get(ent.get("kind", "generic"),
                                        NODE_KIND_STYLE["generic"])["shape"]
        if shape == "ellipse":
            w += 3
            h += 2
        return w, h

    def _group_members(self):
        member_map = defaultdict(list)
        for eid, ent in self.entities.items():
            member_map[ent.get("group", "__none__::" + eid)].append(eid)
        return member_map

    def _entity_edges(self):
        return [(r.get("from"), r.get("to")) for r in self.relations
                if r.get("from") in self.entities and r.get("to") in self.entities]

    def _apply_manual(self, manual):
        for eid, pos in manual.items():
            if eid in self.rects:
                x, y, w, h = self.rects[eid]
                self.rects[eid] = (float(pos.get("x_mm", x)),
                                   float(pos.get("y_mm", y)), w, h)

    def layout(self):
        manual = self.spec.get("layout", {}).get("positions", {})
        # DFS back edges (feedback loops) are identified for side routing
        self.back_edges = classify_back_edges(list(self.entities), self._entity_edges())
        if not self.groups:
            self._layout_direct(manual)
        else:
            self._layout_grouped(manual)

    def _layout_direct(self, manual):
        """Tight layered layout when there are no visible group boxes."""
        ids = list(self.entities)
        edges = self._entity_edges()
        ranks, _ = rank_nodes(ids, edges)
        layers = order_layers(ranks, edges)
        sizes = {eid: self.node_size(self.entities[eid]) for eid in ids}
        pitch_w = max((w for w, h in sizes.values()), default=0) + self.gap_x
        pitch_h = max((h for w, h in sizes.values()), default=0) + self.gap_y
        gutter = 10 if self.back_edges else 0
        for r, mem in layers.items():
            for idx, eid in enumerate(mem):
                w, h = sizes[eid]
                if self.rankdir == "TB":
                    x, y = self.pad + gutter + idx * pitch_w, self.pad + r * pitch_h
                else:
                    x, y = self.pad + r * pitch_w, self.pad + gutter + idx * pitch_h
                self.rects[eid] = (x, y, w, h)
        self._apply_manual(manual)

    def _layout_grouped(self, manual):
        member_map = self._group_members()

        # internal layout per group -----------------------------------------
        group_sizes = {}
        internal_pos = {}
        for gid, members in member_map.items():
            internal = [(r["from"], r["to"]) for r in self.relations
                        if r.get("from") in members and r.get("to") in members]
            ranks, _ = rank_nodes(members, internal)
            layers = order_layers(ranks, internal)
            sizes = {eid: self.node_size(self.entities[eid]) for eid in members}
            pitch_w = max(w for w, h in sizes.values()) + self.gap_x
            pitch_h = max(h for w, h in sizes.values()) + self.gap_y
            header = 5.2 if gid in self.groups else 0
            for r, mem in layers.items():
                for idx, eid in enumerate(mem):
                    w, h = sizes[eid]
                    if self.rankdir == "TB":
                        x = self.pad + idx * pitch_w
                        y = self.pad + header + r * pitch_h
                    else:
                        x = self.pad + r * pitch_w
                        y = self.pad + header + idx * pitch_h
                    internal_pos[eid] = (x, y, w, h)
            gw = max(x + w for x, y, w, h in internal_pos.values()) + self.pad
            gh = max(y + h for x, y, w, h in internal_pos.values()) + self.pad
            group_sizes[gid] = (gw, gh)

        # meta layout over groups -------------------------------------------
        gids = list(member_map.keys())
        cross = [(self.entities[s]["group"] if self.entities[s].get("group")
                  else "__none__::" + s,
                  self.entities[t]["group"] if self.entities[t].get("group")
                  else "__none__::" + t)
                 for s, t in self._entity_edges()]
        cross = [(a, b) for a, b in cross if a != b]
        mranks, _ = rank_nodes(gids, cross)
        mlayers = order_layers(mranks, cross)
        max_w = max((w for w, h in group_sizes.values()), default=0)
        max_h = max((h for w, h in group_sizes.values()), default=0)
        meta_pitch_w = max_w + 16
        meta_pitch_h = max_h + 14

        origins = {}
        gutter = 10 if self.back_edges else 0
        for r, mem in mlayers.items():
            for idx, gid in enumerate(mem):
                if self.rankdir == "TB":
                    origins[gid] = (self.pad + gutter + idx * meta_pitch_w,
                                    self.pad + r * meta_pitch_h)
                else:
                    origins[gid] = (self.pad + r * meta_pitch_w,
                                    self.pad + gutter + idx * meta_pitch_h)

        for gid, members in member_map.items():
            ox, oy = origins[gid]
            gw, gh = group_sizes[gid]
            if gid in self.groups:
                self.group_boxes[gid] = (ox, oy, gw, gh)
            for eid in members:
                x, y, w, h = internal_pos[eid]
                self.rects[eid] = (ox + x, oy + y, w, h)

        self._apply_manual(manual)

        # recompute visible group boxes from member bboxes after overrides
        for gid, members in member_map.items():
            if gid not in self.groups:
                continue
            xs = [self.rects[e][0] for e in members]
            ys = [self.rects[e][1] for e in members]
            x1 = max(self.rects[e][0] + self.rects[e][2] for e in members)
            y1 = max(self.rects[e][1] + self.rects[e][3] for e in members)
            ox, oy, gw, gh = self.group_boxes.get(gid, (0, 0, 0, 0))
            self.group_boxes[gid] = (min(ox, min(xs) - 2.5) if gw else min(xs) - 4,
                                     min(oy, min(ys) - 4.5) if gh else min(ys) - 6,
                                     max(gw, x1 - min(ox, min(xs) - 2.5) + 4),
                                     max(gh, y1 - min(oy, min(ys) - 4.5) + 4))

    # -- mapping resolution ------------------------------------------------
    def mapping_for(self, ent: dict, visual: str):
        """Return fill/stroke declared explicitly or via a data mapping.
        A mapping rule that cannot resolve never yields a null color: it is
        recorded as a warning and falls back to the kind default."""
        key = ent.get("mappings", {})
        if visual in key:
            rule = key[visual]
            if isinstance(rule, str) and rule.startswith("#"):
                return rule
            if isinstance(rule, dict):
                mid = rule.get("mapping")
                value = rule.get("value")
                m = self.mappings.get(mid)
                if m:
                    got = self._lookup_scale(m, value)
                    if got:
                        return got
                    self.warnings.append(
                        f"entity '{ent.get('id')}': mapping '{mid}' could not resolve value "
                        f"{value!r}; using kind default color")
                else:
                    self.warnings.append(
                        f"entity '{ent.get('id')}': references unknown mapping '{mid}'; "
                        f"using kind default color")
        if visual == "fill":
            return NODE_KIND_STYLE.get(ent.get("kind", "generic"),
                                       NODE_KIND_STYLE["generic"])["fill"]
        if visual == "stroke":
            return NODE_KIND_STYLE.get(ent.get("kind", "generic"),
                                       NODE_KIND_STYLE["generic"])["stroke"]
        return None

    @staticmethod
    def _lookup_scale(m, value):
        if m.get("scale_type") == "categorical":
            dom = m.get("domain", [])
            rng = m.get("range", [])
            if value in dom:
                return rng[dom.index(value)]
        else:
            stops = m.get("stops", [])
            try:
                v = float(value)
                lo = min(s["value"] for s in stops)
                hi = max(s["value"] for s in stops)
                t = 0 if hi == lo else (v - lo) / (hi - lo)
                t = max(0, min(1, t))
                return SvgRenderer._interp_stops(stops, t)
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _interp_stops(stops, t):
        stops = sorted(stops, key=lambda s: s["value"])
        lo = min(s["value"] for s in stops)
        hi = max(s["value"] for s in stops)
        v = lo + t * (hi - lo)
        for a, b in zip(stops, stops[1:]):
            if a["value"] <= v <= b["value"]:
                span = b["value"] - a["value"]
                k = 0 if span == 0 else (v - a["value"]) / span
                return SvgRenderer._mix_hex(a["color"], b["color"], k)
        return stops[-1]["color"]

    @staticmethod
    def _mix_hex(c1, c2, k):
        def rgb(h):
            h = h.lstrip("#")
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        a, b = rgb(c1), rgb(c2)
        m = tuple(round(a[i] + (b[i] - a[i]) * k) for i in range(3))
        return "#%02x%02x%02x" % m

    # -- SVG fragment builders --------------------------------------------
    def _defs(self):
        out = ['<defs>']
        for color in sorted(self.marker_colors):
            c = esc(color)
            out.append(
                f'<marker id="arrow-{sanitize_id(color)}" markerUnits="userSpaceOnUse" '
                f'viewBox="0 0 3 2" refX="3" refY="1" markerWidth="3" markerHeight="2" '
                f'orient="auto-start-reverse"><path d="M0,0 L3,1 L0,2 z" fill="{c}"/></marker>')
            out.append(
                f'<marker id="tbar-{sanitize_id(color)}" markerUnits="userSpaceOnUse" '
                f'viewBox="0 -2 0.4 4" refX="0.1" refY="0" markerWidth="1.4" markerHeight="4" '
                f'orient="auto"><path d="M0.1,-2 L0.1,2" fill="none" stroke="{c}" '
                f'stroke-width="0.35"/></marker>')
            out.append(
                f'<marker id="open-{sanitize_id(color)}" markerUnits="userSpaceOnUse" '
                f'viewBox="0 0 3 2" refX="2.8" refY="1" markerWidth="3" markerHeight="2" '
                f'orient="auto-start-reverse"><path d="M0,0 L2.8,1 L0,2" fill="none" '
                f'stroke="{c}" stroke-width="0.35"/></marker>')
        out.append('</defs>')
        return "".join(out)

    def _shape(self, eid, ent, x, y, w, h):
        fill = ent.get("fill") or self.mapping_for(ent, "fill")
        stroke = ent.get("stroke") or self.mapping_for(ent, "stroke")
        sw = float(ent.get("stroke_width_mm", 0.25))
        shape = ent.get("shape") or NODE_KIND_STYLE.get(
            ent.get("kind", "generic"), NODE_KIND_STYLE["generic"])["shape"]
        refs = " ".join(ent.get("source_refs", []))
        common = (f'id="entity-{sanitize_id(eid)}" data-source-refs="{esc(refs)}" '
                  f'fill="{esc(fill)}" stroke="{esc(stroke)}" stroke-width="{sw}"')
        if shape == "ellipse":
            body = f'<ellipse {common} cx="{x + w / 2:.2f}" cy="{y + h / 2:.2f}" rx="{w / 2:.2f}" ry="{h / 2:.2f}"/>'
        elif shape == "rect":
            body = f'<rect {common} x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}"/>'
        elif shape == "capsule":
            body = f'<rect {common} x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{h / 2:.2f}"/>'
        elif shape == "hex":
            c = 1.8
            pts = [(x + c, y), (x + w - c, y), (x + w, y + h / 2),
                   (x + w - c, y + h), (x + c, y + h), (x, y + h / 2)]
            p = " ".join(f"{px:.2f},{py:.2f}" for px, py in pts)
            body = f'<polygon {common} points="{p}"/>'
        else:  # round
            body = f'<rect {common} x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="1.6"/>'
        return body

    def _entity_text(self, eid, ent, x, y, w, h):
        fs = float(ent.get("font_size_pt", self.font_pt))
        lines = str(ent.get("label", eid)).split("\n")
        lh = fs * PT_TO_MM + 1.4
        y0 = y + h / 2 - (len(lines) - 1) * lh / 2
        refs = " ".join(ent.get("source_refs", []))
        out = [f'<g id="label-{sanitize_id(eid)}" data-source-refs="{esc(refs)}">']
        for i, ln in enumerate(lines):
            out.append(
                f'<text x="{x + w / 2:.2f}" y="{y0 + i * lh + fs * PT_TO_MM * 0.35:.2f}" '
                f'font-family="{esc(self.font_family)}" font-size="{fs * PT_TO_MM:.3f}" '
                f'fill="#1b2631" text-anchor="middle" dominant-baseline="middle">{esc(ln)}</text>')
        out.append('</g>')
        return "".join(out)

    def _groups_layer(self):
        out = ['<g id="layer-groups">']
        for gid, (x, y, w, h) in self.group_boxes.items():
            g = self.groups[gid]
            refs = " ".join(g.get("source_refs", []))
            fill = g.get("fill", "#fbfcfd")
            stroke = g.get("stroke", "#85929e")
            out.append(
                f'<rect id="group-{sanitize_id(gid)}" data-source-refs="{esc(refs)}" '
                f'x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="2" '
                f'fill="{esc(fill)}" fill-opacity="0.55" stroke="{esc(stroke)}" '
                f'stroke-width="0.3" stroke-dasharray="1.6,1.2"/>')
            if g.get("label"):
                out.append(
                    f'<text x="{x + 2:.2f}" y="{y + 3.6:.2f}" font-family="{esc(self.font_family)}" '
                    f'font-size="{(self.font_pt - 0.5) * PT_TO_MM:.3f}" font-weight="700" fill="#5d6d7e" '
                    f'data-source-refs="{esc(refs)}">{esc(g["label"])}</text>')
        out.append('</g>')
        return "".join(out)

    def _entities_layer(self):
        out = ['<g id="layer-entities">']
        for eid, (x, y, w, h) in self.rects.items():
            out.append(self._shape(eid, self.entities[eid], x, y, w, h))
        for eid, (x, y, w, h) in self.rects.items():
            out.append(self._entity_text(eid, self.entities[eid], x, y, w, h))
        out.append('</g>')
        return "".join(out)

    def _anchor(self, rect, side):
        x, y, w, h = rect
        return {
            "L": (x, y + h / 2), "R": (x + w, y + h / 2),
            "T": (x + w / 2, y), "B": (x + w / 2, y + h),
        }[side]

    def _retract(self, p, side, gap):
        """Pull an endpoint back from the node border so a T-bar/arrow glyph
        is visible instead of sitting on top of the node outline."""
        if not gap:
            return p
        x, y = p
        if side == "L":
            return (x - gap, y)
        if side == "R":
            return (x + gap, y)
        if side == "T":
            return (x, y - gap)
        return (x, y + gap)  # side B

    def _relations_layer(self):
        out = ['<g id="layer-relations">']
        for rel in self.relations:
            s, t = rel.get("from"), rel.get("to")
            if s not in self.rects or t not in self.rects:
                self.warnings.append(f"relation {rel.get('id')} references unknown entity")
                continue
            rtype = rel.get("type", "unknown")
            style = RELATION_STYLE.get(rtype, RELATION_STYLE["unknown"])
            color = rel.get("color", "#111111")
            self.marker_colors.add(color)
            sw = float(rel.get("stroke_width_mm", 0.3))
            dash = rel.get("dash", style["dash"])
            marker = rel.get("marker", style["marker"])
            end_gap = {"tbar": 1.6, "arrow": 0.6, "open": 0.8}.get(marker, 0.0)
            refs = " ".join(rel.get("source_refs", []))
            attr = (f'id="rel-{sanitize_id(rel.get("id", s + t))}" '
                    f'data-source-refs="{esc(refs)}" fill="none" stroke="{esc(color)}" '
                    f'stroke-width="{sw}"')
            if dash:
                attr += f' stroke-dasharray="{dash}"'

            mx = my = None
            if s == t:  # self loop
                x, y, w, h = self.rects[s]
                ye = y - end_gap
                d = (f"M {x + w * 0.3:.2f},{y:.2f} C {x + w * 0.3:.2f},{y - 5:.2f} "
                     f"{x + w * 0.7:.2f},{y - 5:.2f} {x + w * 0.7:.2f},{ye:.2f}")
                if marker in ("arrow", "tbar", "open"):
                    attr += f' marker-end="url(#{{m}}-{sanitize_id(color)})"'.replace("{m}", marker)
                # label sits at the top of the loop so it is not dropped
                mx, my = x + w / 2, y - 5
            elif (s, t) in self.back_edges:
                # feedback / back edge: route around the side so it never
                # overlays the forward chain
                rs, rt = self.rects[s], self.rects[t]
                if self.rankdir == "TB":
                    p1, p2 = self._anchor(rs, "L"), self._anchor(rt, "L")
                    p2e = self._retract(p2, "L", end_gap)
                    det = min(p1[0], p2[0]) - 8
                    d = (f"M {p1[0]:.2f},{p1[1]:.2f} L {det:.2f},{p1[1]:.2f} "
                         f"L {det:.2f},{p2e[1]:.2f} L {p2e[0]:.2f},{p2e[1]:.2f}")
                    mx, my = det, (p1[1] + p2e[1]) / 2
                else:
                    p1, p2 = self._anchor(rs, "T"), self._anchor(rt, "T")
                    p2e = self._retract(p2, "T", end_gap)
                    det = min(p1[1], p2[1]) - 8
                    d = (f"M {p1[0]:.2f},{p1[1]:.2f} L {p1[0]:.2f},{det:.2f} "
                         f"L {p2e[0]:.2f},{det:.2f} L {p2e[0]:.2f},{p2e[1]:.2f}")
                    mx, my = (p1[0] + p2e[0]) / 2, det
                if marker in ("arrow", "tbar", "open"):
                    attr += f' marker-end="url(#{{m}}-{sanitize_id(color)})"'.replace("{m}", marker)
            else:
                rs, rt = self.rects[s], self.rects[t]
                scx, scy = rs[0] + rs[2] / 2, rs[1] + rs[3] / 2
                tcx, tcy = rt[0] + rt[2] / 2, rt[1] + rt[3] / 2
                dx, dy = tcx - scx, tcy - scy
                bidir = rel.get("direction") == "bidirectional"
                if abs(dx) >= abs(dy):
                    ss, ts = ("R", "L") if dx > 0 else ("L", "R")
                    p1, p2 = self._anchor(rs, ss), self._anchor(rt, ts)
                    p2e = self._retract(p2, ts, end_gap)
                    if bidir:
                        p1 = self._retract(p1, ss, end_gap)
                    midx = (p1[0] + p2e[0]) / 2
                    d = (f"M {p1[0]:.2f},{p1[1]:.2f} L {midx:.2f},{p1[1]:.2f} "
                         f"L {midx:.2f},{p2e[1]:.2f} L {p2e[0]:.2f},{p2e[1]:.2f}")
                    mx, my = midx, (p1[1] + p2e[1]) / 2
                else:
                    ss, ts = ("B", "T") if dy > 0 else ("T", "B")
                    p1, p2 = self._anchor(rs, ss), self._anchor(rt, ts)
                    p2e = self._retract(p2, ts, end_gap)
                    if bidir:
                        p1 = self._retract(p1, ss, end_gap)
                    midy = (p1[1] + p2e[1]) / 2
                    d = (f"M {p1[0]:.2f},{p1[1]:.2f} L {p1[0]:.2f},{midy:.2f} "
                         f"L {p2e[0]:.2f},{midy:.2f} L {p2e[0]:.2f},{p2e[1]:.2f}")
                    mx, my = (p1[0] + p2e[0]) / 2, midy
                if rel.get("direction") == "bidirectional":
                    # glyph must follow the relation type: a bidirectional
                    # inhibition keeps T-bars rather than becoming arrows
                    if marker in ("arrow", "tbar", "open"):
                        ref = f"url(#{marker}-{sanitize_id(color)})"
                        attr += f' marker-start="{ref}" marker-end="{ref}"'
                elif marker in ("arrow", "tbar", "open"):
                    attr += f' marker-end="url(#{{m}}-{sanitize_id(color)})"'.replace("{m}", marker)
            out.append(f'<path {attr} d="{d}"/>')
            if rel.get("label") and mx is not None:
                fs = (self.font_pt - 1) * PT_TO_MM
                common = (f'x="{mx:.2f}" y="{my:.2f}" font-family="{esc(self.font_family)}" '
                          f'font-size="{fs:.3f}" text-anchor="middle" dominant-baseline="middle" '
                          f'data-source-refs="{esc(refs)}"')
                # halo as its own element (paint-order is buggy in some cairosvg
                # builds where the fill then disappears)
                out.append(f'<text {common} fill="none" stroke="white" stroke-width="1.1">'
                           f'{esc(rel["label"])}</text>')
                out.append(f'<text {common} fill="#111111">{esc(rel["label"])}</text>')
        out.append('</g>')
        return "".join(out)

    # -- charts ------------------------------------------------------------
    def _charts_layer(self):
        out = ['<g id="layer-charts">']
        for ch in self.charts:
            out.append(self._one_chart(ch))
        out.append('</g>')
        return "".join(out)

    def _one_chart(self, ch):
        x, y = float(ch.get("x_mm", 10)), float(ch.get("y_mm", 10))
        w, h = float(ch.get("w_mm", 90)), float(ch.get("h_mm", 60))
        refs = " ".join(ch.get("source_refs", []))
        ml, mr, mt, mb = 22, 8, 8, 24
        px, py = x + ml, y + mt
        pw, ph = w - ml - mr, h - mt - mb
        g = [f'<g id="chart-{sanitize_id(ch["id"])}" data-source-refs="{esc(refs)}">']
        cats = ch.get("categories", [])
        series = ch.get("series", [])

        if ch.get("type") in ("bar", "line", "scatter"):
            def _num(v):
                try:
                    if isinstance(v, bool):
                        return None
                    f = float(v)
                    return f if math.isfinite(f) else None
                except (TypeError, ValueError):
                    return None

            # coerce once; invalid/missing points stay None and are never
            # replaced by fabricated zeroes
            clean = []
            for s in series:
                nums = [_num(v) for v in s.get("values", [])]
                if len(nums) < len(cats):
                    nums += [None] * (len(cats) - len(nums))
                if any(x is None for x in nums):
                    self.warnings.append(
                        f"chart '{ch.get('id')}' series '{s.get('name')}': "
                        f"non-numeric/missing values skipped rather than imputed")
                clean.append({"spec": s, "nums": nums})
            valid = [x for grp in clean for x in grp["nums"] if x is not None]
            if valid:
                ymin = float(ch.get("y_min", min(0, min(valid))))
                ymax = float(ch.get("y_max", max(valid)))
            else:
                ymin = float(ch.get("y_min", 0))
                ymax = float(ch.get("y_max", 1))
            ticks, _ = nice_ticks(ymin, ymax)
            ymin = min(ymin, ticks[0])
            ymax = max(ymax, ticks[0])

            def yy(v):
                return py + ph * (1 - (v - ymin) / (ymax - ymin))

            # grid + y ticks
            for tv in ticks:
                ty = yy(tv)
                g.append(f'<line x1="{px:.2f}" y1="{ty:.2f}" x2="{px + pw:.2f}" y2="{ty:.2f}" '
                         f'stroke="#e5e8eb" stroke-width="0.2"/>')
                g.append(f'<text x="{px - 1.5:.2f}" y="{ty:.2f}" font-family="{esc(self.font_family)}" '
                         f'font-size="{(self.font_pt - 1.5) * PT_TO_MM:.3f}" text-anchor="end" '
                         f'dominant-baseline="middle">{esc(tv)}</text>')
            # axes
            g.append(f'<line x1="{px:.2f}" y1="{py:.2f}" x2="{px:.2f}" y2="{py + ph:.2f}" '
                     f'stroke="#222222" stroke-width="0.3"/>')
            g.append(f'<line x1="{px:.2f}" y1="{py + ph:.2f}" x2="{px + pw:.2f}" y2="{py + ph:.2f}" '
                     f'stroke="#222222" stroke-width="0.3"/>')

            n = max(1, len(cats))
            band = pw / n
            if ch["type"] == "bar":
                ns = max(1, len(clean))
                bar_w = min(10, band * 0.7 / ns)
                for ci, cat in enumerate(cats):
                    cx0 = px + band * (ci + 0.5)
                    for si, grp in enumerate(clean):
                        v = grp["nums"][ci] if ci < len(grp["nums"]) else None
                        if v is None:
                            continue  # no bar for an invalid point
                        s = grp["spec"]
                        color = s.get("color") or self._series_color(s) or "#4c78a8"
                        bx = cx0 + (si - (ns - 1) / 2) * bar_w
                        ty = yy(v)
                        g.append(f'<rect x="{bx:.2f}" y="{ty:.2f}" width="{bar_w:.2f}" '
                                 f'height="{py + ph - ty:.2f}" fill="{esc(color)}" '
                                 f'data-source-refs="{esc(" ".join(s.get("source_refs", [])))}"/>')
                    g.append(f'<text x="{cx0:.2f}" y="{py + ph + 3.2:.2f}" font-family="{esc(self.font_family)}" '
                             f'font-size="{(self.font_pt - 1.5) * PT_TO_MM:.3f}" text-anchor="middle">{esc(cat)}</text>')
            else:
                for ci, cat in enumerate(cats):
                    cx0 = px + band * (ci + 0.5)
                    g.append(f'<text x="{cx0:.2f}" y="{py + ph + 3.2:.2f}" font-family="{esc(self.font_family)}" '
                             f'font-size="{(self.font_pt - 1.5) * PT_TO_MM:.3f}" text-anchor="middle">{esc(cat)}</text>')
                for grp in clean:
                    s = grp["spec"]
                    color = s.get("color") or self._series_color(s) or "#4c78a8"
                    pts = []
                    for ci, v in enumerate(grp["nums"]):
                        pts.append(None if v is None
                                   else (px + band * (ci + 0.5), yy(v)))
                    if ch["type"] == "line":
                        # break the polyline at every invalid point
                        seg = []
                        for p in pts:
                            if p is None:
                                if seg:
                                    pstr = " ".join(f"{a:.2f},{b:.2f}" for a, b in seg)
                                    g.append(f'<polyline points="{pstr}" fill="none" '
                                             f'stroke="{esc(color)}" stroke-width="0.4"/>')
                                    seg = []
                            else:
                                seg.append(p)
                        if seg:
                            pstr = " ".join(f"{a:.2f},{b:.2f}" for a, b in seg)
                            g.append(f'<polyline points="{pstr}" fill="none" '
                                     f'stroke="{esc(color)}" stroke-width="0.4"/>')
                    for p in pts:
                        if p is not None:
                            g.append(f'<circle cx="{p[0]:.2f}" cy="{p[1]:.2f}" r="0.9" fill="{esc(color)}"/>')

            # axis titles
            if ch.get("x_title"):
                g.append(f'<text x="{px + pw / 2:.2f}" y="{y + h - 2:.2f}" font-family="{esc(self.font_family)}" '
                         f'font-size="{(self.font_pt - 1) * PT_TO_MM:.3f}" text-anchor="middle">{esc(ch["x_title"])}</text>')
            if ch.get("y_title"):
                yt = ch["y_title"] + (f" ({ch['y_unit']})" if ch.get("y_unit") else "")
                g.append(f'<text transform="translate({x + 3:.2f},{py + ph / 2:.2f}) rotate(-90)" '
                         f'font-family="{esc(self.font_family)}" font-size="{(self.font_pt - 1) * PT_TO_MM:.3f}" '
                         f'text-anchor="middle">{esc(yt)}</text>')
            if ch.get("title"):
                g.append(f'<text x="{x:.2f}" y="{y - 1.5:.2f}" font-family="{esc(self.font_family)}" '
                         f'font-size="{self.font_pt * PT_TO_MM:.3f}" font-weight="700">{esc(ch["title"])}</text>')
            # per-series legend
            if len(series) > 1:
                for si, s in enumerate(series):
                    color = s.get("color") or self._series_color(s) or "#4c78a8"
                    lx = px + si * 26
                    g.append(f'<rect x="{lx:.2f}" y="{py - 5:.2f}" width="3.2" height="2.4" fill="{esc(color)}"/>')
                    g.append(f'<text x="{lx + 4:.2f}" y="{py - 3.2:.2f}" font-family="{esc(self.font_family)}" '
                             f'font-size="{(self.font_pt - 1.5) * PT_TO_MM:.3f}">{esc(s.get("name", ""))}</text>')
        g.append('</g>')
        return "".join(g)

    def _series_color(self, s):
        mid = s.get("mapping")
        if mid and mid in self.mappings:
            return self._lookup_scale(self.mappings[mid], s.get("name"))
        return None

    # -- legend ------------------------------------------------------------
    def _legend_layer(self, content_h):
        if self.legend_spec.get("visible") is False:
            return "", content_h
        entries = []
        # relation glyphs actually used
        for rtype, n in getattr(self, "_used_relation_types", {}).items():
            entries.append(("relation", rtype,
                            RELATION_STYLE.get(rtype, RELATION_STYLE["unknown"])))
        # categorical mappings
        for mid, m in self.mappings.items():
            if m.get("scale_type") == "categorical":
                for v, c in zip(m.get("domain", []), m.get("range", [])):
                    entries.append(("swatch", v, c))
        # explicit items
        for it in self.legend_spec.get("items", []):
            entries.append(("custom", it.get("label"), it))

        if not entries:
            return "", content_h

        row_h = 5.2
        rows = []
        for kind, label, payload in entries:
            rows.append((kind, label, payload))
        box_w = float(self.legend_spec.get("width_mm", 62))
        box_h = len(rows) * row_h + 6 + (4.5 if self.legend_spec.get("title") else 0)
        lx = self.pad
        ly = content_h + 4
        g = [f'<g id="layer-legend">',
             f'<rect x="{lx:.2f}" y="{ly:.2f}" width="{box_w:.2f}" height="{box_h:.2f}" '
             f'rx="1.5" fill="white" stroke="#9aa5b1" stroke-width="0.25"/>']
        if self.legend_spec.get("title"):
            g.append(f'<text x="{lx + 2.5:.2f}" y="{ly + 4:.2f}" font-family="{esc(self.font_family)}" '
                     f'font-size="{(self.font_pt - 0.5) * PT_TO_MM:.3f}" font-weight="700">'
                     f'{esc(self.legend_spec["title"])}</text>')
        top = ly + (5 if self.legend_spec.get("title") else 2.5)
        for i, (kind, label, payload) in enumerate(rows):
            cy = top + i * row_h + 2
            if kind == "relation":
                color = "#111111"
                self.marker_colors.add(color)
                x0, x1 = lx + 3, lx + 12
                marker = payload["marker"]
                line = (f'<line x1="{x0:.2f}" y1="{cy:.2f}" x2="{x1:.2f}" y2="{cy:.2f}" '
                        f'stroke="{color}" stroke-width="0.3"')
                if payload["dash"]:
                    line += f' stroke-dasharray="{payload["dash"]}"'
                if marker in ("arrow", "tbar", "open"):
                    line += f' marker-end="url(#{{m}}-{sanitize_id(color)})"'.replace("{m}", marker)
                line += "/>"
                g.append(line)
                g.append(f'<text x="{x1 + 2.5:.2f}" y="{cy:.2f}" font-family="{esc(self.font_family)}" '
                         f'font-size="{(self.font_pt - 1) * PT_TO_MM:.3f}" dominant-baseline="middle">{esc(label)}</text>')
            else:
                color = payload if kind == "swatch" else payload.get("color", "#cccccc")
                g.append(f'<rect x="{lx + 3:.2f}" y="{cy - 1.6:.2f}" width="4.2" height="3.2" '
                         f'fill="{esc(color)}" stroke="#555555" stroke-width="0.2"/>')
                g.append(f'<text x="{lx + 10:.2f}" y="{cy:.2f}" font-family="{esc(self.font_family)}" '
                         f'font-size="{(self.font_pt - 1) * PT_TO_MM:.3f}" dominant-baseline="middle">{esc(label)}</text>')
        g.append('</g>')
        return "".join(g), ly + box_h

    # -- annotations -------------------------------------------------------
    def _annotations_layer(self):
        out = ['<g id="layer-annotations">']
        for a in self.annotations:
            refs = " ".join(a.get("source_refs", []))
            x, y = float(a.get("x_mm", 5)), float(a.get("y_mm", 8))
            kind = a.get("kind", "note")
            if kind == "panel-label":
                out.append(f'<text id="ann-{sanitize_id(a.get("id", kind))}" x="{x:.2f}" y="{y:.2f}" '
                           f'font-family="{esc(self.font_family)}" font-size="{12 * PT_TO_MM:.3f}" font-weight="700" '
                           f'data-source-refs="{esc(refs)}">{esc(a.get("text", ""))}</text>')
            else:
                out.append(f'<text id="ann-{sanitize_id(a.get("id", kind))}" x="{x:.2f}" y="{y:.2f}" '
                           f'font-family="{esc(self.font_family)}" font-size="{(self.font_pt - 0.5) * PT_TO_MM:.3f}" '
                           f'data-source-refs="{esc(refs)}">{esc(a.get("text", ""))}</text>')
        out.append('</g>')
        return "".join(out)

    def _collect_marker_colors(self):
        """Must run before _defs(): every edge/legend color needs markers."""
        used = defaultdict(int)
        for rel in self.relations:
            self.marker_colors.add(rel.get("color", "#111111"))
            used[rel.get("type", "unknown")] += 1
        if self.legend_spec.get("visible") is not False:
            self.marker_colors.add("#111111")
        self._used_relation_types = used

    # -- main --------------------------------------------------------------
    def render(self) -> str:
        self.layout()
        self._collect_marker_colors()
        extents = []
        for x, y, w, h in self.rects.values():
            extents.append((x + w, y + h))
        for x, y, w, h in self.group_boxes.values():
            extents.append((x + w, y + h))
        for ch in self.charts:
            extents.append((float(ch.get("x_mm", 10)) + float(ch.get("w_mm", 90)),
                            float(ch.get("y_mm", 10)) + float(ch.get("h_mm", 60))))
        content_w = max(e[0] for e in extents) if extents else 100
        content_h = max(e[1] for e in extents) if extents else 60

        legend_svg, legend_bottom = self._legend_layer(content_h)

        cw = float(self.canvas.get("width_mm", 0)) or content_w + self.pad
        ch_h = float(self.canvas.get("height_mm", 0))
        ch_h = ch_h or max(content_h, legend_bottom) + self.pad

        bg = self.canvas.get("background", "white")
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{cw:.2f}mm" height="{ch_h:.2f}mm" '
            f'viewBox="0 0 {cw:.2f} {ch_h:.2f}" version="1.1">',
            self._defs(),
            f'<rect id="background" x="0" y="0" width="{cw:.2f}" height="{ch_h:.2f}" fill="{esc(bg)}"/>',
            self._groups_layer(),
            self._relations_layer(),
            self._entities_layer(),
            self._charts_layer(),
            legend_svg,
            self._annotations_layer(),
            '</svg>',
        ]
        return "\n".join(parts)


def render_svg(spec: dict) -> str:
    return SvgRenderer(spec).render()
