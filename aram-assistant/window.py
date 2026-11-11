# -*- coding: utf-8 -*-
"""把控制台挂成一个置顶、可调透明度、可点击穿透的无边框浮窗。

做法：用 Edge / Chrome 的 --app 模式开一个无边框窗口，再通过 Win32 API
设置 TOPMOST / LAYERED / TRANSPARENT。不需要安装 Electron 或任何依赖。

浮窗始终只有一个：启动前先按页面标题「收养」已存在的窗口（服务重启、句柄
丢失都能自愈），所有打开路径在 app.py 里还有单飞锁兜底。
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
WM_CLOSE = 0x0010
# 浏览器 app 窗口顶部自带一条标题栏（ globe/最小化/关闭 ）。用窗口区域把它裁掉、
# 并把窗口上移同等高度，使网页内容落在预期边距处，视觉上就是纯插件式悬浮框。
CHROME_H = 32

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 收起态胶囊尺寸（物理像素）。收起/展开只是同一个窗口改尺寸，永远只有一个窗口。
# 高度含被裁掉的标题栏 CHROME_H；可见胶囊 = 92-32 = 60px，展开按钮完整可点。
COLLAPSED_W, COLLAPSED_H = 260, 92
# 浮窗页面标题（overlay 模式 JS 会把 title 改成第一个；第二个是旧版/控制台标题，
# 用于收养升级前已开着的窗口）。正常浏览器窗口标题带「 - Edge/Chrome」后缀，不会误匹配。
PAGE_TITLES = ("ARAM 助手浮窗", "ARAM 海克斯大乱斗实时助手")

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
    _USER32.IsWindow.argtypes = [wintypes.HWND]
    _USER32.IsWindow.restype = wintypes.BOOL
    _USER32.IsIconic.argtypes = [wintypes.HWND]
    _USER32.IsIconic.restype = wintypes.BOOL
    _USER32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
    _USER32.GetWindowRect.restype = wintypes.BOOL
    _USER32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _USER32.PostMessageW.restype = wintypes.BOOL
    _USER32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _USER32.ShowWindow.restype = wintypes.BOOL
    _USER32.BringWindowToTop.argtypes = [wintypes.HWND]
    _USER32.BringWindowToTop.restype = wintypes.BOOL
    _USER32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
    _USER32.SetLayeredWindowAttributes.restype = wintypes.BOOL
    _USER32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    _USER32.SetWindowRgn.restype = ctypes.c_int
    _USER32.GetForegroundWindow.restype = wintypes.HWND
    _USER32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _USER32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

_GDI32 = ctypes.windll.gdi32 if os.name == "nt" else None
if _GDI32 is not None:
    _GDI32.CreateRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    _GDI32.CreateRectRgn.restype = wintypes.HRGN

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


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def _visible_windows():
    """枚举可见顶层窗口：(hwnd, pid, title, (l, t, r, b))。"""
    if _USER32 is None:
        return []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    out = []

    def cb(hwnd, _):
        if _USER32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            _USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            n = _USER32.GetWindowTextLengthW(hwnd)
            title = ""
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                _USER32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value
            r = RECT()
            _USER32.GetWindowRect(hwnd, ctypes.byref(r))
            out.append((hwnd, pid.value, title, (r.left, r.top, r.right, r.bottom)))
        return True

    try:
        _USER32.EnumWindows(EnumProc(cb), 0)
    except Exception:
        pass
    return out


def is_game_foreground():
    """前台是不是游戏窗口。屏幕识别只在前台是游戏时做，避免读到浏览器等其它窗口。"""
    if _USER32 is None:
        return False
    try:
        hwnd = _USER32.GetForegroundWindow()
        if not hwnd:
            return False
        cls = ctypes.create_unicode_buffer(128)
        _USER32.GetClassNameW(hwnd, cls, 128)
        if cls.value == "RiotWindowClass":
            return True
        n = _USER32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            _USER32.GetWindowTextW(hwnd, buf, n + 1)
            return "League of Legends" in buf.value
    except Exception:
        return False
    return False


class OverlayWindow:
    def __init__(self):
        self.hwnd = None
        self.proc = None
        self.pid = 0
        self.error = ""
        self.width = 400
        self.height = 820
        self.position = "right"
        self.game_hwnd = None
        self._clip_size = None
        self._clip_at = 0.0

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

    def is_alive(self):
        """窗口或进程任一存活即视为浮窗还在（proc 对象残留不代表进程活着）。"""
        if _USER32 is None:
            return False
        if self.hwnd and _USER32.IsWindow(self.hwnd):
            return True
        if self.proc is not None and self.proc.poll() is None:
            return True
        return False

    def find_existing(self):
        """按页面标题收养已存在的浮窗（服务重启 / 句柄丢失后自愈，避免开第二个）。"""
        for hwnd, pid, title, (l, t, r, b) in _visible_windows():
            if title in PAGE_TITLES and 180 <= (r - l) <= 560 and (b - t) >= 24:
                self.hwnd = hwnd
                self.pid = pid
                return hwnd
        return None

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
        # 上移 CHROME_H：被裁掉的标题栏不占屏幕，网页内容正好落在 margin 处
        return int(x), int(margin - CHROME_H), int(w), int(h)

    def clip_chrome(self):
        """裁掉窗口顶部的浏览器标题栏，只保留网页内容区域。"""
        if not self.hwnd or not _USER32.IsWindow(self.hwnd) or _GDI32 is None:
            return
        r = RECT()
        _USER32.GetWindowRect(self.hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if h <= CHROME_H:
            return
        hrgn = _GDI32.CreateRectRgn(0, CHROME_H, w, h)
        _USER32.SetWindowRgn(self.hwnd, hrgn, True)  # 区域归系统所有，勿 DeleteObject
        self._clip_size = (w, h)
        self._clip_at = time.time()

    def maintain_chrome(self):
        """尺寸变化或浏览器自行重排后补裁；节流避免高频重绘闪烁。"""
        if not self.hwnd or not _USER32.IsWindow(self.hwnd):
            return
        r = RECT()
        _USER32.GetWindowRect(self.hwnd, ctypes.byref(r))
        size = (r.right - r.left, r.bottom - r.top)
        if size != self._clip_size or time.time() - self._clip_at > 3:
            self.clip_chrome()

    def place(self, position=None):
        """重新摆位。无边框游戏里默认靠右，避开中央的海克斯卡牌与 OCR 区域。"""
        if not self.hwnd:
            return
        position = position or self.position
        x, y, w, h = self.xy_for(position)
        self.position = position
        _USER32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)
        self.clip_chrome()

    def set_collapsed(self, collapsed, full_width=400, full_height=820):
        """收起成小胶囊 / 展开成完整面板（同一窗口改尺寸）。返回是否真的摆好了。"""
        if collapsed:
            self.width, self.height = COLLAPSED_W, COLLAPSED_H
        else:
            self.width, self.height = int(full_width), int(full_height)
        if not self.hwnd or not _USER32.IsWindow(self.hwnd):
            self.hwnd = None
            self.find_existing()  # 句柄丢了先尝试收养，而不是静默 no-op
        if not self.hwnd:
            return False
        x, y, w, h = self.xy_for(self.position)
        _USER32.SetWindowPos(
            self.hwnd, HWND_TOPMOST, x, y, w, h,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        self.clip_chrome()
        return True

    def launch(self, url, width=400, height=820, position="right", x=None, y=None):
        # 先收养已存在的浮窗：服务重启 / 重复点击都不会开出第二个窗口
        if self.find_existing():
            self.proc = None
            self.width, self.height, self.position = width, height, position
            self.attach_to_game()
            self.set_topmost(True)
            self.place(position)
            self.error = ""
            return True
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
        self.error = ""
        return True

    def _find_hwnd(self):
        """按 PID + 可见 + 有效尺寸找 --app 窗口。收起态只有 260×48，下限必须够低。"""
        found = []
        for hwnd, pid, _title, (l, t, r, b) in _visible_windows():
            if pid == self.pid and (r - l) >= 80 and (b - t) >= 24:
                found.append(((r - l) * (b - t), hwnd))
        if not found:
            return None
        found.sort(key=lambda t: -t[0])
        return found[0][1]

    def bounds(self):
        """返回窗口当前矩形，用于自检确认它真的在屏幕内。"""
        if not self.hwnd:
            return None
        r = RECT()
        _USER32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return (r.left, r.top, r.right, r.bottom)

    def _exstyle(self, flags, on):
        if not self.hwnd:
            return
        cur = _USER32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        _USER32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, (cur | flags) if on else (cur & ~flags))

    def set_topmost(self, on=True):
        if not self.hwnd:
            return
        _USER32.SetWindowPos(
            self.hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST,
            0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def _find_game_hwnd(self):
        """找全屏的 League of Legends 游戏窗口（RiotWindowClass）。"""
        sw, sh = self.screen_size()
        found = []
        for hwnd, _pid, title, (l, t, r, b) in _visible_windows():
            cls = ctypes.create_unicode_buffer(128)
            _USER32.GetClassNameW(hwnd, cls, 128)
            full = (r - l) >= sw * 0.9 and (b - t) >= sh * 0.9
            if full and (cls.value == "RiotWindowClass" or "League of Legends" in title):
                found.append(hwnd)
        return found[0] if found else None

    def attach_to_game(self):
        """把浮窗设为游戏窗口的 owned window，系统保证它显示在游戏之上。"""
        if not self.hwnd:
            return False
        game = self._find_game_hwnd()
        if not game:
            return False
        try:
            setter = _USER32.SetWindowLongPtrW
            setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            setter.restype = ctypes.c_void_p
            setter(self.hwnd, -8, ctypes.c_void_p(game))  # GWLP_HWNDPARENT
        except AttributeError:
            _USER32.SetWindowLongW(self.hwnd, -8, game)
        self.game_hwnd = game
        return True

    def keep_on_top(self):
        """无边框/全屏游戏会反复抢占顶层，需要高频把浮窗提回来。"""
        if not self.hwnd:
            return
        if not _USER32.IsWindow(self.hwnd):
            # 句柄失效：先尝试收养还活着的浮窗，收养不到才清空（让上层可重开）
            if not self.find_existing():
                self.hwnd = None
                return
        if not self.game_hwnd or not _USER32.IsWindow(self.game_hwnd):
            self.attach_to_game()
        if _USER32.IsIconic(self.hwnd):
            _USER32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE

        FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
        # 若游戏自己也是 TOPMOST，把它降回普通层；其它普通窗口不会因此盖住游戏
        if self.game_hwnd and _USER32.IsWindow(self.game_hwnd):
            ex = _USER32.GetWindowLongW(self.game_hwnd, GWL_EXSTYLE)
            if ex & WS_EX_TOPMOST:
                _USER32.SetWindowPos(self.game_hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, FLAGS)
        _USER32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, FLAGS)
        _USER32.BringWindowToTop(self.hwnd)
        self.maintain_chrome()

    def set_click_through(self, on=True):
        self._exstyle(WS_EX_TRANSPARENT, on)

    def set_opacity(self, alpha=1.0):
        if not self.hwnd:
            return
        a = int(max(0.25, min(1.0, float(alpha))) * 255)
        self._exstyle(WS_EX_LAYERED, True)
        _USER32.SetLayeredWindowAttributes(self.hwnd, 0, a, LWA_ALPHA)

    def close(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        elif self.hwnd and _USER32.IsWindow(self.hwnd):
            # 收养来的窗口没有 proc 句柄，发 WM_CLOSE 让它自己退出
            _USER32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        self.hwnd = None
        self.proc = None
        self.pid = 0
