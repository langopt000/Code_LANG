# -*- coding: utf-8 -*-
"""退出助手：停掉守护进程，关闭浮窗并结束本地服务。"""

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "watcher.stop").write_text("stop", encoding="utf-8")

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8765/api/control",
            data=json.dumps({"action": "quit"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener.open(req, timeout=5).read()
        print("助手已退出。")
    except Exception:
        print("服务本来就没在运行，守护稍后也会自行退出。")


if __name__ == "__main__":
    main()