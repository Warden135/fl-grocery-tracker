"""Draw the app icons (a yellow shelf tag with a down arrow) with Pillow.

Run from the project folder:  python -m scripts.make_icons
"""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "icons")
BG = (15, 85, 96)        # #0F5560
TAG = (255, 206, 46)     # #FFCE2E
INK = (42, 32, 0)        # #2A2000


def draw(size, safe=0.62):
    """safe = fraction of the icon the tag may use (maskable icons need a wide margin)."""
    s = size * 4                                   # draw large, then shrink for smooth edges
    img = Image.new("RGB", (s, s), BG)
    d = ImageDraw.Draw(img)
    w = s * safe
    h = w * 0.82
    x0, y0 = (s - w) / 2, (s - h) / 2
    cut = w * 0.22
    d.polygon([(x0, y0), (x0 + w - cut, y0), (x0 + w, y0 + cut), (x0 + w, y0 + h), (x0, y0 + h)], fill=TAG)
    # punch hole
    r = w * 0.045
    d.ellipse([x0 + w * 0.08, y0 + h * 0.1, x0 + w * 0.08 + 2 * r, y0 + h * 0.1 + 2 * r], fill=BG)
    # down arrow = lower prices
    cx, top, bottom = x0 + w * 0.5, y0 + h * 0.24, y0 + h * 0.82
    t = w * 0.075
    d.rectangle([cx - t, top, cx + t, bottom - w * 0.16], fill=INK)
    d.polygon([(cx - w * 0.2, bottom - w * 0.22), (cx + w * 0.2, bottom - w * 0.22), (cx, bottom)], fill=INK)
    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    draw(192).save(os.path.join(OUT, "icon-192.png"))
    draw(512).save(os.path.join(OUT, "icon-512.png"))
    draw(512, safe=0.5).save(os.path.join(OUT, "icon-maskable-512.png"))
    draw(180).save(os.path.join(OUT, "apple-touch-icon.png"))
    print("Wrote icons to", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
