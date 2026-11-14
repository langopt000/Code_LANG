# ARAM 海克斯大乱斗实时助手 · 交接文档

> 项目目录：`E:\78zaoCoding\Code_LANG\aram-assistant`（2026-11-10 从 WorkBuddy 临时会话目录迁移至此）
> GitHub：`https://github.com/langopt000/aram-assistant`（子目录 `aram-assistant`）
> 文档日期：2026-11-10 · 数据版本：ARAMKit v16.22（2026-11-09，高分段 4,297,659 场）
> 技术栈：Python 3 标准库 + 单页 HTML/CSS/JS，零第三方依赖、零 npm 包、零 Electron

---

## 1. 这个项目做什么

一个本地运行的 LoL 海克斯大乱斗（ARAM Mayhem）辅助浮窗，数据取自 `aramkit.cn` 同源的公开统计，满足以下需求：

| # | 需求 | 实现 | 数据来源 |
|---|---|---|---|
| 1 | 英雄选择时给出每个英雄的排行和胜率 | LCU 读取共享备选席 `benchChampions`，列出排名/梯队/胜率/选取率并按胜率排序 | `champion-rankings.json` |
| 2 | 海克斯出现后显示选取率、胜率、评级 | 屏幕 OCR 识别三选一卡牌 + 模糊匹配；展示全局胜率/选取率/梯队/稀有度、**本英雄搭配胜率与 ±pp**、**第 N 次阶段胜率与阶段排名**、**与已选海克斯的组合胜率**、卡牌原文说明、综合推荐分 | `augment-rankings.json`（含 `stages.1~4`）+ `champion-details/{id}.json` + `resources/augments.json` |
| 3 | 结合当前海克斯推荐出装与顺序 | 按已选海克斯切换专属样本，输出「出门装 → 鞋子 → 第 1/2/3 件 → 后续成装」 | `champion-details/{id}-single-augments.json` |
| 4 | 出装要跟着敌方阵容走 | 由敌方英雄职责 + 已买装备推断物理/法系/坦克/回复/堆抗性，折算为装备加分并给出针对性备选；加成原因标在装备后面 | `resources/items.json`（categories + tooltip）+ `item-rankings.json` |
| 5 | 对局中知道「下一件买什么」 | 读 Live Client Data 的己方装备，按实际已出成品数推进到下一件；排除已买与重复鞋子后，列出这一件胜率最高的 3 个 | `item-rankings.json` + `champion-details/{id}.json` 的 `items.slots` + 购买路线 |

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
- **默认收起**：右上角一条 `260×92` 小胶囊，显示「英雄 · N 个海克斯 · 已选 N」，点「展开」才变成 `400×820` 完整面板，点「收起」回去。
- **智能展开（默认开）**：对局中平时保持小胶囊，只在「海克斯三选一出现」或「商店打开」时自动展开成完整面板；离开商店、选完海克斯后自动收回胶囊。英雄选择阶段仍照旧自动弹出（备选席排行就是要在这时看）。
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
   └─ 按海克斯合并出装顺序 + 当前装备核对（按实际成品数推进下一件 / 胜率前三）
        ▲
        │  app.py（HTTP 服务 127.0.0.1:8765 + 后台线程）
        ├── league.py ── LCU (端口由 lockfile/日志发现) + Live Client Data (2999)
        ├── vision.py ─ GDI 截屏 → Windows.Media.Ocr → 模糊匹配；`detect_shop` 用关键词判断商店是否打开；`detect_chat`+`parse_chat_picks` 读聊天「已选择了X」广播自动记录本人已选海克斯
        ├── window.py ─ Edge/Chrome --app 无边框窗 + Win32 置顶/透明/穿透；`is_game_foreground` 前台守卫
        └── watcher.py ─ 检测客户端进程，监管服务
        ▼
   ui/index.html（单页控制台 / 浮窗，轮询 /api/state）
```

后台线程：`poll_loop`（1.2s，游戏状态 + 智能展开判定）、`ocr_loop`（0.5s tick；海克斯 3.5s、商店 2.0s、聊天 3.0s 到期才识别）、`topmost_loop`（0.3s，重新置顶）、`watcher` 独立进程（5s）。

**智能展开状态机**（`app.py` → `_decision_token` / `_apply_smart_visibility`）：
- 决策 token：`bench`（英雄选择阶段）、`offer:<三张卡 id>`（卡面 10s 内见过）、`shop:<第几次商店>`（商店 4.5s 内命中过）；空串 = 无决策。
- 只有 token 变化才动窗口（`setCollapsed`），避免识别抖动导致面板反复伸缩。
- 只在 `ChampSelect / InProgress / GameStart / Reconnect` 生效，且**只在游戏窗口位于前台时**才动作：点浮窗/切浏览器时状态冻结，不会把面板顺手收走。
- 用户在决策时刻手动收起/展开会写入同一份状态，本决策时刻内不再自动纠正；下一次决策/下一局重新接管。
- 商店识别与海克斯识别解耦：即使关掉「海克斯 OCR」，只要智能模式开着，仍会做商店识别。

---

## 4. 文件清单

| 文件 | 职责 |
|---|---|
| `app.py` | HTTP 服务、状态机、所有 API、控制动作、`/api/diagnose`；阶段推断（已选数量+1，可手动覆盖） |
| `datafeed.py` | ARAMKit JSON 客户端：版本发现、缓存、进程内 TTL memo、名称/图标/装备类别与说明文本、索引 |
| `engine.py` | 海克斯评级（组合/阶段/协同）、敌方阵容画像、出装排序、各件位胜率排行与序贯推荐、实时装备核对、搜索 |
| `league.py` | LCU 连接（多路径凭据发现）、Live Client Data |
| `vision.py` | GDI 抓屏、PNG 编码、Windows OCR、`match_augments` 模糊匹配、聊天选取广播解析（`parse_chat_picks`，按召唤师名/英雄名归属本人） |
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
- `champion-details/{id}.json` → `items.filtered.slots{"1".."6"}`：本英雄每个件位的装备样本，用于「下一件」胜率前三与克制备选。

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

> Riot 的接口**不暴露海克斯卡牌**，这是必须走屏幕 OCR 的原因（商业插件同理）。
> 但聊天会广播「某某 (英雄) 已选择了XX」——`autoRecord` 据此自动记录本人已选海克斯，无需手动点「选这个」。

---

## 7. 浮窗实现要点

- Edge/Chrome `--app` 无边框窗口，独立 `--user-data-dir`（每次新目录，避免单例锁把请求转给旧实例后静默退出）。
- **DPI**：`GetDeviceCaps(LOGPIXELSX)/96` 得到缩放（本机 125%）；Chromium 的 `--window-size/position` 用逻辑像素需除以 scale，Win32 `SetWindowPos` 用物理像素。
- **64 位句柄**：`HWND_TOPMOST` 必须是 `ctypes.c_void_p(-1)`，直接传 int `-1` 会被截成 `0xFFFFFFFF` 导致 `SetWindowPos` 静默失败（曾导致浮窗永远被游戏盖住）。模块级已声明 argtypes。
- **置顶**：`attach_to_game()`（`GWLP_HWNDPARENT` 附属到 `RiotWindowClass`）+ `keep_on_top()`，由 `topmost_loop` 每 0.3s 重新置顶；必要时把游戏窗口从 TOPMOST 降级。
- **折叠**：`set_collapsed()` 改窗口尺寸（收起 260×92 / 展开 400×820），右边缘固定。
- **OCR 与浮窗互不干扰**：识别区默认 `[0.18, 0.14, 0.58, 0.60]`，右边界 0.76；浮窗在最右 0.84 之后，不会被 OCR 读到自己的文字。
- 点击穿透：`WS_EX_TRANSPARENT`；透明度：`WS_EX_LAYERED` + `SetLayeredWindowAttributes`。

---

## 8. 配置（`data/config.json`）

| 字段 | 默认 | 说明 |
|---|---|---|
| `dataset` | `high` | `high` 高分段 / `all` 全分段，对应站点 URL 参数 |
| `ocrEnabled` / `ocrInterval` | `true` / `2.5` | OCR 开关与轮询秒数 |
| `region` | `[0.18,0.14,0.58,0.60]` | 屏幕比例 `(x,y,w,h)`，框住中央卡牌 |
| `opacity` | `0.92` | 浮窗整体透明度 |
| `clickThrough` | `false` | 鼠标穿透（开启后浮窗本身不可点，需在控制台关） |
| `theme` | `dark` | `dark` / `light` |
| `autoOpenBrowser` | `true` | 手动 start.bat 时自动开浏览器 |
| `autoOverlay` | `true` | 进入选人/对局自动弹浮窗 |
| `autoCloseOverlay` | `false` | **默认不自动关**，避免 LCU 抖动导致"弹一下就没" |
| `smartShow` | `true` | 智能展开：只在海克斯三选一 / 商店打开时展开，其余时间收成胶囊 |
| `shopRegion` | `[0,0.02,0.72,0.18]` | 商店识别区域（屏幕比例，框商店顶部标签/搜索栏），控制台可调 |
| `shopInterval` | `2.0` | 商店状态识别间隔（秒） |
| `shopGrace` | `4.5` | 商店看不见后仍算"在商店"的宽限，避免翻页/动画时误收 |
| `offerGrace` | `10.0` | 海克斯卡面消失后仍算"在选海克斯"的宽限 |
| `autoRecord` | `true` | 聊天「已选择了X」广播自动记入已选海克斯（只记本人，浮窗标「自动」） |
| `chatRegion` | `[0,0.55,0.32,0.22]` | 聊天识别区域（屏幕比例，框左下聊天框），控制台可调 |
| `chatInterval` | `3.0` | 聊天识别间隔（秒） |
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
| POST | `/api/ocr` | 立即识别一次（多帧投票口径，画面里没有卡名命中时同步清掉陈旧卡面） |
| POST | `/api/refresh` | 重新同步数据并立即识别一次屏幕 |

`control` 动作：`setChampion`、`clearManual`、`addAugment`、`removeAugment`、`clearAugments`、`setStage`（0=自动，1~4=手动）、`setDataset`、`setOcr`、`setRegion`、`setTheme`、`setOpacity`、`setClickThrough`、`setAutoOverlay`、`setSmartShow`、`setShopRegion`、`testShop`、`setAutoRecord`、`setChatRegion`、`testChat`、`setArchetype`（value=流派 key，空串=自动）、`setOverlayPosition`、`setCollapsed`、`launchOverlay`、`testOverlay`、`closeOverlay`、`redetect`、`refresh`、`quit`（关浮窗并干净退出服务，供发行版「退出助手.bat」用）。

`/api/state` 里与新增功能相关的字段：
- 顶层 `stage` / `stageMode`：当前第几次海克斯、是否手动指定。
- `smartShow` / `decision`（`bench`/`offer`/`shop`/空）/ `overlayCollapsed`（当前实际折叠态）/ `overlayCollapsedPref`（用户偏好）。
- `candidates[]`：除原有胜率/评级外，新增 `description`（卡牌原文）、`stageWinRate` / `stageRank` / `stageOk`、`synergyWinRate` / `synergyPartner` / `synergyDelta`、`scoreBase` / `scoreStage` / `scoreSynergy`。
- 商店识别：`shopOpen`、`shopHit`（命中的关键词）、`shopText`（OCR 原文，调试用）、`shopError`、`shopRegion`。
- 聊天识别：`autoRecord`（开关）、`chatRegion`、`chatText`（OCR 原文，调试用）、`chatError`；`selectedAugments[].auto` 标记该已选是否来自聊天广播自动记录。
- `build`：新增 `profile`（敌方画像与建议）、`next` / `owned` / `offPlan` / `progress`（实时装备核对）、`slotRanking`（各分件位置候选）、`picks`（按实际已出成品数推进的下一件前三）、`counters[]`（针对性备选）。`picks` 规则：已出成品数（`final && acq=shop && price>=900`，含成装鞋）决定「第 N 件」；排除已买、样本 <800、非商店成品、已有鞋子时的其它鞋；按原始胜率排序；`route` 标记表示真实购买路线里紧随已出装备的接法，仅作标签、不改排序。


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
| 已买的散件仍显示「未买」、下一件还是散件 | 只把 `isFinal` 装备算「已有」 | 进度按实际购买 id 判断；「计划外」只算非鞋子成品 |
| 鞋子被推荐成「不买鞋子」 | 数据里 `id 0`（无鞋）确有 ~60% 胜率与高选取率，不是 bug | 保留该选项，仅把名字显示为「不买鞋子」 |
| 页面越用越吃 CPU | 每 1.2s 轮询都重新 `json.loads` 排行榜与 1.6MB 英雄详情 | `fetch_json` 加进程内 TTL memo（已解析对象直接复用） |
| 窄浮窗里装备名被拆成两行 | `.it` 默认按字符换行 | `.it{white-space:nowrap}`，靠 `.its` 整块换行 |
| 三海克斯组合查不到胜率 | 数据只有 `augmentCombinations` 的成对样本 | 只对「已选 1 个 + 本张卡」做组合加成，3 个以上不臆造 |
| 浮窗把浏览器里的「搜索」当成商店打开 | 截屏抓到的是前台浏览器，不是游戏 | `window.is_game_foreground()`：前台不是 `RiotWindowClass` / 标题不像 LoL 时，OCR 与智能折叠全部跳过 |
| 点浮窗时面板莫名收起 | 浮窗成了前台窗口，智能判定以为「离开了游戏」 | 非游戏前台时智能状态冻结，不改窗口、不改 token |
| LCU 抖动导致刚弹出就被收成胶囊 | phase 瞬间 `None` 被当成离开对局 | 仅在 `autoCloseOverlay` 且 `None` 持续 >15s 才清识别状态并关窗 |

---

## 11. 已知限制

1. **服务必须由用户桌面会话启动**（双击 `ARAM-Assistant-Launch.bat`）；AI 会话内启动的进程会被回收。
2. **全屏独占 / 全屏优化**可能仍压住浮窗。高频置顶已加；若仍无效，把 LoL 视频设置改「窗口化」，或对 `League of Legends.exe` 禁用「全屏优化」。
3. **OCR 依赖 Windows 中文识别器**（`ocr.ps1` 优先选 `zh*`），识别不准可在控制台微调 `region` 后点「立即识别」验证；也可用「手动添加海克斯」兜底。
4. 大厅/主页面按设计**不弹浮窗**，仅 `ChampSelect` / `InProgress` 等阶段自动弹。
5. **商店状态没有官方接口**：LCU 与 Live Client Data 都不暴露「商店是否打开」，只能屏幕 OCR 关键词（商店/推荐装备/全部装备/已购/购买/出售/搜索）。区域或分辨率差异导致识别不到时，在控制台调 `shopRegion` 后点「商店识别测试」；识别不到只会表现为「不自动展开」，不影响其它功能。
5. Edge 冷启动偶发找不到窗口句柄（等待已放宽到 25s，失败会清理半开进程），再点一次「展开」或重新双击启动器即可。
6. 评级与出装评分是**加权排序**，不改变原始胜率；样本量小的组合会降权，但不代表因果。
7. `schtasks.exe` 在本环境被安全策略禁用，未使用计划任务方案；开机自启走 Startup 文件夹。
8. **敌方阵容是启发式画像**：靠英雄职责 + 已买装备类别推断物理/法系/坦克/回复，敌方装备还没成型时会偏；由此产生的加分与「针对性备选」属经验建议，不是胜率因果（平均胜率偏低也正常）。
9. **组合加成只有成对样本**（`augmentCombinations`），已选 2 个以上海克斯时只能取「本张卡 + 其中 1 个」的最高组合，不做三件以上的臆造。
10. **阶段默认按「已选数量 + 1」推断**：OCR 漏识别或手动加错会让阶段偏移，控制台可手动指定 1~4。
11. **智能展开依赖屏幕识别**：识别间隔 2s，关闭商店后最多约 6s 才收回胶囊；海克斯卡面消失后最多约 10s 收回。属有意留的宽限，避免翻页/动画时反复伸缩。
12. **自动记录已选依赖聊天可见**：游戏设置里关掉聊天显示、或广播已淡出时记不到；默认 `chatRegion` 按 1440p 左下聊天框取，其它分辨率微调后用「聊天识别测试」验证。记不到时手动添加兜底，功能不受影响。

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
- 性能：`build()` 平均 3.9ms（含敌方画像与克制备选），`state()` 平均 0.2ms、JSON 26.5KB；加 memo 前每次轮询要重复解析 1.6MB 详情。
- 界面：330px 宽（=400 物理像素 @125%）下无横向溢出；标签、说明、协同、下一件、敌方 chips、步骤勾选、克制备选全部在卡片内。控制台阶段选择器 5 个按钮完整。
- 语法：Python 全量 `py_compile`、页面 JS `node --check`、无头浏览器渲染均通过。

**2026-11-10 第三轮（智能展开：只在海克斯/商店时弹出）**
- 需求确认：非决策时刻收小胶囊；英雄选择阶段保留自动弹出。
- 接口复核：LCU / Live Client Data 均不提供商店开关状态 → 商店识别走屏幕 OCR 关键词（商店 / 推荐装备 / 全部装备 / 已购 / 购买 / 出售 / 搜索）。
- 防误判：OCR 与智能折叠只在 `is_game_foreground()` 为真时执行；实测曾截到 Bilibili 全屏页面，加守卫后不再误判。
- 状态机：`bench` / `offer:<ids>` / `shop:<epoch>` 三态 token，只在变化时改窗口尺寸；`shop_epoch` 区分同一局多次开商店；用户手动折叠优先；`ocrEnabled` 关闭时仍保留商店识别。
- 语法：`py_compile app.py vision.py window.py` 通过。
- 页面：本地静态服务 + 无头浏览器验证 — console 仅 3 条预期 `/api` 404、无 JS 错误；智能开关「已开启」、商店状态「未检测到商店」、`shopRegion` 回填 `[0,0.02,0.72,0.18]`、备选席 5 行、海克斯 3 张卡。
- 待办：默认 `shopRegion` 需在真实商店打开时用控制台「商店识别测试」复核（不识别只影响自动展开，不影响其它功能）。

**2026-11-10 第四轮（出装区简化）**
- 按用户反馈移除「查看分件备选」（路线→替换 + pp/样本的写法看不懂）与「写入游戏内配装方案」（无用）。
- 改为在「下一件」下方固定显示「第 N 件 · 胜率最高的 3 个」：按下一件所在分件位置取 `slotRanking`，按原始胜率排序，过滤样本 < 800 的极值；出门装/鞋子阶段退回第 1 件排行。
- 后端同步删除 `itemset` 动作、`push_itemset()`、`league.push_item_set()` / `current_summoner()` 与 engine 的 `slotAdvice`。
- 语法 `py_compile` 通过；无头浏览器渲染验证 pickBox 三条、旧按钮消失、无 JS 错误。

**2026-11-10 第五轮（出装序贯推荐）**
- 用户反馈：出装是序贯决策，推荐要基于已经买到的装备继续往下给，而不是照抄计划顺序。
- 引擎新增 `build.picks`：按实际已出的成品数（含成装鞋）定位「第 N 件」，不再依赖计划里的 `next.slot`；排除已买装备、样本 <800 的极值、非商店成品（如金铲铲），已有鞋子时不再推第二双鞋；候选池内部保留每件位前 12 个，过滤后仍尽量凑满 3 个。
- 用真实购买路线给候选打 `route` 标记：已出成品在路线里出现时，取它之后紧接着的下一件；仅作「同路线」提示，不改原始胜率排序。页面只在部分候选带标记时显示该标签，三项都同路线时不重复刷屏。
- 「下一件」改为显示 `picks` 第一名；计划外提示不再把成装鞋算作计划外（鞋子本来就可能偏离三件套路线）。
- 验证：卢锡安 + 珠光手镯，已出狂战士胫甲 + 收集者 → `第 3 件`，公理圆弧 61.3% / 无尽 59.0% / 多米尼克 57.8%；空装备 → 第 1 件；已出 4 件成品 → 第 5 件；6 件成品 → 「六件装备已出齐」。400px 浮窗宽度无横向溢出，console 仅预期 `/api` 404。

**2026-11-11 第六轮（免安装发行包）**
- 需求：发给别人解压即用，不装 Python、不装任何依赖。产物 `dist/ARAM助手-免安装版.zip`（约 10.8 MB，49 个文件，`dist/` 已 gitignore）。
- 结构：`runtime/` = Windows embeddable Python 3.11.9 amd64（python.org 官方包），`python311._pth` 追加 `..` 把应用根目录挂进 sys.path；其余为源码 + `ui/` + `ocr.ps1` + 三个 bat + `stop.py` + `使用说明.txt`。不含 `data/`（首次运行自建）、不含缓存/预览图/文档。
- 启动链路：`启动助手.bat` → `runtime\python.exe show_overlay.py`（`sys.executable` 推导 pythonw，守护/服务全部落在 runtime 内）；`退出助手.bat` → `stop.py` 写 `data/watcher.stop` 并 POST `quit`；`诊断启动.bat` → 控制台跑 `app.py` 看报错。
- 配套代码改动：`app.py` 新增 `quit` 动作（`SERVER` 全局 + `_shutdown_server()` 线程调 `shutdown()`）；`watcher.py` 停止分支改为无条件 POST `quit`（能停掉 show_overlay 直接拉起的服务），自动拉起前复查 `STOP_FLAG` 防退出竞态；`show_overlay.py` 超时提示不再指向 start.bat。
- 验证：embeddable 解释器 stdlib（ssl/ctypes/http.server 等）+ 全部模块 py_compile + 整体 import 通过；zip 解压回测两次均 ok；`quit` 在 8799 端口独立验证服务干净退出（不碰用户 8765 实例）。
- 坑：`stop.py` 早期版本模块级直接执行，被 import 时会对 8765 发 quit —— 已加 `__main__` 守卫；测试脚本不要 import 带副作用的入口。bat 用 UTF-8 无 BOM + 首行 `chcp 65001`；zip 内中文文件名靠 Python zipfile 的 UTF-8 标志，Win10/11 资源管理器可正常解压。

**2026-11-11 第七轮（已选海克斯自动记录，修手动依赖）**
- 发现：真机截图左下聊天有系统广播「04:49 本人 (诺克萨斯统领) 已选择了沃格勒特的巫师帽.」——选取会进聊天，名字是纯文本，可走现有 OCR 管线，无需读内存。
- `vision.py` 新增 `detect_chat` / `parse_chat_picks`：只认含「已选择了」的行，且行内必须出现本人召唤师名 / 英雄名 / 称号（聊天广播所有人，必须归属）；名称片段走 `match_augments`，门槛 0.75。
- `app.py`：`ocr_loop` 加 3s 聊天节拍；`_run_chat_ocr` 自动记入 `self.selected` 并标 `auto`；同一广播行只处理一次（`_chat_done`），手动删除后不会被旧广播加回；换英雄清空。新增 `autoRecord` / `chatRegion` / `chatInterval` 配置与 `setAutoRecord` / `setChatRegion` / `testChat` 动作。
- 单测：精确广播 1.0；OCR 错一字 0.803 仍命中；他人广播 / 击杀消息 / 空归属一律不记；两条广播按屏幕顺序记录。
- mock 集成：自动记录后 `build` 切专属样本、`stage` 自动推进 2、`selectedAugments[].auto=true`；重复扫描去重、手动删除尊重。
- 语法 `py_compile` 通过；无头浏览器控制台 / 浮窗两模式无 JS 错误（仅预期 `/api` 404），新开关 / 区域 / 测试按钮 / 「自动」标签渲染正常。
- 待真机复核：默认 `chatRegion` 需在真实对局用「聊天识别测试」验证（不识别只影响自动记录，手动添加兜底）。

**2026-11-11 第七轮（OCR 误报收紧）**
- 用户反馈：没有三选一时浮窗也展开显示海克斯。实测日志：对局中反复「识别到海克斯：缩小引擎（1.0）/ 王冠之重（0.771）」→ 自动展开，用户手动收起。来源是计分板/聊天里出现的已选海克斯名字，不是卡面。
- 旧逻辑两个洞：① 名字在 OCR 整段任意位置出现（`name in flat`）即 1.0，绕过了短行过滤；② 单帧只要 1 个 >=0.65 的命中就采纳并保留 25s，模糊噪声也能撑开浮窗。
- 修复：`match_augments` 删除整段子串捷径，精确命中只认短行（== 行 1.0 / 行内且行长 <= 名长+3 0.95），返回全部命中（limit 默认 None）；新增 `app.offer_from()`：同一帧 2~3 个不同海克斯且各自 ocrScore>=0.9 才采纳为三选一（真实卡面是 3 张大字卡；计分板一大片 >3 拒、聊天通常 1 个或长句拒、模糊高分不触发）。自动识别与手动识别共用该规则；`/api/ocr` 返回新增 `accepted`，`candidates` 仍给全部命中供校准。
- 验证：239 个真实海克斯索引，9 用例全过 —— 3 张干净卡 / 带稀有度后缀 / 名字带空格 / 只读到 2 张 → 采纳；1 精确+1 模糊（误报复现）/ 计分板 5 名 / 聊天长句含 2 名 / 对局噪声 → 拒绝；长名压制短名保持。发行 zip 已同步重建。
- 取舍：极小概率 OCR 只读清 1 张卡时不自动展开（等下一帧或手动添加）；计分板恰好只显示 2~3 个海克斯时仍可能误展开，概率远低于旧逻辑。
- 商店误报同步收紧：实测聊天文字让「搜索」单独命中就判商店打开（20:45/20:47 两次）。`shop_hit` 改为强信号（商店/推荐装备/全部装备/已购装备/已购买/recommended/all items）任一、或弱信号去重后 >=2 才开；短词被长词包含只算长词（「已购装备」不再拆成「已购」+「已购买」凑数）。10 用例全过。
- 用户持续看到误弹的直接原因：服务进程 20:09 起一直跑旧代码，修复未生效。20:51 用 watcher.stop 停旧守护+服务后重拉，新进程确认带 `accepted` 字段；死亡回放/聊天噪声帧在新规则下零命中。发行 zip 同步重建（含另一会话加的聊天广播自动记录功能，该功能只写 selected、不影响弹出判定）。

**2026-11-11 第八轮（手动选流派）**
- 用户反馈：应该能选择走什么流派，推荐出装才准。此前流派由 `_profile_score` 自动选最高分，用户想走 AP 时可能仍推暴击。
- `engine.build(..., archetype=None)`：候选按 key 过滤（含海克斯专属样本的 archetypes）；样本里没有该流派时退回自动并在 notes 说明。手动流派时「第 N 件」候选池额外限制为该流派 profiles 真实出过的装备（itemSet + purchaseOrder + laterItems + boots 的并集），过滤后为空则退回全池——避免 AP 路线推无尽。`_route_support` 自然只在过滤后的候选里取路线。
- build 返回新增 `archetypes`（该英雄可选流派 [{key,name}]，按最优分排序去重）、`archetypeForced`；app 新增 `archetype_override` 状态 + `setArchetype` 动作 + state 顶层 `archetypeOverride`，并进 build 缓存 key。换英雄不重置（不可用时引擎自动退回，页面标签可见可改）。
- 页面：出装区顶部新增流派标签行（自动 + 各流派，`.chip.sel` 高亮当前），点击 `ctrl('setArchetype', key)`；DEMO_STATE 补 archetypes。
- 验证：卢锡安自动=暴击（33 万样本）/ 手动穿甲 → 路线改巨蛇之牙起手、第 3 件候选全穿甲件（公理圆弧/赛瑞尔达/无穷饥渴）；指定不存在的 AP → 退回自动+提示；珠光手镯专属样本下强制穿甲同样生效。400px 浮窗宽度标签行无溢出，自动/选中两态正确，点击接线正常，console 仅预期 /api 404。发行 zip 同步重建。

**2026-11-11 第九轮（选取即时交互反馈）**
- 用户反馈：手动选中海克斯时「非常没有交互感」——点了没动静，不知道选上没有。
- 乐观更新：`pickAug` / `dropAug` 点击即改本地视图（`pendingSel` / `pendingRem`），不等 1.2s 轮询；`poll` 回来用服务端 `selectedAugments` 校准，6s 未确认才回滚并提示失败。换英雄/换局清空乐观状态。
- 即时反馈：卡片按钮「选这个」→ 禁用态「已选 ✓」（stampIn 盖章动画）；卡片加 `.picked` 绿描边+渐变底、`.just-picked` 一次性辉光；已选 chip 飞入（chipIn）；底部 toast 确认（移除带「撤销」按钮，4.2s）；出装来源变化时整块 `.flash` 高亮；阶段标签 / 顶部徽章变化 `.pulse`。所有按钮加按压回弹（`:active scale(.94)`）。
- 动画不被轮询截断：`setHTML` 只在 HTML 变化时重写，`freshMark` 给新元素保留动画类至少 800ms（卡片）/420ms（chip），入场动画 `.enter` 只在卡面指纹变化时播一次。`prefers-reduced-motion` 全量关闭动画。
- 顺带修复演示模式标志：旧逻辑第二次轮询失败就把 `demo` 置 false，导致演示态选取走服务端路径 6s 后回滚报错。新增 `everLive`，从未连上服务则一直是演示。
- 验证（无头浏览器，控制台+浮窗两模式）：选取→盖章/绿框/chip 飞入/toast；移除→chip 消失+撤销 toast；撤销→恢复；连续 4 轮快速选/删 toast 上限 2、无残留 pending；动画窗口后类名干净移除；出装来源变化触发 flash；折叠态 toast 隐藏（display:none）；console 仅预期 /api 404、无 JS 错误。本会话浏览器视口不可见，布局未截图复核，但新增 CSS 均为 transform/opacity/box-shadow/background 与 fixed 定位，不改变元素尺寸，无新增溢出。

**2026-11-14 第十轮（OCR 多帧投票 + 刷新联动识别）**
- 用户反馈：①海克斯三选一卡面有时不弹出；②点「刷新」后识别结果不更新。两个独立根因：
  不弹出 = 第七轮单帧口径要求一帧里 2~3 个 >=0.9 强命中，OCR 单帧经常只读清 1~2 张（错一字就掉到 0.8x），整帧作废；
  刷新不更新 = UI「刷新」只调 `/api/refresh` 重同步数据从不跑 OCR，且被拒帧永远不替换 `candidates`（旧卡最长残留 25s）。
- `app.py` 新增 `OfferTracker` 多帧投票（`offer_from` 单帧口径保留为一轮首帧快路径）：
  强命中（>=0.9）任一帧出现即算，次强命中（0.8~0.9）需 >=2 帧复现，入选 id 还须在最近 2 帧内出现过（防旧轮残留回魂）；
  合并 2~3 个才采纳；一帧 4+ 强命中或合并 >3 个按计分板/列表拒绝（进行中的一轮不被 Tab 瞥一眼污染）；
  换轮切断：新帧命中与历史完全不相交 → 历史作废并立即清陈旧卡面（换卡/重随自动换轮，重随 1~2 帧内完成新旧替换）；
  连续 2 个干净空帧只清投票不动展示（offerGrace 宽限不变）；超窗（9s）旧票自动出局。
- 自动识别与手动识别改走共用 `_apply_offer_frame`：换轮即清陈旧候选（不再等 25s）；同一组 id 重复采纳不换对象不重排，防浮窗卡片抖动；
  手动识别（含刷新链路）画面里连一个 >=0.8 命中都没有时以画面为准清空陈旧卡面；`/api/ocr` 遇自动识别在跑时最多等 3s 接手，不再直接「正在识别中」。
- `/api/refresh`（控制台「刷新」按钮）链式触发一次屏幕识别——「刷新即重认」，两个刷新按钮 tooltip 同步改为「重新同步数据并识别屏幕」。
- 聊天广播自动记录到选取时清投票（本轮三选一已结束，防残留命中再弹）；换英雄/关浮窗同样清投票。
- 识别节拍 `ocrInterval` 3.5s → 2.5s（DEFAULT_CONFIG 与 data/config.json 同步）。
- 验证：35 用例全过——第七轮单帧口径 6 例无回退（3 干净卡/只读 2 张强命中采纳；1 精确+1 模糊 0.771/计分板 5 名/两模糊拒绝）；
  多帧新场景 21 例（1 强+次强跨帧弹出并逐帧补全第 3 张、0.771 永不投票、换轮切断清陈旧、重随甲乙丙→甲乙丁 2 帧完成替换、
  2 空帧清票防回魂、计分板帧不污染进行中一轮、超窗旧票不并入）；`_apply_offer_frame` 集成 8 例（换轮立即清、手动空画面清、超 25s 兜底清）。
  `py_compile` 全量通过。取舍：次强命中 2 帧复现的阈值（0.8）若真机出现持续性短行贴纸误弹，调 `OfferTracker.WEAK`/`PROMOTE_FRAMES` 即可。


---

## 13. 故障排查速查

| 症状 | 先查 |
|---|---|
| 完全看不到浮窗 | 端口 8765/8766 是否占用 → 没跑就双击桌面启动器 |
| 自检里 LCU 红点 | 看 `source`/错误文字；确认客户端已登录进大厅；必要时点「重连客户端」 |
| 浮窗在游戏后面 | 看 Z 序；确认 `topmost_loop` 在跑；仍不行改游戏「窗口化」 |
| 浮窗一闪就没 | 确认 `autoCloseOverlay=false`；看 `data/watcher.log` |
| 海克斯没识别 | 看控制台「OCR 识别原文」；点「刷新」/「重新识别」会立即重认一遍；微调 `region`；或手动添加 |
| 识别结果不更新/还是旧卡 | 第十轮已联动：「刷新」会重同步数据并重新识别；换卡/重随后旧卡面最多 1~2 个识别周期（约 2.5~5s）内被换掉；仍残留就看 `ocrText` 是否一直读不到新卡名 |
| 没三选一却展开显示海克斯 | 第七轮单帧口径 + 第十轮多帧投票已收紧；仍出现就看控制台 OCR 原文里是什么文字撞了名字，必要时调高 `OfferTracker.WEAK` |
| 出装没随海克斯变 | 确认海克斯在已选列表里（聊天广播自动记录，或点「选这个」手动添加） |
| 已选海克斯没自动记录 | 看「自动记录已选海克斯」开关、聊天是否可见且广播还在；控制台 OCR 原文里看 `[聊天]` 段；微调 `chatRegion` 后点「聊天识别测试」；手动添加兜底 |
| 「第 N 次」不对 | 控制台「海克斯阶段」改成手动 1~4；漏选/多选海克斯会带偏自动推断 |
| 敌方阵容不显示 | 只有对局内（Live Client Data）才有；选人阶段为空是正常的 |
| 「下一件」不准 | 读 Live 接口有 2~3 秒延迟；散件/回城前购买会短暂对不上，重新轮询即恢复 |
| 「下一件」和步骤里的第 N 件不一样 | 「下一件」按实际已出成品数 + 当前这一件位的胜率算；步骤是路线参考，买了计划外装备时两者会分开 |
| 打开商店面板不自动展开 | 控制台看「商店识别」是否命中；没命中就调 `shopRegion` 后点「商店识别测试」；确认游戏在前台、「智能模式」开启 |
| 离开商店面板不收 | 看 `decision` 是否为 `shop`；识别 2s 一次 + 4.5s 宽限，最多约 6s 才收回；持续不收说明其它文字命中了关键词，看 `shopText` |

---

## 14. 后续可做

已完成（原待办）：
- ✅ 出装区改为按实际已出成品数推进的「下一件 + 胜率前三」→ `build.picks`，排除已买/重复鞋子，真实路线仅作「同路线」提示（原 `slotAdvice` 的路线→替换写法过于绕，已移除）
- ✅ 敌方阵容（AD/AP/坦度）参与出装评分 → `enemy_profile()` + `_enemy_bonus()`，并给克制备选
- ✅ 多海克斯组合加成接入评级 → `augmentCombinations` 成对样本，协同分单独可解释
- ✅ **OCR 多帧投票**：`OfferTracker` 跨帧合并（强命中并集 + 次强 2 帧复现 + 换轮切断），减少漏弹与抖动（第十轮）

还可以继续：
- **敌方出装时序**：现在只看「已买什么」，可再加「谁最肥（等级/装备数）」，给集火与出装优先级
- **多海克斯（3 个以上）组合**：数据没有三件样本，只能做启发式（同构筑方向加权），需要明确标注「经验」
- **战绩/熟练度**：LCU 可读近期对局，可给「你玩这个英雄的胜率」与版本胜率做对照（涉及隐私，需用户明确同意）
- 若换用 Electron/Overwolf 可更稳地解决全屏置顶，但会引入依赖
