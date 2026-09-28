# Casual Board Game Scoring System 🎲
*(Scroll down for English version)*

這是一個為桌遊同好群組設計的「無壓力計分與數據儀表板」系統。有別於傳統強迫所有人看總排名榜單的做法，本系統透過**專屬的 Hash 連結**，讓每位玩家只能看到自己的勝率與統計數據，專注於自我成長，大幅降低排名落後帶來的遊戲焦慮感。

前端使用純 HTML/JS 構建（可託管於 GitHub Pages），後端利用 Google Apps Script 串接 Google Sheets 儲存資料。

## 💡 系統特色

*   **專屬隱私儀表板:** 每個人都有一條專屬網址。點進去只會看到自己的總參與場數、場均積分、第一名次數，以及「對戰特定對手」的勝率。
*   **舒適的積分機制:** 第一至第四名配分為 `8, 5, 3, 2`。保有競爭梯度的同時，確保第四名依然能獲得保底的參與分數 (2分) 與榮譽星章 (🎖️)，避免零分挫折感。
*   **重複防呆機制:** 相同的 Game ID 再次上傳時，系統會自動覆蓋舊紀錄，修改補登非常方便。
*   **隱藏版總榜:** 結算賽季時，提供一組特殊的總榜 Hash，供所有人查看整體排名。

## 🛠️ 安裝與部署指南

### Step 1: 建立 Google Sheets 資料庫
1. 新增一個空白的 Google 試算表。
2. 將 A1 到 F1 依序命名為：`時間戳記`、`GameID`、`第一名`、`第二名`、`第三名`、`第四名`。
3. 點擊「擴充功能 > Apps Script」，貼上本專案提供的 `Code.gs`。
4. 點擊「部署 > 新增部署作業」，選擇「網頁應用程式」，並將存取權限設為**「所有人」**。複製獲得的網址。

### Step 2: 部署前端網頁
1. 將專案中的 `index.html` 第 151 行的 `API_URL` 替換為你在上一步複製的網址。
2. 將 `index.html` 上傳至 GitHub Repository，並開啟 GitHub Pages 功能。

### Step 3: 開始使用
假設你的 GitHub Pages 網址為 `https://YOUR_NAME.github.io/score/`：
*   **填寫紀錄入口:** `https://YOUR_NAME.github.io/score/`
*   **玩家專屬面板 (範例):** `https://YOUR_NAME.github.io/score/?p=p_1a2b3c`
*   **查看總體排行榜:** `https://YOUR_NAME.github.io/score/?p=p_leaderboard`

---

# Casual Board Game Scoring System 🎲 (English)

A stress-free scoring and dashboard system designed for board game groups. Unlike traditional systems that force everyone to stare at a public leaderboard, this system uses **unique Hash links** to provide individual, private dashboards. Players can view their own win rates and statistics, focusing on personal growth and eliminating the anxiety of rank comparisons.

The frontend is built with pure HTML/JS (hostable on GitHub Pages), and the backend utilizes Google Apps Script to store data in Google Sheets.

## 💡 Key Features

*   **Private Dashboards:** Each player gets a unique URL displaying only their total games, average score, 1st place rate, and specific head-to-head win rates against other players.
*   **Positive Scoring Model:** Ranks 1st through 4th are awarded `8, 5, 3, 2` points. This maintains a competitive differential while ensuring 4th place still receives a participatory 2 points and an honorary medal (🎖️) rather than a discouraging zero.
*   **Overwrite Protection:** Submitting a match with an existing Game ID will automatically overwrite the old record, making corrections easy.
*   **Hidden Leaderboard:** A special Hash link is available to view the overall group leaderboard when the season ends.

## 🛠️ Setup Instructions

### Step 1: Google Sheets Database
1. Create a blank Google Sheet.
2. Label cells A1 to F1: `Timestamp`, `GameID`, `1st`, `2nd`, `3rd`, `4th`.
3. Go to "Extensions > Apps Script" and paste the provided `Code.gs` logic.
4. Click "Deploy > New deployment", select "Web app", and set access to **"Anyone"**. Copy the generated Web App URL.

### Step 2: Frontend Deployment
1. Open `index.html` and replace the `API_URL` on line 151 with your copied Web App URL.
2. Upload `index.html` to a GitHub Repository and enable GitHub Pages.

### Step 3: Usage
Assuming your GitHub Pages URL is `https://YOUR_NAME.github.io/score/`:
*   **Score Entry Form:** `https://YOUR_NAME.github.io/score/`
*   **Private Dashboard (Example):** `https://YOUR_NAME.github.io/score/?p=p_1a2b3c`
*   **Global Leaderboard:** `https://YOUR_NAME.github.io/score/?p=p_leaderboard`
