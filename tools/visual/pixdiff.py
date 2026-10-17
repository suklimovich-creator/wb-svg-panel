# -*- coding: utf-8 -*-
"""
Попиксельное сравнение пар name.a.png / name.b.png из tools/visual/shots.js.

    python3 tools/visual/pixdiff.py <папка> [пропустить_сверху_px]

Печатает пары, где есть разница, и прямоугольник разницы. Часы в шапке
меняются от снимка к снимку - их полосу можно пропустить вторым
параметром. Нужен Pillow.
"""
import glob
import os
import sys

from PIL import Image, ImageChops

d = sys.argv[1]
top = int(sys.argv[2]) if len(sys.argv) > 2 else 0
bad = []
for fa in sorted(glob.glob(os.path.join(d, "*.a.png"))):
    fb = fa[:-6] + ".b.png"
    a = Image.open(fa).convert("RGB")
    b = Image.open(fb).convert("RGB")
    name = os.path.basename(fa)[:-6]
    if a.size != b.size:
        bad.append((name, "размер", a.size, b.size))
        continue
    t = 0 if name.startswith("pad-") else top
    box = ImageChops.difference(a.crop((0, t, a.width, a.height)),
                                b.crop((0, t, b.width, b.height))).getbbox()
    if box:
        bad.append((name, box))
print("различий:", len(bad))
for x in bad:
    print("  ", x)
sys.exit(1 if bad else 0)
