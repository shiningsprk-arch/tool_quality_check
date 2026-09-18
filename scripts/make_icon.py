# -*- coding: utf-8 -*-
"""Draw icon.png (256x256). External toolbox tools only accept a PNG at the package root.

图形按 RENDER 画好再降采样到 SIZE：下面所有坐标都是按 1024 写的绝对值，直接把 SIZE 改成
256 会把细节挤出画布；超采样后 LANCZOS 缩放既保形又有抗锯齿，也不必逐个换算坐标。

Usage: python scripts/make_icon.py
"""
import os

from PIL import Image, ImageDraw

#: 出图尺寸。工具箱列表里只按小图标显示，256 就够（原先 1024 只是白占体积）。
SIZE = 256
#: 绘制用的超采样尺寸（下面所有坐标都以它为准）。
RENDER = 1024
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'icon.png')


def lerp(a, b, t):
    return int(round(a + (b - a) * t))


def main():
    image = Image.new('RGB', (RENDER, RENDER), (18, 32, 58))
    draw = ImageDraw.Draw(image)

    # Vertical gradient background.
    top = (28, 58, 110)
    bottom = (14, 26, 48)
    for y in range(RENDER):
        t = y / float(RENDER - 1)
        draw.line([(0, y), (RENDER, y)], fill=(
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

    out = image.resize((SIZE, SIZE), Image.LANCZOS) if SIZE != RENDER else image
    # 量化成 256 色调色板：图形本身是平色 + 渐变，量化后没有可见色带，而体积只有 RGB 的
    # 一半不到（13.7KB → 6.2KB）。图标要的就是小。
    out = out.quantize(colors=256, method=Image.MEDIANCUT)
    out.save(OUT, 'PNG', optimize=True)
    print('wrote %s (%dx%d, drawn at %d)' % (os.path.abspath(OUT), SIZE, SIZE, RENDER))


if __name__ == '__main__':
    main()
