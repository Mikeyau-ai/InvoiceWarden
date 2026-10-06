"""
Generate InvoiceWarden's icon and artwork from one vector drawing.

Design (Mikey, 2026-10-06): the same warden's shield as RamWarden, guarding a tilted invoice
(ticked lines and a "$" total box), in the Sixth Day Studios teal (#2CC4A8) on a dark rounded
tile. Flat for the icon; the glow version is only for marketing (website, Store banner).

Three levels of detail, because fine detail turns to mush at taskbar size:
  full   (64 px and up): "INVOICE" heading, three ticked lines, "Total" and the $ box
  medium (40-48 px):     the page, three ticked lines and the $ box, no words
  small  (32 px and below): the page outline and one bold tick

Writes:
  assets/icon.ico         16-256 px (exe, window and taskbar icon)
  assets/logo.png         40 px mark without the tile
  assets/icon_preview.png 256 px tile
  brand/                  icon-1024, small sizes for checking, wordmark (flat + glow), Store hero

Run: python make_icon.py      (needs: python -m pip install resvg-py pillow; Montserrat installed)
"""
import io
import pathlib

import resvg_py
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
TEAL, BG, BG2 = "#2CC4A8", "#141414", "#24282b"
FONTS = [r"C:\Windows\Fonts\Montserrat-Bold.otf"]

# The shield, on a 512 grid (identical to RamWarden's, so the two read as a family).
SHIELD = ("M256 46 C306 72 370 86 428 86 L428 238 C428 356 356 436 256 482 "
          "C156 436 84 356 84 238 L84 86 C142 86 206 72 256 46 Z")

# The page with its folded top-right corner, centred on (0, 0) before tilting.
PAGE = "M-92 -122 L50 -122 L92 -80 L92 122 L-92 122 Z"
FOLD = "M50 -122 L50 -80 L92 -80"


def page(level):
    """The tilted invoice, drawn in teal lines. level: 'full', 'medium' or 'small'."""
    stroke = {"full": 14, "medium": 18, "small": 26}[level]
    parts = [f'<path d="{PAGE}" fill="none" stroke="{TEAL}" stroke-width="{stroke}" '
             f'stroke-linejoin="round"/>',
             f'<path d="{FOLD}" fill="none" stroke="{TEAL}" stroke-width="{stroke}" '
             f'stroke-linejoin="round"/>']
    if level == "small":
        parts.append(f'<path d="M-46 4 L-14 36 L48 -34" fill="none" stroke="{TEAL}" '
                     f'stroke-width="30" stroke-linecap="round" stroke-linejoin="round"/>')
    else:
        w = 11 if level == "full" else 15
        rows = (-50, -12, 26) if level == "full" else (-58, -14, 30)
        for y in rows:
            parts.append(f'<path d="M-68 {y} l9 9 l17 -19" fill="none" stroke="{TEAL}" '
                         f'stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round"/>'
                         f'<path d="M-28 {y} L62 {y}" stroke="{TEAL}" stroke-width="{w}" '
                         f'stroke-linecap="round"/>')
        box_y = 62 if level == "full" else 66
        parts.append(f'<rect x="22" y="{box_y}" width="48" height="42" rx="4" fill="none" '
                     f'stroke="{TEAL}" stroke-width="{w - 2}"/>'
                     f'<text x="46" y="{box_y + 33}" font-family="Montserrat" font-weight="700" '
                     f'font-size="32" fill="{TEAL}" text-anchor="middle">$</text>')
        if level == "full":
            parts.append(f'<text x="-72" y="-84" font-family="Montserrat" font-weight="700" '
                         f'font-size="24" fill="{TEAL}">INVOICE</text>'
                         f'<text x="-70" y="98" font-family="Montserrat" font-weight="700" '
                         f'font-size="26" fill="{TEAL}">Total</text>')
    return f'<g transform="translate(256 262) rotate(-12)">{"".join(parts)}</g>'


def mark(level="full"):
    """The shield and invoice (no tile)."""
    stroke = {"full": 34, "medium": 40, "small": 50}[level]
    return (f'<path d="{SHIELD}" fill="none" stroke="{TEAL}" stroke-width="{stroke}" '
            f'stroke-linejoin="round"/>' + page(level))


def tile(level="full"):
    """The app icon: the mark on a dark rounded tile."""
    return (f'<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BG2}"/>'
            f'<stop offset="1" stop-color="{BG}"/></linearGradient></defs>'
            f'<rect width="512" height="512" rx="112" fill="url(#bg)"/>'
            f'<g transform="translate(256 256) scale(0.84) translate(-256 -256)">{mark(level)}</g>')


GLOW = ('<defs><filter id="g" x="-20%" y="-20%" width="140%" height="140%">'
        '<feGaussianBlur stdDeviation="12" result="b"/><feMerge><feMergeNode in="b"/>'
        '<feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>')


def wordmark(glow=False):
    """The shield above stacked "INVOICE / WARDEN" lettering, flat or glowing (600 x 760 units)."""
    text = (f'<g fill="{TEAL}" font-family="Montserrat" font-weight="700" text-anchor="middle">'
            f'<text x="300" y="636" font-size="112" letter-spacing="4">INVOICE</text>'
            f'<text x="300" y="740" font-size="96" letter-spacing="6">WARDEN</text></g>')
    body = f'<g transform="translate(44 0)">{mark("full")}</g>{text}'
    return f'{GLOW}<g filter="url(#g)">{body}</g>' if glow else body


def render(body, w, h=None, view=(0, 0, 512, 512)):
    """An SVG body as a PIL image."""
    h = h or w
    doc = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{" ".join(map(str, view))}" '
           f'width="{w}" height="{h}">{body}</svg>')
    data = resvg_py.svg_to_bytes(svg_string=doc, width=w, height=h, font_files=FONTS)
    return Image.open(io.BytesIO(bytes(data))).convert("RGBA")


def level_for(size):
    """Which drawing to use at a given pixel size."""
    return "small" if size <= 32 else ("medium" if size <= 48 else "full")


def main():
    """Write the icon set and the brand artwork."""
    assets = HERE / "assets"
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = {s: render(tile(level_for(s)), s) for s in sizes}
    # Each size drawn on its own (not downscaled), so the small ones keep their simpler drawing.
    frames[256].save(assets / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes],
                     append_images=[frames[s] for s in sizes if s != 256])
    render(mark("medium"), 40).save(assets / "logo.png")
    frames[256].save(assets / "icon_preview.png")

    brand = HERE / "brand"
    brand.mkdir(exist_ok=True)
    for s in (16, 24, 32, 48):                              # the small drawings, for checking by eye
        frames[s].save(brand / f"icon-{s}.png")
    render(tile("full"), 1024).save(brand / "icon-1024.png")
    render(wordmark(False), 600, 760, (0, 0, 600, 760)).save(brand / "wordmark.png")
    render(wordmark(True), 600, 760, (0, 0, 600, 760)).save(brand / "wordmark-glow.png")
    # Store hero / banner art (16:9): the glowing wordmark on dark.
    banner = (f'<rect width="1920" height="1080" fill="{BG}"/>'
              f'<g transform="translate(645 110) scale(1.1)">{wordmark(True)}</g>')
    render(banner, 1920, 1080, (0, 0, 1920, 1080)).save(brand / "store-hero-1920x1080.png")
    print("assets/icon.ico, logo.png, icon_preview.png and brand/ saved")


if __name__ == "__main__":
    main()
