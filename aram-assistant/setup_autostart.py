# -*- coding: utf-8 -*-
"""绑定开机自启：写入 Windows 启动项，并立刻拉起守护进程。

不依赖脚本宿主/计划任务：用 Python 自己复制启动项、以分离进程启动 watcher。
"""

import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VBS = ROOT / "autostart.vbs"
CMD = ROOT / "autostart.cmd"
STARTUP = (
    Path.home()
    / "AppData" / "Roaming" / "Microsoft" / "Windows"
    / "Start Menu" / "Programs" / "Startup"
)
VBS_ENTRY = STARTUP / "ARAM-Mayhem-Assistant.vbs"
CMD_ENTRY = STARTUP / "ARAM-Mayhem-Assistant.cmd"
STOP_FLAG = ROOT / "data" / "watcher.stop"

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def pythonw():
    p = Path(sys.executable).with_name("pythonw.exe")
    if p.is_file():
        return str(p)
    return "pythonw.exe"


def server_alive():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    if not VBS.is_file() or not CMD.is_file():
        print("缺少 autostart.vbs / autostart.cmd")
        return 1
    STARTUP.mkdir(parents=True, exist_ok=True)
    py = pythonw()

    def render(path):
        """把模板里的 __ROOT__ / __PY__ 换成当前真实路径。

        这样项目换目录后，重跑一次本脚本就能修好开机启动项，不用手改模板。
        """
        s = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        s = s.replace("__ROOT__", str(ROOT)).replace("__PY__", py)
        return s.replace("\n", "\r\n")  # 统一 CRLF，避免脚本宿主解析异常

    VBS_ENTRY.write_bytes(render(VBS).encode("utf-8"))
    CMD_ENTRY.write_bytes(render(CMD).encode("utf-8"))
    print("启动项已写入:")
    print("  %s" % VBS_ENTRY)
    print("  %s (备用，无需脚本宿主)" % CMD_ENTRY)

    if STOP_FLAG.exists():
        STOP_FLAG.unlink()

    proc = subprocess.Popen(
        [py, str(ROOT / "watcher.py")],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
    )
    print("守护已启动: PID %s (%s)" % (proc.pid, py))
    print("检测到 League 客户端后会自动拉起助手；进入选人/对局自动弹浮窗。")
    if server_alive():
        print("助手服务当前在线: http://127.0.0.1:8765/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
