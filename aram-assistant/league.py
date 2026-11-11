# -*- coding: utf-8 -*-
"""只读连接英雄联盟本地客户端。

只使用 Riot 自己在本机暴露的两个接口：
  LCU  (127.0.0.1:<随机端口>, 自签证书 + Basic 认证)   英雄选择阶段
  Live Client Data (127.0.0.1:2999)                     对局内状态

不读取游戏内存、不注入进程、不修改游戏文件、不自动化任何输入。
"""

import base64
import json
import os
import re
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import winreg  # Windows 标准库，用来读游戏安装路径
except ImportError:
    winreg = None

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _ps_read_commandline():
    """通过 WMI 读取 LeagueClientUx.exe 的启动参数（含端口与令牌）。"""
    tmp = Path(tempfile.gettempdir()) / "aram_assistant_lcu.txt"
    # 可能存在多个残留进程；优先取带 --app-port 的那个
    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "$all = @(Get-CimInstance Win32_Process -Filter \"Name='LeagueClientUx.exe'\"); "
        "$p = $all | Where-Object { $_.CommandLine -like '*--app-port*' } "
        "| Select-Object -First 1 -ExpandProperty CommandLine; "
        "if (-not $p) { $p = $all | Select-Object -First 1 -ExpandProperty CommandLine }; "
        "if ($p) { $p | Out-File -FilePath '%s' -Encoding utf8 -NoNewline } "
        "else { Remove-Item -Force -ErrorAction SilentlyContinue '%s' }"
    ) % (str(tmp).replace("\\", "/"), str(tmp).replace("\\", "/"))
    try:
        if tmp.exists():
            tmp.unlink()
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=8,
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if tmp.exists():
            return tmp.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception:
        pass
    return ""


def _install_roots():
    """从注册表找游戏安装目录；国服 WeGame 版与国际服 Riot 版都覆盖。"""
    roots = []
    if winreg:
        candidates = [
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Tencent\LOL", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Tencent\LOL", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Riot Games\League of Legends", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Riot Games\League of Legends", "InstallPath"),
        ]
        for hive, key, value in candidates:
            try:
                with winreg.OpenKey(hive, key) as k:
                    p, _ = winreg.QueryValueEx(k, value)
                    if p:
                        roots.append(Path(str(p)))
            except Exception:
                pass
    for p in (
        r"C:\Riot Games\League of Legends",
        r"D:\Riot Games\League of Legends",
        r"D:\WeGameApps\英雄联盟",
        r"C:\WeGameApps\英雄联盟",
    ):
        roots.append(Path(p))
    seen, out = set(), []
    for r in roots:
        key = str(r).lower()
        if key not in seen and r.exists():
            seen.add(key)
            out.append(r)
    return out


def _discover_from_files():
    """WMI 读不到命令行时（常见于国服/权限受限），从 lockfile 或客户端日志取凭据。

    返回 [(port, token, source), ...]，按可信度排序。
    """
    found = []
    for root in _install_roots():
        # lockfile 格式：name:pid:port:password:protocol
        for lf in (root / "lockfile", root / "LeagueClient" / "lockfile"):
            try:
                text = lf.read_text(encoding="utf-8", errors="ignore").strip()
                parts = text.split(":")
                if len(parts) >= 5 and parts[2].isdigit():
                    found.append((int(parts[2]), parts[3], "lockfile"))
            except Exception:
                pass
        # LeagueClientUx 日志首行会打印完整启动参数
        for d in (root / "LeagueClient", root / "LeagueClient Logs", root / "Logs"):
            if not d.is_dir():
                continue
            try:
                logs = [
                    p for p in d.glob("*LeagueClientUx.log")
                    if p.is_file() and "Helper" not in p.name
                ]
                logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                for p in logs[:2]:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    port = re.search(r"--app-port=(\d+)", text)
                    token = re.search(r"--remoting-auth-token=([^\s\"']+)", text)
                    if port and token:
                        found.append((int(port.group(1)), token.group(1), "log"))
            except Exception:
                pass
    # 去重
    out, seen = [], set()
    for item in found:
        if item[:2] not in seen:
            seen.add(item[:2])
            out.append(item)
    return out


class LeagueClient:
    def __init__(self):
        self.port = None
        self.token = None
        self.source = ""          # wmi / lockfile / log
        self.last_probe = 0
        self.phase = "None"
        self.connected = False
        self.error = ""
        self._champ_names = {}

    # ---------- 连接 ----------
    def _verify(self, port, token, timeout=2):
        """直接验证一组凭据能不能访问 LCU（不走 _lcu，避免递归 probe）。"""
        auth = "Basic " + base64.b64encode(("riot:" + token).encode()).decode()
        req = urllib.request.Request(
            "https://127.0.0.1:%d/lol-gameflow/v1/gameflow-phase" % port,
            headers={"Authorization": auth, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
                return r.status == 200
        except Exception:
            return False

    def probe(self, force=False):
        now = time.time()
        throttle = 20 if (self.port and self.connected) else 8
        if not force and now - self.last_probe < throttle:
            return self.connected
        self.last_probe = now

        candidates = []
        cmd = _ps_read_commandline()
        m_port = re.search(r"--app-port=(\d+)", cmd)
        m_token = re.search(r"--(?:remoting-)?auth-token=([^\s\"']+)", cmd)
        if m_port and m_token:
            candidates.append((int(m_port.group(1)), m_token.group(1), "wmi"))
        candidates.extend(_discover_from_files())

        # 已连上且凭据仍有效时直接复用，避免每次重新解析日志
        if self.port and self.connected:
            candidates.insert(0, (self.port, self.token, self.source or "cached"))

        for port, token, source in candidates:
            if self._verify(port, token):
                self.port, self.token, self.source = port, token, source
                self.connected = True
                self.error = ""
                return True

        self.connected = False
        self.error = (
            "客户端进程在运行，但拿不到可用的 LCU 凭据；请确认已登录进大厅"
            if candidates else "未检测到已登录的 League 客户端"
        )
        return False

    def _auth(self):
        return "Basic " + base64.b64encode(("riot:" + self.token).encode()).decode()

    def _lcu(self, path, timeout=3):
        if not self.probe():
            raise RuntimeError(self.error)
        url = "https://127.0.0.1:%d%s" % (self.port, path)
        req = urllib.request.Request(url, headers={"Authorization": self._auth(), "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def _live(self, path, timeout=2):
        req = urllib.request.Request("https://127.0.0.1:2999" + path)
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    # ---------- 状态 ----------
    def gameflow_phase(self):
        try:
            p = self._lcu("/lol-gameflow/v1/gameflow-phase")
            self.phase = p if isinstance(p, str) else "None"
        except Exception as exc:
            self.phase = "None"
            self.error = str(exc)[:120]
        return self.phase

    def champ_select(self):
        """返回 {'bench': [championId...], 'myChampion': id, 'team': [...], 'actions': ...}"""
        try:
            s = self._lcu("/lol-champ-select/v1/session")
        except Exception:
            return None
        if not isinstance(s, dict):
            return None
        bench = [int(c.get("championId", 0)) for c in (s.get("benchChampions") or []) if c.get("championId")]
        my_cell = s.get("localPlayerCellId")
        my_champ = 0
        team = []
        for p in s.get("myTeam") or []:
            cid = int(p.get("championId", 0) or 0)
            if cid:
                team.append({"cellId": p.get("cellId"), "championId": cid, "self": p.get("cellId") == my_cell})
            if p.get("cellId") == my_cell:
                my_champ = cid
        return {"bench": bench, "myChampion": my_champ, "team": team, "raw": s}

    def live_game(self):
        """返回 {'me': {...}, 'allies': [...], 'enemies': [...], 'gameTime': s}"""
        try:
            d = self._live("/liveclientdata/allgamedata")
        except Exception:
            return None
        if not isinstance(d, dict) or not d.get("allPlayers"):
            return None
        active = d.get("activePlayer") or {}
        my_name = str(active.get("summonerName") or "")
        players = []
        for p in d.get("allPlayers", []):
            players.append(
                {
                    "summonerName": p.get("summonerName", ""),
                    "championName": p.get("championName", ""),
                    "team": p.get("team", ""),
                    "level": p.get("level", 0),
                    "items": [i.get("itemID", 0) for i in (p.get("items") or [])],
                    "self": p.get("summonerName") == my_name,
                }
            )
        me = next((p for p in players if p["self"]), None)
        my_team = me.get("team") if me else ""
        return {
            "me": me,
            "allies": [p for p in players if p["team"] == my_team],
            "enemies": [p for p in players if p["team"] != my_team],
            "gameTime": d.get("gameData", {}).get("gameTime", 0),
            "gameMode": d.get("gameData", {}).get("gameMode", ""),
        }
