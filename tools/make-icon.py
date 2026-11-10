#!/usr/bin/env python3
"""Draws Orbit's icon (packaging/icon/orbit.svg) in the same style as Pocketfader's and Nova's, and renders every
size the desktop, Windows, macOS and Android builds use. Needs librsvg (python3-gi + gir1.2-rsvg-2.0) and Pillow.

    python3 tools/make-icon.py
"""
import io
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging" / "icon"

CX, CY = 247, 277          # centre of the orbit, on the card
RX, RY, ROT = 100, 41, -20  # the orbit's ellipse and tilt


def on_ring(t_deg):
    t, r = math.radians(t_deg), math.radians(ROT)
    x, y = RX * math.cos(t), RY * math.sin(t)
    return CX + x * math.cos(r) - y * math.sin(r), CY + x * math.sin(r) + y * math.cos(r)


def satellite(t, color, r=15):
    x, y = on_ring(t)
    return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 10}" fill="{color}" opacity=".28"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{color}"/>'
            f'<rect x="{x - 7:.1f}" y="{y - r + 5:.1f}" width="14" height="5" rx="2.5" fill="#FFFFFF" opacity=".55"/>')


def svg(tile=True, foreground_only=False):
    a0, a180 = on_ring(0), on_ring(180)
    front = f'M{a0[0]:.1f},{a0[1]:.1f} A{RX},{RY} {ROT} 0 1 {a180[0]:.1f},{a180[1]:.1f}'   # the half in front of the core
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">', '''  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#FFF4F7"/><stop offset=".55" stop-color="#FFD3E0"/><stop offset="1" stop-color="#F59AB6"/>
    </linearGradient>
    <linearGradient id="gloss" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#FFFFFF" stop-opacity=".55"/><stop offset=".45" stop-color="#FFFFFF" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="card" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2C323B"/><stop offset="1" stop-color="#15191F"/>
    </linearGradient>
    <radialGradient id="core" cx=".38" cy=".34" r=".7">
      <stop offset="0" stop-color="#FFF1F5"/><stop offset=".55" stop-color="#FF9AB6"/><stop offset="1" stop-color="#F0668E"/>
    </radialGradient>
    <filter id="sh" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur in="SourceAlpha" stdDeviation="16"/><feOffset dy="12" result="b"/>
      <feFlood flood-color="#6B1A35" flood-opacity=".30"/><feComposite in2="b" operator="in"/>
      <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>''']
    if tile:
        parts.append('  <rect x="24" y="24" width="464" height="464" rx="120" fill="url(#bg)"/>'
                     '<rect x="24" y="24" width="464" height="464" rx="120" fill="url(#gloss)"/>')
    parts.append('  <g filter="url(#sh)"><rect x="118" y="150" width="258" height="252" rx="60" fill="url(#card)"/></g>')
    parts.append(f'  <ellipse cx="{CX}" cy="{CY}" rx="{RX}" ry="{RY}" transform="rotate({ROT} {CX} {CY})" fill="none" '
                 f'stroke="#F0668E" stroke-opacity=".16" stroke-width="22"/>')
    parts.append(f'  <ellipse cx="{CX}" cy="{CY}" rx="{RX}" ry="{RY}" transform="rotate({ROT} {CX} {CY})" fill="none" '
                 f'stroke="#3A414B" stroke-width="9"/>')
    parts.append('  ' + satellite(203, "#D0BCFF") + satellite(338, "#FFC94D"))           # behind the core
    parts.append(f'  <circle cx="{CX}" cy="{CY}" r="54" fill="#FF7A9E" opacity=".22"/>'
                 f'<circle cx="{CX}" cy="{CY}" r="34" fill="url(#core)"/>')
    parts.append(f'  <path d="{front}" fill="none" stroke="#3A414B" stroke-width="9" stroke-linecap="round"/>')
    parts.append('  ' + satellite(100, "#38D9C4", 17))                                    # in front
    parts.append('  <g filter="url(#sh)"><circle cx="374" cy="150" r="62" fill="url(#card)" stroke="#FFF0F4" stroke-width="12"/></g>')
    parts.append('  <g fill="none" stroke="#FF8FB0" stroke-width="13" stroke-linecap="round" stroke-linejoin="round">'
                 '<path d="M374,116V166"/><path d="M352,146L374,168L396,146"/><path d="M350,186H398"/></g>')
    parts.append('</svg>\n')
    return "\n".join(parts)


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


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    full, fg = svg(), svg(tile=False)
    (OUT / "orbit.svg").write_text(full)
    render(full, 512).save(OUT / "orbit-512.png")
    render(full, 1024).save(OUT / "orbit-1024.png")          # PyInstaller makes .ico / .icns from this
    (ROOT / "orbit" / "static" / "icon.svg").write_text(full)
    (ROOT / "android" / "app" / "src" / "main" / "assets" / "icons" / "orbit.svg").write_text(full)   # Orbit's own card
    # Android: a full tile for older Android, an adaptive foreground (108 dp, content inside the 66 dp safe zone)
    res = ROOT / "android" / "app" / "src" / "main" / "res"
    for name, px in (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)):
        d = res / f"mipmap-{name}"
        d.mkdir(parents=True, exist_ok=True)
        render(full, px * 4).resize((px, px), resample=3).save(d / "ic_launcher.png")
        side = round(px * 108 / 48)
        art = render(fg, side * 4).resize((side, side), resample=3)
        # the 512 design's content spans ~110..440; scale it into the safe zone of the 108 dp canvas
        canvas = __import__("PIL.Image", fromlist=["Image"]).new("RGBA", (side, side), (0, 0, 0, 0))
        inner = round(side * 0.80)
        canvas.alpha_composite(art.resize((inner, inner), resample=3), ((side - inner) // 2, (side - inner) // 2))
        canvas.save(d / "ic_launcher_foreground.png")
    print("icon written:", OUT / "orbit.svg")


if __name__ == "__main__":
    main()
