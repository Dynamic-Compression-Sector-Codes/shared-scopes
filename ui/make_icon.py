"""
Generate scope_icon.ico — four staggered falling-edge waveforms,
one per channel in standard Tektronix colors.
Rendered at 512x512 with glow, then downsampled with LANCZOS for all .ico sizes.
Run once to regenerate the .ico.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

_CH_COLORS = [
    (255, 255,   0),   # CH1 yellow
    (  0, 188, 212),   # CH2 cyan
    (255,  64, 129),   # CH3 pink
    (105, 240, 174),   # CH4 green
]
_BG       = (13, 13, 26)
_RENDER   = 512
_CORNER_R = 0.14   # fraction of size for corner radius


def _draw_high_res(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (*_BG, 255))

    m    = int(size * 0.04)          # tight margin — waveforms fill the square
    x0   = m
    x1   = size - m
    span = x1 - x0

    n     = len(_CH_COLORS)
    # spread channels from 12% to 88% of height so they nearly touch edges
    ch_ys = [int(size * (0.12 + 0.76 * i / (n - 1))) for i in range(n)]
    amp   = int(size * 0.095)        # taller waveforms
    lw    = max(2, int(size * 0.048))
    ew    = max(2, lw * 2)

    fall_frac = [0.18, 0.37, 0.58, 0.77]

    # --- glow pass (blurred, wide, semi-transparent) ---
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd   = ImageDraw.Draw(glow)
    glw  = lw * 4
    for col, y, ff in zip(_CH_COLORS, ch_ys, fall_frac):
        fx  = int(x0 + span * ff)
        hi  = y - amp
        lo  = y + amp
        gc  = (*col, 55)
        gd.line([(x0, hi), (fx,      hi)], fill=gc, width=glw)
        gd.line([(fx, hi), (fx + ew, lo)], fill=gc, width=glw)
        gd.line([(fx + ew, lo), (x1, lo)], fill=gc, width=glw)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=lw * 2.2))
    img  = Image.alpha_composite(img, glow)

    # --- secondary inner glow (tighter, brighter) ---
    glow2 = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd2   = ImageDraw.Draw(glow2)
    glw2  = lw * 2
    for col, y, ff in zip(_CH_COLORS, ch_ys, fall_frac):
        fx  = int(x0 + span * ff)
        hi  = y - amp
        lo  = y + amp
        gc2 = (*col, 90)
        gd2.line([(x0, hi), (fx,      hi)], fill=gc2, width=glw2)
        gd2.line([(fx, hi), (fx + ew, lo)], fill=gc2, width=glw2)
        gd2.line([(fx + ew, lo), (x1, lo)], fill=gc2, width=glw2)
    glow2 = glow2.filter(ImageFilter.GaussianBlur(radius=lw * 0.8))
    img   = Image.alpha_composite(img, glow2)

    # --- sharp line pass ---
    d = ImageDraw.Draw(img)
    for col, y, ff in zip(_CH_COLORS, ch_ys, fall_frac):
        fx = int(x0 + span * ff)
        hi = y - amp
        lo = y + amp
        d.line([(x0, hi), (fx,      hi)], fill=col, width=lw)
        d.line([(fx, hi), (fx + ew, lo)], fill=col, width=lw)
        d.line([(fx + ew, lo), (x1, lo)], fill=col, width=lw)

    return img


def _round_corners(img: Image.Image, frac: float) -> Image.Image:
    r = int(min(img.size) * frac)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, img.width - 1, img.height - 1], radius=r, fill=255
    )
    img.putalpha(mask)
    return img


if __name__ == "__main__":
    here   = Path(__file__).parent
    out    = here / "scope_icon.ico"
    sizes  = [16, 24, 32, 48, 64, 128, 256]
    render = _draw_high_res(_RENDER)
    render = _round_corners(render, _CORNER_R)

    # preview PNG at full render size for quick visual check
    render.save(str(here / "scope_icon_preview.png"))

    imgs = [render.resize((s, s), Image.LANCZOS) for s in sizes]
    imgs[0].save(
        str(out), format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=imgs[1:],
    )
    print(f"Saved {out}  ({len(sizes)} sizes: {sizes})")
    print(f"Preview PNG: {here / 'scope_icon_preview.png'}")
