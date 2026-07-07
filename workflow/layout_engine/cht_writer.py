"""Emit an ArgyllCMS ``.cht`` chart-recognition template for an engine chart.

A ``.cht`` lets ``scanin`` read patch colours out of a *scanned image* of the
printed chart (a cheap flatbed-scanner alternative to a spectro), and carries the
per-patch reference layout used by the SpectroScan. ``printtarg -s`` writes one by
tracking the rectangle edges it draws; because the ChromIQ engine *computes* the
layout, we know every patch box exactly and can emit the same file directly from
geometry — no image edge-detection heuristics (#93, Knut).

Format (origin is **top-left, millimetres, y down** — the image-style
convention Argyll's own reference ``.cht`` files and rectarg use; scanin's
``-F`` pairs these coordinates with image-pixel corners, so matching the
image's y direction keeps that mapping reflection-free — a y-up file works
for *reading* but makes scanin's diagnostic render every label glyph
mirrored, #108 round 5):

    BOXES <n>
      F _ _ <x1> <y1> … <x4> <y4>                 # patch-area corners TL,TR,BR,BL
      X <loc> <loc> _ _ <w> <h> <xo> <yo> 0 0     # one per patch
    BOX_SHRINK <mm>
    REF_ROTATION 0.0
    XLIST <n> / YLIST <n>                          # vertical / horizontal edges
      <pos> <len> <cc>                             # normalised length + count
    EXPECTED XYZ <n>
      <loc> <X> <Y> <Z>

The ``F`` line gives ``scanin -F`` four reference corners to map a manually
placed marquee onto (#98) — the robust path since the engine prints no fiducial
*marks* on the sheet. Auto-recognition (edge ticks + outer corners) is a
convenience seed; a real scan test still validates registration before relying
on scanner reading.
"""
from __future__ import annotations

from pathlib import Path

_TOL = 0.05  # mm: merge edges this close together


def _edge_list(positions_len: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """Collapse ``(position, edge_length)`` pairs into sorted ``(pos, len, cc)``
    rows with *len* and *cc* (count) normalised to their maxima, the way
    printtarg's XLIST/YLIST are."""
    merged: list[list[float]] = []   # [pos, total_len, count]
    for pos, ln in sorted(positions_len):
        if merged and abs(pos - merged[-1][0]) <= _TOL:
            merged[-1][1] += ln
            merged[-1][2] += 1
        else:
            merged.append([pos, ln, 1.0])
    if not merged:
        return []
    max_len = max(m[1] for m in merged) or 1.0
    max_cc = max(m[2] for m in merged) or 1.0
    return [(m[0], m[1] / max_len, m[2] / max_cc) for m in merged]


def fiducials_from_boxes(boxes: list[dict]) -> tuple[float, ...] | None:
    """The four patch-area corners as ArgyllCMS fiducials, from the boxes'
    bounding box (top-left-origin mm, y down). Order is **TL, TR, BR, BL** —
    the order the user places them in ``scanin -F`` and the marquee draws
    them (#98). In y-down coordinates the top-left is (xmin, ymin). Returns
    ``None`` for an empty box list."""
    if not boxes:
        return None
    xmin = min(b["x"] for b in boxes)
    xmax = max(b["x"] + b["w"] for b in boxes)
    ymin = min(b["y"] for b in boxes)
    ymax = max(b["y"] + b["h"] for b in boxes)
    #     TL          TR          BR          BL
    return (xmin, ymin, xmax, ymin, xmax, ymax, xmin, ymax)


def build_cht_text(boxes: list[dict], expected: list[tuple[str, float, float, float]],
                   emit_fiducials: bool = True) -> str:
    """Render the ``.cht`` text. *boxes* are ``{loc,x,y,w,h}`` in mm with a
    bottom-left origin; *expected* is ``(loc, X, Y, Z)`` reference values.

    When *emit_fiducials* (the default), an ``F`` box line carrying the four
    patch-area corners (TL, TR, BR, BL) is prepended — it lets ``scanin -F``
    register a scan from four manually-placed corners (#98). The ``F`` line is
    **not** included in the ``BOXES`` count: ArgyllCMS ``scanin`` skips the
    fiducial line without counting it (verified against ``scanrd.c`` and by a
    real ``scanin`` read — an over-count makes it abort with "More BOXes than
    declared"), exactly as Argyll's own reference ``.cht`` files do (e.g.
    ``it8.cht``'s ``BOXES 290`` covers its X/Y/D boxes but not its ``F`` line)."""
    fids = fiducials_from_boxes(boxes) if emit_fiducials else None
    n_boxes = len(boxes)
    out: list[str] = ["", "", f"BOXES {n_boxes}"]
    if fids:
        out.append("  F _ _ " + " ".join(f"{v:f}" for v in fids))
    mins = 1e6
    for b in boxes:
        out.append("  X {loc} {loc} _ _ {w:f} {h:f} {x:f} {y:f} 0 0".format(
            loc=b["loc"], w=b["w"], h=b["h"], x=b["x"], y=b["y"]))
        mins = min(mins, b["w"], b["h"])
    out.append("")
    out.append("BOX_SHRINK {:f}".format((mins if mins < 1e6 else 1.0) * 0.15))
    out.append("")
    out.append("REF_ROTATION 0.0")
    out.append("")

    # Vertical edges (constant x) → XLIST, length = patch height; horizontal
    # edges (constant y) → YLIST, length = patch width.
    xedges = [(b["x"], b["h"]) for b in boxes] + [(b["x"] + b["w"], b["h"]) for b in boxes]
    yedges = [(b["y"], b["w"]) for b in boxes] + [(b["y"] + b["h"], b["w"]) for b in boxes]
    xl = _edge_list(xedges)
    yl = _edge_list(yedges)
    out.append(f"XLIST {len(xl)}")
    out += [f"  {p:f} {ln:f} {cc:f}" for p, ln, cc in xl]
    out.append("")
    out.append(f"YLIST {len(yl)}")
    out += [f"  {p:f} {ln:f} {cc:f}" for p, ln, cc in yl]
    out.append("")
    out.append("")

    out.append(f"EXPECTED XYZ {len(expected)}")
    out += [f"  {loc} {x:f} {y:f} {z:f}" for loc, x, y, z in expected]
    out.append("")
    return "\n".join(out)


def boxes_from_patch_rects(patch_rects: list[dict], paper_h_mm: float, dpi: int,
                           page: int = 0) -> list[dict]:
    """Convert engine ``patch_rects_px`` (top-left origin, px) for *page* into
    ``.cht`` boxes (top-left origin, mm, y down — same direction as the image,
    so scanin's ``-F`` mapping carries no reflection; *paper_h_mm* is kept for
    call-site compatibility but no longer used)."""
    del paper_h_mm
    s = 25.4 / dpi
    boxes = []
    for r in patch_rects:
        if r.get("page", 0) != page:
            continue
        boxes.append({"loc": r["loc"], "x": r["x"] * s, "y": r["y"] * s,
                      "w": r["w"] * s, "h": r["h"] * s})
    return boxes


def write_cht(path: str | Path, boxes: list[dict],
              expected: list[tuple[str, float, float, float]],
              emit_fiducials: bool = True) -> Path:
    p = Path(path)
    p.write_text(build_cht_text(boxes, expected, emit_fiducials), encoding="utf-8")
    return p
