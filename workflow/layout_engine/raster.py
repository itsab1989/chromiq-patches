"""Render the chart page TIFF(s) — Qt-free, via Pillow + tifffile.

Places each patch at the *same* slot the ``.ti2`` assigns it (shared seeded
permutation), so the printed raster and the measurement file can't disagree.
Draws colour patches, contrast-chosen spacers, and per-column strip indicators.
TIFFs are written in pixels-per-centimetre (ResolutionUnit=3) exactly like
printtarg, so the existing `page_geometry` / print pipeline read the DPI right.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image, ImageDraw, ImageFont

from core.resource_path import resource_path

from . import contrast, geometry, permutation
from .colorants import to_display_rgb
from .geometry import Layout
from .instruments import Geom
from .ti1_reader import ColorTarget

# Bundled free fonts available for on-chart text (OFL).
FONTS = {
    "JetBrains Mono": "assets/fonts/JetBrainsMono-VariableFont_wght.ttf",
    "Inter": "assets/fonts/Inter-VariableFont_opsz,wght.ttf",
    "Instrument Serif": "assets/fonts/InstrumentSerif-Regular.ttf",
}
# Static (non-variable) bundled families that ship separate style faces. The
# masthead's Instrument Serif has a real Italic file — using it (not a sheared
# regular) is what makes the "IQ" glyphs, e.g. the Q's tail, match the header.
FONT_STYLE_FILES = {
    "Instrument Serif": {"italic": "assets/fonts/InstrumentSerif-Italic.ttf"},
}
DEFAULT_INDICATOR_FONT = "JetBrains Mono"

# Masthead wordmark styling (ui.masthead_header): Instrument Serif, "Chrom" in
# near-black, "IQ" bold-italic in the magenta accent.
WORDMARK_FONT = "Instrument Serif"
WORDMARK_RGB = (28, 27, 24)     # #1c1b18 — light-mode "Chrom" colour
WORDMARK_IQ_RGB = (255, 69, 115)  # #ff4573 — magenta accent for "IQ"

# ChromIQ accent palette (ui.styles TAB_COLORS) as RGB, for the coloured
# under-indicator rule; cycled per strip so adjacent strips read distinctly.
# Printer-safe distance (mm) on-sheet text keeps from the page edge, so it isn't
# clipped by a printer's unprintable border (#93, Knut). Matches the clip-content
# inset so all text/labels share one safe edge.
TEXT_EDGE_MARGIN_MM = 4.0

ACCENT_RGB = (
    (255, 69, 115),    # magenta
    (255, 180, 45),    # amber
    (86, 214, 165),    # green
    (55, 188, 214),    # cyan
    (159, 130, 255),   # violet
)

_SYSTEM_FONT_MAP: dict[str, dict[str, str]] | None = None


def _system_font_dirs() -> list[Path]:
    import sys
    home = Path.home()
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"),
                home / "Library/Fonts"]
    if sys.platform.startswith("win"):
        import os
        return [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
            home / ".fonts", home / ".local/share/fonts"]


def _style_key(subfamily: str) -> str:
    s = (subfamily or "").lower()
    b = "bold" in s
    i = "italic" in s or "oblique" in s
    return ("bolditalic" if b and i else "bold" if b else "italic" if i else "regular")


def _system_font_map() -> dict[str, dict[str, str]]:
    """Lazy family→{style: file} map for installed fonts.

    Per family we record which style faces exist (regular/bold/italic/
    bolditalic) so we can both render the right face *and* report truthfully
    which styles a font actually supports.
    """
    global _SYSTEM_FONT_MAP
    if _SYSTEM_FONT_MAP is not None:
        return _SYSTEM_FONT_MAP
    out: dict[str, dict[str, str]] = {}
    for d in _system_font_dirs():
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if f.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            try:
                fam, sub = ImageFont.truetype(str(f), 12).getname()
            except Exception:
                continue
            out.setdefault(fam, {}).setdefault(_style_key(sub or ""), str(f))
    _SYSTEM_FONT_MAP = out
    return out


def _font_path(family: str, style: str = "regular") -> str | None:
    sf = FONT_STYLE_FILES.get(family)
    if sf and style in sf:
        return resource_path(sf[style])
    if family in FONTS:
        return resource_path(FONTS[family])
    faces = _system_font_map().get(family)
    if not faces:
        return None
    return faces.get(style) or faces.get("regular") or next(iter(faces.values()))


def font_supports(family: str) -> tuple[bool, bool]:
    """``(has_bold, has_italic)`` as the engine can actually render *family*.

    Bundled variable fonts are probed via their named instances; system fonts
    by which separate style faces are installed.  This is the single source of
    truth shared by the renderer and the UI's bold/italic enable logic.
    """
    if family in FONTS:
        # Static bundled family with separate style faces (e.g. Instrument Serif
        # ships a real Italic but no Bold).
        sf = FONT_STYLE_FILES.get(family)
        if sf is not None:
            return ("bold" in sf or "bolditalic" in sf,
                    "italic" in sf or "bolditalic" in sf)
        try:
            f = ImageFont.truetype(resource_path(FONTS[family]), 12)
            low = [(_n.decode() if isinstance(_n, bytes) else _n).replace(" ", "").lower()
                   for _n in f.get_variation_names()]
        except Exception:
            return (False, False)
        return (any("bold" in n for n in low),
                any(("italic" in n or "oblique" in n) for n in low))
    faces = _system_font_map().get(family, {})
    return ("bold" in faces or "bolditalic" in faces,
            "italic" in faces or "bolditalic" in faces)


def _font(px: int, family: str = DEFAULT_INDICATOR_FONT,
          bold: bool = False, italic: bool = False
          ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    style = ("bolditalic" if bold and italic else "bold" if bold
             else "italic" if italic else "regular")
    path = _font_path(family, style) or resource_path(FONTS[DEFAULT_INDICATOR_FONT])
    try:
        f = ImageFont.truetype(path, max(6, px))
    except Exception:  # pragma: no cover - font load fallback
        return ImageFont.load_default()
    if bold or italic:
        want = ("Bold Italic" if bold and italic else "Bold" if bold else "Italic")
        want_key = want.replace(" ", "").lower()
        try:    # variable fonts (our bundled ones) expose named instances
            for n in f.get_variation_names():
                name = n.decode() if isinstance(n, bytes) else n
                if name.replace(" ", "").lower() == want_key:
                    f.set_variation_by_name(n)
                    break
        except Exception:
            pass    # static font without that instance — render regular
    return f


_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Tiny gap inserted between letters of a multi-letter strip label (e.g. "AB"),
# as a fraction of the font size, so the two letters stay distinguishable.
INDICATOR_LETTER_SPACING = 0.12
# Auto-sized multi-letter labels fill only this fraction of the strip width, so
# the inter-indicator gap exceeds the intra-letter gap (#93).
INDICATOR_FIT_FRAC = 0.80
INDICATOR_MIN_LEGIBLE_MM = 1.5   # auto-size floor — smaller is unreadable in print


def _draw_indicator(draw, cx: int, top: int, text: str, font, spacing_px: int) -> None:
    """Draw a strip label centred at *cx*, with a small gap between letters so a
    two-letter label (e.g. "AB") stays legible."""
    if len(text) <= 1 or spacing_px <= 0:
        try:
            draw.text((cx, top), text, font=font, fill=(0, 0, 0), anchor="ma")
        except Exception:             # default bitmap font: no anchor support
            tw = int(draw.textlength(text, font=font))
            draw.text((cx - tw // 2, top), text, font=font, fill=(0, 0, 0))
        return
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + spacing_px * (len(text) - 1)
    x = cx - total / 2
    for ch, w in zip(text, widths):
        try:
            draw.text((x, top), ch, font=font, fill=(0, 0, 0), anchor="la")
        except Exception:             # default bitmap font: top-left default
            draw.text((x, top), ch, font=font, fill=(0, 0, 0))
        x += w + spacing_px


def _indicator_tile(text: str, font, spacing_px: int, degrees: int) -> Image.Image:
    """A transparent tile of the strip label (letters spaced) rotated *degrees*."""
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    widths = [probe.textlength(c, font=font) for c in text]
    try:
        asc, desc = font.getmetrics()
    except Exception:  # pragma: no cover - default bitmap font
        asc, desc = 12, 3
    W = int(sum(widths) + spacing_px * (len(text) - 1)) + 4
    H = asc + desc + 4
    tile = Image.new("RGBA", (max(1, W), max(1, H)), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    x = 2.0
    for ch, w in zip(text, widths):
        d.text((x, 2), ch, font=font, fill=(0, 0, 0, 255))
        x += w + spacing_px
    if degrees % 360:
        tile = tile.rotate(degrees, expand=True)   # CCW; 90 reads bottom-to-top
    return tile


def effective_indicator_size_mm(geom, dpi: int, font: str, size_mm: float) -> float:
    """The indicator font size to use. An explicit *size_mm* is returned as-is;
    *size_mm* 0 = auto, where the size is chosen so the widest two-letter label
    (plus the inter-letter gap) fits the strip width (capped at the instrument
    text height)."""
    if size_mm:
        return float(size_mm)
    mm2px = dpi / 25.4
    target = geom.txhisl
    f = _font(max(6, round(target * mm2px)), font)
    try:
        widest2 = (2.0 * max(f.getlength(c) for c in _UPPER) / mm2px
                   + INDICATOR_LETTER_SPACING * target)   # + one inter-letter gap
    except Exception:
        return target
    # Fit the label into a FRACTION of the strip width, not the whole width, so
    # the gap BETWEEN indicators stays larger than the gap between the two
    # letters of one indicator (otherwise "AA AB" reads as "A AA B"). (#93)
    avail = geom.pwid * INDICATOR_FIT_FRAC
    if widest2 <= avail:
        return target
    # Never shrink below legibility: with a wide proportional font on a
    # narrow-patch chart the fit collapsed to a fraction of a millimetre —
    # labels so small a user thought they were switched off (#108 follow-up).
    # A slightly-too-wide label beats an invisible one; the preflight
    # too-wide warning still tells the user why.
    return max(min(target, INDICATOR_MIN_LEGIBLE_MM), target * avail / widest2)


def _furniture_reserves_mm(geom, kw: dict) -> tuple[float, float]:
    """``(label_band_mm, bottom_reserve_mm)`` — the vertical space the rendered
    strip-label band (indicator + underline) and the bottom sheet-text/stamp
    block actually consume, so :func:`geometry.compute` can reserve them.

    An auto-sized upright indicator measures its *ink* height (≈ cap height),
    which stays under the instrument ``txhisl`` so default charts keep
    printtarg-parity capacity; a big, rotated, or underlined label grows the band
    and reduces the count instead of overlapping the patches (#93).
    """
    dpi = int(kw.get("dpi") or 150)
    mm2px = dpi / 25.4
    label_band = 0.0   # indicators off ⇒ reclaim the whole label band
    if kw.get("draw_indicators", True):
        fam = kw.get("indicator_font", DEFAULT_INDICATOR_FONT)
        raw_size = float(kw.get("indicator_size_mm") or 0.0)   # 0 = auto
        size_mm = effective_indicator_size_mm(geom, dpi, fam, raw_size)
        ind_px = max(6, round(size_mm * mm2px))
        f = _font(ind_px, fam, bool(kw.get("indicator_bold")),
                  bool(kw.get("indicator_italic")))
        rot = int(kw.get("indicator_rotation") or 0) % 360
        spc = max(1, round(ind_px * INDICATOR_LETTER_SPACING))
        if rot in (90, 270):
            # Side-rotated: the band runs along the strip, so its height is the
            # label's drawn length. Reserve for up to two letters (≤702 strips).
            band_px = _indicator_tile("WW", f, spc, rot).height
        else:
            # Upright: the visible ink height of representative cap/digit glyphs.
            probe = Image.new("RGBA", (ind_px * 4, ind_px * 4), (0, 0, 0, 0))
            ImageDraw.Draw(probe).text((ind_px, ind_px), "W8", font=f,
                                       fill=(0, 0, 0, 255))
            bb = probe.getbbox()
            band_px = (bb[3] - bb[1]) if bb else ind_px
        band = band_px / mm2px
        if kw.get("underline_mode", "off") in ("segments", "cycle", "black", "colored"):
            band += (float(kw.get("underline_gap_mm") or 0.0)
                     + max(0.0, float(kw.get("underline_thickness_mm") or 0.0)))
        # Auto size keeps the instrument label floor (txhisl) so default charts
        # stay printtarg-identical; an EXPLICIT size reserves exactly what it
        # draws, so a smaller font frees space for more patches (#93).
        label_band = band if raw_size > 0 else max(geom.txhisl, band)
    # Bottom-of-sheet block: one line each for custom sheet text and the stamp,
    # drawn at line_h = px(4.2) above the printer-safe bottom inset (see
    # render_pages); the inset keeps the text clear of a printer's unprintable
    # edge (#93, Knut's "distance from page edge to text").
    nlines = (1 if kw.get("chart_text") else 0) + (1 if kw.get("stamp_command") else 0)
    _edge = float(kw.get("text_edge") or TEXT_EDGE_MARGIN_MM)
    bottom = (_edge + 4.2 * nlines) if nlines else 0.0
    return label_band, bottom


def apply_furniture_reserves(geom, kw: dict):
    """Return *geom* with label_band_mm / bottom_reserve_mm filled from the
    rendered furniture (single source of truth shared by the renderer and every
    capacity estimate, so they can't disagree — #93)."""
    lb, br = _furniture_reserves_mm(geom, kw)
    return replace(geom, label_band_mm=lb, bottom_reserve_mm=br)


def render_clip_strip(mode: str, *, width_px: int, height_px: int, dpi: int,
                      text: str = "", font_family: str = "Inter",
                      image_path: str = "", ctx: dict | None = None,
                      image_rotation: int = 0, image_scale: float = 100.0,
                      image_offset_x_mm: float = 0.0,
                      image_offset_y_mm: float = 0.0,
                      image_obj: "Image.Image | None" = None) -> Image.Image:
    """Render the left clip-strip content as a ``width_px × height_px`` image.

    The strip is tall and narrow, so text/branding are drawn on a landscape
    canvas and rotated 90° to read up the strip. Shared by the page renderer and
    the standalone template export.

    *ctx* supplies the auto-filled values for the ``notes`` design (patch count,
    instrument, paper, page, profile name, date…); when absent a sample is used
    so the panel preview / template export still shows the layout (#93).
    """
    mm2px = dpi / 25.4
    strip = Image.new("RGB", (max(1, width_px), max(1, height_px)), (255, 255, 255))

    if mode == "image" and (image_path or image_obj is not None):
        try:
            # A pre-loaded (and possibly downscaled) image lets the panel preview
            # stay smooth on a big file; generation passes the path = full quality.
            logo = (image_obj if image_obj is not None
                    else Image.open(image_path)).convert("RGBA")
            if image_rotation % 360:
                logo = logo.rotate(image_rotation % 360, expand=True,
                                   resample=Image.BICUBIC)
            # Scale = fit-to-band × the user's percent (100 = fit), then move.
            fit = min(width_px / logo.width, height_px / logo.height)
            scale = fit * max(0.05, (image_scale or 100.0) / 100.0)
            nw, nh = max(1, int(logo.width * scale)), max(1, int(logo.height * scale))
            logo = logo.resize((nw, nh))
            cx = (width_px - nw) // 2 + round(image_offset_x_mm * mm2px)
            cy = (height_px - nh) // 2 + round(image_offset_y_mm * mm2px)
            strip.paste(logo, (cx, cy), logo)
        except Exception:  # pragma: no cover - bad/missing image falls back blank
            pass
        return strip

    if mode == "notes":
        return _render_notes_strip(width_px, height_px, dpi, ctx, font_family)

    if mode == "branding":
        extra = [ln for ln in (text or "").splitlines() if ln.strip()]
        overlay = _vwordmark(extra, width_px, height_px, font_family)
        strip.paste(overlay, (0, 0), overlay)
        return strip

    # plain text → rotated text up the strip
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return strip
    overlay = _vtext("\n".join(lines), font_family, width_px, height_px)
    strip.paste(overlay, (0, 0), overlay)
    return strip


def _italic_tile(text: str, font, fill: tuple, stroke_w: int = 0,
                 shear: float = 0.22) -> tuple[Image.Image, int, int]:
    """Render *text* sheared right (faux-italic) on a baseline-aware tile.

    Returns ``(image, baseline_y, left_x)`` where *baseline_y* is the row the
    text sits on (unchanged by the horizontal shear) and *left_x* is the first
    inked column — so the caller can align it to another glyph's baseline and
    butt it up tightly.
    """
    asc, desc = font.getmetrics()
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    w = int(probe.textlength(text, font=font))
    pad = stroke_w + 3
    W, H = w + pad * 2, asc + desc + pad * 2
    base_y = pad + asc
    tile = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((pad, base_y), text, font=font, fill=fill,
                              stroke_width=stroke_w, stroke_fill=fill, anchor="ls")
    if not shear:                       # real italic face → no faux slant/resample
        bbox = tile.getbbox()
        return tile, base_y, (bbox[0] if bbox else 0)
    # AFFINE maps output→input: input_x = x + shear*(H - y) leans the top right.
    sheared = tile.transform((W + int(H * shear), H), Image.AFFINE,
                             (1, shear, -shear * H, 0, 1, 0), resample=Image.BICUBIC)
    bbox = sheared.getbbox()
    return sheared, base_y, (bbox[0] if bbox else 0)


def _vwordmark(extra_lines: list[str], width_px: int, height_px: int,
               font_family: str = "Inter") -> Image.Image:
    """The masthead "ChromIQ" wordmark — Instrument Serif, "Chrom" near-black,
    "IQ" bold-italic in magenta — plus optional lines, read up the strip. The
    optional lines use *font_family* (the user's chosen clip font), not the
    wordmark face (#93, Knut)."""
    canvas = Image.new("RGBA", (max(1, height_px), max(1, width_px)), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    n = 1 + len(extra_lines)
    chrom_fill = WORDMARK_RGB + (255,)
    iq_fill = WORDMARK_IQ_RGB + (255,)
    size = max(10, int(width_px * 0.55))
    for _ in range(40):
        f = _font(size, WORDMARK_FONT)
        f_extra = _font(size, font_family)
        wm_w = d.textlength("Chrom", font=f) + d.textlength("IQ", font=f) * 1.25
        widest = max([wm_w] + [d.textlength(l, font=f_extra) for l in extra_lines])
        if size * 1.25 * n <= width_px * 0.92 and widest <= height_px * 0.95:
            break
        size = int(size * 0.9)
        if size <= 10:
            break
    f = _font(size, WORDMARK_FONT)
    asc, desc = f.getmetrics()
    line_h = size * 1.25
    cy = (width_px - line_h * n) / 2
    # "IQ" is the masthead's real Instrument Serif *Italic* face (the masthead
    # asks for bold too, but Instrument Serif has no bold face and Qt doesn't
    # synthesise one — so the header renders plain italic). Use the genuine
    # italic glyphs (no faux shear, no faux bold) so the "IQ" — notably the Q's
    # tail — matches the header exactly instead of a sheared regular face.
    f_iq = _font(size, WORDMARK_FONT, italic=True)
    iq_tile, iq_base, iq_left = _italic_tile("IQ", f_iq, iq_fill, shear=0.0)
    chrom_w = d.textlength("Chrom", font=f)
    kern = size * 0.02
    wm_w = chrom_w + kern + (iq_tile.width - iq_left)
    x = (height_px - wm_w) / 2
    # Share one baseline so "IQ" sits level with "Chrom" (not raised).
    baseline = cy + line_h * 0.5 + (asc - desc) / 2
    try:
        d.text((x, baseline), "Chrom", font=f, fill=chrom_fill, anchor="ls")
        canvas.paste(iq_tile,
                     (int(x + chrom_w + kern - iq_left), int(baseline - iq_base)),
                     iq_tile)
        f_extra = _font(size, font_family)      # user's chosen clip font
        for i, ln in enumerate(extra_lines, start=1):
            d.text((height_px / 2, cy + line_h * (i + 0.5)), ln, font=f_extra,
                   fill=chrom_fill, anchor="mm")
    except Exception:  # pragma: no cover - default font without anchor
        d.text((x, baseline), "ChromIQ", font=f, fill=chrom_fill)
    return canvas.rotate(90, expand=True)


def _vtext(text: str, font_family: str, width_px: int, height_px: int,
           *, valign: str = "center", bold: bool = False) -> Image.Image:
    """A transparent ``width_px × height_px`` overlay with *text* read up the strip."""
    # Draw on a landscape canvas (long = height_px, short = width_px), rotate 90°.
    canvas = Image.new("RGBA", (max(1, height_px), max(1, width_px)), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    lines = text.split("\n")
    size = max(8, int(width_px * 0.42))
    f = _font(size, font_family, bold=bold)
    # shrink to fit the short dimension across all stacked lines
    for _ in range(40):
        f = _font(size, font_family, bold=bold)
        line_h = size * 1.2
        block_h = line_h * len(lines)
        widest = max((d.textlength(ln, font=f) for ln in lines), default=0)
        if block_h <= width_px * 0.9 and widest <= height_px * 0.95:
            break
        size = int(size * 0.9)
        if size <= 8:
            break
    line_h = size * 1.2
    block_h = line_h * len(lines)
    cy = (width_px - block_h) / 2
    cx = (height_px * 0.04 if valign == "top" else height_px / 2)
    anchor = "lm" if valign == "top" else "mm"
    for i, ln in enumerate(lines):
        y = cy + line_h * (i + 0.5)
        try:
            d.text((cx, y), ln, font=f, fill=(0, 0, 0, 255), anchor=anchor)
        except Exception:  # pragma: no cover
            d.text((cx, y), ln, font=f, fill=(0, 0, 0, 255))
    return canvas.rotate(90, expand=True)


def _notes_sample_ctx() -> dict:
    """Placeholder values for the notes design when no real chart context is
    supplied (panel preview / template export)."""
    return {"count": "560", "instrument": "i1Pro", "paper": "A4 landscape",
            "page": "page 1/2", "strips": "12", "date": "2026-01-01",
            "project": "My printer profile"}


def _draw_wordmark_h(canvas: Image.Image, draw: "ImageDraw.ImageDraw",
                     x: float, top: float, height: float, max_w: float) -> float:
    """Draw the ChromIQ wordmark (serif "Chrom" + italic-magenta "IQ") left-
    anchored in a band of *height* at (x, top); returns the width consumed."""
    size = max(8, int(height * 0.74))
    f = _font(size, WORDMARK_FONT)
    for _ in range(24):
        f = _font(size, WORDMARK_FONT)
        f_iq = _font(size, WORDMARK_FONT, italic=True)
        iq_tile, iq_base, iq_left = _italic_tile(
            "IQ", f_iq, WORDMARK_IQ_RGB + (255,), shear=0.0)
        chrom_w = draw.textlength("Chrom", font=f)
        kern = size * 0.02
        total = chrom_w + kern + (iq_tile.width - iq_left)
        if total <= max_w and iq_tile.height <= height * 1.05:
            break
        size = int(size * 0.9)
        if size <= 8:
            break
    asc, desc = f.getmetrics()
    baseline = top + height * 0.5 + (asc - desc) / 2
    try:
        draw.text((x, baseline), "Chrom", font=f, fill=WORDMARK_RGB, anchor="ls")
        canvas.paste(iq_tile,
                     (int(x + chrom_w + kern - iq_left), int(baseline - iq_base)),
                     iq_tile)
    except Exception:  # pragma: no cover - default font without anchor
        draw.text((x, top), "ChromIQ", font=f, fill=WORDMARK_RGB)
    return chrom_w + kern + (iq_tile.width - iq_left)


def _notes_row(draw: "ImageDraw.ImageDraw", font, y_center: float, x0: float,
               x_end: float, segments: list, mm2px: float,
               rule_rgb=(120, 120, 120)) -> None:
    """Lay out one info row left→right. *segments* are ``("text", s)`` for a
    filled value or ``("rule", label)`` for a handwrite label followed by a write
    line. The spare length is split among the rules, so a longer strip (taller
    page) gives the user more room to write — not just a stretched bitmap."""
    gap = max(2, round(3.0 * mm2px))
    label_gap = max(1, round(1.5 * mm2px))
    fixed = 0.0
    n_rules = 0
    for kind, s in segments:
        fixed += draw.textlength(s, font=font) + gap
        if kind == "rule":
            n_rules += 1
            fixed += label_gap
    rule_w = max(0.0, (x_end - x0) - fixed) / n_rules if n_rules else 0.0
    asc, desc = font.getmetrics()
    baseline = y_center + (asc - desc) / 2
    lw = max(1, round(0.3 * mm2px))
    x = x0
    for kind, s in segments:
        draw.text((x, baseline), s, font=font, fill=(0, 0, 0), anchor="ls")
        x += draw.textlength(s, font=font)
        if kind == "rule":
            x += label_gap
            ly = baseline + max(1, round(0.6 * mm2px))
            draw.line([(x, ly), (x + rule_w, ly)], fill=rule_rgb, width=lw)
            x += rule_w
        x += gap


def _render_notes_strip(width_px: int, height_px: int, dpi: int,
                        ctx: dict | None, font_family: str) -> Image.Image:
    """The ChromIQ clip-border notes design (#93): a spectrum accent bar, the
    wordmark, and three info rows — auto-filled values plus handwrite rules.

    Drawn on a horizontal length×thickness canvas (so it scales by *content*, not
    by stretching) and rotated 90° to read up the strip. Font size follows the
    clip-border thickness; the handwrite rules absorb the extra length."""
    c = dict(_notes_sample_ctx())
    if ctx:
        c.update({k: str(v) for k, v in ctx.items() if v not in (None, "")})
    mm2px = dpi / 25.4
    L, T = max(1, height_px), max(1, width_px)         # length × thickness (px)
    canvas = Image.new("RGB", (L, T), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    pad = max(2, round(2.0 * mm2px))

    # Full-length spectrum accent bar along the top edge.
    bar_h = max(2, round(0.06 * T))
    seg = L / len(ACCENT_RGB)
    for i, col in enumerate(ACCENT_RGB):
        d.rectangle([round(i * seg), 0, round((i + 1) * seg) - 1, bar_h - 1], fill=col)

    top = bar_h + pad
    avail = max(1.0, T - top - pad)
    row_h = avail / 3.0

    logo_w = _draw_wordmark_h(canvas, d, pad, top, avail, max_w=L * 0.20)
    x0 = pad + logo_w + max(round(6 * mm2px), pad * 2)
    x_end = L - pad
    avail_w = max(1.0, x_end - x0)
    yc = [top + row_h * (i + 0.5) for i in range(3)]

    # Row content. Rules ("rule") absorb spare length; texts are fixed-width.
    left1 = (f"{c['count']} patches  ·  {c['instrument']}  ·  {c['paper']}  ·  "
             f"colour management: OFF")
    right1 = f"{c['page']}  ·  strips on page: {c['strips']}"
    row2 = [("text", f"date: {c['date']}"), ("rule", "printer:"),
            ("rule", "ink set:"), ("rule", "paper brand / type:")]
    row3 = [("rule", "media / resolution setting:"),
            ("text", f"profile name: {c['project']}")]

    # Font: sized from the clip thickness for legibility, then shrunk so the
    # busiest row still fits the length (keeping a minimum write-line per rule),
    # so a wider clip means bigger text — never text running off the strip (#93).
    gap = max(2, round(3.0 * mm2px))
    min_line = round(12.0 * mm2px)

    def _need(font) -> float:
        n1 = d.textlength(left1, font=font) + gap + d.textlength(right1, font=font)
        n2 = sum(d.textlength(s, font=font) + gap for _, s in row2) \
            + 3 * min_line
        n3 = sum(d.textlength(s, font=font) + gap for _, s in row3) + min_line
        return max(n1, n2, n3)

    size = max(8, int(row_h * 0.46))
    font = _font(size, font_family)
    need = _need(font)
    if need > avail_w:
        size = max(8, int(size * avail_w / need))
        font = _font(size, font_family)
    asc, desc = font.getmetrics()

    b1 = yc[0] + (asc - desc) / 2
    d.text((x0, b1), left1, font=font, fill=(0, 0, 0), anchor="ls")
    d.text((x_end, b1), right1, font=font, fill=(90, 90, 90), anchor="rs")
    _notes_row(d, font, yc[1], x0, x_end, row2, mm2px)
    _notes_row(d, font, yc[2], x0, x_end, row3, mm2px)

    return canvas.rotate(90, expand=True)


@dataclass(frozen=True)
class RenderResult:
    images: list[Image.Image]
    low_contrast_passes: list[int]   # global pass indices flagged by the guard
    # Bottom of the rendered strip-label band (labels + underline) in page px,
    # or None when indicators are off. The measure-tab scan arrow hangs from
    # this line, printtarg-style; without it the arrow floats above the patches.
    label_band_bottom_px: int | None = None


def _hexagon_points(x0: int, y0: int, w: int, ph: int, step: int):
    """Six vertices of a printtarg-style SpectroScan hexagon for the patch slot
    at ``(x0, y0)`` sized ``w × ph`` (px), staggered ±¼·w by the patch's index
    in the strip (#93, Knut). Pointed top and bottom, flat vertical sides; the
    apexes reach ⅙·ph beyond the slot top and bottom (the geometry reserves that
    as ``hxeh``), so neighbouring rows interlock as in ``printtarg -h``."""
    dx = round(-w / 4) if step % 2 == 0 else round(w / 4)
    t6 = ph / 6.0
    left, right = x0 + dx, x0 + w + dx
    cx = round(x0 + w / 2 + dx)
    return [
        (cx, round(y0 - t6)),               # top apex
        (right, round(y0 + t6)),            # upper-right
        (right, round(y0 + 5 * t6)),        # lower-right
        (cx, round(y0 + ph + t6)),          # bottom apex
        (left, round(y0 + 5 * t6)),         # lower-left
        (left, round(y0 + t6)),             # upper-left
    ]


def render_pages(
    target: ColorTarget,
    layout: Layout,
    geom: Geom,
    *,
    seed: int,
    randomize: bool = True,
    paper_w_mm: float,
    paper_h_mm: float,
    dpi: int = 300,
    strip_pattern: str = permutation.DEFAULT_STRIP_PATTERN,
    spacer_mode: str = "colored",
    spacer_palette: "list[tuple[int, int, int]] | None" = None,
    spacer_overrides: "dict[int, tuple[int, int, int]] | None" = None,
    edge_spacers: bool = False,
    draw_indicators: bool = True,
    indicator_font: str = DEFAULT_INDICATOR_FONT,
    indicator_size_mm: float = 0.0,
    indicator_bold: bool = False,
    indicator_italic: bool = False,
    indicator_rotation: int = 0,
    indicator_align: str = "left",
    underline_mode: str = "off",
    underline_thickness_mm: float = 0.5,
    underline_gap_mm: float = 0.5,
    chart_text: str = "",
    chart_text_font: str = "Inter",
    chart_text_size_mm: float = 0.0,
    chart_text_bold: bool = False,
    chart_text_italic: bool = False,
    stamp_text: str = "",
    text_edge_mm: float = TEXT_EDGE_MARGIN_MM,
    clip_content_mode: str = "off",
    clip_text: str = "",
    clip_text_font: str = "Inter",
    clip_image_path: str = "",
    clip_image_rotation: int = 0,
    clip_image_scale: float = 100.0,
    clip_image_offset_x_mm: float = 0.0,
    clip_image_offset_y_mm: float = 0.0,
    strip_label_offset_mm: float = 0.0,
    text_ctx: "dict | None" = None,
) -> RenderResult:
    """Render one :class:`PIL.Image` per page for *target*.

    *spacer_mode* picks the inter-patch spacer colour: ``"colored"`` (default,
    like printtarg) or ``"bw"``.  No spacers are drawn when the geometry has no
    gap (``spacer_mode`` ``"none"`` ⇒ build with ``spacer_on=False``).
    """
    mm2px = dpi / 25.4
    W = max(1, round(paper_w_mm * mm2px))
    H = max(1, round(paper_h_mm * mm2px))

    # Patch list incl. padding, then slot assignment (identical to ti2_writer).
    media = target.media_patch()
    patches = list(target.patches) + [media] * layout.padding
    total = len(patches)
    slots = permutation.location_permutation(total, seed, randomize)
    rgb_by_slot: list[tuple[int, int, int]] = [(255, 255, 255)] * total
    for i, (dev, _xyz) in enumerate(patches):
        rgb_by_slot[slots[i]] = to_display_rgb(dev, target.color_rep)

    place = geometry.placement(geom, paper_w_mm, paper_h_mm, layout)
    steps = layout.steps_in_pass
    pppage = layout.patches_per_page
    label_strip = permutation.make_labeller(strip_pattern)
    label_patch = permutation.make_labeller(permutation.DEFAULT_PATCH_PATTERN)

    def px(mm: float) -> int:
        return round(mm * mm2px)

    pl_px = px(place.plen)
    sp_px = px(place.pspa)
    # SpectroScan hexagonal patches (printtarg -h): draw interlocking hexagons
    # instead of rectangles (#93, Knut). Capacity is unchanged — only the shape.
    ss_hex = getattr(geom, "key", "") == "SS" and getattr(geom, "hxew", 0.0) > 0
    # Row-number band width (SpectroScan labels the grid 2-D): 0 for instruments
    # without it. Drawn to the left of the patches, the band placement reserves.
    _row_band_px = px(getattr(geom, "rlwi", 0.0))
    ind_px = px(effective_indicator_size_mm(
        geom, dpi, indicator_font, indicator_size_mm))
    font = _font(ind_px, indicator_font, indicator_bold, indicator_italic)
    if underline_mode == "colored":          # legacy alias → 5-segment bar
        underline_mode = "segments"
    underline_on = draw_indicators and underline_mode in ("segments", "cycle", "black")
    ul_th = max(1, px(underline_thickness_mm or 0.5))
    ul_gap = px(underline_gap_mm)

    # Inter-letter gap (constant for the whole chart) and the vertical height the
    # label band reserves. For side-rotated labels (90°/270°) the band is sized
    # to the LONGEST label on the chart so every strip's reading-start letter can
    # be anchored on the same line (the patch-side line stays fixed regardless of
    # how many letters a label has) and the underline clears the tallest label.
    _spc = max(1, round(ind_px * INDICATOR_LETTER_SPACING))
    _rot = indicator_rotation % 360
    _is_side = _rot in (90, 270)
    if draw_indicators and _is_side:
        _n_total_strips = max(1, (total + steps - 1) // steps)
        _longest = label_strip(_n_total_strips)
        label_band_h = _indicator_tile(_longest, font, _spc, _rot).height
    else:
        label_band_h = ind_px

    # Strip-label vertical position: leader_top is where the band sits; a user
    # offset (mm) nudges the labels up (negative, toward the top margin) or down,
    # together with their underline (#93).
    _lbl_top = px(place.leader_top + strip_label_offset_mm)
    _band_bottom = None
    if draw_indicators:
        _band_bottom = _lbl_top + label_band_h + \
            ((ul_gap + ul_th) if underline_on else 0)

    def _resolve_with(t: str, ctx: dict) -> str:
        try:
            return t.format(**ctx) if t else ""
        except (KeyError, IndexError, ValueError):
            return t                       # leave unknown placeholders literal

    images: list[Image.Image] = []
    for page in range(layout.pages):
        img = Image.new("RGB", (W, H), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        # Per-page placeholder context: {page} = "page X/Y", plus the chart-wide
        # {project}/{paper}/… from text_ctx. Used for chart text + clip text.
        _pctx = dict(text_ctx or {})
        _pctx["page"] = f"page {page + 1}/{layout.pages}"
        _chart_text = _resolve_with(chart_text, _pctx)
        _clip_text = _resolve_with(clip_text, _pctx)
        first = page * pppage
        last = min(total, first + pppage)
        n_on_page = last - first
        n_passes = (n_on_page + steps - 1) // steps

        for p in range(n_passes):
            x0 = px(place.x_of(p))
            # Right edge from the patch's true mm position, not x0 + a fixed
            # rounded width: when strips touch (pwid == rrsp) the 8 mm pitch
            # rounds alternately to 94/95 px, so a fixed 94 px width left a 1 px
            # gap after every other strip. Deriving xR here tiles them seamlessly
            # while still leaving any intended gap when pwid < rrsp.
            xR = px(place.x_of(p) + place.pwid)
            strip_w = xR - x0
            global_strip = (first // steps) + p
            # ColorMunki "offset every second strip": odd strips shift down by
            # the rig stagger (#93, Knut). 0 for everything else.
            _stag = px(getattr(geom, "row_stagger_mm", 0.0)) if (global_strip & 1) else 0
            col_slots = list(range(first + p * steps,
                                   min(last, first + (p + 1) * steps)))
            if draw_indicators:
                _lbl = label_strip(global_strip + 1)
                _cx = x0 + strip_w // 2          # centre over the strip
                _y = _lbl_top
                if _rot == 0:
                    _draw_indicator(draw, _cx, _y, _lbl, font, _spc)
                else:                            # rotated label → tile + paste
                    _tile = _indicator_tile(_lbl, font, _spc, indicator_rotation)
                    # Justify within the label band along the reading axis. The
                    # patch-side line (band bottom) is fixed for every strip, so a
                    # label gaining a second letter grows AWAY from the patches
                    # instead of creeping toward them (#93).
                    _extra = max(0, label_band_h - _tile.height)
                    if not _is_side:             # 180°: top-aligned like before
                        _off = 0
                    elif indicator_align == "center":
                        _off = _extra // 2
                    elif indicator_align == "left":   # reading-start anchored
                        _off = _extra if _rot == 90 else 0
                    else:                             # right: reading-end anchored
                        _off = 0 if _rot == 90 else _extra
                    img.paste(_tile, (_cx - _tile.width // 2, _y + _off), _tile)
                if underline_on and underline_mode == "cycle":   # one accent / strip
                    _ly = _y + label_band_h + ul_gap
                    draw.rectangle([x0, _ly, xR - 1, _ly + ul_th - 1],
                                   fill=ACCENT_RGB[global_strip % len(ACCENT_RGB)])
                # SpectroScan labels the grid 2-D: column letters on top (above)
                # plus row NUMBERS down the side, in the reserved rlwi band to the
                # left of the patches. Drawn once, against the leftmost strip (#93,
                # Knut). The band sits in [x0 - rlwi, x0].
                if _row_band_px > 0 and p == 0:
                    # Right-align each number so it ends just left of the patches
                    # and grows LEFT into the band — a two-digit number (10–13…)
                    # can't spill over the patches (#93, Knut). For hex patches the
                    # left column's even rows stagger ¼·width LEFT past x0, so clear
                    # that protrusion too, else the hexagons cover the numbers.
                    _gap = max(1, px(1.0))
                    _protrude = (strip_w // 4) if ss_hex else 0
                    _rx = x0 - _protrude - _gap
                    for _j in range(len(col_slots)):
                        _ry = (px(place.y_of(_j)) + px(place.y_of(_j) + place.plen)) // 2
                        _txt = label_patch(_j + 1)
                        _tw = int(draw.textlength(_txt, font=font))
                        draw.text((_rx - _tw, _ry - ind_px // 2), _txt,
                                  font=font, fill=(0, 0, 0))
            for j, gslot in enumerate(col_slots):
                y0 = px(place.y_of(j)) + _stag
                # Derive each row's bottom edge from its true mm position (the
                # way xR does horizontally) instead of adding a fixed rounded
                # height: round(plen)+round(pspa) drifts from round(plen+pspa),
                # which left a 1 px gap between the spacer and the next patch on
                # every other row. Tying the spacer's bottom to the next patch's
                # top tiles them seamlessly (#93).
                yB = px(place.y_of(j) + place.plen) + _stag    # patch bottom edge
                rgb = rgb_by_slot[gslot]
                if ss_hex:
                    draw.polygon(_hexagon_points(x0, y0, xR - x0, yB - y0, j),
                                 fill=rgb)
                else:
                    draw.rectangle([x0, y0, xR - 1, yB - 1], fill=rgb)
                if sp_px > 0 and j + 1 < len(col_slots):
                    y_next = px(place.y_of(j + 1)) + _stag     # next patch top
                    nxt = rgb_by_slot[col_slots[j + 1]]
                    # A per-spacer manual override (keyed by flat geometric index)
                    # wins over the auto/contrast colour.
                    _flat = global_strip * steps + j
                    _ov = spacer_overrides.get(_flat) if spacer_overrides else None
                    _fill = _ov if _ov is not None else contrast.spacer_for_mode(
                        spacer_mode, rgb, nxt, spacer_palette)
                    draw.rectangle([x0, yB, xR - 1, y_next - 1], fill=_fill)
            # Bracket the strip with a leading + trailing spacer (printtarg does
            # this). Fits in space the layout already reserves, so it doesn't
            # change the patch count. Auto-coloured against the paper white on the
            # outer side and the adjacent patch on the inner; not individually
            # recolourable (the override scheme covers the between-patch spacers).
            if edge_spacers and sp_px > 0 and col_slots:
                _white = (255, 255, 255)
                _first = rgb_by_slot[col_slots[0]]
                _last = rgb_by_slot[col_slots[-1]]
                _yl = px(place.y_of(0)) + _stag - sp_px     # leading: above patch 0
                draw.rectangle(
                    [x0, _yl, xR - 1, _yl + sp_px - 1],
                    fill=contrast.spacer_for_mode(spacer_mode, _white, _first,
                                                  spacer_palette))
                _yt = px(place.y_of(len(col_slots) - 1) + place.plen) + _stag  # trailing
                draw.rectangle(
                    [x0, _yt, xR - 1, _yt + sp_px - 1],
                    fill=contrast.spacer_for_mode(spacer_mode, _last, _white,
                                                  spacer_palette))
        # Full-width rule under the whole label row (one continuous line):
        # "segments" splits it into the five accents across the entire width;
        # "black" is a single plain line. ("cycle" is drawn per strip above.)
        if (draw_indicators and underline_mode in ("segments", "black")
                and n_passes > 0):
            _ly = _lbl_top + label_band_h + ul_gap
            _yb = _ly + ul_th - 1
            x_left = px(place.x_of(0))
            x_right = px(place.x_of(n_passes - 1) + place.pwid) - 1
            if underline_mode == "black":
                draw.rectangle([x_left, _ly, x_right, _yb], fill=(0, 0, 0))
            else:                                     # 5 equal segments full-width
                _span = x_right - x_left + 1
                _n = len(ACCENT_RGB)
                for _k in range(_n):
                    _sx0 = x_left + round(_span * _k / _n)
                    _sx1 = x_left + round(_span * (_k + 1) / _n) - 1
                    draw.rectangle([_sx0, _ly, _sx1, _yb], fill=ACCENT_RGB[_k])

        # Left clip-strip content (i1/p3): rendered natively into the reserved
        # lbord band, since the engine knows its exact geometry.
        if clip_content_mode != "off":
            _area = geometry.clip_area_px(geom, paper_h_mm, dpi, paper_w_mm)
            if _area is not None and _area[2] > 0 and _area[3] > 0:
                _ax, _ay, _aw, _ah = _area
                _notes_ctx = dict(_pctx)
                _notes_ctx["count"] = str(layout.total_patches)
                _notes_ctx["strips"] = str(n_passes)
                _clip = render_clip_strip(
                    clip_content_mode, width_px=_aw, height_px=_ah, dpi=dpi,
                    text=_clip_text, font_family=clip_text_font,
                    image_path=clip_image_path, ctx=_notes_ctx,
                    image_rotation=clip_image_rotation,
                    image_scale=clip_image_scale,
                    image_offset_x_mm=clip_image_offset_x_mm,
                    image_offset_y_mm=clip_image_offset_y_mm)
                # On the right edge the band sits on the far side of the sheet, so
                # turn the content 180° to keep it the right way up for the reader
                # (Knut, #93). Left clips are unchanged.
                if getattr(geom, "clip_side", "left") == "right":
                    _clip = _clip.rotate(180, expand=True)
                img.paste(_clip, (_ax, _ay))

        # Bottom-of-sheet text: custom chart text + optional command stamp,
        # drawn in the bottom margin (clear of the patches).
        _btxt = [t for t in (_chart_text, stamp_text) if t]
        if _btxt:
            sfont = _font(px(chart_text_size_mm or 3.2), chart_text_font,
                          chart_text_bold, chart_text_italic)
            line_h = px(4.2)
            yy = H - px(text_edge_mm) - line_h * len(_btxt)
            for ln in _btxt:
                draw.text((px(geom.margin_l), yy), ln, font=sfont, fill=(0, 0, 0))
                yy += line_h
        images.append(img)

    flagged = contrast.low_contrast_passes(rgb_by_slot, steps)
    return RenderResult(images=images, low_contrast_passes=flagged,
                        label_band_bottom_px=_band_bottom)


def export_clip_template(out_base: str | Path, *, width_px: int, height_px: int,
                         width_mm: float, height_mm: float, dpi: int) -> list[Path]:
    """Write blank clip-strip design templates at the exact clip size.

    Produces ``<out_base>.png`` (pixels at *dpi*) and ``<out_base>.pdf`` (sized
    in mm) so a user can design a graphic in another tool and import it back at a
    perfect fit.  A faint border + corner ticks + a dimension caption mark the
    bounds and orientation; they sit on a separate guide layer so the user can
    delete them.  Returns the written paths.
    """
    base = Path(out_base).with_suffix("")
    mm2px = dpi / 25.4
    img = Image.new("RGB", (max(1, width_px), max(1, height_px)), (255, 255, 255))
    d = ImageDraw.Draw(img)
    guide = (200, 200, 200)
    d.rectangle([0, 0, width_px - 1, height_px - 1], outline=guide, width=1)
    tick = max(3, round(3 * mm2px))               # corner crop ticks
    for cx, cy in ((0, 0), (width_px - 1, 0), (0, height_px - 1),
                   (width_px - 1, height_px - 1)):
        d.line([(cx, cy), (cx + (tick if cx == 0 else -tick), cy)], fill=guide, width=2)
        d.line([(cx, cy), (cx, cy + (tick if cy == 0 else -tick))], fill=guide, width=2)
    cap = f"{width_mm:.0f} × {height_mm:.0f} mm @ {dpi} dpi"
    overlay = _vtext(cap, "Inter", width_px, height_px, valign="top")
    img.paste(overlay, (0, 0), overlay)
    out: list[Path] = []
    png = base.with_suffix(".png")
    img.save(str(png), dpi=(dpi, dpi))
    out.append(png)
    pdf = base.with_suffix(".pdf")
    img.save(str(pdf), "PDF", resolution=float(dpi))  # px/dpi → exact physical mm
    out.append(pdf)
    return out


def save_tiffs(images: list[Image.Image], base_path: str | Path, dpi: int = 300,
               *, bit16: bool = False, compression: str = "lzw") -> list[Path]:
    """Write *images* as TIFF(s) in px/cm (ResolutionUnit=3); return paths.

    Single page → ``base.tif``; multiple → ``base_01.tif`` ….  *bit16* writes
    16-bit channels (8-bit values scaled up); *compression* is the tifffile
    codec name ("lzw", "zlib", or "none").
    """
    base = Path(base_path)
    stem = base.with_suffix("")
    res = dpi / 2.54  # pixels per centimetre, matching printtarg
    comp = None if compression in ("none", "", None) else compression
    out: list[Path] = []
    for i, img in enumerate(images):
        arr = np.asarray(img)
        if bit16:
            arr = (arr.astype(np.uint16) * 257)   # 8-bit → 16-bit (×257)
        path = base if len(images) == 1 else stem.parent / f"{stem.name}_{i + 1:02d}.tif"
        tifffile.imwrite(
            str(path), arr, photometric="rgb",
            resolution=(res, res), resolutionunit=3, compression=comp,
        )
        out.append(path)
    return out
