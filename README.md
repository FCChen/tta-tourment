# Through the Ages 賽事計分 & 趣味統計系統 🎲

為桌遊群組設計的「無壓力」計分與數據儀表板。原本只記錄名次，現在可以自動解析遊戲 Replay，產生 50 項趣味頭銜、各時代人氣領袖與奇觀、行動順位分析等統計，讓大家不用只盯著排名。

- **前端**：純 HTML / JS（`index.html`），託管在 GitHub Pages
- **後端**：Google Apps Script（`Code.gs`）+ Google Sheets
- **Replay 解析器**：Python（`tta_parser.py`、`auto_collector.py`），在 Windows 上自動操作 Steam 版 *Through the Ages* 並讀取遊戲記憶體

---

## 📁 專案檔案

| 檔案 | 說明 |
|---|---|
| `index.html` | 前端網頁：登錄 GameID、總榜、個人儀表板 |
| `Code.gs` | Google Apps Script 後端：讀寫 Sheet、計算所有統計與頭銜 |
| `tta_parser.py` | 解析核心：讀取遊戲記憶體、解析 `TTA::GameStats` 紀錄、計算每場每位玩家的數據 |
| `auto_collector.py` | 自動化工具：開 Replay、推進到結算、擷取、上傳，支援批次處理 |
| `parsed_games/`（選擇性上傳） | 每場的解析結果 `<GameID>.json` 與原始紀錄 `<GameID>.gamestats.txt`。有了它，不用開遊戲也能用 `--reparse` 重算 |

專案已附 `.gitignore`，會排除快取、截圖、除錯用檔案與本機記錄檔。

---

## 🌐 網址與兩種版本

| 頁面 | 公開版（只有趣味統計） | 完整版（趣味統計 + 賽季積分） |
|---|---|---|
| 登錄 GameID | `https://fcchen.github.io/tta-tourment/` | — |
| 總榜 | `?p=p_leaderboard` | `?p=p_leaderboard&k=<FULL_VIEW_KEY>` |
| 個人儀表板 | `?p=p_1a2b3c`（每人一組代碼，定義在 `Code.gs` 的 `PLAYERS_HASH`） | `?p=p_1a2b3c&k=<FULL_VIEW_KEY>` |

- 沒有 `k` 金鑰時，**後端直接不回傳**積分、名次分佈、勝率矩陣、個人行動順位戰績等資料，所以公開網址看不到賽季積分。
- `FULL_VIEW_KEY` 定義在 `Code.gs`，`auto_collector.py` 也要設定成同一個值（查詢 Sheet 名次時會用到）。
- ⚠️ 原始碼公開後，金鑰也會一起公開。如果想繼續隱藏賽季積分，請在部署用的 `Code.gs` 和本機的 `auto_collector.py` 改成另一組金鑰，而且不要把新金鑰 commit 進 repo。

---

## 🛠️ 安裝與部署

### 1. Google Sheets + Apps Script
1. 建立 Google 試算表，第一個分頁的 A1~F1 依序為：`時間戳記`、`GameID`、`第一名`、`第二名`、`第三名`、`第四名`。
2. 「擴充功能 → Apps Script」，貼上 `Code.gs`。
3. 依需要修改 `PLAYERS_HASH`、`PLAYERS`、`FULL_VIEW_KEY`。
4. 「部署 → 新增部署作業 → 網頁應用程式」，存取權限設為「所有人」，複製網址。**每次修改 `Code.gs` 都要「管理部署作業 → 編輯 → 新版本」。**
5. 第一次執行時會自動建立 `GameStats` 分頁，存放每場的解析結果（JSON）。

### 2. 前端
1. 把 `index.html` 裡的 `API_URL` 換成上一步的網址。
2. 推上 GitHub 並開啟 GitHub Pages。

### 3. Replay 解析器（Windows）
- 需要 Python 3.10 以上，只用標準函式庫，不用另外安裝套件。
- 需要已安裝 Steam 版 *Through the Ages*，並登入一個帳號。
- 依自己的環境修改以下設定：
  - `tta_parser.py`：`TTA_GAME_DIR`（遊戲安裝路徑，用來讀卡片資料檔 `Cards/*.yaml`）、`LOGIN_TO_PLAYER`、`KNOWN_USER_ID_TO_LOGIN`（遊戲帳號 ↔ 網站名稱）
  - `auto_collector.py`：`DEFAULT_API_URL`、`FULL_VIEW_KEY`、`TTA_SAVES_DIR`

---

## ▶️ 使用方式

1. 比賽結束後，在登錄頁輸入 10 碼觀戰代碼（例如 `LATADELUMA`），名次免填。
2. 開啟遊戲，停在主畫面或線上大廳。
3. 執行：

```powershell
python auto_collector.py --no-upload      # 批次解析所有尚未解析的 GameID（自動跳過本機已解析過的）
python auto_collector.py --compare        # 比對 Sheet 名次與 Replay 名次
python auto_collector.py --upload-local   # 把本機結果一次上傳
```

腳本會全自動完成：點左上角眼睛 → 貼上代碼 → 把回合滑桿拉到最後 → 載入 → 推進最後動作與「继续」→ 擷取 → 回到主畫面 → 點「在线游戏」→ 下一場。

### 其他參數
| 指令 | 用途 |
|---|---|
| `python auto_collector.py` | 解析並直接上傳 |
| `--game-id XXXX` | 只處理單一場 |
| `--redo` | 批次時也重跑本機已有結果的場次 |
| `--reparse` | **修改解析規則後**，用新規則重算本機所有對局（不用開遊戲） |
| `--compare` | 比對每場的 Sheet 名次與 Replay 名次 |
| `--upload-local` | 上傳本機所有解析結果 |
| `--stay-in-replay` | 擷取後停在 Replay 畫面（除錯用） |
| `--stop-before-load` | 停在按下「Load Game」之前（除錯用） |

### 上傳保護
上傳時只有在「解析出 4 位賽事玩家，而且 Sheet 原本沒填名次或名次相同」時，才會寫入名次。不一致時會保留原本的名次、只上傳統計，並記錄到 `parsed_games/_rank_mismatch.txt`。

---

## 🎪 統計項目

總榜依分類顯示 50 個頭銜。個人儀表板會列出每一項的**自己的數值與全服名次**。單場紀錄都附上 GameID 與回合。

**門檻**
- 參與超過 10 場（`RANK_THRESHOLD`）才列入頭銜評選。
- 被逆轉王／逆轉勝王：相關場次需 ≥5 場。
- 侵略成功率／防禦成功率：次數需 ≥5。
- 反打王／普丁：已結算的戰爭需 ≥3 場。

| 分類 | 項目 |
|---|---|
| 🏆 勝負與逆轉 | 領跑王、逆轉勝王、被逆轉王、同分王、險勝一分王、惜敗一分王、投降王 |
| ⚔️ 戰爭 | 好戰王、苦主王、反打王（被宣戰卻戰勝）、普丁（宣戰卻戰敗）、北韓（宣戰當回合拆掉最多農場、礦場、城市建築）、提款王、提款機王、和平王 |
| 🗡️ 侵略與防守 | 卑鄙王（場均侵略成功）、單場侵略成功最多、侵略成功率、可憐王、防守王（場均擋下）、單場防禦成功最多、防禦成功率 |
| 🕊️ 外交、政治與事件 | 外交大師、毀約王、革命家、改革王（和平換政體）、政體專情王、最愛打事件、最不愛打事件、影響王 |
| 🏛️ 建設與奇觀 | 奇觀王、奇觀收集王、超級奇觀王（只算時代 III 奇觀完成分，加紅十字會每步 6 分）、城市建築王（同時在城市建築的工人數）、殖民王 |
| 📈 產能與行動紀錄 | 食物王、礦物王、科學王、文化王（單回合產量，含加成）、超級軍事王、白點王、紅點王、三白王（花 3 白點拿牌） |
| 📦 庫存紀錄 | 食物、礦物、科學庫存王（同一時間擁有最多） |
| 🎲 風格與災難 | 好色王（女性領袖率）、暴動王、老鼠王（單場）、焚書王（單場） |

另外還有：各時代全服／個人最愛領袖與奇觀、好色指數、最愛後宮角色、最愛政體（不含獨裁）。

### 🔍 數據分析（公開版與完整版都有）
- **行動順位分析**：P1（最先行動）~ P4（最後行動）各自的場均積分、第一名機率、前兩名機率。完整版的個人儀表板另外有自己的順位戰績。
- **總產量與名次相關性**：每位玩家每場的食物總產量（扣除消耗）、礦物總產量、科學總產量，和名次（1~4 視為線性）的皮爾森相關係數，以及各名次的平均值。

### 重要規則
- **同分**：遊戲判定同分時，行動順序較晚的玩家排名較前。
- **投降**：投降者算最後一名。
- **毀約**：主動取消合約，或攻擊自己的合約夥伴，都算毀約。因為簽新約、有人投降而被動取消的合約不算。
- **革命**：暴力革命（用光行動點）才算革命；用科學發展的算和平更換。

---

## 🔧 技術筆記（給想參與開發的隊友）

- 遊戲在記憶體中保存了每場的 `TTA::GameStats` 紀錄（每行如 `5 19 3 0 EOG_ORDER 1 0 0 231`：時代、回合、行動、玩家 slot、標籤、參數）。原始紀錄存在 `parsed_games/<GameID>.gamestats.txt`。
- 卡片在紀錄裡以數字 `statId` 表示。對照表直接讀遊戲的 `Cards/*.yaml` 與 `exp_data/v*/*.yaml`（文明卡和軍事卡的編號空間不同）。
- 已驗證的主要標籤：`ET_RATES`（科學/文化產量/武力）、`ET_STUFF`（生產前庫存）、`PR_FOOD` / `PR_RES`（生產階段）、`EOG_ORDER`（名次）、`P_WAR` + `WAR_*`（戰爭；結算行的 slot 是勝者）、`P_AGGR` + `AGGR_*` / `DEFEND`（侵略）、`P_PACT` / `PACT_BREAK`、`A_REV` / `A_DEV`（政體）、`P_RESIGN`、`E1_RATS` / `E1_DA`、`A_TAKE`（拿牌，第 4 參數是實付白點）。
- 主動取消合約沒有寫進 GameStats，改從 Replay 記憶體中的 `EventCancelPact` 物件取得。
- 新增統計的流程：在 `tta_parser.py` 的 `parse_gamestats_lines` 新增欄位 → `--reparse` → 在 `Code.gs` 的 `computeFunStatistics` 加總並加入 `AWARD_DEFS` → `--upload-local` → 重新部署。

---

## English summary

A stress-free scoring & fun-statistics dashboard for a *Through the Ages* (Steam) group.
`auto_collector.py` drives the game's spectate/replay UI automatically, reads `TTA::GameStats` from process memory (`tta_parser.py`), and uploads per-player statistics to a Google Apps Script backend (`Code.gs`). The static frontend (`index.html`) shows 50 categorized fun titles, per-Age favourite leaders/wonders, turn-order analysis and production-vs-rank correlation. A public (fun-only) view and a full view (with season scores, `&k=<FULL_VIEW_KEY>`) are served from the same URLs.
