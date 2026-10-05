"""
make_icon.py
------------
Generate assets/receipt_saver.ico — a receipt on an accent-blue rounded square.
Run once:  python make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).with_name("assets")
ICO = ASSETS / "receipt_saver.ico"

BG = (79, 140, 255, 255)      # --accent
PAPER = (247, 249, 254, 255)
INK = (79, 140, 255, 255)
INK_SOFT = (154, 163, 178, 255)


def _render(size: int) -> Image.Image:
    # Supersample for clean edges, then downscale.
    S = size * 8
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # rounded-square background
    d.rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.22), fill=BG)

    # receipt body
    m = S * 0.24
    top = S * 0.16
    bot = S * 0.74
    left, right = m, S - m
    d.rectangle((left, top, right, bot), fill=PAPER)

    # torn bottom edge (triangles)
    teeth = 6
    tw = (right - left) / teeth
    pts = []
    for i in range(teeth + 1):
        x = left + i * tw
        y = bot + (0 if i % 2 == 0 else tw * 0.55)
        pts.append((x, y))
    pts += [(right, bot), (left, bot)]
    d.polygon(pts, fill=PAPER)

    # text lines
    pad = (right - left) * 0.16
    lx0, lx1 = left + pad, right - pad
    rows = [0.30, 0.42, 0.54, 0.66]
    for i, fr in enumerate(rows):
        y = top + (bot - top) * fr
        x1 = lx1 if i not in (0, 3) else lx0 + (lx1 - lx0) * (0.55 if i == 0 else 0.4)
        d.line((lx0, y, x1, y), fill=INK if i == 0 else INK_SOFT,
               width=int(S * (0.028 if i == 0 else 0.018)))

    return img.resize((size, size), Image.LANCZOS if hasattr(Image, "LANCZOS")
                      else Image.Resampling.LANCZOS)


def main():
    ASSETS.mkdir(exist_ok=True)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master = _render(256)
    master.save(ICO, format="ICO", sizes=sizes)
    print(f"Wrote {ICO} ({', '.join(f'{w}px' for w, _ in sizes)})")


if __name__ == "__main__":
    main()
