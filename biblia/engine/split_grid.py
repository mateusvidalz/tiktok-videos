#!/usr/bin/env python3
"""Recorta uma grade 2x2 (storyboard) em 4 quadros. Uso: split_grid.py grade.png prefixo"""
import sys
import numpy as np
from PIL import Image

im = Image.open(sys.argv[1]).convert("RGB")
pre = sys.argv[2]
a = np.asarray(im).astype(np.float32)
H, W, _ = a.shape
cream = np.array([248, 235, 212], np.float32)


def gutter(axis):
    # perfil de "creme liso" ao longo do eixo, procurando perto do meio
    d = np.abs(a - cream).mean(axis=2)
    prof = d.mean(axis=1 - axis) + a.std(axis=2).mean(axis=1 - axis) * 0  # distancia media ao creme
    n = prof.shape[0]
    lo, hi = int(n * 0.40), int(n * 0.60)
    seg = prof[lo:hi]
    thr = seg.min() + 6
    idx = np.where(seg <= thr)[0] + lo
    return idx.min(), idx.max()


x0, x1 = gutter(1)  # colunas
y0, y1 = gutter(0)  # linhas
m = 6  # margem extra para tirar resto de calha
boxes = [(0, 0, x0 - m, y0 - m), (x1 + m, 0, W, y0 - m), (0, y1 + m, x0 - m, H), (x1 + m, y1 + m, W, H)]
for i, b in enumerate(boxes):
    p = im.crop(b)
    # aparar bordas externas creme
    arr = np.asarray(p).astype(np.float32)
    d = np.abs(arr - cream).mean(axis=2)
    rows = np.where(d.mean(axis=1) > 14)[0]
    cols = np.where(d.mean(axis=0) > 14)[0]
    if len(rows) and len(cols):
        p = p.crop((cols[0] + 2, rows[0] + 2, cols[-1] - 1, rows[-1] - 1))
    p.save(f"{pre}{i + 1}.png")
    print(f"{pre}{i + 1}.png", p.size)
print("gutters x", x0, x1, "y", y0, y1)
