# -*- coding: utf-8 -*-
"""Draw icon.png (1024x1024). External toolbox tools only accept a PNG at the package root.

Usage: python scripts/make_icon.py
"""
import os

from PIL import Image, ImageDraw

SIZE = 1024
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'icon.png')


def lerp(a, b, t):
    return int(round(a + (b - a) * t))


def main():
    image = Image.new('RGB', (SIZE, SIZE), (18, 32, 58))
    draw = ImageDraw.Draw(image)

    # Vertical gradient background.
    top = (28, 58, 110)
    bottom = (14, 26, 48)
    for y in range(SIZE):
        t = y / float(SIZE - 1)
        draw.line([(0, y), (SIZE, y)], fill=(
            lerp(top[0], bottom[0], t),
            lerp(top[1], bottom[1], t),
            lerp(top[2], bottom[2], t)))

    # Magnifier lens.
    cx, cy, r = 448, 432, 232
    ring = 30
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(240, 246, 255))
    draw.ellipse([cx - r + ring, cy - r + ring, cx + r - ring, cy + r - ring],
                 fill=(41, 74, 132))

    # Checkmark inside the lens.
    stroke = 46
    points = [(cx - 118, cy + 6), (cx - 34, cy + 96), (cx + 132, cy - 96)]
    draw.line(points, fill=(120, 226, 168), width=stroke, joint='curve')
    for point in points:
        draw.ellipse([point[0] - stroke / 2, point[1] - stroke / 2,
                      point[0] + stroke / 2, point[1] + stroke / 2],
                     fill=(120, 226, 168))

    # Handle.
    draw.line([(cx + 168, cy + 168), (cx + 372, cy + 372)],
              fill=(240, 246, 255), width=74)
    draw.ellipse([cx + 330, cy + 330, cx + 414, cy + 414], fill=(240, 246, 255))

    # Book spine at the bottom right, so the icon reads as "book check" rather than
    # "search".
    bx, by, bw, bh = 596, 636, 300, 268
    draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=28,
                           fill=(246, 178, 74))
    draw.rounded_rectangle([bx + 22, by + 22, bx + bw - 22, by + bh - 22], radius=16,
                           fill=(255, 226, 176))
    for i in range(3):
        y = by + 74 + i * 62
        draw.rounded_rectangle([bx + 60, y, bx + bw - 60, y + 22], radius=11,
                               fill=(198, 126, 32))

    image.save(OUT, 'PNG', optimize=True)
    print('wrote %s (%dx%d)' % (os.path.abspath(OUT), SIZE, SIZE))


if __name__ == '__main__':
    main()
