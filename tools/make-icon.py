#!/usr/bin/env python3
"""Draws Orbit's icon (packaging/icon/orbit.svg) on the same grid as Nova's, and renders every size the desktop,
Windows, macOS and Android builds use. Needs librsvg (python3-gi + gir1.2-rsvg-2.0) and Pillow.

    python3 tools/make-icon.py

The grid is Android's adaptive icon: a 108 x 108 canvas whose middle 72 x 72 (18..90) is the visible tile. As in Nova's
icon, the dark card is 42 x 42 (30..72, 36..78), the badge (r 11.2) sits on its top-right corner at (70, 38), and the
whole picture is scaled by 0.93 so that the card's centre lands on the tile's centre. The desktop icon is that tile
cropped to its edges (viewBox 18 18 72 72, no margin), the same as Nova's.
"""
import io
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging" / "icon"

CARD = "#1D1B20"                     # the card and the badge's rim (Nova's)
DETAIL = "#49454F"                   # the orbit's line (Nova's detail grey)
BADGE = "#5E1133"                    # the badge's disc: Orbit's rose, deep
ARROW = "#FFD9E2"                    # the badge's arrow: Orbit's rose, light
TILE = ("#FFE3EB", "#F5A6BF")        # the tile, light to deep (Nova: #EADDFF → #CBB6F7)

CX, CY = 51, 57                      # the card's centre: the orbit is drawn around it
RX, RY, ROT = 15.6, 6.3, -20         # the orbit's ellipse and tilt
PLACE = "translate(54 54) scale(0.93) translate(-51 -57)"   # Nova's: the card's centre onto the tile's


def on_ring(t_deg):
    t, r = math.radians(t_deg), math.radians(ROT)
    x, y = RX * math.cos(t), RY * math.sin(t)
    return CX + x * math.cos(r) - y * math.sin(r), CY + x * math.sin(r) + y * math.cos(r)


def satellite(t, color, r=2.35):
    x, y = on_ring(t)
    return (f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r + 1.6:.2f}" fill="{color}" opacity=".28"/>'
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="{color}"/>'
            f'<rect x="{x - 1.1:.2f}" y="{y - r + 0.75:.2f}" width="2.2" height="0.8" rx="0.4" fill="#FFFFFF" opacity=".55"/>')


DEFS = f'''<defs>
  <linearGradient id="tile" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{TILE[0]}"/><stop offset="1" stop-color="{TILE[1]}"/></linearGradient>
  <radialGradient id="light" cx="0.28" cy="0.22" r="0.65"><stop offset="0" stop-color="#fff" stop-opacity=".45"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
  <radialGradient id="core" cx=".38" cy=".34" r=".7"><stop offset="0" stop-color="#FFF1F5"/><stop offset=".55" stop-color="#FF9AB6"/><stop offset="1" stop-color="#F0668E"/></radialGradient>
</defs>'''

CARD_PATH = "M40,36H62A10,10 0 0 1 72,46V68A10,10 0 0 1 62,78H40A10,10 0 0 1 30,68V46A10,10 0 0 1 40,36Z"   # Nova's card
ARROW_PATHS = '<path d="M70,32.9V40.3"/><path d="M66.8,37.3L70,40.5L73.2,37.3"/><path d="M66.4,43.4H73.6"/>'
SATELLITES = ((205, "#D0BCFF", 2.35), (330, "#FFC94D", 2.35), (100, "#38D9C4", 2.7))   # two behind the core, one in front


def picture():
    """The card with the orbit, and the badge: drawn around the card's centre, before PLACE moves it."""
    a0, a180 = on_ring(0), on_ring(180)
    front = f'M{a0[0]:.2f},{a0[1]:.2f} A{RX},{RY} {ROT} 0 1 {a180[0]:.2f},{a180[1]:.2f}'   # the half in front of the core
    ring = f'cx="{CX}" cy="{CY}" rx="{RX}" ry="{RY}" transform="rotate({ROT} {CX} {CY})" fill="none"'
    behind = "".join(satellite(t, c, r) for t, c, r in SATELLITES[:2])
    return "".join([
        f'<path fill="{CARD}" d="{CARD_PATH}"/>',
        f'<ellipse {ring} stroke="#F0668E" stroke-opacity=".18" stroke-width="3.4"/>',
        f'<ellipse {ring} stroke="{DETAIL}" stroke-width="1.45"/>',
        behind,
        f'<circle cx="{CX}" cy="{CY}" r="8.6" fill="#FF7A9E" opacity=".22"/><circle cx="{CX}" cy="{CY}" r="5.5" fill="url(#core)"/>',
        f'<path d="{front}" fill="none" stroke="{DETAIL}" stroke-width="1.45" stroke-linecap="round"/>',
        satellite(*SATELLITES[2]),
        f'<circle cx="70" cy="38" r="11.2" fill="{CARD}"/><circle cx="70" cy="38" r="9.2" fill="{BADGE}"/>',
        f'<g fill="none" stroke="{ARROW}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">{ARROW_PATHS}</g>',
    ])


def tile_svg():
    """The desktop icon: the tile edge to edge, like Nova's."""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="18 18 72 72">' + DEFS +
            '<rect x="18" y="18" width="72" height="72" rx="20" fill="url(#tile)"/>'
            '<rect x="18" y="18" width="72" height="72" rx="20" fill="url(#light)"/>'
            f'<g transform="{PLACE}">{picture()}</g></svg>\n')


def foreground_svg():
    """Android's adaptive foreground: the picture on the whole 108 canvas, no tile."""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108">' + DEFS +
            f'<g transform="{PLACE}">{picture()}</g></svg>\n')


def monochrome_svg():
    """Android 13 themed icons: one shape, tinted by the system. The orbit and the badge's disc are cut out of it."""
    cut = "".join([
        f'<ellipse cx="{CX}" cy="{CY}" rx="{RX}" ry="{RY}" transform="rotate({ROT} {CX} {CY})" fill="none" stroke="#000" stroke-width="1.6"/>',
        f'<circle cx="{CX}" cy="{CY}" r="5.5" fill="#000"/>',
        *[f'<circle cx="{on_ring(t)[0]:.2f}" cy="{on_ring(t)[1]:.2f}" r="{r}" fill="#000"/>' for t, _, r in SATELLITES],
        '<circle cx="70" cy="38" r="9.2" fill="#000"/>',
    ])
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108"><defs><mask id="m" maskUnits="userSpaceOnUse" x="0" y="0" width="108" height="108">'
            f'<path fill="#fff" d="{CARD_PATH}"/><circle cx="70" cy="38" r="11.2" fill="#fff"/>{cut}</mask></defs>'
            f'<g transform="{PLACE}"><rect x="0" y="0" width="108" height="108" fill="#fff" mask="url(#m)"/>'
            f'<g fill="none" stroke="#fff" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">{ARROW_PATHS}</g></g></svg>\n')


def render(svg_text, size):
    import gi
    gi.require_version("Rsvg", "2.0")
    from gi.repository import Rsvg
    import cairo  # noqa: F401  (pycairo, which Rsvg draws into)
    handle = Rsvg.Handle.new_from_data(svg_text.encode())
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)
    vp = Rsvg.Rectangle()
    vp.x, vp.y, vp.width, vp.height = 0, 0, size, size
    handle.render_document(ctx, vp)
    buf = io.BytesIO()
    surface.write_to_png(buf)
    from PIL import Image
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGBA")


def sharp(svg_text, size):
    """Small sizes come out crisper drawn large and scaled down."""
    return render(svg_text, size * 4).resize((size, size), resample=3)


def main():
    from PIL import Image
    OUT.mkdir(parents=True, exist_ok=True)
    full = tile_svg()
    (OUT / "orbit.svg").write_text(full)
    render(full, 512).save(OUT / "orbit-512.png")
    render(full, 1024).save(OUT / "orbit-1024.png")          # Windows: PyInstaller makes the .ico from this
    # macOS draws app icons on Apple's grid: the tile is 824 of 1024, centred, so Orbit is the same size as other apps
    mac = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    mac.alpha_composite(render(full, 824), (100, 100))
    mac.save(OUT / "orbit-mac-1024.png")
    (ROOT / "orbit" / "static" / "icon.svg").write_text(full)
    (ROOT / "android" / "app" / "src" / "main" / "assets" / "icons" / "orbit.svg").write_text(full)   # Orbit's own card
    # Android: the tile for Android 7 and older, and the adaptive icon's foreground and themed (monochrome) layers on
    # the 108 dp canvas (the background is res/drawable/ic_launcher_background.xml)
    res = ROOT / "android" / "app" / "src" / "main" / "res"
    fg, mono = foreground_svg(), monochrome_svg()
    for name, px in (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)):
        d = res / f"mipmap-{name}"
        d.mkdir(parents=True, exist_ok=True)
        sharp(full, px).save(d / "ic_launcher.png")
        side = round(px * 108 / 48)
        sharp(fg, side).save(d / "ic_launcher_foreground.png")
        sharp(mono, side).save(d / "ic_launcher_monochrome.png")
    print("icon written:", OUT / "orbit.svg")


if __name__ == "__main__":
    main()
