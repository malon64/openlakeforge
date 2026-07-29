#!/usr/bin/env python3
"""Fail the build when chart text escapes the shape it belongs to.

k8ssvg draws every string as one <text> element, so nothing stops a label from
running past its box or off the plate — the failure is invisible until someone
opens the SVG. This re-parses each generated chart, re-derives every text extent
from the same estimator the composer wraps with, and reports anything that
leaves its smallest enclosing <rect>.
"""
import re
import sys
from pathlib import Path

from k8ssvg import text_width

TOLERANCE = 6  # user units of slack before a near-miss counts as a spill

TEXT_RE = re.compile(r'<text x="([\d.-]+)" y="([\d.-]+)"([^>]*)>(.*?)</text>')
RECT_RE = re.compile(
    r'<rect x="([\d.-]+)" y="([\d.-]+)" width="([\d.]+)" height="([\d.]+)"'
)


def extents(x, attrs, body):
    size = float(re.search(r'font-size="([\d.]+)"', attrs).group(1))
    bold = 'font-weight="700"' in attrs or 'font-weight="600"' in attrs
    mono = "SF Mono" in attrs
    anchor = re.search(r'text-anchor="(\w+)"', attrs)
    anchor = anchor.group(1) if anchor else "start"
    w = text_width(body, size, bold, mono)
    if anchor == "middle":
        return x - w / 2, x + w / 2
    if anchor == "end":
        return x - w, x
    return x, x + w


def check(path):
    src = path.read_text()
    canvas_w = float(re.search(r'width="([\d.]+)"', src).group(1))
    rects = [tuple(map(float, m.groups())) for m in RECT_RE.finditer(src)]
    findings = []

    for m in TEXT_RE.finditer(src):
        x, y, attrs, body = float(m.group(1)), float(m.group(2)), m.group(3), m.group(4)
        x0, x1 = extents(x, attrs, body)

        if x0 < TOLERANCE or x1 > canvas_w - TOLERANCE:
            findings.append(
                f"  canvas[0..{canvas_w:.0f}] text[{x0:.0f}..{x1:.0f}] :: {body}"
            )
            continue

        # smallest rect whose area contains the text anchor point; the full-bleed
        # background plate is excluded so it never counts as the owning box
        owners = [
            r for r in rects
            if r[0] <= x <= r[0] + r[2] and r[1] <= y <= r[1] + r[3] and r[2] < canvas_w
        ]
        if not owners:
            continue
        rx, _, rw, _ = min(owners, key=lambda r: r[2] * r[3])
        if x0 < rx + TOLERANCE or x1 > rx + rw - TOLERANCE:
            findings.append(
                f"  box[{rx:.0f}..{rx+rw:.0f}] text[{x0:.0f}..{x1:.0f}] :: {body}"
            )

    return findings


def main():
    charts = sorted((Path(__file__).resolve().parent.parent).glob("*.svg"))
    if not charts:
        print("no charts found — run the spec_chartN.py generators first", file=sys.stderr)
        return 1

    failed = False
    for chart in charts:
        findings = check(chart)
        if findings:
            failed = True
            print(f"{chart.name}: {len(findings)} overflowing text element(s)")
            print("\n".join(findings))
        else:
            print(f"{chart.name}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
