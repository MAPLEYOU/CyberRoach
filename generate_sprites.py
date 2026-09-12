# -*- coding: utf-8 -*-
"""
程序化生成蟑螂精灵图（保证透明背景 + 4 帧行走动画）
产出:
  assets/sprites/roach_f{0..3}.png   96x96 顶视蟑螂(朝右) 4 帧
  assets/sprites/egg.png             蟑螂卵鞘
  assets/sprites/slipper.png         拖鞋(拖鞋模式光标用图)
  assets/sprites/splat.png           拍扁效果
  assets/sprites/icon.png            托盘图标
"""
import math
import os

from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "assets", "sprites")
S = 96

BODY_DARK = (64, 38, 16, 255)
BODY = (94, 58, 27, 255)
BODY_LIGHT = (120, 78, 38, 255)
OUTLINE = (28, 16, 6, 255)
LEG = (44, 26, 10, 255)
ANT = (38, 22, 9, 255)


def _qbez(p0, p1, p2, n=8):
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        pts.append((x, y))
    return pts


def draw_roach(frame: int) -> Image.Image:
    """画一只朝右的顶视蟑螂, frame 0..3 为行走相位."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = 42, 48
    half_w = 12  # 半身宽
    swing = [-5, 5, 5, -5][frame]
    lift = 1 if frame in (0, 3) else 0

    # ---- 腿(先画, 压在身体下面) ----
    for side in (-1, 1):
        phase = (frame + (2 if side > 0 else 0)) % 4
        sw = [-5, 5, 5, -5][phase]
        for i, (ax, fwd) in enumerate([(-16, 13), (-2, 2), (12, -11)]):
            bx, by = cx + ax, cy + side * (half_w - 3)
            dx = fwd + sw
            dy = side * (11 + (3 if phase in (0, 3) else 0))
            kx, ky = bx + dx * 0.45 + side * 3, by + side * 8
            fx, fy = bx + dx + side * 1, by + dy
            d.line([bx, by, kx, ky, fx, fy], fill=LEG, width=3)
            d.ellipse([fx - 2, fy - 2, fx + 2, fy + 2], fill=LEG)

    # ---- 腹部 ----
    d.ellipse([cx - 30, cy - half_w, cx + 10, cy + half_w],
              fill=BODY, outline=OUTLINE, width=2)
    # ---- 前胸(更深) ----
    d.ellipse([cx + 2, cy - half_w + 1, cx + 24, cy + half_w - 1],
              fill=BODY_DARK, outline=OUTLINE, width=2)
    # ---- 头 ----
    d.ellipse([cx + 22, cy - 7, cx + 34, cy + 7],
              fill=(52, 30, 13, 255), outline=OUTLINE, width=2)
    # ---- 翅膀中线 + 高光 ----
    d.line([cx - 27, cy, cx + 6, cy], fill=OUTLINE, width=2)
    d.arc([cx - 27, cy - half_w + 4, cx + 8, cy + half_w - 4],
          200, 340, fill=BODY_LIGHT, width=2)

    # ---- 触须(每帧摆动) ----
    wig = [-4, 0, 4, 0][frame]
    for side in (-1, 1):
        p0 = (cx + 33, cy + side * 4)
        p1 = (cx + 46, cy + side * 12 + wig * side)
        p2 = (cx + 55 + wig, cy + side * 26 + wig * 2 * side)
        pts = _qbez(p0, p1, p2)
        d.line(pts, fill=ANT, width=2)
    return img


def draw_egg() -> Image.Image:
    """蟑螂卵鞘: 深褐色小胶囊带环节."""
    w, h = 44, 26
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 7, w - 4, h - 7], radius=6,
                        fill=(72, 44, 20, 255), outline=OUTLINE, width=2)
    for x in (14, 22, 30):
        d.line([x, 9, x, h - 9], fill=(50, 30, 14, 255), width=2)
    return img


def draw_slipper() -> Image.Image:
    """一只人字拖, 鞋头朝上, 64x64."""
    w = h = 64
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 鞋底
    d.rounded_rectangle([22, 6, 42, 58], radius=10,
                        fill=(205, 66, 52, 255), outline=(110, 30, 22, 255), width=2)
    # 鞋底纹
    for y in (16, 26, 36, 46):
        d.line([26, y, 38, y], fill=(170, 50, 40, 255), width=2)
    # 人字带
    d.line([32, 36, 24, 16], fill=(40, 52, 120, 255), width=4)
    d.line([32, 36, 40, 16], fill=(40, 52, 120, 255), width=4)
    d.ellipse([29, 33, 35, 39], fill=(30, 40, 95, 255))
    return img


def draw_splat() -> Image.Image:
    """拍扁效果: 压扁的身体 + 四溅线条."""
    w = h = 110
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = w // 2, h // 2
    # 溅射
    for k in range(10):
        ang = k / 10 * 2 * math.pi
        r0, r1 = 26, 40 + (k % 3) * 6
        d.line([cx + r0 * math.cos(ang), cy + r0 * math.sin(ang),
                cx + r1 * math.cos(ang), cy + r1 * math.sin(ang)],
               fill=(96, 56, 22, 220), width=3)
    # 压扁身体
    d.ellipse([cx - 34, cy - 10, cx + 34, cy + 10],
              fill=(84, 50, 22, 235), outline=OUTLINE, width=2)
    d.ellipse([cx + 18, cy - 6, cx + 34, cy + 6],
              fill=(60, 34, 14, 235), outline=OUTLINE, width=1)
    # 断腿
    for side in (-1, 1):
        for ax, fwd in [(-20, 14), (-2, 0), (14, -12)]:
            d.line([cx + ax, cy + side * 8,
                    cx + ax + fwd, cy + side * 22],
                   fill=LEG, width=3)
    return img


def make_cursor_frames():
    """生成 .ani 光标用的 4 帧: 48x48, 蟑螂朝右上, 热点在头部."""
    frames = []
    for f in range(4):
        base = draw_roach(f).rotate(-45, expand=True, resample=Image.BICUBIC)
        base = base.resize((int(base.width * 0.62), int(base.height * 0.62)),
                           Image.LANCZOS)
        # 头部在原图 (76,48) -> 相对中心 (34,0) -> rotate -45 后
        hx, hy = 34 * math.cos(math.radians(-45)), 34 * math.sin(math.radians(-45))
        hx *= 0.62
        hy *= 0.62
        canvas = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        px = int(38 - (base.width / 2 + hx))
        py = int(12 - (base.height / 2 + hy))
        canvas.alpha_composite(base, (px, py))
        frames.append((canvas, (38, 12)))
    return frames


def draw_crumb(size: int = 56) -> Image.Image:
    """画一块不规则面包屑(喂食用), 浅麦色带深色麸皮斑点."""
    import random
    rng = random.Random(20260912)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    # 不规则主体
    pts = []
    for i in range(14):
        ang = i / 14 * 2 * math.pi
        r = size * 0.34 * rng.uniform(0.72, 1.08)
        pts.append((cx + math.cos(ang) * r, cy + math.sin(ang) * r * 0.86))
    d.polygon(pts, fill=(224, 186, 132, 255),
              outline=(150, 112, 66, 255))
    # 内部浅色高光
    pts2 = [(cx + (x - cx) * 0.55, cy + (y - cy) * 0.55 - size * 0.04)
            for x, y in pts]
    d.polygon(pts2, fill=(243, 214, 168, 255))
    # 麸皮斑点
    for _ in range(9):
        ang = rng.uniform(0, 2 * math.pi)
        r = size * rng.uniform(0.05, 0.26)
        x, y = cx + math.cos(ang) * r, cy + math.sin(ang) * r
        rr = size * rng.uniform(0.02, 0.045)
        d.ellipse((x - rr, y - rr, x + rr, y + rr),
                  fill=(146, 105, 60, 255))
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in range(4):
        draw_roach(f).save(os.path.join(OUT, f"roach_f{f}.png"))
    draw_egg().save(os.path.join(OUT, "egg.png"))
    draw_slipper().save(os.path.join(OUT, "slipper.png"))
    draw_splat().save(os.path.join(OUT, "splat.png"))
    draw_crumb().save(os.path.join(OUT, "crumb.png"))
    icon = draw_roach(1).resize((64, 64), Image.LANCZOS)
    icon.save(os.path.join(OUT, "icon.png"))
    print("sprites ->", OUT)


if __name__ == "__main__":
    main()
