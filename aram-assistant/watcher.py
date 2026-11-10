# -*- coding: utf-8 -*-
"""常驻守护：检测到英雄联盟客户端启动，就自动拉起 ARAM 助手。

- 无窗口运行（由 autostart.vbs 用 pythonw.exe 启动）
- 单实例保护：占用本地 8766 端口，重复启动会直接退出
- 客户端出现 -> 启动 app.py（服务已存在则不重复启动）
- 客户端退出超过 120 秒 -> 关闭浮窗并停止它自己拉起的服务
- 日志：data/watcher.log（自动限制大小）
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

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

PORT = 8765
LOCK_PORT = 8766
CLIENT_PROC = "LeagueClientUx.exe"
CHECK_INTERVAL = 5
EXIT_GRACE = 120
LOG_FILE = ROOT / "data" / "watcher.log"
STOP_FLAG = ROOT / "data" / "watcher.stop"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def log(msg):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 200_000:
            LOG_FILE.write_text("", encoding="utf-8")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def single_instance():
    """返回锁 socket；拿不到说明已有守护在跑，调用方应退出。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def client_running():
    try:
        p = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq %s" % CLIENT_PROC, "/NH"],
            capture_output=True, text=True, timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
        return CLIENT_PROC.lower() in (p.stdout or "").lower()
    except Exception:
        return False


def server_running():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % PORT, timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def api_post(path, payload):
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (PORT, path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            r.read()
    except Exception:
        pass


def python_exe():
    d = os.path.dirname(sys.executable)
    p = os.path.join(d, "python.exe")
    return p if os.path.isfile(p) else sys.executable


def start_server():
    env = os.environ.copy()
    env["ARAM_NO_BROWSER"] = "1"   # 由守护/浮窗接管，不额外弹浏览器标签
    try:
        proc = subprocess.Popen(
            [python_exe(), str(ROOT / "app.py")],
            cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        return proc
    except Exception as exc:
        log("启动助手失败: %s" % str(exc)[:160])
        return None


def main():
    lock = single_instance()
    if lock is None:
        return  # 已有守护在运行
    log("守护已启动（单实例锁 127.0.0.1:%d）" % LOCK_PORT)

    proc = None
    was_client = None
    client_gone_at = 0
    server_ready_at = 0

    while True:
        try:
            if STOP_FLAG.exists():
                try:
                    STOP_FLAG.unlink()
                except Exception:
                    pass
                log("收到停止标志，守护退出")
                if proc and proc.poll() is None:
                    api_post("/api/control", {"action": "closeOverlay"})
                    proc.terminate()
                return

            online = client_running()

            # 客户端刚上线
            if online and was_client is False:
                log("检测到 League 客户端启动")
            if not online and was_client is True:
                log("客户端已退出，%d 秒后停止助手" % EXIT_GRACE)
                client_gone_at = time.time()

            if online:
                client_gone_at = 0
                if not server_running():
                    if proc is None or proc.poll() is not None:
                        log("正在自动启动 ARAM 助手 ...")
                        proc = start_server()
                        server_ready_at = time.time()
                        # 等数据就绪后，进入选人/对局时由 app.py 自动弹浮窗
                else:
                    server_ready_at = server_ready_at or time.time()
            else:
                # 客户端长时间不在：释放它自己拉起的服务
                if (
                    proc is not None
                    and proc.poll() is None
                    and client_gone_at
                    and time.time() - client_gone_at > EXIT_GRACE
                ):
                    log("停止助手服务与浮窗")
                    api_post("/api/control", {"action": "closeOverlay"})
                    time.sleep(0.4)
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    proc = None
                    client_gone_at = 0

            was_client = online
        except Exception as exc:
            log("守护异常: %s" % str(exc)[:160])
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
