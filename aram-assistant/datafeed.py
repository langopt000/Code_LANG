# -*- coding: utf-8 -*-
"""ARAMKit 官方 JSON 数据源。

站点 aramkit.cn 的前端直接消费 data.aramkit.cn 上的静态 JSON；
这里复用同一套公开数据（无需登录、无需 key），并做本地磁盘缓存。

可用数据：
  data/versions.json                         版本清单
  {resourcePath}/{locale}/resources/*.json   中文名称 / 图标 / 稀有度
  {dataPath}/stats/{dataset}/*-rankings.json 英雄、海克斯、装备排行
  {dataPath}/stats/{dataset}/champion-details/{id}.json                英雄详情
  {dataPath}/stats/{dataset}/champion-details/{id}-single-augments.json  按单个海克斯拆分的出装
  {dataPath}/stats/{dataset}/augment-details/{id}.json                 海克斯详情
"""

import gzip
import hashlib
import json
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://data.aramkit.cn"
LOCALE = "zh-CN"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

_lock = threading.Lock()
_MEM = {}          # path -> (fetched_at, obj)  进程内解析缓存，避免每次轮询重读大 JSON
_MEM_LOCK = threading.Lock()

# 装备 categories -> 中文标签，用来说明「这件装备管什么」
CAT_CN = {
    "Armor": "护甲",
    "SpellBlock": "魔抗",
    "MagicResist": "魔抗",
    "Health": "生命",
    "Damage": "攻击",
    "SpellDamage": "法强",
    "AbilityHaste": "急速",
    "CooldownReduction": "急速",
    "AttackSpeed": "攻速",
    "CriticalStrike": "暴击",
    "LifeSteal": "吸血",
    "SpellVamp": "吸血",
    "ArmorPenetration": "穿甲",
    "MagicPenetration": "法穿",
    "Tenacity": "韧性",
    "Mana": "法力",
    "ManaRegen": "回蓝",
    "HealthRegen": "回血",
    "Boots": "鞋子",
    "OnHit": "命中",
    "Slow": "减速",
    "Aura": "光环",
    "Active": "主动",
    "Vision": "视野",
    "Consumable": "消耗品",
}


def _http(path, timeout=25):
    url = BASE + "/" + path.lstrip("/")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Encoding": "gzip",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": "https://aramkit.cn",
            "Referer": "https://aramkit.cn/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw


def _cache_path(key):
    return CACHE / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


def fetch_json(path, ttl=21600, force=False):
    """下载并缓存 JSON。ttl 单位秒；离线时返回过期缓存。"""
    path = path.lstrip("/")
    cf = _cache_path(path)
    now = time.time()
    if not force:
        with _MEM_LOCK:
            hit = _MEM.get(path)
        if hit and now - hit[0] < ttl:
            return hit[1]
    with _lock:
        if not force and cf.exists():
            age = now - cf.stat().st_mtime
            if age < ttl:
                try:
                    data = json.loads(cf.read_text(encoding="utf-8"))
                    with _MEM_LOCK:
                        _MEM[path] = (now, data)
                    return data
                except Exception:
                    pass
        last_error = None
        for attempt in range(3):
            try:
                raw = _http(path, timeout=25 if attempt == 0 else 15)
                data = json.loads(raw)
                tmp = cf.with_suffix(".tmp")
                tmp.write_bytes(raw)
                tmp.replace(cf)
                with _MEM_LOCK:
                    _MEM[path] = (now, data)
                return data
            except Exception as exc:  # 网络抖动 / TLS / 限流
                last_error = exc
                time.sleep(0.6 * (attempt + 1))
        if cf.exists():
            try:
                data = json.loads(cf.read_text(encoding="utf-8"))
                # 断网时也放进内存：否则每 1.2s 轮询都会重试网络
                with _MEM_LOCK:
                    _MEM[path] = (now, data)
                return data
            except Exception:
                pass
        raise RuntimeError("无法获取 %s: %s" % (path, last_error))


def _tooltip_text(meta):
    """把 tooltip 的富文本 blocks 拍平成纯文本（保留原文，不翻译）。"""
    parts = []
    for block in (meta.get("tooltip") or {}).get("blocks") or []:
        line = "".join(str(s.get("text", "")) for s in (block.get("spans") or []))
        line = line.strip()
        if line:
            parts.append(line)
    return " ".join(parts)


class AramData:
    def __init__(self, dataset="high", locale=LOCALE):
        self.dataset = dataset if dataset in ("all", "high") else "high"
        self.locale = locale
        self._versions = None
        self._version_row = None
        self._res = {}
        self._detail = {}
        self._single = {}
        self._aug_detail = {}

    # ---------- 版本 ----------
    @property
    def versions(self):
        if self._versions is None:
            self._versions = fetch_json("data/versions.json", ttl=43200)
        return self._versions

    @property
    def version_row(self):
        if self._version_row is None:
            latest = self.versions.get("latest")
            rows = self.versions.get("versions", [])
            self._version_row = next(
                (v for v in rows if v.get("version") == latest), rows[0] if rows else {}
            )
        return self._version_row

    @property
    def version(self):
        return self.version_row.get("version", "?")

    @property
    def data_date(self):
        return self.version_row.get("dataDate", "")

    @property
    def sample_size(self):
        key = "highMatches" if self.dataset == "high" else "allMatches"
        return self.version_row.get(key, 0)

    def set_dataset(self, dataset):
        if dataset in ("all", "high") and dataset != self.dataset:
            self.dataset = dataset
            self._detail.clear()
            self._single.clear()
            self._aug_detail.clear()

    # ---------- 资源（名称 / 图标） ----------
    def resource(self, kind):
        if kind not in self._res:
            path = "%s/%s/resources/%s.json" % (
                self.version_row.get("resourcePath", ""),
                self.locale,
                kind,
            )
            self._res[kind] = fetch_json(path, ttl=86400)
        return self._res[kind]

    def asset_url(self, rel):
        if not rel:
            return ""
        if rel.startswith("http"):
            return rel
        return BASE + "/" + rel.lstrip("/")

    def name_of(self, kind, rid):
        row = self.resource(kind).get(str(rid))
        if not row:
            return str(rid)
        return row.get("name") or row.get("title") or str(rid)

    def icon_of(self, kind, rid):
        row = self.resource(kind).get(str(rid))
        return self.asset_url(row.get("icon")) if row else ""

    def augment_meta(self, rid):
        return self.resource("augments").get(str(rid), {})

    def champion_meta(self, rid):
        return self.resource("champions").get(str(rid), {})

    def item_meta(self, rid):
        return self.resource("items").get(str(rid), {})

    def item_text(self, rid):
        """装备说明纯文本，用于识别「重伤」这类没有 category 的效果。"""
        return _tooltip_text(self.item_meta(rid))

    def augment_text(self, rid):
        """海克斯说明纯文本，用于三选一时解释「这个到底做什么」。"""
        return _tooltip_text(self.augment_meta(rid))

    def archetype_name(self, key):
        return self.resource("archetypes").get(key, {}).get("name", key)

    # ---------- 统计数据 ----------
    def stats(self, rel, ttl=21600):
        return fetch_json(
            "%s/stats/%s/%s" % (self.version_row.get("dataPath", ""), self.dataset, rel),
            ttl=ttl,
        )

    def champion_rankings(self):
        return self.stats("champion-rankings.json").get("rows", [])

    def augment_rankings(self):
        return self.stats("augment-rankings.json")

    def item_rankings(self):
        return self.stats("item-rankings.json")

    def champion_detail(self, cid):
        cid = str(cid)
        if cid not in self._detail:
            try:
                self._detail[cid] = self.stats("champion-details/%s.json" % cid, ttl=86400)
            except Exception:
                self._detail[cid] = {}
        return self._detail[cid]

    def champion_single_augments(self, cid):
        cid = str(cid)
        if cid not in self._single:
            try:
                self._single[cid] = self.stats(
                    "champion-details/%s-single-augments.json" % cid, ttl=86400
                )
            except Exception:
                self._single[cid] = {"items": [], "builds": []}
        return self._single[cid]

    def augment_detail(self, aid):
        aid = str(aid)
        if aid not in self._aug_detail:
            try:
                self._aug_detail[aid] = self.stats("augment-details/%s.json" % aid, ttl=86400)
            except Exception:
                self._aug_detail[aid] = {}
        return self._aug_detail[aid]

    # ---------- 便于 UI 消费的索引 ----------
    def champion_index(self):
        ranks = {str(r["id"]): r for r in self.champion_rankings()}
        out = []
        for rid, meta in self.resource("champions").items():
            r = ranks.get(str(rid), {})
            out.append(
                {
                    "id": rid,
                    "name": meta.get("title") or meta.get("name") or rid,
                    "title": meta.get("name", ""),
                    "slug": meta.get("slug", ""),
                    "riotId": meta.get("riotId", ""),
                    "icon": self.asset_url(meta.get("icon")),
                    "roles": meta.get("roles", []),
                    "tier": r.get("tier", ""),
                    "rank": r.get("rank", 0),
                    "winRate": r.get("winRate", 0),
                    "pickRate": r.get("pickRate", 0),
                    "sampleCount": r.get("sampleCount", 0),
                }
            )
        out.sort(key=lambda x: (x["rank"] or 9999))
        return out

    def augment_index(self):
        ranks = {str(r["id"]): r for r in self.augment_rankings().get("all", [])}
        try:
            en = fetch_json(
                "%s/en-US/resources/augments.json" % self.version_row.get("resourcePath", ""),
                ttl=86400,
            )
        except Exception:
            en = {}
        out = []
        for rid, meta in self.resource("augments").items():
            r = ranks.get(str(rid), {})
            en_name = (en.get(str(rid)) or {}).get("name", "")
            out.append(
                {
                    "id": rid,
                    "name": meta.get("name", rid),
                    "aliases": [x for x in (en_name, meta.get("slug", "")) if x],
                    "slug": meta.get("slug", ""),
                    "icon": self.asset_url(meta.get("icon")),
                    "rarity": meta.get("rarity", ""),
                    "tier": r.get("tier", ""),
                    "rank": r.get("rank", 0),
                    "winRate": r.get("winRate", 0),
                    "pickRate": r.get("pickRate", 0),
                    "sampleCount": r.get("sampleCount", 0),
                    "availableStages": r.get("availableStages", []),
                }
            )
        out.sort(key=lambda x: (x["rank"] or 9999))
        return out

    def item_index(self):
        out = {}
        for rid, meta in self.resource("items").items():
            cats = list(meta.get("categories") or [])
            tags = []
            for c in cats:
                label = CAT_CN.get(c)
                if label and label not in tags:
                    tags.append(label)
            text = _tooltip_text(meta)
            # 「重伤」在数据里没有独立 category，只能从说明文本判断
            if "重伤" in text and "重伤" not in tags:
                tags.append("重伤")
            out[str(rid)] = {
                "id": rid,
                "name": meta.get("name", rid),
                "icon": self.asset_url(meta.get("icon")),
                "price": meta.get("priceTotal", 0),
                "cats": cats,
                "tags": tags,
                "final": bool(meta.get("isFinal")),
                "acq": meta.get("acquisition", ""),
            }
        return out
