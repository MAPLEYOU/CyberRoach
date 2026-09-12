# -*- coding: utf-8 -*-
"""
赛博小强 CyberRoach — 桌面蟑螂宠物
  · 鼠标变蟑螂(动态光标) / 拖鞋模式拍蟑螂
  · 蟑螂在桌面爬行、试探、冲刺、怕人(会逃跑)
  · 成虫产卵, 卵孵化成长, 速度由 CPU 温度/负载构成的"虚拟湿度"驱动
托盘菜单: 拖鞋模式 / 蟑螂光标开关 / 还原光标 / 退出
"""
import atexit
import ctypes
import json
import math
import os
import random
import subprocess
import sys
import threading
import time
import wave
import winsound
import winreg

import numpy as np
import psutil
import pygame
import pystray
import win32api
import win32con
import win32gui
from PIL import Image

from generate_sprites import draw_roach
from make_cursors import _write_ani, make_cursor_frames, draw_slipper

BASE = os.path.dirname(os.path.abspath(__file__))
RES = getattr(sys, "_MEIPASS", BASE)  # PyInstaller 打包后的资源目录
SPR = os.path.join(RES, "assets", "sprites")
CUR = os.path.join(RES, "assets", "cursors")
ROACH_ANI = os.path.join(CUR, "roach.ani")
SLIPPER_ANI = os.path.join(CUR, "slipper.ani")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "CyberRoach"

COLORKEY = (255, 0, 255)
MAX_ROACH = 20
OCR_NORMAL = 32512
IMAGE_CURSOR = 2
LR_LOADFROMFILE = 0x10

# 用户配置文件: 打包后放 exe 旁边, 源码运行放源码目录
APP_DIR = (os.path.dirname(sys.executable)
           if getattr(sys, "frozen", False) else BASE)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
DEFAULT_HOTKEY = "ctrl+q"

# ------------------------------------------------------------ 快捷键配置
_HOTKEY_MODS = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
_HOTKEY_VK = {
    "space": 0x20, "tab": 0x09, "capslock": 0x14, "esc": 0x1B,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}


def parse_hotkey(text):
    """'ctrl+q' / 'ctrl+alt+f' / 'ctrl+shift+1' -> (mods, vk, 显示名)。
    修饰键: ctrl alt shift win; 主键: 字母/数字/F1-F24/方向键等。
    非法返回 None。"""
    if not isinstance(text, str):
        return None
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods, seen = 0, set()
    for p in parts[:-1]:
        if p not in _HOTKEY_MODS or p in seen:
            return None
        seen.add(p)
        mods |= _HOTKEY_MODS[p]
    key = parts[-1]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
        name = key.upper()
    elif len(key) in (2, 3) and key[0] == "f" and key[1:].isdigit() \
            and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
        name = key.upper()
    elif key in _HOTKEY_VK:
        vk = _HOTKEY_VK[key]
        name = key.capitalize()
    else:
        return None
    disp = "+".join(p.capitalize() for p in parts[:-1])
    disp2 = "+".join([disp, name])
    return mods, vk, disp2


def load_hotkey():
    """从 config.json 读快捷键, 非法/缺失回退默认值。"""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            text = json.load(f).get("hotkey")
    except Exception:
        text = None
    parsed = parse_hotkey(text) or parse_hotkey(DEFAULT_HOTKEY)
    return parsed


def ensure_config_file():
    """config.json 不存在时写入带注释的默认模板。"""
    if not os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"hotkey": DEFAULT_HOTKEY}, f,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass


user32 = ctypes.windll.user32


def binarize_alpha(surf, thresh=110):
    """alpha 二值化: 色键透明窗口下, 半透明像素会与品红底混成紫边,
    必须强制 只透明 / 不透明 两态. 阈值要低, 保住细腿等纤细结构."""
    a = pygame.surfarray.array_alpha(surf).astype(np.uint8)
    a[a < thresh] = 0
    a[a >= thresh] = 255
    rgb = pygame.surfarray.array3d(surf)
    rgba = np.concatenate([rgb, a.reshape(*a.shape, 1)], axis=2)
    h, w = a.shape[1], a.shape[0]
    s = pygame.image.frombuffer(rgba.transpose(1, 0, 2).tobytes(),
                                (w, h), "RGBA")
    return s.convert_alpha()


_ROT_CACHE = {}


def cached_rotated(img, key, deg, size):
    """旋转+缩放+二值化, 按 (帧, 角度档, 尺寸) 缓存."""
    ck = (key, (int(deg) % 360) // 3, size)
    got = _ROT_CACHE.get(ck)
    if got is not None:
        return got
    rot = pygame.transform.rotate(img, deg)
    if size >= 4 and size != rot.get_width():
        rot = pygame.transform.smoothscale(rot, (size, int(
            size * rot.get_height() / rot.get_width())))
    rot = binarize_alpha(rot)
    if len(_ROT_CACHE) > 600:
        _ROT_CACHE.clear()
    _ROT_CACHE[ck] = rot
    return rot


SLAP_WAV = os.path.join(RES, "assets", "sounds", "slap.wav")


def make_slap_sound():
    """用 numpy 合成一声"啪": 短噪声爆发 + 低频闷响, 快速衰减."""
    if os.path.exists(SLAP_WAV):
        return
    os.makedirs(os.path.dirname(SLAP_WAV), exist_ok=True)
    sr = 22050
    dur = 0.18
    n = int(sr * dur)
    rng = np.random.default_rng(7)
    noise = rng.uniform(-1, 1, n)
    env = np.exp(-np.linspace(0, 30, n))
    thump = (np.sin(2 * np.pi * 90 * np.linspace(0, dur, n))
             * np.exp(-np.linspace(0, 18, n)))
    sig = np.clip((noise * 0.7 + thump * 0.9) * env, -1, 1)
    pcm = (sig * 32767 * 0.8).astype("<i2")
    with wave.open(SLAP_WAV, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _autostart_cmd():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pyw}" "{os.path.join(BASE, "main.py")}"'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
        return True
    except OSError:
        return False


def set_autostart(enable):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
        if enable:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _autostart_cmd())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


def ensure_assets():
    os.makedirs(SPR, exist_ok=True)
    os.makedirs(CUR, exist_ok=True)
    if not os.path.exists(os.path.join(SPR, "roach_f0.png")):
        import generate_sprites
        generate_sprites.main()
    if not os.path.exists(ROACH_ANI):
        _write_ani(ROACH_ANI, make_cursor_frames(), rate_jiffies=6)
    if not os.path.exists(SLIPPER_ANI):
        _write_ani(SLIPPER_ANI, [(draw_slipper(), (32, 46))], rate_jiffies=36)
    make_slap_sound()


# ---------------------------------------------------------------- 光标管理
class CursorManager:
    def __init__(self):
        self._backup = None
        self._applied = False

    def _make_backup(self):
        if self._backup is None:
            h = win32gui.LoadImage(0, OCR_NORMAL, IMAGE_CURSOR, 0, 0,
                                   win32con.LR_SHARED)
            self._backup = ctypes.windll.user32.CopyImage(
                h, IMAGE_CURSOR, 0, 0, win32con.LR_COPYFROMRESOURCE)

    def apply(self, path):
        self._make_backup()
        h = win32gui.LoadImage(0, path, IMAGE_CURSOR, 0, 0, LR_LOADFROMFILE)
        if user32.SetSystemCursor(h, OCR_NORMAL):
            self._applied = True

    def restore(self):
        if self._applied and self._backup:
            user32.SetSystemCursor(self._backup, OCR_NORMAL)
            self._applied = False
        # 兜底: 从系统方案重载默认光标 (W 后缀是实际导出名)
        user32.SystemParametersInfoW(0x57, 0, None, 0x03)


# ---------------------------------------------------------------- 环境感知
USE_REAL_TEMP = False  # 真实温度(需管理员+内核驱动)问题太多, 已禁用;
# 温度用 CPU 负载驱动的模拟值, 本身就是动态变化的, 玩法不受影响


class EnvSensor:
    """CPU 负载 -> 模拟温度 -> 湿度适宜度。温度随负载动态变化,
    湿度由温度+负载合成。"""

    def __init__(self):
        self.cpu = psutil.cpu_percent(interval=None)
        self.temp = 36.0
        self.humidity = 0.5
        self.real = False
        self._last = time.time()
        self._t0 = time.time()

    def _real_temp(self):
        return None  # 已禁用 (USE_REAL_TEMP = False)

    def update(self):
        now = time.time()
        if now - self._last < 2.0:
            return
        self._last = now
        self.cpu = psutil.cpu_percent(interval=None)
        real = self._real_temp() if USE_REAL_TEMP else None
        if real is not None:
            self.real = True
            self.temp += (real - self.temp) * 0.5
        else:
            self.real = False
            target = (33 + self.cpu * 0.38
                      + 3 * math.sin((now - self._t0) / 97))
            self.temp += (target - self.temp) * 0.35
        self.humidity = math.exp(-(((self.temp - 38) / 20) ** 2))
        self.humidity = min(1.0, max(0.05, self.humidity))


# ---------------------------------------------------------------- 窗口
class OverlayWindow:
    """全屏透明置顶窗口。colorkey 像素自动点击穿透,
    蟑螂本体像素可接收点击(点击小强会把它吓跑)。"""

    def __init__(self):
        pygame.init()
        self.w = user32.GetSystemMetrics(0)
        self.h = user32.GetSystemMetrics(1)
        os.environ["SDL_VIDEO_WINDOW_POS"] = "0,0"
        self.screen = pygame.display.set_mode((self.w, self.h),
                                              pygame.NOFRAME)
        pygame.display.set_caption("CyberRoach")
        self.hwnd = pygame.display.get_wm_info()["window"]
        ex = win32gui.GetWindowLong(self.hwnd, win32con.GWL_EXSTYLE)
        ex |= (win32con.WS_EX_LAYERED | win32con.WS_EX_TOOLWINDOW
               | win32con.WS_EX_TOPMOST)
        win32gui.SetWindowLong(self.hwnd, win32con.GWL_EXSTYLE, ex)
        ctypes.windll.user32.SetLayeredWindowAttributes(
            self.hwnd, win32api.RGB(*COLORKEY), 0, 1)  # LWA_COLORKEY
        self._topmost()

    def _topmost(self):
        ctypes.windll.user32.SetWindowPos(
            self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)

    def pump(self):
        self._topmost()  # 每 2s 由主循环调一次, 防止被其他置顶窗口压住


# ---------------------------------------------------------------- 蟑螂
class Roach:
    SPEED = 55.0

    def __init__(self, x, y, scale=1.0, nymph=False):
        self.x, self.y = x, y
        self.heading = random.uniform(0, 2 * math.pi)
        self.scale = scale
        self.nymph = nymph
        self.target_scale = random.uniform(0.9, 1.1) if not nymph else 0.35
        self.state = "rest"
        self.timer = random.uniform(0.5, 2.0)
        self.speed = 0.0
        self.frame = 0
        self.anim_t = 0.0
        self.probes = 0
        self.pregnant = False
        self.gestation = 0.0
        self.satiety = random.uniform(0.15, 0.35)  # 饱食度 0..1
        self._crumb = None  # 正在追/吃的面包屑

    @property
    def fed(self):
        return self.satiety > 0.25

    @property
    def adult(self):
        return not self.nymph and self.scale > 0.8

    def update(self, dt, env, cur_x, cur_y, bounds, crumbs=()):
        # 饱食度缓慢消退
        self.satiety = max(0.0, self.satiety - dt * 0.008)

        # 成长(吃饱 x3)
        boost = 3.0 if self.fed else 1.0
        if self.nymph:
            self.scale += dt * (0.004 + env.humidity * 0.010) * boost
            if self.scale >= 0.85:
                self.nymph = False
                # 羽化成虫: 必须重设目标体型, 否则成年分支的
                # min(target_scale, ...) 会把体型打回出生值 0.35
                self.target_scale = random.uniform(0.9, 1.1)
        else:
            self.scale = min(self.target_scale,
                             self.scale + dt * (0.002 + env.humidity * 0.004)
                             * boost)

        # 觅食: 附近有面包屑且还饿就去吃
        if (self._crumb is not None and self._crumb.gone):
            self._crumb = None
            if self.state in ("seek", "eat"):
                self.state, self.timer = "rest", random.uniform(0.4, 1.0)
        if (self.state not in ("flee", "seek", "eat")
                and self.satiety < 0.95 and self._crumb is None):
            best, bd = None, 480.0
            for c in crumbs:
                d = math.hypot(c.x - self.x, c.y - self.y)
                if d < bd:
                    best, bd = c, d
            if best is not None:
                self._crumb = best
                self.state = "seek"

        # 怕人: 光标靠近就逃跑
        d = math.hypot(cur_x - self.x, cur_y - self.y)
        if d < 90 and self.state != "flee":
            self.state = "flee"
            self.timer = 0.8
            self.heading = math.atan2(self.y - cur_y, self.x - cur_x)
        elif self.state == "flee" and self.timer <= 0 and d > 120:
            self.state = "rest"
            self.timer = random.uniform(0.5, 1.5)

        self.timer -= dt
        if self.state in ("rest", "walk", "dash", "flee") and self.timer <= 0:
            self._next_state()

        if self.state == "seek" and self._crumb is not None:
            # 朝面包屑走, 快到嘴边转成进食
            d = math.hypot(self._crumb.x - self.x, self._crumb.y - self.y)
            if d < 24 + 30 * self.scale:
                self.state = "eat"
            else:
                want = math.atan2(self._crumb.y - self.y,
                                  self._crumb.x - self.x)
                diff = (want - self.heading + math.pi) % (2 * math.pi) - math.pi
                turn = max(-4.0 * dt, min(4.0 * dt, diff))
                self.heading += turn
                sp = self.SPEED * 1.5 * (0.7 if self.nymph else 1.0)
                self.x += math.cos(self.heading) * sp * dt
                self.y += math.sin(self.heading) * sp * dt
                self.anim_t += dt * sp / 18.0
                self.frame = int(self.anim_t) % 4
        elif self.state == "eat" and self._crumb is not None:
            # 进食: 啃食物, 饱食度上涨; 边吃边小幅扭动
            rate = 26.0 * (0.7 if self.nymph else 1.0)
            self._crumb.bite(rate * dt)
            self.satiety = min(1.0, self.satiety + rate * dt / 90.0)
            self.heading += random.uniform(-1.2, 1.2) * dt
            self.anim_t += dt * 5
            self.frame = int(self.anim_t) % 4
            if self.satiety >= 0.98:
                self.state, self.timer = "rest", random.uniform(0.5, 1.0)
        elif self.state in ("walk", "dash", "flee", "probe"):
            sp = {"walk": self.SPEED, "dash": self.SPEED * 2.2,
                  "flee": 150.0, "probe": 30.0}[self.state]
            if self.nymph:
                sp *= 0.7
            self.heading += random.gauss(0, math.radians(8)) * dt * 3
            # 边缘回避
            m = 50
            if (self.x < m or self.x > bounds[0] - m
                    or self.y < m or self.y > bounds[1] - m):
                self.heading = math.atan2(bounds[1] / 2 - self.y,
                                          bounds[0] / 2 - self.x)
                self.heading += random.uniform(-0.5, 0.5)
            self.x = max(10, min(bounds[0] - 10,
                                 self.x + math.cos(self.heading) * sp * dt))
            self.y = max(10, min(bounds[1] - 10,
                                 self.y + math.sin(self.heading) * sp * dt))
            self.anim_t += dt * sp / 18.0
            self.frame = int(self.anim_t) % 4
        else:
            self.anim_t += dt * 2
            self.frame = int(self.anim_t) % 4

    def _next_state(self):
        if self.state == "rest":
            if random.random() < 0.3 and self.probes < 2:
                self.probes += 1
                self.state, self.timer = "probe", random.uniform(0.4, 0.8)
            else:
                self.probes = 0
                self.state, self.timer = "walk", random.uniform(1.0, 3.0)
        elif self.state == "walk" and random.random() < 0.25:
            self.state, self.timer = "dash", random.uniform(0.6, 1.2)
        else:
            self.probes = 0
            self.state, self.timer = "rest", random.uniform(1.0, 3.5)

    def draw(self, surf, frames, egg_img=None):
        f = self.frame
        deg = -math.degrees(self.heading) - 90
        size = max(4, int(200 * self.scale))
        rot = cached_rotated(frames[f], id(frames[f]), deg, size)
        r = rot.get_rect(center=(int(self.x), int(self.y)))
        surf.blit(rot, r)
        if self.pregnant and egg_img is not None:
            tx = self.x - math.cos(self.heading) * 34 * self.scale
            ty = self.y - math.sin(self.heading) * 34 * self.scale
            e = cached_rotated(egg_img, id(egg_img),
                               -math.degrees(self.heading),
                               max(4, int(46 * self.scale)))
            surf.blit(e, e.get_rect(center=(int(tx), int(ty))))

    def hit(self, x, y, r=52):
        return (self.x - x) ** 2 + (self.y - y) ** 2 <= r * r


class Egg:
    def __init__(self, x, y):
        self.x, self.y = x, y
        self.progress = 0.0
        self.wobble = random.uniform(0, 6)

    def update(self, dt, env):
        self.progress += dt * (0.008 + env.humidity * 0.030)
        self.wobble += dt * 2

    @property
    def hatched(self):
        return self.progress >= 1.0

    def draw(self, surf, img):
        rot = pygame.transform.rotate(img, math.sin(self.wobble) * 8)
        surf.blit(rot, rot.get_rect(center=(int(self.x), int(self.y))))

    def hit(self, x, y, r=40):
        return (self.x - x) ** 2 + (self.y - y) ** 2 <= r * r


class Crumb:
    """面包屑: 小强的食物。放桌上 90 秒风干, 被吃到 food 耗尽消失。"""
    LIFE = 90.0
    MAX_ON_SCREEN = 8

    def __init__(self, x, y):
        self.x, self.y = x, y
        self.food = 100.0
        self.age = 0.0
        self.seed = random.uniform(0, 6)

    def bite(self, amount):
        self.food -= amount

    @property
    def gone(self):
        return self.food <= 0 or self.age > self.LIFE

    def update(self, dt):
        self.age += dt

    def draw(self, surf, img):
        s = max(8, int(46 + math.sin(self.age * 2.2 + self.seed) * 2.5
                       * (0.6 + 0.4 * self.food / 100)))
        scaled = pygame.transform.smoothscale(img, (s, int(s * 0.82)))
        scaled = binarize_alpha(scaled)  # 色键窗口: 缩放后必须重新二值化
        surf.blit(scaled, scaled.get_rect(center=(int(self.x), int(self.y))))


class Splat:
    def __init__(self, x, y, scale):
        self.x, self.y, self.life = x, y, 0.9
        self.scale = scale

    def update(self, dt):
        self.life -= dt

    def draw(self, surf, img):
        # 色键窗口不能做整体半透明淡出(会紫边), 直接原样显示到超时
        size = int(110 * self.scale)
        s = pygame.transform.smoothscale(img, (size, int(size * 0.8)))
        surf.blit(s, s.get_rect(center=(int(self.x), int(self.y))))


class FloatText:
    """连杀提示: 上浮文字, 不用半透明(色键窗口会紫边)."""

    def __init__(self, x, y, text):
        self.x, self.y, self.text, self.life = x, y, text, 1.0

    def update(self, dt):
        self.life -= dt
        self.y -= 28 * dt

    def draw(self, surf, font):
        img = font.render(self.text, True, (255, 235, 190))
        outline = font.render(self.text, True, (45, 22, 8))
        px, py = int(self.x), int(self.y)
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            surf.blit(outline, (px - img.get_width() // 2 + dx, py + dy))
        surf.blit(img, (px - img.get_width() // 2, py))


# ---------------------------------------------------------------- 主程序
class App:
    def __init__(self):
        ensure_assets()
        self.win = OverlayWindow()
        self.screen = self.win.screen
        self.w, self.h = self.win.w, self.win.h

        # 优先写实素材(AI 生成照片), 不存在则退回程序绘制
        prefix = "roach_real" if os.path.exists(
            os.path.join(SPR, "roach_real_f0.png")) else "roach"
        self.frames = [binarize_alpha(pygame.image.load(
            os.path.join(SPR, f"{prefix}_f{i}.png")).convert_alpha())
            for i in range(4)]
        egg_path = os.path.join(SPR, "egg_real.png" if os.path.exists(
            os.path.join(SPR, "egg_real.png")) else "egg.png")
        self.egg_img = binarize_alpha(pygame.image.load(
            egg_path).convert_alpha())
        self.splat_img = binarize_alpha(pygame.image.load(
            os.path.join(SPR, "splat.png")).convert_alpha(), thresh=150)
        crumb_path = os.path.join(SPR, "crumb.png")
        if not os.path.exists(crumb_path):
            from generate_sprites import draw_crumb
            draw_crumb().save(crumb_path)
        self.crumb_img = binarize_alpha(pygame.image.load(
            crumb_path).convert_alpha())

        self.env = EnvSensor()
        self.cursor = CursorManager()
        self.slipper_mode = False
        self.roach_cursor_on = True
        self.kills = 0

        self.roaches = [Roach(random.uniform(200, self.w - 200),
                              random.uniform(200, self.h - 200))
                        for _ in range(2)]
        self.eggs = []
        self.splats = []
        self.floats = []
        self.crumbs = []
        self.cjk_font = True
        try:
            # 直接加载字体文件, 绕开 pygame SysFont 注册表枚举(本机有脏数据会崩)
            self.font = pygame.font.Font(
                r"C:\Windows\Fonts\msyh.ttc", 18)
            self.font.set_bold(True)
        except Exception:
            try:
                self.font = pygame.font.Font(
                    r"C:\Windows\Fonts\simhei.ttf", 18)
            except Exception:
                self.font = pygame.font.Font(None, 20)
                self.cjk_font = False
        self.combo = 0
        self.max_combo = 0
        self._last_kill = 0.0

        atexit.register(self.cursor.restore)
        if self.roach_cursor_on and not os.environ.get("CYBERROACH_NOCURSOR"):
            self.cursor.apply(ROACH_ANI)

        ensure_config_file()
        self.hotkey_text = load_hotkey()[2]
        self.tray = self._build_tray()
        threading.Thread(target=self._hotkey_loop, daemon=True).start()
        threading.Thread(target=self.tray.run, daemon=True).start()

        self.clock = pygame.time.Clock()
        self.running = True
        self._last_pump = 0.0

    # ---- 托盘 ----
    def _build_tray(self):
        icon_img = Image.open(os.path.join(SPR, "icon.png"))

        def toggle_slipper(icon, item):
            self.slipper_mode = not self.slipper_mode
            if self.slipper_mode:
                self.cursor.apply(SLIPPER_ANI)
            else:
                self.cursor.apply(ROACH_ANI) if self.roach_cursor_on \
                    else self.cursor.restore()
            icon.update_menu()

        def toggle_cursor(icon, item):
            self.roach_cursor_on = not self.roach_cursor_on
            if self.roach_cursor_on:
                self.cursor.apply(ROACH_ANI)
            else:
                self.cursor.restore()
            icon.update_menu()

        def restore_cursor(icon, item):
            self.cursor.restore()
            self.roach_cursor_on = False
            icon.update_menu()

        def drop_crumb_menu(icon, item):
            self.drop_crumb()

        def edit_hotkey(icon, item):
            """打开 config.json 让用户改快捷键, 保存后热键线程自动重载"""
            ensure_config_file()
            try:
                subprocess.Popen(["notepad.exe", CONFIG_PATH])
            except OSError:
                pass

        def quit_app(icon, item):
            self.running = False
            icon.stop()

        def toggle_autostart(icon, item):
            try:
                set_autostart(not autostart_enabled())
            except OSError:
                pass
            icon.update_menu()

        menu = pystray.Menu(
            pystray.MenuItem(lambda item: "拖鞋模式: 开" if self.slipper_mode
                             else "拖鞋模式: 关", toggle_slipper, default=True),
            pystray.MenuItem(
                lambda item: f"扔面包屑 ({getattr(self, 'hotkey_text', '')}"
                             f" 或点这里)", drop_crumb_menu),
            pystray.MenuItem("自定义快捷键...", edit_hotkey),
            pystray.MenuItem(lambda item: "蟑螂光标: 开" if self.roach_cursor_on
                             else "蟑螂光标: 关", toggle_cursor),
            pystray.MenuItem("还原默认光标", restore_cursor),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: f"开机自启: "
                             f"{'开' if autostart_enabled() else '关'}",
                             toggle_autostart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: self._status_text(), None,
                             enabled=False),
            pystray.MenuItem("退出", quit_app),
        )
        return pystray.Icon("cyber_roach", icon_img, "赛博小强", menu)

    def _status_text(self):
        tag = "真实" if self.env.real else "虚拟"
        return (f"CPU {self.env.temp:.0f}C({tag}) "
                f"| 湿度 {self.env.humidity * 100:.0f}%"
                f" | 小强 {len(self.roaches) + len(self.eggs)} 只"
                f" | 面包屑 {len(self.crumbs)}"
                f" | 拍死 {self.kills} | 最高连杀 {self.max_combo}")

    # ---- 喂食 ----
    def drop_crumb(self):
        while len(self.crumbs) >= Crumb.MAX_ON_SCREEN:
            self.crumbs.pop(0)
        try:
            x, y = win32api.GetCursorPos()
        except win32api.error:
            x, y = self.w // 2, self.h // 2
        x = max(20, min(self.w - 20, x))
        y = max(20, min(self.h - 20, y))
        self.crumbs.append(Crumb(x, y))
        self.floats.append(FloatText(x, y - 34, "♪ 好香"))

    def _hotkey_loop(self):
        """全局快捷键扔面包屑。默认 Ctrl+Q, 可在程序旁 config.json 里
        改 "hotkey" 字段(如 "ctrl+alt+f"), 保存后 1 秒内自动生效。"""
        import ctypes.wintypes
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        WM_HOTKEY, WM_TIMER = 0x0312, 0x0113
        TIMER_ID = 2

        def mtime():
            try:
                return os.path.getmtime(CONFIG_PATH)
            except OSError:
                return None

        def register(mods_vk):
            if u32.RegisterHotKey(None, 1, mods_vk[0], mods_vk[1]):
                return True
            return False  # 被其他程序占用, 托盘菜单仍可用

        cur = load_hotkey()
        self.hotkey_text = cur[2]
        reg = register(cur)
        last_mtime = mtime()
        u32.SetTimer(None, TIMER_ID, 1000, None)
        msg = ctypes.wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.drop_crumb()
            elif msg.message == WM_TIMER and msg.wParam == TIMER_ID:
                m = mtime()
                if m != last_mtime:
                    last_mtime = m
                    new = load_hotkey()
                    if new[:2] != cur[:2]:
                        if reg:
                            u32.UnregisterHotKey(None, 1)
                            reg = False
                        cur = new
                        reg = register(cur)
                        self.hotkey_text = cur[2]
        if reg:
            u32.UnregisterHotKey(None, 1)
        u32.KillTimer(None, TIMER_ID)

    # ---- 拍击 ----
    def _register_kill(self, x, y):
        now = time.time()
        self.combo = self.combo + 1 if now - self._last_kill < 2.0 else 1
        self._last_kill = now
        self.max_combo = max(self.max_combo, self.combo)
        if self.combo >= 2:
            label = (f"连杀 x{self.combo}" if self.cjk_font
                     else f"COMBO x{self.combo}")
            self.floats.append(FloatText(x, y - 30, label))
        try:
            winsound.PlaySound(SLAP_WAV,
                               winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass

    def _swat(self, x, y):
        hit_any = False
        for r in list(self.roaches):
            if r.hit(x, y):
                self.roaches.remove(r)
                self.splats.append(Splat(r.x, r.y, r.scale))
                self.kills += 1
                hit_any = True
                if r.pregnant:
                    # 卵鞘爆开: 2-3 只小若虫四散奔逃
                    for _ in range(random.randint(2, 3)):
                        if len(self.roaches) >= MAX_ROACH:
                            break
                        nb = Roach(r.x + random.uniform(-15, 15),
                                   r.y + random.uniform(-15, 15),
                                   scale=0.3, nymph=True)
                        nb.state, nb.timer = "flee", 1.4
                        nb.heading = random.uniform(0, 2 * math.pi)
                        self.roaches.append(nb)
        for e in list(self.eggs):
            if e.hit(x, y):
                self.eggs.remove(e)
                self.splats.append(Splat(e.x, e.y, 0.4))
                self.kills += 1
                hit_any = True
        if hit_any:
            self._register_kill(x, y)

    # ---- 主循环 ----
    def run(self):
        while self.running:
            dt = min(self.clock.tick(60) / 1000.0, 0.05)
            self.env.update()

            try:
                pos = win32api.GetCursorPos()
                self._last_cursor = pos
            except win32api.error:
                # UAC 安全桌面 / 锁屏 / 桌面切换时拒绝访问, 用上次位置
                pos = getattr(self, "_last_cursor", None) or (self.w // 2,
                                                              self.h // 2)
            cx, cy = pos
            for evt in pygame.event.get():
                if evt.type == pygame.QUIT:
                    self.running = False
                elif evt.type == getattr(pygame, "WINDOWCLOSE", -1):
                    self.running = False  # WM_CLOSE 等系统关闭消息
                elif evt.type == pygame.MOUSEBUTTONDOWN and evt.button == 1:
                    if self.slipper_mode:
                        self._swat(*evt.pos)
                    else:
                        for r in self.roaches:  # 平时点一下吓跑它
                            if r.hit(*evt.pos) and r.state != "flee":
                                r.state = "flee"
                                r.timer = 0.9
                                r.heading = math.atan2(r.y - evt.pos[1],
                                                       r.x - evt.pos[0])
                elif evt.type == pygame.KEYDOWN and evt.key == pygame.K_ESCAPE:
                    self.running = False

            # 生态: 成虫按湿度怀孕 -> 拖着卵鞘跑 -> 孕满落卵
            total = len(self.roaches) + len(self.eggs)
            if total == 0:
                # 灭绝保护: 全拍光后几秒自动从屏幕边缘爬回来一只
                self._extinct_t = getattr(self, "_extinct_t", 0) + dt
                if self._extinct_t > 4:
                    self._extinct_t = 0.0
                    self.roaches.append(Roach(
                        random.choice([20, self.w - 20]),
                        random.uniform(100, self.h - 100)))
            else:
                self._extinct_t = 0.0
            for c in self.crumbs:
                c.update(dt)
            self.crumbs = [c for c in self.crumbs if not c.gone]
            for r in self.roaches:
                r.update(dt, self.env, cx, cy, (self.w, self.h), self.crumbs)
                fed_mul = 2.2 if r.fed else 1.0
                if r.adult and not r.pregnant and total < MAX_ROACH and \
                        random.random() < dt * (0.03 + self.env.humidity
                                                * 0.12) * fed_mul:
                    r.pregnant = True
                    r.gestation = 0.0
                if r.pregnant:
                    r.gestation += dt * (0.05 + self.env.humidity * 0.18) \
                        * (2.0 if r.fed else 1.0)
                    if r.gestation >= 1.0:
                        r.pregnant = False
                        back = r.heading + math.pi
                        ex = max(30, min(self.w - 30,
                                         r.x + math.cos(back) * 34))
                        ey = max(30, min(self.h - 30,
                                         r.y + math.sin(back) * 34))
                        self.eggs.append(Egg(ex, ey))
                        total += 1
            for e in self.eggs:
                e.update(dt, self.env)
            for e in list(self.eggs):
                if e.hatched and len(self.roaches) < MAX_ROACH:
                    self.eggs.remove(e)
                    self.roaches.append(
                        Roach(e.x, e.y, scale=0.3, nymph=True))
            for s in self.splats:
                s.update(dt)
            self.splats = [s for s in self.splats if s.life > 0]
            for f in self.floats:
                f.update(dt)
            self.floats = [f for f in self.floats if f.life > 0]

            # 渲染
            self.screen.fill(COLORKEY)
            for c in self.crumbs:
                c.draw(self.screen, self.crumb_img)
            for e in self.eggs:
                e.draw(self.screen, self.egg_img)
            for r in self.roaches:
                r.draw(self.screen, self.frames, self.egg_img)
            for s in self.splats:
                s.draw(self.screen, self.splat_img)
            for f in self.floats:
                f.draw(self.screen, self.font)
            pygame.display.flip()

            if time.time() - self._last_pump > 2:
                self._last_pump = time.time()
                self.win.pump()
                try:
                    self.tray.title = self._status_text()
                except Exception:
                    pass

        # 退出看门狗: 1秒后无论清理是否卡住都强制退场,
        # 彻底杜绝"窗口没了但进程还在"的任务管理器僵尸
        threading.Timer(1.0, os._exit, args=(0,)).start()
        self.cursor.restore()   # 先还原光标, 避免后续步骤卡住时鼠标仍是蟑螂
        self.tray.stop()
        pygame.quit()
        # 清理完成后强制结束进程: 某些库的非守护线程会卡住正常的
        # 解释器退出流程, 导致窗口没了进程还在(任务管理器僵尸)
        os._exit(0)

def _crash_log_path():
    # 冻结 exe 时 __file__ 在临时解包目录, 日志放 exe 旁边更好找
    base = (os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False) else BASE)
    return os.path.join(base, "crash.log")


def _install_crash_hooks():
    """闪退排障: 未捕获异常 + 原生崩溃(faulthandler) 都落盘 crash.log"""
    try:
        import faulthandler
        faulthandler.enable(open(_crash_log_path(), "a", encoding="utf-8"))
    except Exception:
        pass

    def hook(tp, val, tb):
        import traceback
        try:
            with open(_crash_log_path(), "a", encoding="utf-8") as f:
                f.write("\n==== %s ====\n" % time.strftime(
                    "%Y-%m-%d %H:%M:%S"))
                traceback.print_exception(tp, val, tb, file=f)
        except Exception:
            pass
        sys.__excepthook__(tp, val, tb)

    sys.excepthook = hook


def _acquire_single_instance():
    """Windows 命名互斥量: 重复启动直接退出, 防止多个全屏透明窗口打架。
    注意:
    1. 必须 use_last_error=True, 否则 GetLastError 会被 ctypes
       内部调用冲掉, 锁失效。
    2. 只用 CreateMutexW 一次调用判定, 不做 OpenMutexW 预检——
       两步之间有竞态窗口, 并行启动可能双开。
    3. 句柄必须保存在全局变量里, 进程存活期间锁才持续有效
       (进程退出时内核会自动释放, 无需清理代码)。"""
    import ctypes.wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.wintypes.HANDLE
    k32.CloseHandle = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.wintypes.HANDLE)(("CloseHandle", k32))
    handle = k32.CreateMutexW(None, True, "CyberRoachMutex")
    already = ctypes.get_last_error() == 183  # 183=ERROR_ALREADY_EXISTS
    if already or not handle:
        # 关键: 抢锁失败必须立刻关掉句柄再弹窗, 否则弹窗进程会一直
        # 持有互斥量对象, 把"已存在"状态续命, 后续启动永远失败
        if handle:
            k32.CloseHandle(handle)
        return False
    globals()["_mutex_handle"] = handle  # 防止句柄被回收
    return True


def _visible_roach_pids():
    """枚举所有拥有可见 pygame 窗口(=活着的桌面小强)的进程 PID"""
    import ctypes.wintypes
    u32 = ctypes.windll.user32
    alive = set()
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def cb(h, l):
        pid = ctypes.wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if u32.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(h, buf, 64)
            if "pygame" in buf.value.lower():
                alive.add(pid.value)
        return True
    u32.EnumWindows(CB(cb), 0)
    return alive


def _kill_stale_instances():
    """启动自检: 杀掉历史残留的僵尸进程。
    判定标准: CyberRoach.exe 进程存在, 但整棵进程树里没有任何
    可见的桌面小强窗口 => 窗口已死进程未退 => 僵尸, 直接清掉。
    正在运行的小强(有可见窗口)完全不受影响。"""
    procs = [p for p in psutil.process_iter(["name"])
             if (p.info["name"] or "").lower() == "cyberroach.exe"]
    if not procs:
        return
    # 排除自己这棵进程树: 启动早期还没有窗口, 不排除会把自己当僵尸杀掉
    # (单文件 exe: 自己是子进程, 引导器是父进程, 同名 CyberRoach.exe)
    own = {os.getpid()}
    try:
        par = psutil.Process(os.getpid()).parent()
        # 注意: Process 对象没有 .info 属性(那是 process_iter 的),
        # 必须用 par.name(), 否则这里抛异常导致引导器被误杀
        if par is not None and par.name().lower() == "cyberroach.exe":
            own.add(par.pid)
    except Exception:
        pass
    procs = [p for p in procs if p.pid not in own]
    if not procs:
        return
    alive = _visible_roach_pids()
    # 单文件 exe 是父子两进程, 窗口在子进程上; 父进程算活
    live = set(alive)
    for p in procs:
        try:
            if p.pid in alive:
                continue
            par = p.parent()
            if par is not None and par.pid in alive:
                live.add(p.pid)
            for c in p.children(recursive=True):
                if c.pid in alive:
                    live.add(p.pid)
                    live.add(c.pid)
        except Exception:
            pass
    killed = 0
    for p in procs:
        if p.pid not in live:
            try:
                p.kill()
                killed += 1
            except Exception:
                pass
    if killed:
        time.sleep(1.0)  # 等内核销毁互斥量对象, 避免锁残留


if __name__ == "__main__":
    if "--lhm-worker" in sys.argv:  # 冻结 exe 的 LHM 隔离工作模式
        import lhm_worker
        lhm_worker.main()
    else:
        _kill_stale_instances()  # 先清历史僵尸再抢锁, 残留进程不再挡路
        if _acquire_single_instance():
            _install_crash_hooks()
            App().run()
        else:
            # 真的有一个活着的小强在跑: 弹窗告知而不是静默退出
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "小强已经在桌面上跑啦！\n\n"
                    "看屏幕右下角托盘区的小强图标（可能藏在 ^ 里），\n"
                    "右键图标可以退出它，退出后就能重新启动啦。\n\n"
                    "如果托盘里也没有小强，重启本程序即可自动清理残留进程。",
                    "CyberRoach 赛博小强",
                    0x00000040,  # MB_ICONINFORMATION
                )
            except Exception:
                pass
