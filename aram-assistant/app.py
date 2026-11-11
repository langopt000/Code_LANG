# -*- coding: utf-8 -*-
"""ARAM 海克斯大乱斗实时助手 —— 本地服务入口。

启动：python app.py   （或双击 start.bat）
控制台：http://127.0.0.1:8765/
"""

import json
import os
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 由 pythonw.exe（无控制台）启动时 stdout 为 None，避免 print 报错
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from datafeed import AramData  # noqa: E402
from engine import Engine  # noqa: E402
from league import LeagueClient  # noqa: E402
from vision import detect as ocr_detect, detect_shop, detect_chat, parse_chat_picks, normalize  # noqa: E402
from window import OverlayWindow, COLLAPSED_W, COLLAPSED_H, is_game_foreground  # noqa: E402

PORT = 8765
CONFIG_FILE = ROOT / "data" / "config.json"
UI_DIR = ROOT / "ui"
UI_FILE = UI_DIR / "index.html"
SERVER = None


def ui_ver():
    """页面版本号（index.html 修改时间）：浮窗轮询发现版本变了就自动 reload 到新 UI。"""
    try:
        return str(int(UI_FILE.stat().st_mtime))
    except Exception:
        return "0"


def offer_from(cands):
    """同一帧命中 2~3 个不同海克斯、且每个都是短行近精确匹配（>=0.9）才算三选一。
    真实卡面是 3 张大字卡；计分板/聊天里的名字要么只有一个、要么一大片，
    模糊高分也不采纳，避免把已选海克斯展示误判成新的三选一。"""
    strong = [c for c in cands if (c.get("ocrScore") or 0) >= 0.9]
    return strong[:3] if 2 <= len(strong) <= 3 else []

DEFAULT_CONFIG = {
    "dataset": "high",
    "ocrEnabled": True,
    "ocrInterval": 3.5,
    "region": [0.18, 0.14, 0.58, 0.60],  # 只框屏幕中央卡牌，避开右侧浮窗
    "opacity": 0.92,
    "clickThrough": False,
    "theme": "dark",
    "autoOpenBrowser": True,
    "autoOverlay": True,        # 进入英雄选择/对局时自动弹出浮窗
    "autoCloseOverlay": False,  # 默认不自动关，避免 LCU 抖动导致"弹一下就没了"
    "smartShow": True,          # 对局中只在商店打开/海克斯三选一时展开，其余时间收成胶囊
    "shopRegion": [0.0, 0.02, 0.72, 0.18],  # 商店顶部标签/搜索栏（屏幕比例），用于判断商店是否打开
    "shopInterval": 2.0,        # 商店状态识别间隔（秒）
    "shopGrace": 4.5,           # 商店关闭后保持展开的宽限，避免识别抖动
    "offerGrace": 10.0,         # 海克斯卡面消失后保持展开的宽限
    "autoRecord": True,         # 聊天广播「已选择了X」自动记入已选海克斯
    "chatRegion": [0.0, 0.55, 0.32, 0.22],  # 左下聊天区（屏幕比例）
    "chatInterval": 3.0,        # 聊天识别间隔（秒）
    "overlayPosition": "right",  # right / left / center
    "overlayWidth": 400,
    "overlayHeight": 820,
    "overlayCollapsed": True,   # 默认收成小胶囊条，点击才展开
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


class Assistant:
    def __init__(self):
        self.cfg = load_config()
        self.data = AramData(dataset=self.cfg.get("dataset", "high"))
        self.league = LeagueClient()
        self.engine = None
        self.overlay = OverlayWindow()
        self.lock = threading.RLock()
        self.logs = []
        self.error = ""
        self.ready = False

        self.phase = "None"
        self.live = None
        self.champ_select = None
        self.detected_champion = ""
        self.manual_champion = ""
        self.archetype_override = ""   # 手动指定的出装流派 key，空=自动
        self.selected = []           # 已选海克斯 id
        self.selected_source = {}    # id -> auto / manual，浮窗标来源
        self._chat_done = set()      # 已处理过的聊天广播行，手动删除后不会被旧广播加回
        self.stage_override = 0      # 0 = 自动（按已选数量推断第几次海克斯）
        self.candidates = []         # OCR 识别到的三选一
        self.ocr_text = ""
        self.ocr_error = ""
        self.ocr_busy = False
        self.last_ocr = 0
        self.last_card_seen = 0
        self.shop_open = False
        self.shop_hit = ""
        self.shop_text = ""
        self.shop_error = ""
        self.shop_epoch = 0          # 每次"关闭 -> 打开"加一，用来区分不同一次逛商店
        self.last_shop_seen = 0
        self.last_shop_ocr = 0
        self._shop_miss = 0
        self.chat_text = ""
        self.chat_error = ""
        self.last_chat_ocr = 0
        self._smart_token = None     # 当前决策标识（海克斯卡面 / 第几次商店），变了才动窗口
        self._smart_collapse = None  # 智能模式下的临时折叠态；None 表示跟随用户配置
        self.build_cache = {}
        self.build_cache_key = None
        self.build = {}
        self.started_at = time.time()
        self._overlay_busy = False
        self._auto_overlay_disabled = False
        self._none_since = 0.0

    # ---------- 工具 ----------
    def log(self, msg):
        with self.lock:
            self.logs.append({"t": time.strftime("%H:%M:%S"), "msg": str(msg)[:220]})
            self.logs = self.logs[-40:]

    def init_data(self):
        try:
            self.log("正在同步 ARAMKit 数据 v%s ..." % self.data.version)
            self.engine = Engine(self.data)
            self.data.resource("archetypes")
            self.ready = True
            self.error = ""
            self.log("数据就绪：v%s / %s / %s 场样本" % (
                self.data.version,
                "高分段" if self.data.dataset == "high" else "全分段",
                format(self.data.sample_size, ","),
            ))
        except Exception as exc:
            self.error = "数据同步失败：%s" % str(exc)[:180]
            self.log(self.error)

    def champion_id(self):
        if self.manual_champion:
            return str(self.manual_champion)
        return str(self.detected_champion or "")

    def champion_name(self):
        cid = self.champion_id()
        if not cid or not self.engine:
            return ""
        c = self.engine.champion_card(cid)
        return (c or {}).get("name", "")

    def current_stage(self):
        """第几次海克斯：手动指定优先，否则按已选数量推断（1~4）。"""
        if self.stage_override:
            return max(1, min(4, int(self.stage_override)))
        return max(1, min(4, len(self.selected) + 1))

    # ---------- 后台轮询 ----------
    def poll_loop(self):
        self.init_data()
        while True:
            try:
                self.poll_once()
            except Exception as exc:
                self.log("轮询异常：%s" % str(exc)[:120])
            time.sleep(1.2)

    GAME_PHASES = ("ChampSelect", "InProgress", "GameStart", "Reconnect")

    def on_phase_change(self, previous, current):
        """客户端一进入选人/对局就自动弹浮窗；空闲只做长宽限期判断，避免抖动误关。"""
        if current != "None":
            self._none_since = 0.0
        if current in self.GAME_PHASES:
            self._smart_token = None  # 阶段切换后重新判断该展开还是收胶囊
        if current == "None":
            self._auto_overlay_disabled = False  # 新一局重新允许自动弹出
            if not self._none_since:
                self._none_since = time.time()
            # LCU 探测偶发失败会让 phase 瞬间变 None，不能立刻关窗或清识别状态
            idle_ok = (
                self.cfg.get("autoCloseOverlay", False)
                and time.time() - self._none_since > 15
                and self.overlay.is_alive()
            )
            if idle_ok:
                with self.lock:
                    self.shop_open = False
                    self.shop_hit = ""
                    self._shop_miss = 0
                    self.candidates = []
                self._smart_token = None
                self._smart_collapse = None
                threading.Thread(target=self.overlay.close, daemon=True).start()
        if not self.cfg.get("autoOverlay", True) or self._auto_overlay_disabled:
            return
        if current in self.GAME_PHASES and not self.overlay.is_alive():
            threading.Thread(target=self._open_overlay_guarded, daemon=True).start()

    def _open_overlay_guarded(self):
        """单飞：自动弹出 / 手动 launch / setCollapsed 共用，杜绝并发开出多个窗口。"""
        with self.lock:
            if self._overlay_busy:
                return True
            self._overlay_busy = True
        try:
            return self.open_overlay_sync()
        finally:
            with self.lock:
                self._overlay_busy = False

    def open_overlay_sync(self):
        """同步打开并返回是否成功（用于一键唤起时给明确反馈）。"""
        if self.overlay.is_alive():
            self.overlay.keep_on_top()
            return True
        collapsed = bool(self.cfg.get("overlayCollapsed", True))
        if self.cfg.get("smartShow", True) and self.phase == "ChampSelect":
            collapsed = False  # 选人就是要看备选席排行，别先弹个胶囊再展开
        if collapsed:
            width, height = COLLAPSED_W, COLLAPSED_H
        else:
            width = int(self.cfg.get("overlayWidth", 400))
            height = int(self.cfg.get("overlayHeight", 820))
        ok = self.overlay.launch(
            "http://127.0.0.1:%d/?overlay=1" % PORT,
            width=width, height=height,
            position=self.cfg.get("overlayPosition", "right"),
        )
        if ok:
            self.overlay.set_opacity(self.cfg.get("opacity", 0.92))
            self.overlay.set_click_through(bool(self.cfg.get("clickThrough")))
            self.overlay.keep_on_top()
            self.log("已弹出浮窗 · %s · 位置 %s · 矩形 %s" % (
                "收起态" if collapsed else "展开态",
                self.cfg.get("overlayPosition", "right"), self.overlay.bounds()))
        else:
            self.log(self.overlay.error)
            # 失败时确保下次点击能重新创建窗口，而不是被"已在运行"挡住
            self.overlay.close()
        return ok

    def poll_once(self):
        phase = self.league.gameflow_phase()
        if phase != self.phase:
            previous = self.phase
            self.phase = phase
            self.log("客户端状态：%s" % phase)
            self.on_phase_change(previous, phase)
        cs = None
        live = None
        if phase == "ChampSelect":
            cs = self.league.champ_select()
        if phase in ("InProgress", "GameStart", "Reconnect", "WaitingForStats", "PreEndOfGame"):
            live = self.league.live_game()
        with self.lock:
            self.champ_select = cs
            self.live = live

        # 解析当前英雄
        cid = ""
        if cs and cs.get("myChampion"):
            cid = str(cs["myChampion"])
        elif live and live.get("me"):
            me = live["me"]
            if self.engine:
                c = self.engine.champion_by_name(me.get("championName", ""))
                cid = c["id"] if c else ""
        if cid and cid != self.detected_champion:
            self.detected_champion = cid
            with self.lock:
                self.selected = []
                self.selected_source = {}
                self._chat_done = set()
                self.candidates = []
                self.build_cache_key = None
            self.log("识别到英雄：%s" % self.champion_name())

        self.refresh_state()
        self._apply_smart_visibility()
        # 全屏游戏会不断把自己提到 topmost，浮窗必须周期性重新置顶
        if self.overlay.hwnd:
            self.overlay.keep_on_top()

    def topmost_loop(self):
        """全屏游戏每帧都可能抢占顶层，浮窗需要持续重新置顶。"""
        while True:
            try:
                if self.overlay.hwnd:
                    self.overlay.keep_on_top()
            except Exception:
                pass
            time.sleep(0.3)

    def ocr_loop(self):
        while True:
            time.sleep(0.5)
            try:
                if not self.ready or self.ocr_busy:
                    continue
                aug_on = bool(self.cfg.get("ocrEnabled", True))
                smart_on = bool(self.cfg.get("smartShow", True))
                chat_on = bool(self.cfg.get("autoRecord", True))
                if not aug_on and not smart_on and not chat_on:
                    continue
                if self.phase not in ("InProgress", "GameStart", "Reconnect"):
                    continue
                if not is_game_foreground():
                    continue  # 只认游戏画面；切出去看网页/视频时不识别，也避免误读
                now = time.time()
                aug_due = aug_on and now - self.last_ocr >= float(self.cfg.get("ocrInterval", 3.5))
                shop_due = smart_on and now - self.last_shop_ocr >= float(self.cfg.get("shopInterval", 2.0))
                chat_due = chat_on and now - self.last_chat_ocr >= float(self.cfg.get("chatInterval", 3.0))
                if not aug_due and not shop_due and not chat_due:
                    continue
                self.ocr_busy = True
                try:
                    if aug_due:
                        self._run_augment_ocr()
                    if shop_due:
                        self._run_shop_ocr()
                    if chat_due:
                        self._run_chat_ocr()
                finally:
                    self.ocr_busy = False
                self._apply_smart_visibility()
            except Exception as exc:
                with self.lock:
                    self.ocr_error = str(exc)[:140]
                self.ocr_busy = False

    def _run_augment_ocr(self):
        if not is_game_foreground():
            return
        self.last_ocr = time.time()
        cands, text, err = ocr_detect(
            self.cfg.get("region", DEFAULT_CONFIG["region"]),
            self.engine.augments,
        )
        offer = offer_from(cands)
        with self.lock:
            self.ocr_text = text[:500] if text else ""
            self.ocr_error = err
            if offer:
                self.candidates = offer
                self.last_card_seen = time.time()
            elif time.time() - self.last_card_seen > 25:
                self.candidates = []
        if offer:
            self.log("识别到海克斯：%s" % " / ".join(x["name"] for x in offer))

    def _run_shop_ocr(self):
        if not is_game_foreground():
            return
        self.last_shop_ocr = time.time()
        hit, text, err = detect_shop(self.cfg.get("shopRegion", DEFAULT_CONFIG["shopRegion"]))
        with self.lock:
            self.shop_text = text[:400] if text else ""
            self.shop_error = err
            was_open = self.shop_open
            if hit:
                self._shop_miss = 0
                if not self.shop_open:
                    self.shop_epoch += 1
                self.shop_open = True
                self.shop_hit = hit
                self.last_shop_seen = time.time()
            elif not err:
                # 偶发一帧识别不到（动画/遮挡）不算关闭，连续两帧才算
                self._shop_miss += 1
                if self.shop_open and self._shop_miss >= 2:
                    self.shop_open = False
                    self.shop_hit = ""
        if hit and not was_open:
            self.log("检测到商店已打开（命中「%s」）" % hit)
        elif was_open and not self.shop_open:
            self.log("商店已关闭")

    def _run_chat_ocr(self, out_png=None):
        """读聊天里的「已选择了X」广播，把本人的选取自动记入已选。返回 (picks, text, err)。"""
        if not is_game_foreground():
            return [], "", "游戏窗口不在前台"
        self.last_chat_ocr = time.time()
        text, err = detect_chat(
            self.cfg.get("chatRegion", DEFAULT_CONFIG["chatRegion"]), out_png=out_png
        )
        with self.lock:
            self.chat_text = text[:400] if text else ""
            self.chat_error = err
        if err or not text or not self.engine:
            return [], text, err
        me = (self.live or {}).get("me") or {}
        who = [me.get("summonerName", "")]
        card = self.engine.champion_card(self.champion_id())
        if card:
            who += [card.get("name", ""), card.get("title", "")]
        picks = parse_chat_picks(text, self.engine.augments, who)
        added = []
        with self.lock:
            for p in picks:
                key = normalize(p["line"])
                if key in self._chat_done:
                    continue
                self._chat_done.add(key)
                if p["id"] not in self.selected:
                    self.selected.append(p["id"])
                    self.selected_source[p["id"]] = "auto"
                    added.append(p["name"])
            if added:
                self.build_cache_key = None
        for name in added:
            self.log("自动记录已选海克斯：%s（聊天广播）" % name)
        return picks, text, err

    def _effective_collapsed(self):
        if self._smart_collapse is not None:
            return bool(self._smart_collapse)
        return bool(self.cfg.get("overlayCollapsed", True))

    def _decision_token(self):
        """当前是否处于决策时刻：英雄选择 / 海克斯三选一 / 商店打开。返回标识，空串表示无。"""
        if self.phase == "ChampSelect":
            return "bench"
        now = time.time()
        offer = bool(self.candidates) and now - self.last_card_seen <= float(self.cfg.get("offerGrace", 10))
        shop = self.shop_open and now - self.last_shop_seen <= float(self.cfg.get("shopGrace", 4.5))
        if offer:
            return "offer:" + ",".join(str(a.get("id")) for a in self.candidates[:3])
        if shop:
            return "shop:%d" % self.shop_epoch
        return ""

    def _apply_smart_visibility(self):
        """智能模式：有决策就展开，没有就收成小胶囊。只在决策切换时动窗口，避免抖动。"""
        if not self.cfg.get("smartShow", True):
            return
        if not self.cfg.get("autoOverlay", True) or self._auto_overlay_disabled:
            return
        if self.phase not in ("ChampSelect", "InProgress", "GameStart", "Reconnect"):
            return
        if not is_game_foreground():
            return  # 游戏不在前台时保持现状：点浮窗/切窗口不会把面板收走
        if not self.overlay.is_alive():
            self._smart_token = None
            return
        token = self._decision_token()
        if token == self._smart_token:
            return
        self._smart_token = token
        collapsed = not token
        previous = self._smart_collapse
        self._smart_collapse = collapsed
        self.overlay.set_collapsed(
            collapsed,
            int(self.cfg.get("overlayWidth", 400)),
            int(self.cfg.get("overlayHeight", 820)),
        )
        if previous is not None and previous != collapsed:
            if token:
                label = {"bench": "英雄选择", "offer": "海克斯三选一", "shop": "商店"}[token.split(":")[0]]
                self.log("进入决策时刻：展开浮窗（%s）" % label)
            else:
                self.log("离开商店/选择：浮窗收成小胶囊")

    def force_ocr(self):
        if not self.ready:
            return {"ok": False, "error": "数据尚未就绪"}
        if self.ocr_busy:
            return {"ok": False, "error": "正在识别中"}
        self.ocr_busy = True
        try:
            cands, text, err = ocr_detect(
                self.cfg.get("region", DEFAULT_CONFIG["region"]), self.engine.augments, keep_png=True
            )
            offer = offer_from(cands)
            with self.lock:
                self.ocr_text = text[:500] if text else ""
                self.ocr_error = err
                if offer:
                    self.candidates = offer
                    self.last_card_seen = time.time()
            if offer:
                self.log("手动识别：%s" % " / ".join(x["name"] for x in offer))
            return {"ok": True, "candidates": cands, "accepted": offer, "text": text, "error": err}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:160]}
        finally:
            self.ocr_busy = False

    # ---------- 组装状态 ----------
    def refresh_state(self):
        if not self.ready:
            return
        cid = self.champion_id()
        live = self.live or {}
        my_items = tuple((live.get("me") or {}).get("items") or [])
        enemy_sig = tuple(
            (p.get("championName", ""), tuple(p.get("items") or [])) for p in (live.get("enemies") or [])
        )
        key = (cid, tuple(self.selected), self.data.dataset, self.data.version, self.current_stage(), my_items, enemy_sig, self.archetype_override)
        if key != self.build_cache_key:
            try:
                enemies = live.get("enemies") or []
                self.build = self.engine.build(cid, self.selected, enemies, my_items, archetype=self.archetype_override)
                self.build_cache_key = key
            except Exception as exc:
                self.build = {"steps": [], "order": [], "notes": ["出装计算失败：%s" % str(exc)[:100]], "source": ""}
                self.log("出装计算异常：%s" % str(exc)[:120])

    def state(self):
        with self.lock:
            cs = self.champ_select or {}
            live = self.live or {}
            bench_ids = cs.get("bench", [])
            my_id = cs.get("myChampion", 0)
            cid = self.champion_id()
            champions = []
            cards = []
            build = dict(self.build) if self.build else {}
            selected = list(self.selected)
            selected_source = dict(self.selected_source)
            candidates = list(self.candidates)
            phase = self.phase
            logs = list(self.logs[-12:])
            ocr_text = self.ocr_text
            ocr_error = self.ocr_error
        token = self._decision_token()

        result = {
            "ok": True,
            "ready": self.ready,
            "error": self.error,
            "serverTime": time.strftime("%H:%M:%S"),
            "version": self.data.version if self.ready else "",
            "dataDate": self.data.data_date if self.ready else "",
            "sampleSize": self.data.sample_size if self.ready else 0,
            "dataset": self.data.dataset,
            "phase": phase,
            "clientConnected": self.league.connected,
            "clientError": self.league.error,
            "gameTime": round(live.get("gameTime", 0), 0),
            "championId": cid,
            "championName": self.champion_name(),
            "myChampionId": my_id,
            "bench": [],
            "allies": live.get("allies", []),
            "enemies": live.get("enemies", []),
            "candidates": [],
            "selectedAugments": [],
            "build": build,
            "archetypeOverride": self.archetype_override,
            "ocrText": ocr_text,
            "ocrError": ocr_error,
            "ocrEnabled": bool(self.cfg.get("ocrEnabled", True)),
            "region": self.cfg.get("region", DEFAULT_CONFIG["region"]),
            "opacity": self.cfg.get("opacity", 0.96),
            "clickThrough": bool(self.cfg.get("clickThrough", False)),
            "autoOverlay": bool(self.cfg.get("autoOverlay", True)),
            "theme": self.cfg.get("theme", "dark"),
            "overlayActive": self.overlay.is_alive(),
            "overlayBounds": self.overlay.bounds(),
            "overlayPosition": self.cfg.get("overlayPosition", "right"),
            "overlayCollapsed": self._effective_collapsed(),
            "overlayCollapsedPref": bool(self.cfg.get("overlayCollapsed", True)),
            "overlayError": self.overlay.error,
            "smartShow": bool(self.cfg.get("smartShow", True)),
            "shopOpen": self.shop_open,
            "shopHit": self.shop_hit,
            "shopText": self.shop_text,
            "shopError": self.shop_error,
            "shopRegion": self.cfg.get("shopRegion", DEFAULT_CONFIG["shopRegion"]),
            "autoRecord": bool(self.cfg.get("autoRecord", True)),
            "chatText": self.chat_text,
            "chatError": self.chat_error,
            "chatRegion": self.cfg.get("chatRegion", DEFAULT_CONFIG["chatRegion"]),
            "decision": (
                "bench" if token == "bench" else
                ("offer" if token.startswith("offer") else ("shop" if token else ""))
            ),
            "uiVer": ui_ver(),
            "logs": logs,
            "config": self.cfg,
            "manualMode": bool(self.manual_champion),
            "stage": self.current_stage(),
            "stageMode": int(self.stage_override or 0),
        }
        if not self.ready:
            return result
        E = self.engine
        result["bench"] = [E.champion_card(i) for i in bench_ids if E.champion_card(i)]
        result["bench"].sort(key=lambda c: -(c.get("winRate") or 0))
        if cid:
            c = E.champion_card(cid)
            if c:
                result["champion"] = c
        result["candidates"] = [
            E.augment_card(a["id"], cid, stage=self.current_stage(), selected=selected)
            | {"ocrScore": a.get("ocrScore", 0)}
            for a in candidates
        ]
        result["selectedAugments"] = [
            E.augment_card(a, cid, selected=selected) | {"auto": selected_source.get(str(a)) == "auto"}
            for a in selected
        ]
        result["topChampions"] = E.champions[:12]
        result["topAugments"] = E.augments[:12]
        return result

    # ---------- 自检 ----------
    def diagnose(self):
        import socket
        try:
            from watcher import client_running
        except Exception:
            client_running = lambda: False

        def busy(port):
            s = socket.socket()
            try:
                s.bind(("127.0.0.1", port))
                return False
            except OSError:
                return True
            finally:
                s.close()

        try:
            client_proc = client_running()
        except Exception:
            client_proc = False
        try:
            self.league.probe(force=True)
            lcu_ok = self.league.connected
            lcu_phase = self.league.gameflow_phase() if lcu_ok else "None"
            lcu_port = self.league.port
            lcu_source = self.league.source
        except Exception as exc:
            lcu_ok, lcu_phase, lcu_port, lcu_source = False, "None", None, str(exc)[:80]
        try:
            live = self.league.live_game()
            live_ok = bool(live)
        except Exception:
            live_ok = False

        checks = [
            {
                "key": "watcher", "label": "开机守护进程", "ok": busy(8766),
                "detail": "127.0.0.1:8766 已占用" if busy(8766) else "未运行；双击 setup_autostart.bat 启动",
            },
            {
                "key": "service", "label": "助手本地服务", "ok": busy(8765),
                "detail": "http://127.0.0.1:8765/ 可访问" if busy(8765) else "未运行",
            },
            {
                "key": "process", "label": "英雄联盟客户端进程", "ok": client_proc,
                "detail": "已检测到 LeagueClientUx.exe" if client_proc else "未检测到，请先启动并登录客户端",
            },
            {
                "key": "lcu", "label": "客户端数据接口 (LCU)", "ok": lcu_ok,
                "detail": ("已连接 · 端口 %s · 凭据来源 %s · 阶段 %s" % (lcu_port, lcu_source, lcu_phase))
                if lcu_ok else (self.league.error or "凭据获取失败"),
            },
            {
                "key": "live", "label": "对局内数据 (Live Client Data)", "ok": live_ok,
                "detail": "正在对局，可读取英雄与阵容" if live_ok else "当前不在对局中（选人阶段也正常）",
            },
            {
                "key": "overlay", "label": "置顶浮窗", "ok": self.overlay.is_alive(),
                "detail": "浮窗已打开" if self.overlay.is_alive() else ("未打开；进入选人/对局会自动弹出，或点下方按钮测试"
                                                          if lcu_phase in self.GAME_PHASES else "未打开；当前在大厅"),
            },
        ]
        return {
            "ok": True,
            "ready": all(c["ok"] for c in checks[:4]),
            "phase": lcu_phase,
            "autoOverlay": bool(self.cfg.get("autoOverlay", True)),
            "checks": checks,
            "hint": (
                "当前阶段 %s：%s" % (
                    lcu_phase,
                    "应该自动弹浮窗了；若没看到，点「立即弹出浮窗」。" if lcu_phase in self.GAME_PHASES
                    else "大厅阶段按设计不弹浮窗，排队进选人后才会出现。",
                )
            ),
        }

    # ---------- 操作 ----------
    def control(self, body):
        action = body.get("action", "")
        value = body.get("value")
        if action == "setChampion":
            self.manual_champion = str(value or "")
            self.build_cache_key = None
            self.log("手动设定英雄：%s" % (self.champion_name() or "已清除"))
        elif action == "clearManual":
            self.manual_champion = ""
            self.build_cache_key = None
            self.log("恢复自动识别英雄")
        elif action == "addAugment":
            aid = str(value)
            if aid and aid not in self.selected:
                self.selected.append(aid)
                self.selected_source[aid] = "manual"
                self.build_cache_key = None
                self.log("已选海克斯：%s" % (self.engine.aug_by_id.get(aid, {}).get("name", aid) if self.engine else aid))
        elif action == "removeAugment":
            aid = str(value)
            if aid in self.selected:
                self.selected.remove(aid)
                self.selected_source.pop(aid, None)
                self.build_cache_key = None
        elif action == "clearAugments":
            self.selected = []
            self.selected_source = {}
            self.build_cache_key = None
        elif action == "setAutoRecord":
            self.cfg["autoRecord"] = bool(value)
            save_config(self.cfg)
            self.log("自动记录已选海克斯（聊天识别）：%s" % ("开启" if self.cfg["autoRecord"] else "关闭"))
        elif action == "setChatRegion":
            if isinstance(value, list) and len(value) == 4:
                self.cfg["chatRegion"] = [float(v) for v in value]
                save_config(self.cfg)
                self.log("已更新聊天识别区域")
        elif action == "testChat":
            if not self.ready:
                return {"ok": False, "error": "数据尚未就绪"}
            if not is_game_foreground():
                return {"ok": False, "error": "游戏窗口不在前台：请先切回游戏（聊天里有选取广播时）再点测试"}
            picks, text, err = self._run_chat_ocr(out_png=str(ROOT / "data" / "_chat_test.png"))
            return {"ok": True, "picks": picks, "text": text[:500], "error": err}
        elif action == "setDataset":
            if value in ("all", "high"):
                self.cfg["dataset"] = value
                self.data.set_dataset(value)
                self.build_cache_key = None
                save_config(self.cfg)
                self.log("切换数据集：%s" % ("高分段" if value == "high" else "全分段"))
        elif action == "setStage":
            try:
                self.stage_override = max(0, min(4, int(value or 0)))
            except Exception:
                self.stage_override = 0
            self.build_cache_key = None
            self.log("海克斯阶段：%s" % ("自动（第 %d 次）" % self.current_stage() if not self.stage_override else "第 %d 次" % self.stage_override))
        elif action == "setArchetype":
            self.archetype_override = str(value or "")
            self.build_cache_key = None
            name = self.engine.data.archetype_name(self.archetype_override) if self.engine and self.archetype_override else ""
            self.log("出装流派：%s" % (name or self.archetype_override or "自动"))
        elif action == "setOcr":
            self.cfg["ocrEnabled"] = bool(value)
            save_config(self.cfg)
        elif action == "setSmartShow":
            self.cfg["smartShow"] = bool(value)
            self._smart_token = None
            self._smart_collapse = None
            save_config(self.cfg)
            self.log("智能模式（只在商店/海克斯时展开）：%s" % ("开启" if self.cfg["smartShow"] else "关闭"))
        elif action == "setShopRegion":
            if isinstance(value, list) and len(value) == 4:
                self.cfg["shopRegion"] = [float(v) for v in value]
                save_config(self.cfg)
                self.log("已更新商店识别区域")
        elif action == "testShop":
            if not is_game_foreground():
                return {"ok": False, "error": "游戏窗口不在前台：请先切回游戏、打开商店，再点测试"}
            hit, text, err = detect_shop(
                self.cfg.get("shopRegion", DEFAULT_CONFIG["shopRegion"]),
                out_png=str(ROOT / "data" / "_shop_test.png"),
            )
            with self.lock:
                self.shop_text = text[:400] if text else ""
                self.shop_error = err
                if hit:
                    self._shop_miss = 0
                    if not self.shop_open:
                        self.shop_epoch += 1
                    self.shop_open = True
                    self.shop_hit = hit
                    self.last_shop_seen = time.time()
                elif not err:
                    self.shop_open = False
                    self.shop_hit = ""
                    self._shop_miss = 0
            self._apply_smart_visibility()
            return {"ok": True, "open": bool(hit), "hit": hit, "text": text[:500], "error": err}
        elif action == "setRegion":
            if isinstance(value, list) and len(value) == 4:
                self.cfg["region"] = [float(v) for v in value]
                save_config(self.cfg)
                self.log("已更新识别区域")
        elif action == "setTheme":
            self.cfg["theme"] = "light" if value == "light" else "dark"
            save_config(self.cfg)
        elif action == "setOpacity":
            self.cfg["opacity"] = float(value)
            self.overlay.set_opacity(value)
            save_config(self.cfg)
        elif action == "setClickThrough":
            self.cfg["clickThrough"] = bool(value)
            self.overlay.set_click_through(bool(value))
            save_config(self.cfg)
        elif action == "setAutoOverlay":
            self.cfg["autoOverlay"] = bool(value)
            self._auto_overlay_disabled = False
            self._smart_token = None
            save_config(self.cfg)
            self.log("随对局自动弹浮窗：%s" % ("开启" if self.cfg["autoOverlay"] else "关闭"))
        elif action == "setOverlayPosition":
            if value in ("left", "center", "right"):
                self.cfg["overlayPosition"] = value
                if self.overlay.is_alive():
                    self.overlay.set_collapsed(
                        self._effective_collapsed(),
                        int(self.cfg.get("overlayWidth", 400)),
                        int(self.cfg.get("overlayHeight", 820)),
                    )
                    self.overlay.place(value)
                save_config(self.cfg)
                self.log("浮窗位置：%s" % value)
        elif action == "setCollapsed":
            collapsed = bool(value)
            self.cfg["overlayCollapsed"] = collapsed
            if self.cfg.get("smartShow", True) and self.phase in ("ChampSelect", "InProgress", "GameStart", "Reconnect"):
                # 用户手动收起/展开优先于智能模式，本决策时刻内不再自动改回去
                self._smart_collapse = collapsed
                self._smart_token = self._decision_token()
            save_config(self.cfg)
            if self.overlay.is_alive():
                self.overlay.set_collapsed(
                    collapsed,
                    int(self.cfg.get("overlayWidth", 400)),
                    int(self.cfg.get("overlayHeight", 820)),
                )
                self.overlay.keep_on_top()
            else:
                # 窗口不在：后台单飞打开（launch 会先收养已存在的窗口），立即返回不阻塞 UI
                threading.Thread(target=self._open_overlay_guarded, daemon=True).start()
            self.log("浮窗：%s" % ("收起" if collapsed else "展开"))
            self.refresh_state()
            return {"ok": True, "collapsed": collapsed}
        elif action in ("launchOverlay", "testOverlay"):
            if action == "testOverlay":
                self._auto_overlay_disabled = False
                # 一键唤起脚本要同步结果；同样走单飞锁，避免和自动弹出撞车
                ok = self._open_overlay_guarded()
                if not ok:
                    return {"ok": False, "error": self.overlay.error or "浮窗启动失败"}
            else:
                # 控制台按钮：异步打开，立即返回，UI 靠轮询看到窗口出现
                threading.Thread(target=self._open_overlay_guarded, daemon=True).start()
        elif action == "redetect":
            self.league.probe(force=True)
            phase = self.league.gameflow_phase()
            self.log("重新检测客户端：%s / %s" % (
                "已连接" if self.league.connected else "未连接", phase))
            self.on_phase_change("None", phase)
        elif action == "closeOverlay":
            self.overlay.close()
            # 用户手动关闭后，本局不再反复自动弹出
            if body.get("fromUser"):
                self._auto_overlay_disabled = True
            self.log("已关闭浮窗")
        elif action == "quit":
            self.overlay.close()
            self.log("收到退出指令，服务正在关闭")
            threading.Thread(target=_shutdown_server, daemon=True).start()
        elif action == "refresh":
            self.ready = False
            self.build_cache_key = None
            threading.Thread(target=self.init_data, daemon=True).start()
            self.log("正在重新同步数据...")
        else:
            return {"ok": False, "error": "未知操作 %s" % action}
        self.refresh_state()
        return {"ok": True}


ASSISTANT = Assistant()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                html = UI_FILE.read_bytes().replace(b"__UI_VER__", ui_ver().encode("ascii"))
                return self._send(200, html, "text/html; charset=utf-8")
            if path == "/api/state":
                return self._send(200, ASSISTANT.state())
            if path == "/api/search":
                q = qs.get("q", [""])[0]
                kind = qs.get("kind", ["all"])[0]
                if not ASSISTANT.engine:
                    return self._send(200, {"ok": True, "results": []})
                return self._send(200, {"ok": True, "results": ASSISTANT.engine.search(q, kind)})
            if path == "/api/health":
                return self._send(200, {"ok": True})
            if path == "/api/diagnose":
                return self._send(200, ASSISTANT.diagnose())
            if path.startswith("/ui/"):
                f = UI_DIR / path[4:]
                if f.is_file():
                    import mimetypes

                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                        ctype += "; charset=utf-8"
                    return self._send(200, f.read_bytes(), ctype)
            return self._send(404, {"ok": False, "error": "not found"})
        except Exception as exc:
            return self._send(500, {"ok": False, "error": str(exc)[:200]})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        try:
            if parsed.path == "/api/control":
                return self._send(200, ASSISTANT.control(body))
            if parsed.path == "/api/ocr":
                return self._send(200, ASSISTANT.force_ocr())
            if parsed.path == "/api/refresh":
                ASSISTANT.control({"action": "refresh"})
                return self._send(200, {"ok": True})
            return self._send(404, {"ok": False, "error": "not found"})
        except Exception as exc:
            traceback.print_exc()
            return self._send(500, {"ok": False, "error": str(exc)[:200]})


def _shutdown_server():
    if SERVER is not None:
        SERVER.shutdown()


def main():
    global SERVER
    os.chdir(ROOT)
    url = "http://127.0.0.1:%d/" % PORT
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # 守护已经把服务拉起来了：不要抢端口，直接打开已有控制台即可
        print("助手已在运行，直接打开控制台：%s" % url)
        if not os.environ.get("ARAM_NO_BROWSER"):
            webbrowser.open(url)
        return
    SERVER = server
    threading.Thread(target=ASSISTANT.poll_loop, daemon=True).start()
    threading.Thread(target=ASSISTANT.ocr_loop, daemon=True).start()
    threading.Thread(target=ASSISTANT.topmost_loop, daemon=True).start()
    print("=" * 58)
    print("  ARAM 海克斯大乱斗实时助手")
    print("  控制台: " + url)
    print("  数据源: ARAMKit (data.aramkit.cn) 官方公开统计 JSON")
    print("  关闭: 在本窗口按 Ctrl+C")
    print("=" * 58)
    if ASSISTANT.cfg.get("autoOpenBrowser", True) and not os.environ.get("ARAM_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
