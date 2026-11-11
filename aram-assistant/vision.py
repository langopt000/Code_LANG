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


def match_augments(text, augment_index, limit=None, min_score=0.65):
    """把 OCR 文本模糊匹配到海克斯名称，返回按置信度排序的候选。

    精度约束（针对实测误报）：
    - 最长名称优先：「终极循环往复」不能被短名「循环往复」抢走（短名是长名子串时压制）。
    - 只在「像卡牌名」的短行上匹配：城邦贴纸/标语/描述等长行一律不参与，避免把贴纸认成海克斯。
      聊天/计分板里整段出现名字也不算精确命中——精确命中只认短行。
    - 模糊匹配提高门槛（覆盖率与相似度双下限）。是否构成三选一由调用方按
      「2~3 个不同海克斯且各自 >=0.9」判定，这里只负责打分。
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
        if limit is not None and len(out) >= limit:
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


# 商店面板独有的强信号：命中任一即视为打开。
SHOP_STRONG = ("商店", "推荐装备", "全部装备", "已购装备", "已购买", "recommended", "all items")
# 弱信号聊天/死亡回放里也可能出现（实测「搜索」单独命中过），须两个以上同时出现才算。
# 刻意不收录「装备」等通用词，避免把浮窗自己的「出装与顺序」误判成商店。
SHOP_HINTS = SHOP_STRONG + (
    "已购", "购买", "出售", "搜索",
    "shop", "owned", "purchase", "sell", "search",
)


def shop_hits(text):
    """返回去重后的命中关键词（短词被长词包含时只算长词）。"""
    flat = normalize(text)
    if len(flat) < 2:
        return []
    hits = [h for h in SHOP_HINTS if normalize(h) in flat]
    return [h for h in hits if not any(h != o and normalize(h) in normalize(o) for o in hits)]


def shop_hit(text):
    """商店打开判定：强信号任一，或弱信号 >=2 个。返回命中描述，未打开返回空串。"""
    hits = shop_hits(text)
    if not hits:
        return ""
    if any(h in SHOP_STRONG for h in hits) or len(hits) >= 2:
        return "/".join(hits)
    return ""


def detect_chat(region, keep_png=False, out_png=None):
    """识别聊天区文字。返回 (text, error)。"""
    sw, sh = screen_size()
    x, y, w, h = region
    tmp = Path(out_png) if out_png else (Path(tempfile.gettempdir()) / "aram_assistant_chat.png")
    try:
        grab_region(x * sw, y * sh, w * sw, h * sh, tmp)
        return run_ocr(tmp)
    except Exception as exc:
        return "", "截屏失败: %s" % str(exc)[:120]
    finally:
        if not keep_png and not out_png and tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


# 聊天里的选取广播：「04:49 名字 (英雄) 已选择了沃格勒特的巫师帽.」
CHAT_PICK_RE = re.compile(r"已选择了\s*([^。.!！?？\n]+)")


def parse_chat_picks(text, augment_index, who=(), min_score=0.75):
    """从聊天原文里挑出「who 已选择了X」的广播，按屏幕顺序返回。

    who 为本人召唤师名 / 英雄名 / 称号；聊天会广播所有人的选取，
    必须归属到本人，空 who 时不记录任何行。
    """
    who_n = [normalize(w) for w in who if w]
    who_n = [w for w in who_n if len(w) >= 2]
    if not who_n:
        return []
    out, seen = [], set()
    for raw in str(text or "").splitlines():
        m = CHAT_PICK_RE.search(raw)
        if not m:
            continue
        line_n = normalize(raw)
        if not any(w in line_n for w in who_n):
            continue
        hits = match_augments(m.group(1), augment_index, limit=1, min_score=min_score)
        if not hits or hits[0]["id"] in seen:
            continue
        seen.add(hits[0]["id"])
        out.append(
            {
                "id": hits[0]["id"],
                "name": hits[0]["name"],
                "ocrScore": hits[0]["ocrScore"],
                "line": raw.strip(),
            }
        )
    return out


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
