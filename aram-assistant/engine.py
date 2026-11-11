# -*- coding: utf-8 -*-
"""推荐引擎：英雄评级、海克斯评级、结合海克斯 / 敌方阵容 / 当前装备的出装。

所有数字都来自 ARAMKit 公开统计 JSON，本模块只做加权与排序，
不臆造胜率。评分规则在 augment_card() / build() 里明确写出，便于自行调整。
"""

import math
from collections import defaultdict

TIER_BONUS = {"S": 12, "A": 6, "B": 0, "C": -7, "D": -14}
RARITY_LABEL = {"silver": "白银", "gold": "黄金", "prismatic": "棱彩"}

# 只用于「敌方大概打什么伤害」的兜底判断（优先看敌方已买装备）
DMG_ROLE = {
    "Mage": "ap",
    "Support": "ap",
    "Marksman": "ad",
    "Assassin": "ad",
    "Fighter": "ad",
    "Tank": "tank",
}

# 回复能力突出的英雄（slug）：只用于「是否考虑重伤」的经验提示，不是胜率数据
HEALER_SLUGS = {
    "aatrox", "briar", "illaoi", "milio", "mundo", "nami", "senna", "seraphine",
    "sona", "soraka", "swain", "tahmkench", "vladimir", "warwick", "yuumi", "zac",
}

HEAL_ITEM_CATS = {"LifeSteal", "SpellVamp"}


def pct(v):
    try:
        return round(float(v) * 100, 1)
    except Exception:
        return 0.0


def grade(score):
    if score >= 78:
        return "S"
    if score >= 68:
        return "A"
    if score >= 56:
        return "B"
    if score >= 44:
        return "C"
    return "D"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Engine:
    def __init__(self, data):
        self.data = data
        self.champions = data.champion_index()
        self.augments = data.augment_index()
        self.items = data.item_index()
        self.champ_by_id = {str(c["id"]): c for c in self.champions}
        self.aug_by_id = {str(a["id"]): a for a in self.augments}
        self.champ_lookup = {}
        for c in self.champions:
            for key in (c.get("riotId"), c.get("name"), c.get("title"), c.get("slug")):
                if key:
                    self.champ_lookup[str(key).lower()] = c["id"]
        self._item_text = {}
        self._combos = {}

    # ---------- 基础查询 ----------
    def champion_by_name(self, name):
        return self.champ_by_id.get(str(self.champ_lookup.get(str(name or "").lower(), "")), None)

    def champion_card(self, cid):
        return self.champ_by_id.get(str(cid))

    def augment_text(self, aid):
        return self.data.augment_text(aid)

    def _item_text_of(self, iid):
        iid = str(iid)
        if iid not in self._item_text:
            self._item_text[iid] = self.data.item_text(iid)
        return self._item_text[iid]

    def _combos_of(self, cid):
        cid = str(cid)
        if cid not in self._combos:
            self._combos[cid] = (self.data.champion_detail(cid) or {}).get("augmentCombinations") or []
        return self._combos[cid]

    def champion_baseline(self, cid):
        d = self.data.champion_detail(cid)
        return float((d.get("champion") or {}).get("stats", {}).get("winRate", 0) or 0)

    # ---------- 海克斯卡 ----------
    def _synergy(self, cid, aid, selected, baseline):
        """已选海克斯与本张卡的「双海克斯组合」实际胜率（数据里只有成对组合）。"""
        if not cid or not selected:
            return None
        best, best_ids = None, []
        for combo in self._combos_of(cid):
            ids = [str(x) for x in (combo.get("augmentIds") or [])]
            if str(aid) not in ids:
                continue
            partners = [x for x in ids if x != str(aid)]
            if not any(p in selected for p in partners):
                continue
            if best is None or float(combo.get("winRate") or 0) > float(best.get("winRate") or 0):
                best, best_ids = combo, partners
        if not best:
            return None
        wr = float(best.get("winRate") or 0)
        partner = next((p for p in best_ids if p in selected), best_ids[0] if best_ids else "")
        return {
            "winRate": wr,
            "sample": best.get("sampleCount", 0),
            "partnerId": partner,
            "partnerName": self.aug_by_id.get(partner, {}).get("name", partner),
            "delta": round((wr - baseline) * 100, 1) if baseline else 0,
        }

    def augment_card(self, aid, champion_id=None, stage=None, selected=None):
        a = dict(self.aug_by_id.get(str(aid), {"id": aid, "name": str(aid)}))
        a.setdefault("tier", "")
        a.setdefault("winRate", 0)
        a.setdefault("pickRate", 0)
        a.setdefault("sampleCount", 0)
        a.setdefault("icon", "")
        a["rarityLabel"] = RARITY_LABEL.get(a.get("rarity", ""), a.get("rarity", ""))
        a["stages"] = a.get("availableStages", [])
        a["globalWinRate"] = pct(a["winRate"])
        a["globalPickRate"] = pct(a["pickRate"])
        a["globalTier"] = a["tier"]
        a["description"] = self.augment_text(aid)[:240]
        selected = [str(s) for s in (selected or []) if str(s) != str(aid)]

        # 阶段专属排行（第 1~4 次海克斯各自的统计）
        if stage:
            try:
                rows = self.data.augment_rankings().get("stages", {}).get(str(stage), [])
                r = next((x for x in rows if str(x.get("id")) == str(aid)), None)
                if r:
                    a["stageTier"] = r.get("tier", "")
                    a["stageWinRate"] = pct(r.get("winRate", 0))
                    a["stageRank"] = r.get("rank", 0)
            except Exception:
                pass
            a["stage"] = int(stage)
            a["stageOk"] = (not a["stages"]) or int(stage) in [int(x) for x in a["stages"] if str(x).isdigit()]

        # 当前英雄 + 该海克斯的组合胜率
        combo = None
        baseline = 0.0
        if champion_id:
            d = self.data.champion_detail(champion_id)
            baseline = float((d.get("champion") or {}).get("stats", {}).get("winRate", 0) or 0)
            combo = next(
                (x for x in (d.get("augments") or {}).get("all", []) if str(x.get("id")) == str(aid)),
                None,
            )
        if combo:
            wr = float(combo.get("winRate", 0) or 0)
            a["comboWinRate"] = pct(wr)
            a["comboPickRate"] = pct(combo.get("pickRate", 0))
            a["comboSample"] = combo.get("sampleCount", 0)
            a["delta"] = round((wr - baseline) * 100, 1) if baseline else 0
            a["comboTier"] = combo.get("tier", "")
            base = 50 + (wr - baseline) * 420 + TIER_BONUS.get(combo.get("tier", ""), 0)
            base += min(8, math.log10(max(combo.get("sampleCount", 1), 10)) * 2)
            if combo.get("sampleCount", 0) < 800:
                base -= 10
            a["basis"] = "本英雄 + 该海克斯的实际组合胜率"
        else:
            wr = float(a["winRate"])
            base = 50 + (wr - 0.5) * 300 + TIER_BONUS.get(a["tier"], 0)
            base += min(8, math.log10(max(a.get("sampleCount", 1), 10)) * 2)
            a["delta"] = 0
            a["basis"] = "全英雄通用胜率（该英雄暂无组合样本）"

        # 阶段校正：与本阶段同池对手比较，而不是与全局比较
        stage_adj = 0.0
        if a.get("stageWinRate") is not None and wr:
            stage_adj = clamp((a["stageWinRate"] / 100.0 - wr) * 200, -6, 6)

        # 与已选海克斯的协同
        syn = self._synergy(champion_id, aid, selected, baseline)
        syn_adj = 0.0
        if syn:
            a["synergyWinRate"] = pct(syn["winRate"])
            a["synergyDelta"] = syn["delta"]
            a["synergySample"] = syn["sample"]
            a["synergyPartner"] = syn["partnerName"]
            a["synergyPartnerId"] = syn["partnerId"]
            syn_adj = clamp((syn["winRate"] - baseline) * 250, -12, 12)

        score = clamp(base + stage_adj + syn_adj, 0, 100)
        a["score"] = round(score, 1)
        a["grade"] = grade(score)
        a["scoreBase"] = round(base, 1)
        a["scoreStage"] = round(stage_adj, 1)
        a["scoreSynergy"] = round(syn_adj, 1)
        return a

    # ---------- 敌方阵容 ----------
    def enemy_profile(self, enemies):
        """由敌方英雄职责 + 已买装备推断伤害构成、坦克度、回复与堆抗性情况。"""
        if not enemies:
            return None
        ad = ap = tank = heal = armor = mr = 0
        rows = []
        for p in enemies:
            c = self.champion_by_name(p.get("championName", "")) or {}
            roles = c.get("roles") or []
            slug = c.get("slug", "")
            cats = set()
            armor_n = mr_n = 0
            texts = []
            for iid in p.get("items") or []:
                meta = self.items.get(str(iid)) or {}
                c_cats = set(meta.get("cats") or [])
                cats |= c_cats
                if "Armor" in c_cats:
                    armor_n += 1
                if "SpellBlock" in c_cats or "MagicResist" in c_cats:
                    mr_n += 1
                t = self._item_text_of(iid)
                if t:
                    texts.append(t)
            ad_hits = len(cats & {"Damage", "ArmorPenetration", "CriticalStrike", "AttackSpeed", "OnHit", "LifeSteal"})
            ap_hits = len(cats & {"SpellDamage", "MagicPenetration"})
            if ap_hits > ad_hits:
                dmg = "ap"
            elif ad_hits > ap_hits:
                dmg = "ad"
            else:
                dmg = DMG_ROLE.get(roles[0] if roles else "", "ad")
            tanky = ("Tank" in roles) or ("Health" in cats and (armor_n >= 1 or mr_n >= 1))
            heals = (
                bool(cats & HEAL_ITEM_CATS)
                or slug in HEALER_SLUGS
                or any(h in " ".join(texts) for h in ("生命偷取", "全能吸血"))
            )
            if dmg == "ap":
                ap += 1
            elif dmg == "ad":
                ad += 1
            if tanky:
                tank += 1
            if heals:
                heal += 1
            if armor_n >= 2:
                armor += 1
            if mr_n >= 2:
                mr += 1
            rows.append(
                {
                    "champion": c.get("name") or p.get("championName", ""),
                    "icon": c.get("icon", ""),
                    "dmg": dmg,
                    "tank": tanky,
                    "heal": heals,
                }
            )

        parts = []
        if ad:
            parts.append("物理 %d" % ad)
        if ap:
            parts.append("法系 %d" % ap)
        if tank:
            parts.append("坦克 %d" % tank)
        if heal:
            parts.append("回复倾向 %d" % heal)
        advice = []
        if ap >= 2 and ap > ad:
            advice.append("对方偏法系：若被压，水银之靴 / 振奋盔甲更稳")
        if ad >= 2 and ad >= ap:
            advice.append("对方偏物理：若被压，铁板靴 / 兰顿之兆更稳")
        if heal >= 2:
            advice.append("对方有回复能力，考虑重伤装备")
        if armor >= 2:
            advice.append("对方多人堆护甲，护甲穿透收益提高")
        if mr >= 2:
            advice.append("对方多人堆魔抗，法术穿透收益提高")
        return {
            "ad": ad,
            "ap": ap,
            "tank": tank,
            "heal": heal,
            "armor": armor,
            "mr": mr,
            "rows": rows,
            "summary": " · ".join(parts) if parts else "阵容信息不足",
            "advice": advice,
        }

    def _enemy_bonus(self, item, profile):
        """把敌方阵容折算成装备加分。只加在自己确实需要的属性上。"""
        if not profile:
            return 0.0, []
        tags = set(item.get("tags") or [])
        bonus, why = 0.0, []
        if profile["ad"] >= 2 and "护甲" in tags:
            bonus += min(4.0, profile["ad"] * 1.1)
            why.append("扛 %d 物理" % profile["ad"])
        if profile["ap"] >= 2 and "魔抗" in tags:
            bonus += min(4.0, profile["ap"] * 1.1)
            why.append("扛 %d 法系" % profile["ap"])
        if profile["heal"] >= 2 and "重伤" in tags:
            bonus += 5.0
            why.append("压制回复")
        if profile["armor"] >= 2 and "穿甲" in tags:
            bonus += 3.0
            why.append("破甲")
        if profile["mr"] >= 2 and "法穿" in tags:
            bonus += 3.0
            why.append("法穿")
        if (profile["ad"] + profile["ap"]) >= 4 and "生命" in tags:
            bonus += 1.5
            why.append("撑血量")
        return round(bonus, 1), why

    # ---------- 出装 ----------
    def _item(self, iid, extra=None):
        iid = str(iid)
        it = dict(
            self.items.get(
                iid, {"id": iid, "name": iid, "icon": "", "price": 0, "tags": [], "cats": [], "final": False}
            )
        )
        if extra:
            if extra.get("winRate") is not None:
                it["winRate"] = pct(extra.get("winRate"))
            if extra.get("pickRate") is not None:
                it["pickRate"] = pct(extra.get("pickRate"))
            if extra.get("sampleCount") is not None:
                it["sampleCount"] = extra.get("sampleCount")
        if str(it.get("id")) == "0":
            it["name"] = "不买鞋子"
        return it

    def _aug_entries(self, cid, augment_ids):
        single = self.data.champion_single_augments(cid)
        builds = {str(b.get("augmentId")): b for b in (single.get("builds") or [])}
        items = {str(b.get("augmentId")): b for b in (single.get("items") or [])}
        matched = [aid for aid in augment_ids if str(aid) in builds]
        return single, builds, items, matched

    def _profile_score(self, arch, prof):
        sample = max(prof.get("sampleCount", 0) or 0, 1)
        # 胜率为主，样本量用对数做置信度加权，避免几百场的高胜率被过度放大
        return (
            float(prof.get("winRate", 0) or 0) * 100
            + float(arch.get("winRate", 0) or 0) * 10
            + min(6.0, math.log10(sample) * 1.5)
            + min(3.0, math.log10(sample) * float(prof.get("pickRate", 0) or 0) * 2)
        )

    def _route_support(self, candidates, owned_ids, max_profiles=6):
        """真实购买路线里，已出成品之后紧接着买的是什么。

        只用来给候选件标「同路线」，不改变原始胜率排序。
        """
        support = set()
        seen = set()
        for _, _, prof, _ in (candidates or [])[:max_profiles]:
            if id(prof) in seen:
                continue
            seen.add(id(prof))
            for route in (prof.get("routes") or [])[:2]:
                seq = [str(r.get("id")) for r in (route.get("purchaseOrder") or [])]
                seq += [str((r.get("item") or {}).get("id")) for r in (route.get("laterItems") or [])]
                seq = [
                    i for i in seq
                    if (self.items.get(i) or {}).get("final") and (self.items.get(i) or {}).get("acq") == "shop"
                ]
                pos = [i for i, iid in enumerate(seq) if iid in owned_ids]
                if not pos:
                    continue
                for iid in seq[max(pos) + 1:]:
                    if iid not in owned_ids:
                        support.add(iid)
                        break
        return support

    def _counters(self, cid, profile, exclude_ids, limit=3):
        """按敌方阵容挑针对性备选：优先用「本英雄实际出过这件」的样本。"""
        if not profile:
            return []
        need = []
        if profile["heal"] >= 2:
            need.append("重伤")
        if profile["ad"] >= 3 or (profile["ad"] >= 2 and profile["ad"] > profile["ap"]):
            need.append("护甲")
        if profile["ap"] >= 3 or (profile["ap"] >= 2 and profile["ap"] > profile["ad"]):
            need.append("魔抗")
        if profile["armor"] >= 3:
            need.append("穿甲")
        if profile["mr"] >= 2:
            need.append("法穿")
        if not need:
            return []

        detail = self.data.champion_detail(cid) or {}
        rows = ((detail.get("items") or {}).get("filtered") or {}).get("all") or []
        min_sample = 800
        if not rows:  # 该英雄样本不足时退回全局榜
            rows = self.data.item_rankings().get("all") or []
            min_sample = 20000

        out = []
        for r in rows:
            iid = str(r.get("id"))
            it = self.items.get(iid) or {}
            # 只推商店成品：ARAM 专属/条件装备的胜率不能当通用建议
            if not it.get("final") or it.get("acq") != "shop" or (it.get("price") or 0) < 2000:
                continue
            if iid in exclude_ids:
                continue
            tags = set(it.get("tags") or [])
            hit = [t for t in need if t in tags]
            if not hit:
                continue
            if (r.get("sampleCount") or 0) < min_sample:
                continue
            out.append(
                {
                    "id": iid,
                    "name": it.get("name", iid),
                    "icon": it.get("icon", ""),
                    "tier": r.get("tier", ""),
                    "winRate": pct(r.get("winRate")),
                    "sampleCount": r.get("sampleCount", 0),
                    "why": " / ".join(hit),
                }
            )
        out.sort(key=lambda x: (-x["winRate"], -(x["sampleCount"] or 0)))
        return out[:limit]

    def build(self, champion_id, augment_ids=None, enemy_players=None, my_items=None, archetype=None):
        """返回带购买顺序的出装方案（含敌方阵容加分与实时装备核对）。
        archetype 为手动指定的流派 key；样本里没有该流派时退回自动。"""
        augment_ids = [str(a) for a in (augment_ids or []) if a]
        if not champion_id:
            return {"steps": [], "order": [], "notes": ["先确定英雄"], "source": "无"}
        cid = str(champion_id)
        detail = self.data.champion_detail(cid)
        single, builds, item_entries, matched = self._aug_entries(cid, augment_ids)
        used_names = [self.aug_by_id.get(str(a), {}).get("name", str(a)) for a in matched]
        profile = self.enemy_profile(enemy_players)

        # 1) 选出最优「构筑方向 + 三件套 + 购买路线」
        candidates = []
        if matched:
            for aid in matched:
                e = builds[str(aid)]
                for arch in e.get("archetypes", []):
                    for prof in arch.get("profiles", []):
                        candidates.append((self._profile_score(arch, prof), arch, prof, aid))
            source = "按已选海克斯「%s」的专属样本" % " / ".join(used_names[:2])
        if not candidates:
            base = (detail.get("builds") or {}).get("filtered") or {}
            for arch in base.get("archetypes", []):
                for prof in arch.get("profiles", []):
                    candidates.append((self._profile_score(arch, prof), arch, prof, None))
            source = "英雄通用样本（尚未选定海克斯）"

        if not candidates:
            return {
                "steps": [], "order": [], "archetype": "", "source": source,
                "notes": ["该英雄暂无足够的出装样本"], "sampleCount": 0, "winRate": 0,
                "slotRanking": {}, "profile": profile,
                "archetypes": [], "archetypeForced": False,
            }

        candidates.sort(key=lambda t: -t[0])
        archetypes, seen_keys = [], set()
        for _, a, _, _ in candidates:
            k = a.get("key", "")
            if k and k not in seen_keys:
                seen_keys.add(k)
                archetypes.append({"key": k, "name": self.data.archetype_name(k)})
        forced = False
        if archetype:
            sub = [c for c in candidates if c[1].get("key") == archetype]
            if sub:
                candidates = sub
                forced = True
        _, arch, prof, from_aug = candidates[0]
        route = (prof.get("routes") or [{}])[0]

        # 手动流派时，「第 N 件」候选只取该流派真实出过的装备，避免 AP 路线推无尽
        arch_items = set()
        if forced:
            for _, _, p, _ in candidates:
                for it in p.get("itemSet") or []:
                    arch_items.add(str(it.get("id")))
                for r in p.get("routes") or []:
                    for it in r.get("purchaseOrder") or []:
                        arch_items.add(str(it.get("id")))
                    for row in r.get("laterItems") or []:
                        arch_items.add(str((row.get("item") or {}).get("id")))
                    for b in r.get("boots") or []:
                        arch_items.add(str((b.get("item") or {}).get("id")))
            arch_items.discard("0")

        def item_value(item, wr, sample, pick=0.0):
            bonus, _ = self._enemy_bonus(item, profile)
            value = wr * 100 + min(3.0, math.log10(max(sample, 1))) + bonus
            if sample < 800:  # 几百场的极值不参与前排
                value -= 6
            return value

        def pick_best(rows, min_sample=1500):
            """优先取样本充足的最高胜率项，再叠加敌方阵容加分。"""
            if not rows:
                return None
            pool = [r for r in rows if (r.get("sampleCount", 0) or 0) >= min_sample] or rows
            return max(
                pool,
                key=lambda r: item_value(
                    self._item(r.get("id")),
                    float(r.get("winRate", 0) or 0),
                    r.get("sampleCount", 0) or 0,
                    float(r.get("pickRate", 0) or 0),
                ),
            )

        starters = pick_best(route.get("starters") or [])
        boots = pick_best(route.get("boots") or [])
        purchase = route.get("purchaseOrder") or prof.get("itemSet") or []
        later = (route.get("laterItems") or [])[:3]

        # 2) 按「第 N 件」合并多个海克斯的分件数据；没有海克斯时退回英雄自身分件样本
        slot_rows = defaultdict(lambda: defaultdict(lambda: {"w": 0.0, "wr": 0.0, "pick": 0.0, "n": 0.0}))
        sources = []
        if matched:
            for aid in matched:
                ent = item_entries.get(str(aid))
                if not ent:
                    continue
                weight = max(0.3, min(3.0, math.log10(max(ent.get("sampleCount", 1), 10)) / 3))
                sources.append((ent.get("slots") or {}, weight))
        if not sources:
            champ_slots = ((detail.get("items") or {}).get("filtered") or {}).get("slots") or {}
            sources.append((champ_slots, 1.0))
        for slots, weight in sources:
            for slot, rows in slots.items():
                for r in rows[:15]:
                    iid = str(r.get("id"))
                    if iid == "0":
                        continue
                    acc = slot_rows[str(slot)][iid]
                    acc["w"] += weight
                    acc["wr"] += float(r.get("winRate", 0) or 0) * weight
                    acc["pick"] += float(r.get("pickRate", 0) or 0) * weight
                    acc["n"] += float(r.get("sampleCount", 0) or 0) * weight

        slot_ranking = {}
        slot_pool = {}  # 内部保留更大候选池，供「按当前装备」推荐过滤后仍有 3 个
        for slot, acc in slot_rows.items():
            ranked = []
            for iid, a in acc.items():
                if a["w"] <= 0:
                    continue
                item = self._item(iid)
                wr = a["wr"] / a["w"]
                sample = a["n"] / a["w"]
                pick = a["pick"] / a["w"]
                item["winRate"] = pct(wr)
                item["pickRate"] = pct(pick)
                item["sampleCount"] = int(sample)
                bonus, why = self._enemy_bonus(item, profile)
                item["enemyBonus"] = bonus
                item["why"] = why
                ranked.append((item_value(item, wr, sample, pick), item))
            ranked.sort(key=lambda t: -t[0])
            slot_pool[slot] = [x[1] for x in ranked[:12]]
            slot_ranking[slot] = slot_pool[slot][:4]

        # 3) 组装顺序步骤
        steps = []
        if starters:
            steps.append(
                {
                    "slot": "出门装",
                    "items": [self._item(i.get("id")) for i in starters.get("items", [])],
                    "winRate": pct(starters.get("winRate")),
                    "pickRate": pct(starters.get("pickRate")),
                    "sampleCount": starters.get("sampleCount", 0),
                }
            )
        if boots:
            b = boots.get("item") or {}
            boot_item = self._item(b.get("id", 0), boots)
            _, why = self._enemy_bonus(boot_item, profile)
            steps.append(
                {
                    "slot": "鞋子",
                    "items": [boot_item],
                    "winRate": pct(boots.get("winRate")),
                    "pickRate": pct(boots.get("pickRate")),
                    "sampleCount": boots.get("sampleCount", 0),
                    "why": why,
                }
            )
        for idx, it in enumerate(purchase, start=1):
            item = self._item(it.get("id"))
            slot_items = slot_ranking.get(str(idx)) or []
            stat = next((x for x in slot_items if x["id"] == item["id"]), None)
            _, why = self._enemy_bonus(item, profile)
            steps.append(
                {
                    "slot": "第 %d 件" % idx,
                    "items": [item],
                    "winRate": stat.get("winRate") if stat else None,
                    "pickRate": stat.get("pickRate") if stat else None,
                    "sampleCount": stat.get("sampleCount", 0) if stat else 0,
                    "why": why,
                }
            )
        for idx, row in enumerate(later, start=1):
            it = row.get("item") or {}
            item = self._item(it.get("id"))
            _, why = self._enemy_bonus(item, profile)
            steps.append(
                {
                    "slot": "后续 %d" % idx,
                    "items": [item],
                    "winRate": pct(row.get("winRate")),
                    "pickRate": pct(row.get("pickRate")),
                    "sampleCount": row.get("sampleCount", 0),
                    "why": why,
                }
            )

        # 4) 顺序列表 + 实时核对当前装备
        order = []
        seen = set()
        for s in steps:
            for it in s["items"]:
                if it["id"] in seen or it["id"] == "0":
                    continue
                seen.add(it["id"])
                order.append(
                    {
                        "id": it["id"],
                        "name": it["name"],
                        "icon": it.get("icon", ""),
                        "price": it.get("price", 0),
                        "slot": s["slot"],
                    }
                )

        owned_all = [str(i) for i in (my_items or []) if str(i) in self.items and str(i) != "0"]
        # 已买的东西可能只是散件：进度按实际购买判断，计划外只算成品
        owned = [self._item(i) for i in owned_all]
        bought_ids = {i["id"] for i in owned}
        order_ids = {o["id"] for o in order}
        for o in order:
            o["owned"] = o["id"] in bought_ids
        next_item = next((o for o in order if not o["owned"]), None)
        off_plan = [
            i for i in owned
            if i.get("final") and "Boots" not in (i.get("cats") or []) and i["id"] not in order_ids
        ][:4]
        done = sum(1 for o in order if o["owned"])

        counters = self._counters(cid, profile, order_ids | bought_ids, limit=3)

        # 5) 序贯推荐：按实际已出的成品数定位「下一件」，排除已买 / 重复鞋子，再按胜率取前三
        owned_final = [
            i for i in owned
            if i.get("final") and i.get("acq") == "shop" and (i.get("price") or 0) >= 900
        ]
        owned_final_ids = {i["id"] for i in owned_final}
        owns_boots = any("Boots" in (i.get("cats") or []) for i in owned)
        picks = {"slot": 0, "label": "", "rows": [], "ownedCount": len(owned_final), "full": False}
        if len(owned_final) >= 6:
            picks["full"] = True
        else:
            slot_no = len(owned_final) + 1
            support = self._route_support(candidates, owned_final_ids)

            def slot_rows(use_arch):
                rows = []
                for item in slot_pool.get(str(slot_no), []):
                    iid = item.get("id")
                    if iid in bought_ids or iid == "0":
                        continue
                    if (item.get("sampleCount") or 0) < 800:
                        continue
                    if not item.get("final") or item.get("acq") != "shop":
                        continue
                    if owns_boots and "Boots" in (item.get("cats") or []):
                        continue
                    if use_arch and arch_items and iid not in arch_items:
                        continue
                    row = dict(item)
                    row["route"] = iid in support
                    rows.append(row)
                return rows

            rows = slot_rows(True) or slot_rows(False)
            rows.sort(key=lambda x: -(x.get("winRate") or 0))
            picks["slot"] = slot_no
            picks["label"] = "第 %d 件" % slot_no
            picks["rows"] = rows[:3]

        # 6) 说明
        notes = [
            "评分 = 组合胜率 + 构筑方向胜率 + 样本量对数加权；只做排序，不改变原始胜率。",
            "出门装 / 鞋子 / 三件套来自同一条真实购买路线，后续成装为胜率最高的可选件。",
        ]
        if forced:
            notes.append("流派已手动指定为「%s」：路线与候选件都按该流派的真实样本。" % self.data.archetype_name(arch.get("key", "")))
        elif archetype:
            notes.append("当前样本没有「%s」流派，已按自动选择。" % self.data.archetype_name(archetype))
        if matched:
            notes.append("已切换为「%s」专属样本；换掉海克斯后出装会重新计算。" % " / ".join(used_names[:3]))
        else:
            notes.append("选定海克斯后会切换为该海克斯的专属出装样本。")
        if profile:
            notes.append("敌方阵容已参与装备排序（加分项标在各件后面），属经验判断，非胜率因果。")
            notes.extend(profile.get("advice") or [])

        return {
            "archetype": self.data.archetype_name(arch.get("key", "")),
            "archetypeKey": arch.get("key", ""),
            "archetypes": archetypes,
            "archetypeForced": forced,
            "source": source,
            "fromAugment": self.aug_by_id.get(str(from_aug), {}).get("name") if from_aug else "",
            "sampleCount": prof.get("sampleCount", 0),
            "winRate": pct(prof.get("winRate")),
            "pickRate": pct(prof.get("pickRate")),
            "augmentsUsed": used_names,
            "steps": steps,
            "order": order,
            "slotRanking": slot_ranking,
            "counters": counters,
            "profile": profile,
            "owned": owned,
            "offPlan": off_plan,
            "progress": {"done": done, "total": len(order)},
            "next": next_item,
            "picks": picks,
            "notes": notes,
        }

    # ---------- 搜索 ----------
    def search(self, q, kind="all", limit=12):
        q = str(q or "").strip().lower()
        if not q:
            return []
        out = []
        if kind in ("all", "champion"):
            for c in self.champions:
                hay = " ".join([c["name"], c["title"], c["slug"], c["riotId"]]).lower()
                if q in hay:
                    out.append({"kind": "champion", "id": c["id"], "name": c["name"], "sub": c["title"], "icon": c["icon"]})
        if kind in ("all", "augment"):
            for a in self.augments:
                hay = " ".join([a["name"], a["slug"]]).lower()
                if q in hay:
                    out.append({"kind": "augment", "id": a["id"], "name": a["name"], "sub": a.get("rarityLabel", ""), "icon": a["icon"]})
        return out[:limit]
