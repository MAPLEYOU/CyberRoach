# -*- coding: utf-8 -*-
"""
把 AI 生成的写实蟑螂/拖鞋图处理成游戏素材:
  抠图(洪泛填白) -> PCA 摆正朝向 -> 裁剪缩放 -> 4 帧微动画
  并重建 roach.ani / slipper.ani 动态光标
"""
import math
import os
import random
from collections import deque

from PIL import Image, ImageFilter

from make_cursors import _write_ani

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, "assets", "raw")
SPR = os.path.join(BASE, "assets", "sprites")
CUR = os.path.join(BASE, "assets", "cursors")

ROACH_RAW = os.path.join(RAW, "Photorealistic_top_down_macro__2026-09-12T10-21-17.png")
SLIPPER_RAW = os.path.join(RAW, "Photorealistic_top_down_view_o_2026-09-12T10-15-08.png")
EGG_RAW = os.path.join(RAW, "Photorealistic_macro_photograp_2026-09-12T10-28-00.png")


def needs_bg_removal(img):
    if "A" not in img.getbands():
        return True  # RGB 图, 背景是白底
    alpha = img.getchannel("A")
    lo, hi = alpha.getextrema()
    return lo >= 250  # 全不透明 => 背景没抠


def flood_remove_white(img, thresh=238):
    """从四边洪泛清除近白背景, 保护体内白色高光."""
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    q = deque()

    def is_white(p):
        return p[0] >= thresh and p[1] >= thresh and p[2] >= thresh

    for x in range(w):
        for y in (0, h - 1):
            if is_white(px[x, y]):
                q.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if is_white(px[x, y]):
                q.append((x, y))
    seen = set(q)
    while q:
        x, y = q.popleft()
        px[x, y] = (255, 255, 255, 0)
        for nx, ny in ((x+1, y), (x-1, y), (x, y+1), (x, y-1)):
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen:
                if is_white(px[nx, ny]):
                    seen.add((nx, ny))
                    q.append((nx, ny))
    # 边缘收 1px 去白晕(只做一次, 多次会吃掉细腿)
    a = img.getchannel("A").filter(ImageFilter.MinFilter(3))
    img.putalpha(a)
    # 全局去封闭白区(天线死角): 阈值收紧到 252, 避免吃掉腹部高光
    px = img.load()
    for y in range(h):
        for x in range(w):
            p = px[x, y]
            if p[3] > 0 and p[0] >= 252 and p[1] >= 252 and p[2] >= 252:
                px[x, y] = (255, 255, 255, 0)
    return img


def clean_alpha(img):
    """已有透明通道时只做边缘去晕."""
    a = img.getchannel("A")
    if a.getextrema()[0] >= 250:
        return flood_remove_white(img)
    img.putalpha(a.filter(ImageFilter.MinFilter(3)))
    return img


def trim(img):
    bbox = img.getchannel("A").getbbox()
    return img.crop(bbox) if bbox else img


def pca_angle(img):
    """不透明像素的主轴角度(相对水平轴)."""
    w, h = img.size
    mask = img.getchannel("A")
    mpx = mask.load()
    sx = sy = sxx = syy = sxy = n = 0
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            if mpx[x, y] > 128:
                sx += x; sy += y; n += 1
    if n == 0:
        return 0.0
    cx, cy = sx / n, sy / n
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            if mpx[x, y] > 128:
                dx, dy = x - cx, y - cy
                sxx += dx * dx; syy += dy * dy; sxy += dx * dy
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    # 主轴选更接近水平的方向
    if abs(ang) > math.pi / 4:
        ang -= math.copysign(math.pi / 2, ang)
    return ang


def head_is_right(img):
    """用红色复眼定位头: 严格纯红阈值, 排除体表红棕色干扰."""
    w, h = img.size
    px = img.load()
    xs = [x for y in range(0, h, 2) for x in range(0, w, 2)
          if px[x, y][3] > 128 and px[x, y][0] > 160
          and px[x, y][1] < 60 and px[x, y][2] < 60]
    if len(xs) >= 10:
        xs.sort()
        return xs[len(xs) // 2] > w / 2
    return True  # 找不到复眼就默认头在右


def process_roach(head_right=None):
    """head_right: True=头朝右, False=头朝左, None=自动检测(红眼法, 不准就手动指定)"""
    img = Image.open(ROACH_RAW)
    if needs_bg_removal(img):
        img = flood_remove_white(img)
    else:
        img = clean_alpha(img)
    img = trim(img)

    ang = pca_angle(img)
    img = img.rotate(math.degrees(ang), expand=True,
                     resample=Image.BICUBIC)
    img = trim(img)
    flip = not (head_right if head_right is not None
                else head_is_right(img))
    if flip:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)

    # 缩放: 身体大约 170px 宽
    tw = 170
    img = img.resize((tw, int(img.height * tw / img.width)), Image.LANCZOS)
    # 画布统一 200x150, 身体居中
    canvas = Image.new("RGBA", (200, 150), (0, 0, 0, 0))
    canvas.alpha_composite(img, ((200 - img.width) // 2,
                                 (150 - img.height) // 2))

    # 4 帧: 轻微摆动 + 呼吸缩放(照片无法真动腿, 快速爬行时看不出破绽)
    rots = [-1.6, 1.6, -1.0, 1.0]
    scales = [1.00, 0.99, 1.01, 1.00]
    frames = []
    for i in range(4):
        f = canvas.rotate(rots[i], expand=False,
                          resample=Image.BICUBIC)
        s = scales[i]
        f = f.resize((int(200 * s), int(150 * s)), Image.LANCZOS)
        c = Image.new("RGBA", (200, 150), (0, 0, 0, 0))
        c.alpha_composite(f, ((200 - f.width) // 2, (150 - f.height) // 2))
        c.save(os.path.join(SPR, f"roach_real_f{i}.png"))
        frames.append(c)
    return frames


def make_roach_cursor(frames):
    """写实版光标帧: 朝右上, 热点在头部."""
    cur_frames = []
    for f in frames:
        body = f.crop((40, 20, 180, 130))  # 去掉部分触角留白
        body = body.rotate(-45, expand=True, resample=Image.BICUBIC)
        k = 44.0 / max(body.width, body.height)
        body = body.resize((max(1, int(body.width * k)),
                            max(1, int(body.height * k))), Image.LANCZOS)
        c = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        c.alpha_composite(body, (24 - body.width // 2, 24 - body.height // 2))
        cur_frames.append((c, (33, 15)))  # 右上 = 头部方向
    return cur_frames


def process_slipper():
    img = Image.open(SLIPPER_RAW)
    if needs_bg_removal(img):
        img = flood_remove_white(img)
    else:
        clean_alpha(img)
    img = trim(img)
    k = 46.0 / max(img.width, img.height)
    img = img.resize((max(1, int(img.width * k)),
                      max(1, int(img.height * k))), Image.LANCZOS)
    c = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
    c.alpha_composite(img, (24 - img.width // 2, 24 - img.height // 2))
    c.save(os.path.join(SPR, "slipper_real.png"))
    return (c, (24, 30))


def process_egg():
    """写实卵鞘: 抠图 -> 缩到 46px 宽."""
    img = Image.open(EGG_RAW)
    if needs_bg_removal(img):
        img = flood_remove_white(img)
    else:
        clean_alpha(img)
    img = trim(img)
    k = 46.0 / max(img.width, img.height)
    img = img.resize((max(1, int(img.width * k)),
                      max(1, int(img.height * k))), Image.LANCZOS)
    img.save(os.path.join(SPR, "egg_real.png"))
    return img


def main():
    os.makedirs(SPR, exist_ok=True)
    os.makedirs(CUR, exist_ok=True)
    frames = process_roach(head_right=True)  # 美洲大蠊原图头朝右
    _write_ani(os.path.join(CUR, "roach.ani"), make_roach_cursor(frames),
               rate_jiffies=6)
    _write_ani(os.path.join(CUR, "slipper.ani"), [process_slipper()],
               rate_jiffies=36)
    process_egg()
    print("realistic assets done")


if __name__ == "__main__":
    main()
