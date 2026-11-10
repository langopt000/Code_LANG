# -*- coding: utf-8 -*-
"""把控制台挂成一个置顶、可调透明度、可点击穿透的无边框浮窗。

做法：用 Edge / Chrome 的 --app 模式开一个无边框窗口，再通过 Win32 API
设置 TOPMOST / LAYERED / TRANSPARENT。不需要安装 Electron 或任何依赖。
"""

import ctypes
import ctypes.wintypes as wintypes
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
# 64 位下 HWND 是指针：必须用 c_void_p(-1)，直接传 int -1 会被截成 0xFFFFFFFF
# （无效的句柄），SetWindowPos 静默失败 —— 这正是浮窗一直被游戏盖住的根因。
HWND_TOPMOST = ctypes.c_void_p(-1)
HWND_NOTOPMOST = ctypes.c_void_p(-2)
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
LWA_ALPHA = 0x00000002

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_USER32 = ctypes.windll.user32 if os.name == "nt" else None
if _USER32 is not None:
    _USER32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _USER32.SetWindowPos.restype = wintypes.BOOL
    _USER32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _USER32.GetWindowLongW.restype = ctypes.c_long
    _USER32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    _USER32.SetWindowLongW.restype = ctypes.c_long

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def find_browser():
    for p in BROWSER_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    for name in ("msedge.exe", "chrome.exe"):
        p = shutil.which(name)
        if p:
            return p
    return ""


class OverlayWindow:
    def __init__(self):
        self.hwnd = None
        self.proc = None
        self.pid = 0
        self.error = ""
        self.width = 400
        self.height = 820
        self.position = "right"

    @staticmethod
    def screen_size():
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return (
                int(ctypes.windll.user32.GetSystemMetrics(0)),
                int(ctypes.windll.user32.GetSystemMetrics(1)),
            )
        except Exception:
            return 1920, 1080

    @staticmethod
    def dpi_scale():
        """Chromium 的 --window-size/position 用逻辑像素，Win32 API 用物理像素。"""
        try:
            hdc = ctypes.windll.user32.GetDC(0)
            lpx = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, hdc)
            return (lpx or 96) / 96.0
        except Exception:
            return 1.0

    def xy_for(self, position):
        sw, sh = self.screen_size()
        margin = 16
        w = min(self.width, sw - 40)
        h = min(self.height, sh - 40)
        if position == "left":
            x = margin
        elif position == "center":
            x = (sw - w) // 2
        else:
            x = sw - w - margin
        return int(x), int(margin), int(w), int(h)

    def place(self, position=None):
        """重新摆位。无边框游戏里默认靠右，避开中央的海克斯卡牌与 OCR 区域。"""
        if not self.hwnd:
            return
        position = position or self.position
        x, y, w, h = self.xy_for(position)
        self.position = position
        user32 = ctypes.windll.user32
        user32.SetWindowPos(
            self.hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE
        )

    def set_collapsed(self, collapsed, full_width=400, full_height=820):
        """收起成小胶囊条 / 展开成完整面板，窗口尺寸随之变化。"""
        if collapsed:
            self.width, self.height = 236, 46
        else:
            self.width, self.height = int(full_width), int(full_height)
        if not self.hwnd:
            return
        x, y, w, h = self.xy_for(self.position)
        user32 = ctypes.windll.user32
        user32.SetWindowPos(
            self.hwnd, HWND_TOPMOST, x, y, w, h,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def launch(self, url, width=400, height=820, position="right", x=None, y=None):
        browser = find_browser()
        if not browser:
            self.error = "未找到 Edge / Chrome，无法开启浮窗；可直接在浏览器里使用控制台。"
            return False
        self.width, self.height, self.position = width, height, position
        if x is None or y is None:
            x, y, width, height = self.xy_for(position)
        scale = self.dpi_scale() or 1.0
        # 每次用独立 profile：残留的单例锁会让新进程把 URL 转给旧实例后直接退出
        base = Path(tempfile.gettempdir())
        profile = base / ("aram_overlay_%d" % int(time.time() * 1000))
        profile.mkdir(exist_ok=True)
        now = time.time()
        for old in base.glob("aram_overlay_*"):
            try:
                if old.is_dir() and now - old.stat().st_mtime > 86400:
                    shutil.rmtree(old, ignore_errors=True)
            except Exception:
                pass
        args = [
            browser,
            "--app=" + url,
            # Chromium 参数是逻辑像素；125% 缩放时直接传物理像素会把窗口推到屏幕外
            "--window-size=%d,%d" % (int(width / scale), int(height / scale)),
            "--window-position=%d,%d" % (int(x / scale), int(y / scale)),
            "--user-data-dir=" + str(profile),
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=msEdgeSplitScreen,msWebOOUI,msPdfOOUI",
            "--allow-insecure-localhost",
        ]
        try:
            # 注意：不能加 CREATE_NO_WINDOW —— 它会把 Chromium 的主窗口一起隐藏
            self.proc = subprocess.Popen(args)
            self.pid = self.proc.pid
        except Exception as exc:
            self.error = "启动浮窗失败: %s" % str(exc)[:120]
            return False
        for _ in range(100):  # 最多等 25 秒，Edge 冷启动可能较慢
            time.sleep(0.25)
            self.hwnd = self._find_hwnd()
            if self.hwnd:
                break
        if not self.hwnd:
            # 句柄没锁定就无法置顶/摆位，不能谎报成功；清理掉半开的进程便于重试
            self.error = "浮窗进程已启动，但没找到窗口句柄；请再点一次「展开」或双击 show_overlay.bat。"
            try:
                self.proc.terminate()
            except Exception:
                pass
            self.proc = None
            return False
        self.attach_to_game()
        self.set_topmost(True)
        self.place(position)
        return True

    def _find_hwnd(self):
        """--app 窗口标题可能为空，按 PID + 可见 + 有效尺寸来找。"""
        found = []
        user32 = ctypes.windll.user32
        EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        def cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == self.pid:
                r = RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                if r.right - r.left >= 200 and r.bottom - r.top >= 200:
                    found.append(((r.right - r.left) * (r.bottom - r.top), hwnd))
            return True

        try:
            user32.EnumWindows(EnumProc(cb), 0)
        except Exception:
            pass
        if not found:
            return None
        found.sort(key=lambda t: -t[0])
        return found[0][1]

    def bounds(self):
        """返回窗口当前矩形，用于自检确认它真的在屏幕内。"""
        if not self.hwnd:
            return None
        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]
        r = RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return (r.left, r.top, r.right, r.bottom)

    def _exstyle(self, flags, on):
        if not self.hwnd:
            return
        user32 = ctypes.windll.user32
        cur = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, (cur | flags) if on else (cur & ~flags))

    def set_topmost(self, on=True):
        if not self.hwnd:
            return
        user32 = ctypes.windll.user32
        user32.SetWindowPos(
            self.hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST,
            0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def _find_game_hwnd(self):
        """找全屏的 League of Legends 游戏窗口（RiotWindowClass）。"""
        user32 = ctypes.windll.user32
        EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        found = []

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        def cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, cls, 128)
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1) if n else None
            if buf:
                user32.GetWindowTextW(hwnd, buf, n)
            title = buf.value if buf else ""
            r = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            full = (r.right - r.left) >= sw * 0.9 and (r.bottom - r.top) >= sh * 0.9
            if full and (cls.value == "RiotWindowClass" or "League of Legends" in title):
                found.append(hwnd)
            return True

        try:
            user32.EnumWindows(EnumProc(cb), 0)
        except Exception:
            pass
        return found[0] if found else None

    def attach_to_game(self):
        """把浮窗设为游戏窗口的 owned window，系统保证它显示在游戏之上。"""
        if not self.hwnd:
            return False
        game = self._find_game_hwnd()
        if not game:
            return False
        user32 = ctypes.windll.user32
        try:
            setter = user32.SetWindowLongPtrW
            setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            setter.restype = ctypes.c_void_p
            setter(self.hwnd, -8, ctypes.c_void_p(game))  # GWLP_HWNDPARENT
        except AttributeError:
            user32.SetWindowLongW(self.hwnd, -8, game)
        self.game_hwnd = game
        return True

    def keep_on_top(self):
        """无边框/全屏游戏会反复抢占顶层，需要高频把浮窗提回来。"""
        if not self.hwnd:
            return
        user32 = ctypes.windll.user32
        if not user32.IsWindow(self.hwnd):
            self.hwnd = None
            return
        if not getattr(self, "game_hwnd", None) or not user32.IsWindow(self.game_hwnd):
            self.attach_to_game()
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE

        FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | 0x0040  # + SHOWWINDOW
        # 若游戏自己也是 TOPMOST，把它降回普通层；其它普通窗口不会因此盖住游戏
        game = getattr(self, "game_hwnd", None)
        if game and user32.IsWindow(game):
            ex = user32.GetWindowLongW(game, GWL_EXSTYLE)
            if ex & WS_EX_TOPMOST:
                user32.SetWindowPos(game, HWND_NOTOPMOST, 0, 0, 0, 0, FLAGS)
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, FLAGS)
        user32.BringWindowToTop(self.hwnd)

    def set_click_through(self, on=True):
        self._exstyle(WS_EX_TRANSPARENT, on)

    def set_opacity(self, alpha=1.0):
        if not self.hwnd:
            return
        user32 = ctypes.windll.user32
        a = int(max(0.25, min(1.0, float(alpha))) * 255)
        self._exstyle(WS_EX_LAYERED, True)
        user32.SetLayeredWindowAttributes(self.hwnd, 0, a, LWA_ALPHA)

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.hwnd = None
        self.proc = None
