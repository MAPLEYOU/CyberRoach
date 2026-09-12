# -*- coding: utf-8 -*-
"""
用 ani_file 把 PNG 帧合成为 Windows 动态光标:
  assets/cursors/roach.ani      蟑螂动态光标(腿会动)
  assets/cursors/slipper.ani    拖鞋光标
"""
import os

from PIL import Image

if not hasattr(Image, "ANTIALIAS"):  # Pillow>=10 兼容
    Image.ANTIALIAS = Image.LANCZOS
    Image.BICUBIC = Image.Resampling.BICUBIC

from ani_file import ani_file  # noqa: E402

from generate_sprites import make_cursor_frames, draw_slipper, OUT

BASE = os.path.dirname(os.path.abspath(__file__))
CUR = os.path.join(BASE, "assets", "cursors")


def _write_ani(path, frames_with_hotspot, rate_jiffies=8):
    tmp = os.path.join(CUR, "_tmp")
    os.makedirs(tmp, exist_ok=True)
    paths = []
    for i, (img, _hot) in enumerate(frames_with_hotspot):
        p = os.path.join(tmp, f"f{i}.png")
        img.save(p)
        paths.append(p)
    f = ani_file.open(path, "w")
    f.setframespath(paths, xy=frames_with_hotspot[0][1])
    f.setrate([rate_jiffies] * len(paths))
    try:
        f.close()
    except TypeError:
        f.close
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass
    os.rmdir(tmp)


def main():
    os.makedirs(CUR, exist_ok=True)
    frames = make_cursor_frames()
    _write_ani(os.path.join(CUR, "roach.ani"), frames, rate_jiffies=6)
    _write_ani(os.path.join(CUR, "slipper.ani"),
               [(draw_slipper(), (32, 46))], rate_jiffies=36)
    print("cursors ->", CUR)


if __name__ == "__main__":
    main()
