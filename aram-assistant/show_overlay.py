# -*- coding: utf-8 -*-
"""一键唤起：确保助手服务在跑，并立刻弹出游戏内置顶浮窗。

双击 show_overlay.bat 即可。不依赖开机守护，随时可以手动叫出来。
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

URL = "http://127.0.0.1:8765"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path, payload=None, timeout=3):
    if payload is None:
        return json.load(opener.open(URL + path, timeout=timeout))
    req = urllib.request.Request(
        URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.load(opener.open(req, timeout=timeout))


def service_up():
    try:
        return api("/api/health", timeout=1.5).get("ok", False)
    except Exception:
        return False


def ensure_watcher():
    """让守护监管服务，服务异常退出时能被自动拉回（避免"弹一下就没了"）。"""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 8766))
        busy = False
    except OSError:
        busy = True
    finally:
        s.close()
    if busy:
        print("守护已在运行。")
        return
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pyw if pyw.is_file() else sys.executable)
    subprocess.Popen(
        [exe, str(ROOT / "watcher.py")],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
    )
    print("已启动守护（服务掉线会自动拉回）。")


def main():
    print("ARAM 助手 · 一键唤起")
    ensure_watcher()
    if not service_up():
        print("正在启动本地服务 ...")
        pyw = Path(sys.executable).with_name("pythonw.exe")
        exe = str(pyw if pyw.is_file() else sys.executable)
        env = os.environ.copy()
        env["ARAM_NO_BROWSER"] = "1"
        subprocess.Popen(
            [exe, str(ROOT / "app.py")],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
        )
    else:
        print("本地服务已在运行。")

    ready = False
    for _ in range(40):
        time.sleep(0.8)
        try:
            st = api("/api/state", timeout=2)
            if st.get("ready"):
                ready = True
                print("数据就绪：ARAMKit v%s / %s" % (
                    st.get("version"), "高分段" if st.get("dataset") == "high" else "全分段"))
                print("客户端阶段：%s" % st.get("phase"))
                break
        except Exception:
            pass
    if not ready:
        print("服务启动超时。首次启动下载数据可能较慢，请稍后再双击一次；仍失败请用诊断模式启动查看报错。")
        return 1

    ok = False
    try:
        r = api("/api/control", {"action": "testOverlay"}, timeout=30)
        ok = bool(r.get("ok", True))
        if not ok:
            print("弹窗失败：%s" % r.get("error"))
    except Exception as exc:
        print("接口调用失败：%s" % str(exc)[:100])

    if not ok:
        print("尝试直接创建窗口 ...")
        try:
            from window import OverlayWindow
            w = OverlayWindow()
            ok = w.launch(URL + "/?overlay=1", position="right")
            if ok:
                print("直接创建的浮窗矩形：%s" % (w.bounds(),))
        except Exception as e2:
            print("浮窗创建失败：%s" % str(e2)[:160])

    time.sleep(1.5)
    try:
        st = api("/api/state", timeout=3)
        b = st.get("overlayBounds")
        if b:
            print("浮窗矩形：(%d, %d) - (%d, %d)  位置：%s" % (
                b[0], b[1], b[2], b[3], st.get("overlayPosition")))
            sw = 2560
            if b[0] < 0 or b[2] > sw + 50:
                print("警告：窗口可能跑到屏幕外了，请把这个矩形发给我。")
            else:
                print("确认：浮窗已在屏幕内，应该能看到了。")
        else:
            print("还没拿到窗口矩形，稍等 2 秒后打开控制台点「重新检测」。")
    except Exception as e:
        print("状态读取失败：%s" % str(e)[:100])
    print("完成。之后开客户端会自动弹出；也可以随时再双击本脚本。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
