# -*- coding: utf-8 -*-
"""取消开机自启并停止当前守护。不会删除助手本体。"""

import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STARTUP = (
    Path.home()
    / "AppData" / "Roaming" / "Microsoft" / "Windows"
    / "Start Menu" / "Programs" / "Startup"
)
ENTRIES = [STARTUP / "ARAM-Mayhem-Assistant.vbs", STARTUP / "ARAM-Mayhem-Assistant.cmd"]
STOP_FLAG = ROOT / "data" / "watcher.stop"


def main():
    removed = 0
    for entry in ENTRIES:
        if entry.exists():
            entry.unlink()
            print("已移除启动项: %s" % entry)
            removed += 1
    if not removed:
        print("启动项本来就不存在。")
    STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)
    STOP_FLAG.write_text("stop", encoding="utf-8")
    print("已写入停止标志，守护会在几秒内退出（服务也会随之关闭）。")
    time.sleep(6)
    try:
        urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=1.5)
        print("助手服务仍在运行（可能是你手动启动的，可自行关闭）。")
    except Exception:
        print("助手服务已停止。")
    print("助手本体未删除，随时可以运行 start.bat 手动启动，或 setup_autostart.bat 重新绑定。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
