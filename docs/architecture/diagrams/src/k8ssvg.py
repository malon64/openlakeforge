#!/usr/bin/env python3
"""Self-contained SVG composer for the OpenLakeForge architecture charts.

Uses the official CNCF Kubernetes icon set (downloaded beside this file),
embedded once per (kind, variant) as a <symbol> wrapping a base64 data-URI
<image>, then <use>d at each placement. No external references — renders on
GitHub and under the Artifact CSP. K8s blue #326ce5 is string-replaced for
the purple "ephemeral" variant before encoding.
"""
import base64
import re
from pathlib import Path
from xml.sax.saxutils import escape

ICON_DIR = Path(__file__).parent
K8S_BLUE = "#326ce5"
EPHEMERAL = "#8A4B9E"

FONT = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,'SF Mono',Menlo,Consolas,monospace"

# semantic palette (matches v1 legend)
C = {
    "control": "#3A5BA0",
    "platform": "#2F6F5E",
    "ephemeral": EPHEMERAL,
    "storage": "#4A4A55",
    "bronze": "#8C5A2B",
    "silver": "#6B7280",
    "gold": "#A67C00",
    "managed": "#B25E19",
    "ink": "#211F1A",
    "dim": "#6B6860",
    "plate": "#F6F4EE",
    "card": "#FFFFFF",
}

ICON_AR = 17.500378 / 18.035334  # h/w of the CNCF icons


class Chart:
    def __init__(self, width, height, title=None, subtitle=None):
        self.w, self.h = width, height
        self.defs = {}
        self.body = []
        if title:
            self.body.append(
                f'<text x="{width/2}" y="46" text-anchor="middle" font-family="{FONT}" '
                f'font-size="24" font-weight="700" fill="{C["ink"]}">{escape(title)}</text>'
            )
        if subtitle:
            self.body.append(
                f'<text x="{width/2}" y="70" text-anchor="middle" font-family="{MONO}" '
                f'font-size="13" fill="{C["dim"]}">{escape(subtitle)}</text>'
            )

    # ---------- defs ----------
    def _symbol(self, kind, variant):
        key = f"{kind}-{variant}"
        if key in self.defs:
            return key
        raw = (ICON_DIR / f"{kind}.svg").read_text()
        if variant == "ephemeral":
            raw = raw.replace(K8S_BLUE, EPHEMERAL).replace(K8S_BLUE.upper(), EPHEMERAL)
        b64 = base64.b64encode(raw.encode()).decode()
        self.defs[key] = (
            f'<symbol id="{key}" viewBox="0 0 100 97">'
            f'<image width="100" height="97" href="data:image/svg+xml;base64,{b64}"/>'
            f"</symbol>"
        )
        return key

    def _arrow_marker(self, color):
        key = "arr" + color.lstrip("#")
        if key not in self.defs:
            self.defs[key] = (
                f'<marker id="{key}" viewBox="0 0 10 10" refX="9" refY="5" '
                f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M 0 1 L 9 5 L 0 9 z" fill="{color}"/></marker>'
            )
        return key

    # ---------- primitives ----------
    def box(self, x, y, w, h, title=None, color="platform", fill=None, dashed=False,
            title_size=15, radius=10):
        stroke = C.get(color, color)
        f = fill if fill is not None else "none"
        dash = ' stroke-dasharray="9 6"' if dashed else ""
        self.body.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
            f'fill="{f}" stroke="{stroke}" stroke-width="2"{dash}/>'
        )
        if title:
            self.body.append(
                f'<text x="{x+16}" y="{y+26}" font-family="{FONT}" font-size="{title_size}" '
                f'font-weight="700" fill="{stroke}">{escape(title)}</text>'
            )

    def icon(self, cx, top, kind, label, size=58, variant="blue", label2=None,
             label_color=None):
        key = self._symbol(kind, variant)
        ih = size * ICON_AR
        self.body.append(
            f'<use href="#{key}" x="{cx-size/2}" y="{top}" width="{size}" height="{ih}"/>'
        )
        lc = label_color or C["ink"]
        ly = top + ih + 16
        self.body.append(
            f'<text x="{cx}" y="{ly}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="12.5" font-weight="600" fill="{lc}">{escape(label)}</text>'
        )
        if label2:
            self.body.append(
                f'<text x="{cx}" y="{ly+15}" text-anchor="middle" font-family="{MONO}" '
                f'font-size="10.5" fill="{C["dim"]}">{escape(label2)}</text>'
            )

    def badge(self, x, y, w, h, lines, color="control", text_color="#FFFFFF", size=14):
        fill = C.get(color, color)
        self.body.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}"/>'
        )
        n = len(lines)
        lh = size + 6
        y0 = y + h / 2 - (n - 1) * lh / 2 + size / 3
        for i, line in enumerate(lines):
            weight = "700" if i == 0 else "500"
            self.body.append(
                f'<text x="{x+w/2}" y="{y0+i*lh}" text-anchor="middle" '
                f'font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
                f'fill="{text_color}">{escape(line)}</text>'
            )

    def label(self, x, y, text, size=13, color=None, anchor="start", mono=False,
              weight="500"):
        fam = MONO if mono else FONT
        c = color or C["dim"]
        self.body.append(
            f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="{fam}" '
            f'font-size="{size}" font-weight="{weight}" fill="{c}">{escape(text)}</text>'
        )

    def edge(self, points, color="dim", dashed=False, label=None, label_dy=-7,
             width=2):
        c = C.get(color, color)
        marker = self._arrow_marker(c)
        pts = " ".join(f"{px},{py}" for px, py in points)
        dash = ' stroke-dasharray="7 5"' if dashed else ""
        self.body.append(
            f'<polyline points="{pts}" fill="none" stroke="{c}" stroke-width="{width}"'
            f'{dash} marker-end="url(#{marker})"/>'
        )
        if label:
            mx = (points[0][0] + points[-1][0]) / 2
            my = (points[0][1] + points[-1][1]) / 2 + label_dy
            self.body.append(
                f'<text x="{mx}" y="{my}" text-anchor="middle" font-family="{MONO}" '
                f'font-size="11" fill="{c}">{escape(label)}</text>'
            )

    def cylinder(self, cx, cy, w, h, title, color="storage", sub=None):
        """Bucket / datastore: classic cylinder."""
        stroke = C.get(color, color)
        ry = min(14, h * 0.16)
        top = cy - h / 2
        bot = cy + h / 2
        self.body.append(
            f'<path d="M {cx-w/2} {top+ry} A {w/2} {ry} 0 0 1 {cx+w/2} {top+ry} '
            f'L {cx+w/2} {bot-ry} A {w/2} {ry} 0 0 1 {cx-w/2} {bot-ry} Z" '
            f'fill="{stroke}" fill-opacity="0.12" stroke="{stroke}" stroke-width="2"/>'
        )
        self.body.append(
            f'<ellipse cx="{cx}" cy="{top+ry}" rx="{w/2}" ry="{ry}" fill="{stroke}" '
            f'fill-opacity="0.25" stroke="{stroke}" stroke-width="2"/>'
        )
        ty = cy + 5 if sub is None else cy
        self.body.append(
            f'<text x="{cx}" y="{ty}" text-anchor="middle" font-family="{MONO}" '
            f'font-size="12.5" font-weight="700" fill="{C["ink"]}">{escape(title)}</text>'
        )
        if sub:
            self.body.append(
                f'<text x="{cx}" y="{cy+17}" text-anchor="middle" font-family="{FONT}" '
                f'font-size="11" fill="{C["dim"]}">{escape(sub)}</text>'
            )

    def doc(self, x, y, w, h, title, sub=None, color="control"):
        """Document shape with a folded corner — for HCL/YAML sources."""
        stroke = C.get(color, color)
        fold = 16
        self.body.append(
            f'<path d="M {x} {y} L {x+w-fold} {y} L {x+w} {y+fold} L {x+w} {y+h} '
            f'L {x} {y+h} Z" fill="#FFFFFF" stroke="{stroke}" stroke-width="2"/>'
            f'<path d="M {x+w-fold} {y} L {x+w-fold} {y+fold} L {x+w} {y+fold}" '
            f'fill="{stroke}" fill-opacity="0.18" stroke="{stroke}" stroke-width="1.5"/>'
        )
        ty = y + h / 2 + (0 if sub else 5)
        self.body.append(
            f'<text x="{x+w/2}" y="{ty}" text-anchor="middle" font-family="{MONO}" '
            f'font-size="12" font-weight="700" fill="{C["ink"]}">{escape(title)}</text>'
        )
        if sub:
            self.body.append(
                f'<text x="{x+w/2}" y="{ty+16}" text-anchor="middle" font-family="{FONT}" '
                f'font-size="10.5" fill="{C["dim"]}">{escape(sub)}</text>'
            )

    def chip(self, x, y, w, h, text, color="silver", dashed=False, mono=True):
        """Small labeled pill — for tables, files, env vars."""
        stroke = C.get(color, color)
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        fam = MONO if mono else FONT
        self.body.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h/2}" '
            f'fill="{stroke}" fill-opacity="0.14" stroke="{stroke}" '
            f'stroke-width="1.5"{dash}/>'
            f'<text x="{x+w/2}" y="{y+h/2+4}" text-anchor="middle" font-family="{fam}" '
            f'font-size="11" font-weight="600" fill="{C["ink"]}">{escape(text)}</text>'
        )

    def raw(self, fragment):
        self.body.append(fragment)

    # ---------- output ----------
    def write(self, path):
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" font-family="{FONT}">'
            f'<rect width="{self.w}" height="{self.h}" fill="{C["plate"]}" rx="14"/>'
            f'<defs>{"".join(self.defs.values())}</defs>'
            f'{"".join(self.body)}</svg>'
        )
        Path(path).write_text(svg, encoding="utf-8")
        return len(svg)
