# -*- coding: utf-8 -*-
"""纯外部屏幕识别：截取屏幕指定区域 -> Windows 自带 OCR -> 模糊匹配海克斯名称。

Riot 的 LCU / Live Client Data 接口都不暴露海克斯三选一卡牌，
所以只能走「看屏幕」这条路（与市面上的商业插件原理一致）。
不读取游戏内存、不截图上传、不联网传输图像。
"""

import ctypes
import ctypes.wintypes as wintypes
import difflib
import os
import re
import struct
import subprocess
import tempfile
import unicodedata
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OCR_SCRIPT = ROOT / "ocr.ps1"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

SRCCOPY = 0x00CC0020


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def screen_size():
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return (
            int(ctypes.windll.user32.GetSystemMetrics(0)),
            int(ctypes.windll.user32.GetSystemMetrics(1)),
        )
    except Exception:
        return (1920, 1080)


def _write_png(path, width, height, rgb):
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # filter type: none
        raw.extend(rgb[y * stride:(y + 1) * stride])

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    png += chunk(b"IEND", b"")
    Path(path).write_bytes(png)


def grab_region(x, y, w, h, out_png):
    """GDI BitBlt 抓屏，输出 PNG。坐标为物理像素。"""
    sw, sh = screen_size()
    x = max(0, min(int(x), sw - 10))
    y = max(0, min(int(y), sh - 10))
    w = max(16, min(int(w), sw - x))
    h = max(16, min(int(h), sh - y))

    # 必须用 64 位安全的句柄类型；默认 restype(c_int) 会在 x64 上截断 HDC
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    V = ctypes.c_void_p
    I = ctypes.c_int
    U = ctypes.c_uint
    user32.GetDC.argtypes = [V]
    user32.GetDC.restype = V
    user32.ReleaseDC.argtypes = [V, V]
    gdi32.CreateCompatibleDC.argtypes = [V]
    gdi32.CreateCompatibleDC.restype = V
    gdi32.CreateCompatibleBitmap.argtypes = [V, I, I]
    gdi32.CreateCompatibleBitmap.restype = V
    gdi32.SelectObject.argtypes = [V, V]
    gdi32.SelectObject.restype = V
    gdi32.BitBlt.argtypes = [V, I, I, I, I, V, I, I, ctypes.c_ulong]
    gdi32.BitBlt.restype = ctypes.c_bool
    gdi32.GetDIBits.argtypes = [
        V, V, U, U, V, ctypes.POINTER(BITMAPINFO), U,
    ]
    gdi32.GetDIBits.restype = I
    gdi32.DeleteObject.argtypes = [V]
    gdi32.DeleteDC.argtypes = [V]

    hdc = user32.GetDC(0)
    memdc = gdi32.CreateCompatibleDC(hdc)
    hbmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, hbmp)
    gdi32.BitBlt(memdc, 0, 0, w, h, hdc, x, y, SRCCOPY)

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h  # top-down
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0

    buf = (ctypes.c_ubyte * (w * h * 4))()
    gdi32.GetDIBits(
        memdc, hbmp, 0, h, ctypes.cast(buf, ctypes.c_void_p), ctypes.byref(bi), 0
    )
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(0, hdc)

    bgra = bytes(buf)
    rgb = bytearray(w * h * 3)
    rgb[0::3] = bgra[2::4]
    rgb[1::3] = bgra[1::4]
    rgb[2::3] = bgra[0::4]
    _write_png(out_png, w, h, bytes(rgb))
    return (w, h)


def run_ocr(png_path, timeout=15):
    """返回 (text, error)。"""
    if os.name != "nt" or not OCR_SCRIPT.exists():
        return "", "OCR 仅在 Windows 可用"
    try:
        p = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(OCR_SCRIPT), "-Path", str(png_path),
            ],
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        text = p.stdout.decode("utf-8", errors="ignore").strip()
        if "__OCR_UNAVAILABLE__" in text:
            return "", "系统未安装中文 OCR 语言包"
        if "__OCR_ERROR__" in text:
            return "", text.replace("__OCR_ERROR__", "")[:160]
        return text, ""
    except subprocess.TimeoutExpired:
        return "", "OCR 超时"
    except Exception as exc:
        return "", "OCR 失败: %s" % str(exc)[:120]


_PUNCT = re.compile(r"[\s\-—_·:：,，.。!！?？'\"“”‘’()（）\[\]【】/\\+]+")


def normalize(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    return _PUNCT.sub("", s).lower()


def match_augments(text, augment_index, limit=3, min_score=0.65):
    """把 OCR 文本模糊匹配到海克斯名称，返回按置信度排序的候选。

    精度约束（针对实测误报）：
    - 最长名称优先：「终极循环往复」不能被短名「循环往复」抢走（短名是长名子串时压制）。
    - 只在「像卡牌名」的短行上匹配：城邦贴纸/标语/描述等长行一律不参与，避免把贴纸认成海克斯。
    - 模糊匹配提高门槛（覆盖率与相似度双下限），整段仅用于名称完整出现的精确命中。
    """
    raw_text = str(text or "")
    flat = normalize(raw_text)
    if len(flat) < 2:
        return []
    lines = [normalize(l) for l in raw_text.splitlines()]
    lines = [l for l in lines if l]
    all_names = set()
    for a in augment_index:
        for raw in [a.get("name", "")] + list(a.get("aliases") or []):
            n = normalize(raw)
            if len(n) >= 2:
                all_names.add(n)

    def shadowed(nm, hay):
        # 存在另一个更长、且同样出现在 hay 里的名称包含 nm → nm 是它的子串，应让位
        return any(n2 != nm and len(n2) > len(nm) and nm in n2 and n2 in hay for n2 in all_names)

    scored = []
    for a in augment_index:
        best = 0.0
        for raw in [a.get("name", "")] + list(a.get("aliases") or []):
            name = normalize(raw)
            if len(name) < 2:
                continue
            if name in flat and not shadowed(name, flat):
                best = 1.0
                continue
            for ln in lines:
                if len(ln) > 12:
                    continue  # 卡牌名很短；长行是描述/标语/贴纸文案
                if name == ln:
                    s = 1.0
                elif name in ln:
                    if shadowed(name, ln):
                        continue
                    s = 0.95 if len(ln) <= len(name) + 3 else 0.5
                else:
                    if len(ln) > len(name) * 3:
                        continue
                    grams = [name[i:i + 2] for i in range(len(name) - 1)] or [name]
                    cover = sum(1 for g in grams if g in ln) / len(grams)
                    if cover < 0.5:
                        continue
                    ratio = difflib.SequenceMatcher(None, name, ln).ratio()
                    if ratio < 0.5:
                        continue
                    s = 0.45 * cover + 0.55 * ratio
                if s > best:
                    best = s
        if best >= min_score:
            scored.append((best, a))
    scored.sort(key=lambda t: -t[0])
    out, seen = [], set()
    for score, a in scored:
        if a["id"] in seen:
            continue
        seen.add(a["id"])
        item = dict(a)
        item["ocrScore"] = round(score, 3)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def detect(region, augment_index, keep_png=False, out_png=None):
    """region: 屏幕比例 (x, y, w, h)，各为 0~1。返回 (candidates, text, error)。"""
    sw, sh = screen_size()
    x, y, w, h = region
    tmp = Path(out_png) if out_png else (Path(tempfile.gettempdir()) / "aram_assistant_capture.png")
    try:
        grab_region(x * sw, y * sh, w * sw, h * sh, tmp)
        text, err = run_ocr(tmp)
        if err:
            return [], "", err
        return match_augments(text, augment_index), text, ""
    except Exception as exc:
        return [], "", "截屏失败: %s" % str(exc)[:120]
    finally:
        if not keep_png and not out_png and tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


# 商店界面才有的文字：标签页 / 搜索框 / 购买按钮。刻意不收录「装备」等
# 通用词，避免把浮窗自己的「出装与顺序」或对局内其它文字误判成商店。
SHOP_HINTS = (
    "商店", "推荐装备", "全部装备", "已购装备", "已购买", "已购", "购买", "出售", "搜索",
    "shop", "recommended", "all items", "owned", "purchase", "sell", "search",
)


def shop_hit(text):
    """返回命中的关键词，没有命中返回空串。"""
    flat = normalize(text)
    if len(flat) < 2:
        return ""
    for hint in SHOP_HINTS:
        h = normalize(hint)
        if h and h in flat:
            return hint
    return ""


def detect_shop(region, keep_png=False, out_png=None):
    """识别商店是否打开。返回 (hit, text, error)，hit 非空即视为打开。"""
    sw, sh = screen_size()
    x, y, w, h = region
    tmp = Path(out_png) if out_png else (Path(tempfile.gettempdir()) / "aram_assistant_shop.png")
    try:
        grab_region(x * sw, y * sh, w * sw, h * sh, tmp)
        text, err = run_ocr(tmp)
        if err:
            return "", "", err
        return shop_hit(text), text, ""
    except Exception as exc:
        return "", "", "截屏失败: %s" % str(exc)[:120]
    finally:
        if not keep_png and not out_png and tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
