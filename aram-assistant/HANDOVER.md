# ARAM 海克斯大乱斗实时助手 · 交接文档

> 项目目录：`E:\78zaoCoding\Code_LANG\aram-assistant`（2026-11-10 从 WorkBuddy 临时会话目录迁移至此）
> GitHub：`https://github.com/langopt000/Code_LANG`（子目录 `aram-assistant`）
> 文档日期：2026-11-10 · 数据版本：ARAMKit v16.22（2026-11-09，高分段 4,297,659 场）
> 技术栈：Python 3 标准库 + 单页 HTML/CSS/JS，零第三方依赖、零 npm 包、零 Electron

---

## 1. 这个项目做什么

一个本地运行的 LoL 海克斯大乱斗（ARAM Mayhem）辅助浮窗，数据取自 `aramkit.cn` 同源的公开统计，满足以下需求：

| # | 需求 | 实现 | 数据来源 |
|---|---|---|---|
| 1 | 英雄选择时给出每个英雄的排行和胜率 | LCU 读取共享备选席 `benchChampions`，列出排名/梯队/胜率/选取率并按胜率排序 | `champion-rankings.json` |
| 2 | 海克斯出现后显示选取率、胜率、评级 | 屏幕 OCR 识别三选一卡牌 + 模糊匹配；展示全局胜率/选取率/梯队/稀有度、**本英雄搭配胜率与 ±pp**、**第 N 次阶段胜率与阶段排名**、**与已选海克斯的组合胜率**、卡牌原文说明、综合推荐分 | `augment-rankings.json`（含 `stages.1~4`）+ `champion-details/{id}.json` + `resources/augments.json` |
| 3 | 结合当前海克斯推荐出装与顺序 | 按已选海克斯切换专属样本，输出「出门装 → 鞋子 → 第 1/2/3 件 → 后续成装」，可一键写入游戏内配装方案 | `champion-details/{id}-single-augments.json` |
| 4 | 出装要跟着敌方阵容走 | 由敌方英雄职责 + 已买装备推断物理/法系/坦克/回复/堆抗性，折算为装备加分并给出针对性备选；加成原因标在装备后面 | `resources/items.json`（categories + tooltip）+ `item-rankings.json` |
| 5 | 对局中知道「下一件买什么」 | 读 Live Client Data 的己方装备，标出已有 / 下一件 / 计划外，分件位置给出更高胜率的替换建议 | `item-rankings.json` + `champion-details/{id}.json` 的 `items.slots` |

**合规性**：只读 Riot 官方本地接口（LCU、Live Client Data）与自身屏幕；不读游戏内存、不注入进程、不修改游戏文件、不自动化输入。仍属第三方软件，Riot 不公布批准名单，使用前需自行确认所在服务器规则。

---

## 2. 快速开始（接手人第一步）

### 立刻看到浮窗
双击桌面 **`ARAM-Assistant-Launch.bat`**（已放好）。
它会：确保守护在跑 → 启动本地服务 → 弹出置顶浮窗 → 打印窗口矩形。
看到 `确认：浮窗已在屏幕内` 即成功。黑色控制台窗口可以关掉，服务和浮窗继续运行。

- 控制台（完整版）：`http://127.0.0.1:8765/`
- 浮窗地址：`http://127.0.0.1:8765/?overlay=1`
- 自检接口：`http://127.0.0.1:8765/api/diagnose`

### 浮窗形态
- **默认收起**：右上角一条 `236×48` 小胶囊，显示「英雄 · N 个海克斯 · 已选 N」，点「展开」才变成 `400×820` 完整面板，点「收起」回去。
- 完整面板只显示三大信息块；自检、设置、日志在浮窗里自动隐藏（控制台里可见）。
- 位置可切左/中/右（默认右，避开中央卡牌区）；透明度、点击穿透可调。
- 卡牌在浮窗里压缩为：名字+首选徽章 / 三列指标 / 2 行说明 / 组合协同 / 推荐条 / 一键选取；说明与解释行在紧凑模式下隐藏，避免超过 820px。
- 展开态预览：`data/preview_overlay.png`（整页）、`data/preview_build.png`（出装区特写）；控制台预览：`data/preview.png`。

### 开机自启 / 取消
- 安装：`setup_autostart.bat`（也可重新绑定）
- 取消：`remove_autostart.bat`
- 写入位置：`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\` 下
  `ARAM-Mayhem-Assistant.vbs`（隐藏窗口，主）与 `ARAM-Mayhem-Assistant.cmd`（不依赖脚本宿主，备用）
- 两者由单实例锁去重，启动的都是 `watcher.py`。

> **重要**：由 AI 会话/沙箱里启动的后台进程会被会话结束时回收，服务无法由助手长期保持。必须由用户从桌面双击一次，或重启登录由启动项拉起。这是排查"看不到浮窗"时第一件要确认的事。

---

## 3. 架构与数据流

```
ARAMKit 公开 JSON (data.aramkit.cn)
        │  datafeed.py（版本发现 / 磁盘缓存 / 中英名称资源）
        ▼
   Engine（engine.py）
   ├─ 英雄/海克斯评级加权（组合样本 + 第 N 次阶段 + 与已选海克斯的成对组合）
   ├─ 敌方阵容画像（职责 + 已买装备）→ 装备加减分与克制备选
   └─ 按海克斯合并出装顺序 + 当前装备核对（下一件 / 计划外 / 分件替换）
        ▲
        │  app.py（HTTP 服务 127.0.0.1:8765 + 后台线程）
        ├── league.py ── LCU (端口由 lockfile/日志发现) + Live Client Data (2999)
        ├── vision.py ─ GDI 截屏 → Windows.Media.Ocr → 模糊匹配
        ├── window.py ─ Edge/Chrome --app 无边框窗 + Win32 置顶/透明/穿透
        └── watcher.py ─ 检测客户端进程，监管服务
        ▼
   ui/index.html（单页控制台 / 浮窗，轮询 /api/state）
```

后台线程：`poll_loop`（1.2s，游戏状态）、`ocr_loop`（3.5s）、`topmost_loop`（0.3s，重新置顶）、`watcher` 独立进程（5s）。

---

## 4. 文件清单

| 文件 | 职责 |
|---|---|
| `app.py` | HTTP 服务、状态机、所有 API、控制动作、`/api/diagnose`；阶段推断（已选数量+1，可手动覆盖） |
| `datafeed.py` | ARAMKit JSON 客户端：版本发现、缓存、进程内 TTL memo、名称/图标/装备类别与说明文本、索引 |
| `engine.py` | 海克斯评级（组合/阶段/协同）、敌方阵容画像、出装排序、分件替换、实时装备核对、搜索 |
| `league.py` | LCU 连接（多路径凭据发现）、Live Client Data、配装方案写入 |
| `vision.py` | GDI 抓屏、PNG 编码、Windows OCR、`match_augments` 模糊匹配 |
| `ocr.ps1` | PowerShell WinRT OCR（优先中文识别器） |
| `window.py` | 浮窗创建/摆位/折叠/置顶/透明度/点击穿透 |
| `watcher.py` | 常驻守护、单实例锁(8766)、服务保活、日志 |
| `show_overlay.py/.bat` | 一键唤起（服务 + 浮窗 + 守护），打印窗口矩形 |
| `setup_autostart.py/.bat` | 安装开机自启并立即启动守护 |
| `remove_autostart.py/.bat` | 移除启动项并停止守护 |
| `autostart.vbs` / `autostart.cmd` | 登录时启动项模板 |
| `start.bat` | 手动启动完整控制台（端口占用时直接打开已有页面） |
| `ui/index.html` | 全部 UI（内嵌离线演示数据 `DEMO_STATE`） |
| `data/config.json` | 用户配置（见第 8 节） |
| `data/cache/*.json` | 下载缓存（离线可回退） |
| `data/watcher.log` | 守护日志 |
| `data/watcher.stop` | 存在时守护优雅退出 |
| `HANDOVER.md` | 本文档 |

---

## 5. 数据源（最重要的技术发现）

站点是 Nuxt 预渲染 HTML，`?dataset=high` 只在客户端生效，抓 HTML 拿不到高分段数据。真实数据源是前端消费的公开静态 JSON：

- 基址：`https://data.aramkit.cn`
- 版本清单：`data/versions.json` → `latest` / `versions[].dataPath` / `resourcePath` / `dataDate` / `allMatches` / `highMatches`
- 统计：`{dataPath}/stats/{all|high}/…`
  - `champion-rankings.json`、`augment-rankings.json`（含 `stages.1~4`）、`item-rankings.json`
  - `champion-details/{id}.json` → `champion / summoners / skills / augments / augmentCombinations / items / builds`
  - **`champion-details/{id}-single-augments.json`** ← 按单个海克斯拆分的出装，是需求 3 的核心
  - `augment-details/{id}.json`
- 中文资源：`{resourcePath}/zh-CN/resources/{champions|augments|items|archetypes|roles}.json`
  （同时抓 `en-US` 的 augment 名做 OCR 别名，兼容英文客户端）
- 图标：资源里的 `icon` 字段拼 `https://data.aramkit.cn/`
- 缓存：按 URL 的 SHA1 存 `data/cache/`，排行 6h、详情/资源 24h、版本 12h；断网时回退过期缓存。
  另有一层**进程内 memo**（`fetch_json` 的 `_MEM`）：TTL 内直接返回已解析对象。加它是为了让 1.2s 轮询不必反复 `json.loads` 1.6MB 的英雄详情。

进阶字段（做阵容/阶段/说明功能时用得到）：
- `resources/items.json`：每件装备有 `categories`（`Armor` / `SpellBlock` / `Damage` / `SpellDamage` / `LifeSteal` / `ArmorPenetration` …）、`tooltip.blocks[].spans[]`（富文本说明）、`priceTotal`、`isFinal`、`acquisition`。
  「重伤」没有独立 category，只能从 tooltip 文本里找。
- `resources/augments.json`：`tooltip` 富文本 → 三选一时的卡牌原文说明。
- `champion-details/{id}.json` → `augmentCombinations[]`：`{augmentIds:[a,b], winRate, sampleCount}`，**只有成对样本**，没有 3 个以上的组合。
- `champion-details/{id}.json` → `items.filtered.slots{"1".."6"}`：本英雄每个位置的装备样本，用于分件替换与克制备选。

`champion-details/{id}-single-augments.json` 的结构要点：
```
builds[]: {augmentId, sampleCount, pickRate, archetypes[]}
  archetypes[]: {key, winRate, profiles[]}
    profiles[]: {itemSet[], winRate, pickRate, routes[]}
      routes[]: {purchaseOrder[], starters[], boots[], laterItems[]}
items[]: {augmentId, all[], slots{"1".."6"}}
```
`purchaseOrder` 就是真实购买顺序；`slots` 用于给「第 N 件」备选排序。多个已选海克斯按样本量加权合并。

---

## 6. 客户端接入（国服 WeGame 版坑）

WMI 读 `LeagueClientUx.exe` 的 `CommandLine` / `ExecutablePath` 常返回空（权限受限），且 `lockfile` 可能是 0 字节。`league.probe()` 依次尝试：

1. WMI 命令行里的 `--app-port` + `--remoting-auth-token`
2. `winreg` 读 `HKCU\SOFTWARE\Tencent\LOL InstallPath`（及 Riot 版注册表）→ 找 `lockfile`
3. 扫描 `LeagueClient\*LeagueClientUx.log`，日志首行含完整启动参数
4. 每组候选都用 `/lol-gameflow/v1/gameflow-phase` 实际验证，成功才采用；`source` 记为 `wmi/lockfile/log`

实测本机：端口 `7686`、来源 `log`、阶段 `InProgress`、英雄 `乌迪尔`。

- LCU：`https://127.0.0.1:<port>`，Basic 认证 `riot:<token>`，自签证书需 unverified context
- 英雄选择：`/lol-champ-select/v1/session` → `benchChampions` / `myTeam` / `localPlayerCellId`
- 阶段：`/lol-gameflow/v1/gameflow-phase`
- 对局：`https://127.0.0.1:2999/liveclientdata/allgamedata` → `allPlayers`（英雄名、装备、阵营）
- 可选写入：`POST /lol-item-sets/v1/item-sets/{summonerId}/sets`（仅用户点击「写入游戏内配装方案」时）

> Riot 的接口**不暴露海克斯卡牌**，这是必须走屏幕 OCR 的原因（商业插件同理）。

---

## 7. 浮窗实现要点

- Edge/Chrome `--app` 无边框窗口，独立 `--user-data-dir`（每次新目录，避免单例锁把请求转给旧实例后静默退出）。
- **DPI**：`GetDeviceCaps(LOGPIXELSX)/96` 得到缩放（本机 125%）；Chromium 的 `--window-size/position` 用逻辑像素需除以 scale，Win32 `SetWindowPos` 用物理像素。
- **64 位句柄**：`HWND_TOPMOST` 必须是 `ctypes.c_void_p(-1)`，直接传 int `-1` 会被截成 `0xFFFFFFFF` 导致 `SetWindowPos` 静默失败（曾导致浮窗永远被游戏盖住）。模块级已声明 argtypes。
- **置顶**：`attach_to_game()`（`GWLP_HWNDPARENT` 附属到 `RiotWindowClass`）+ `keep_on_top()`，由 `topmost_loop` 每 0.3s 重新置顶；必要时把游戏窗口从 TOPMOST 降级。
- **折叠**：`set_collapsed()` 改窗口尺寸（收起 236×46 / 展开 400×820），右边缘固定。
- **OCR 与浮窗互不干扰**：识别区默认 `[0.18, 0.14, 0.58, 0.60]`，右边界 0.76；浮窗在最右 0.84 之后，不会被 OCR 读到自己的文字。
- 点击穿透：`WS_EX_TRANSPARENT`；透明度：`WS_EX_LAYERED` + `SetLayeredWindowAttributes`。

---

## 8. 配置（`data/config.json`）

| 字段 | 默认 | 说明 |
|---|---|---|
| `dataset` | `high` | `high` 高分段 / `all` 全分段，对应站点 URL 参数 |
| `ocrEnabled` / `ocrInterval` | `true` / `3.5` | OCR 开关与轮询秒数 |
| `region` | `[0.18,0.14,0.58,0.60]` | 屏幕比例 `(x,y,w,h)`，框住中央卡牌 |
| `opacity` | `0.92` | 浮窗整体透明度 |
| `clickThrough` | `false` | 鼠标穿透（开启后浮窗本身不可点，需在控制台关） |
| `theme` | `dark` | `dark` / `light` |
| `autoOpenBrowser` | `true` | 手动 start.bat 时自动开浏览器 |
| `autoOverlay` | `true` | 进入选人/对局自动弹浮窗 |
| `autoCloseOverlay` | `false` | **默认不自动关**，避免 LCU 抖动导致"弹一下就没" |
| `overlayPosition` | `right` | `left` / `center` / `right` |
| `overlayWidth` / `overlayHeight` | `400` / `820` | 展开态物理像素 |
| `overlayCollapsed` | `true` | 默认收起成小胶囊 |

> 海克斯「第几次」默认自动推断（已选数量 + 1，上限 4），控制台可手动指定；手动值**不写盘**，服务重启后回到自动，避免下一局沿用错阶段。

---

## 9. HTTP API

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/` | 控制台 / 浮窗页面 |
| GET | `/api/state` | 全量状态（英雄、备选席、候选海克斯、已选、出装、日志、配置、浮窗矩形） |
| GET | `/api/search?q=&kind=champion\|augment` | 手动搜索 |
| GET | `/api/diagnose` | 6 项自检（守护/服务/客户端进程/LCU/LiveData/浮窗） |
| GET | `/api/health` | 健康检查 |
| POST | `/api/control` | `{"action":..., "value":..., "fromUser":...}` |
| POST | `/api/ocr` | 立即识别一次 |
| POST | `/api/refresh` | 重新同步数据 |

`control` 动作：`setChampion`、`clearManual`、`addAugment`、`removeAugment`、`clearAugments`、`setStage`（0=自动，1~4=手动）、`setDataset`、`setOcr`、`setRegion`、`setTheme`、`setOpacity`、`setClickThrough`、`setAutoOverlay`、`setOverlayPosition`、`setCollapsed`、`launchOverlay`、`testOverlay`、`closeOverlay`、`redetect`、`itemset`、`refresh`。

`/api/state` 里与新增功能相关的字段：
- 顶层 `stage` / `stageMode`：当前第几次海克斯、是否手动指定。
- `candidates[]`：除原有胜率/评级外，新增 `description`（卡牌原文）、`stageWinRate` / `stageRank` / `stageOk`、`synergyWinRate` / `synergyPartner` / `synergyDelta`、`scoreBase` / `scoreStage` / `scoreSynergy`。
- `build`：新增 `profile`（敌方画像与建议）、`next` / `owned` / `offPlan` / `progress`（实时装备核对）、`slotAdvice[]`（分件替换）、`counters[]`（针对性备选）。


---

## 10. 踩过的坑与修复记录（高价值）

| 现象 | 根因 | 修复 |
|---|---|---|
| 高分段数据抓不到 | Nuxt 预渲染，`dataset` 客户端生效 | 改用官方 JSON 端点 |
| LCU 连不上（国服） | WMI 命令行读不到、lockfile 空 | 注册表定位 + 客户端日志解析 + 实际请求校验 |
| 浮窗完全不出现 | `CREATE_NO_WINDOW` 把 Chromium 主窗口一起隐藏 | 启动 Edge 时去掉该 flag |
| "已打开"却没窗口 | `_find_hwnd` 里 `append(a,b)` 两个参数，ctypes 回调异常被吞 | 改成 append 单个 tuple，按 PID+可见+尺寸匹配 |
| 窗口跑到屏幕外 | 125% DPI，Chromium 逻辑像素 vs Win32 物理像素 | `dpi_scale()` 换算 |
| 浮窗被游戏盖住 | **64 位 `HWND_TOPMOST=-1` 被截断**，`SetWindowPos` 静默失败 | `c_void_p(-1)` + 显式 argtypes + 0.3s 高频置顶 |
| 弹一下就消失 | LCU 探测抖动 → phase 瞬间 `None` → `autoCloseOverlay` 立即关窗 | 默认不自动关 + 15s 宽限 + 守护保活 |
| 服务/守护莫名消失 | AI 会话沙箱回收后台进程 | 用户从桌面双击启动；开机自启项接管 |
| 紧凑模式右侧被裁 | 400 物理像素 = 320 CSS 像素，行内列过多 | 梯队+胜率内联到名字后，按钮移到卡片底部 |
| Edge 单例锁导致新进程退出 | 复用 profile 残留锁 | 每次独立 profile，清理 1 天前旧目录 |
| 克制备选推「金铲铲 / 虚空献祭」 | 这类 ARAM 条件装备（6000 金、`acquisition=conditional`）胜率高但不是常规出装 | 过滤 `isFinal && acq=="shop" && price>=2000`，并优先用本英雄自己的装备样本 |
| 已买的散件仍显示「未买」、下一件还是散件 | 只把 `isFinal` 装备算「已有」 | 进度按实际购买 id 判断；「计划外」才只算成品 |
| 鞋子被推荐成「不买鞋子」 | 数据里 `id 0`（无鞋）确有 ~60% 胜率与高选取率，不是 bug | 保留该选项，仅把名字显示为「不买鞋子」 |
| 页面越用越吃 CPU | 每 1.2s 轮询都重新 `json.loads` 排行榜与 1.6MB 英雄详情 | `fetch_json` 加进程内 TTL memo（已解析对象直接复用） |
| 窄浮窗里装备名被拆成两行 | `.it` 默认按字符换行 | `.it{white-space:nowrap}`，靠 `.its` 整块换行 |
| 三海克斯组合查不到胜率 | 数据只有 `augmentCombinations` 的成对样本 | 只对「已选 1 个 + 本张卡」做组合加成，3 个以上不臆造 |

---

## 11. 已知限制

1. **服务必须由用户桌面会话启动**（双击 `ARAM-Assistant-Launch.bat`）；AI 会话内启动的进程会被回收。
2. **全屏独占 / 全屏优化**可能仍压住浮窗。高频置顶已加；若仍无效，把 LoL 视频设置改「窗口化」，或对 `League of Legends.exe` 禁用「全屏优化」。
3. **OCR 依赖 Windows 中文识别器**（`ocr.ps1` 优先选 `zh*`），识别不准可在控制台微调 `region` 后点「立即识别」验证；也可用「手动添加海克斯」兜底。
4. 大厅/主页面按设计**不弹浮窗**，仅 `ChampSelect` / `InProgress` 等阶段自动弹。
5. Edge 冷启动偶发找不到窗口句柄（等待已放宽到 25s，失败会清理半开进程），再点一次「展开」或重新双击启动器即可。
6. 评级与出装评分是**加权排序**，不改变原始胜率；样本量小的组合会降权，但不代表因果。
7. `schtasks.exe` 在本环境被安全策略禁用，未使用计划任务方案；开机自启走 Startup 文件夹。
8. **敌方阵容是启发式画像**：靠英雄职责 + 已买装备类别推断物理/法系/坦克/回复，敌方装备还没成型时会偏；由此产生的加分与「针对性备选」属经验建议，不是胜率因果（平均胜率偏低也正常）。
9. **组合加成只有成对样本**（`augmentCombinations`），已选 2 个以上海克斯时只能取「本张卡 + 其中 1 个」的最高组合，不做三件以上的臆造。
10. **阶段默认按「已选数量 + 1」推断**：OCR 漏识别或手动加错会让阶段偏移，控制台可手动指定 1~4。

---

## 12. 验证记录

**2026-11-10 第一轮（三大块基础能力）**
- 数据：v16.22 / 高分段 4,297,659 场；173 英雄、239 海克斯、197 装备。
- 引擎：卢锡安 + 珠光护手 → 专属样本「暴击」，出门装长剑+十字镐 → 暴食胫甲 → 收集者 → 无尽之刃 → 公理圆弧；组合胜率 58.7% vs 全局 48.6%，推荐分 94.7 / S。
- LCU：端口 7686、来源 `log`、`InProgress`；Live Client Data 可读；OCR 在对局中识别到「黎明使者的坚决 / 术士果汁盒 / 暴击律动 / 巨人杀手」。
- 自检 6 项全绿；`/api/diagnose` 返回阶段与凭据来源。
- 浮窗：Z 序「浮窗 > 游戏 > 其他」；真机截图确认盖在游戏画面右上且不遮中央与小地图。
- 折叠：`overlayCollapsed true` → `(2308,16)-(2544,64)`；`false` → `(2144,16)-(2544,836)`。

**2026-11-10 第二轮（阶段 / 组合 / 敌方阵容 / 实时核对）**
- 阶段排行：珠光手镯在第 2 次阶段胜率 49.5%、阶段排名 114 → 阶段校正 -6.0 分；说明「全局强 ≠ 这一轮强」确实被区分开。
- 组合加成：卢锡安已选珠光手镯时，珠光护手组合胜率 67.0%（+14.3pp，11,046 场）→ 协同加分封顶 +12，总分 100 / S。
- 敌方画像：5 人物理偏多 → 「物理 4 · 法系 1 · 坦克 1 · 回复 1 · 堆护甲 1」，针对性备选给出兰顿之兆 / 千变者贾修 / 中娅沙漏（均为该英雄实际出过的护甲成品，样本 1,511~9,697）。
- 实时核对：已买长剑+狂战士胫甲+收集者 → 进度 2/8、下一件十字镐（875 金币）、计划外「狂战士胫甲」。
- 分件替换：第 1 件路线收集者 59.4% → 巨蛇之牙 61.6%（+2.2pp，1,884 场）。
- 性能：`build()` 平均 3.9ms（含敌方画像与克制备选），`state()` 平均 0.2ms、JSON 26.5KB；加 memo 前每次轮询要重复解析 1.6MB 详情。
- 界面：330px 宽（=400 物理像素 @125%）下无横向溢出；标签、说明、协同、下一件、敌方 chips、步骤勾选、克制备选全部在卡片内。控制台阶段选择器 5 个按钮完整。
- 语法：Python 全量 `py_compile`、页面 JS `node --check`、无头浏览器渲染均通过。


---

## 13. 故障排查速查

| 症状 | 先查 |
|---|---|
| 完全看不到浮窗 | 端口 8765/8766 是否占用 → 没跑就双击桌面启动器 |
| 自检里 LCU 红点 | 看 `source`/错误文字；确认客户端已登录进大厅；必要时点「重连客户端」 |
| 浮窗在游戏后面 | 看 Z 序；确认 `topmost_loop` 在跑；仍不行改游戏「窗口化」 |
| 浮窗一闪就没 | 确认 `autoCloseOverlay=false`；看 `data/watcher.log` |
| 海克斯没识别 | 看控制台「OCR 识别原文」；微调 `region`；或手动添加 |
| 出装没随海克斯变 | 确认点了「选它」把海克斯加入已选列表 |
| 「第 N 次」不对 | 控制台「海克斯阶段」改成手动 1~4；漏选/多选海克斯会带偏自动推断 |
| 敌方阵容不显示 | 只有对局内（Live Client Data）才有；选人阶段为空是正常的 |
| 「下一件」不准 | 读 Live 接口有 2~3 秒延迟；散件/回城前购买会短暂对不上，重新轮询即恢复 |

---

## 14. 后续可做

已完成（原待办）：
- ✅ 用「第 N 件」slot 数据做分件替换建议 → `build.slotAdvice`，在出装区显示「路线 X → 可换 Y（+Npp，样本）」
- ✅ 敌方阵容（AD/AP/坦度）参与出装评分 → `enemy_profile()` + `_enemy_bonus()`，并给克制备选
- ✅ 多海克斯组合加成接入评级 → `augmentCombinations` 成对样本，协同分单独可解释

还可以继续：
- **OCR 多帧投票**：同一轮卡牌连续识别 2~3 次取交集，减少抖动与误识别（目前靠 25s 保持窗口）
- **一键替换分件**：把 `slotAdvice` 做成按钮，直接改写本次推荐顺序或写入配装方案
- **敌方出装时序**：现在只看「已买什么」，可再加「谁最肥（等级/装备数）」，给集火与出装优先级
- **多海克斯（3 个以上）组合**：数据没有三件样本，只能做启发式（同构筑方向加权），需要明确标注「经验」
- **战绩/熟练度**：LCU 可读近期对局，可给「你玩这个英雄的胜率」与版本胜率做对照（涉及隐私，需用户明确同意）
- 若换用 Electron/Overwolf 可更稳地解决全屏置顶，但会引入依赖
