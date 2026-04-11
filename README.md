# Taiwan Electricity Map

台灣即時電力監控儀表板。GitHub Actions 每 10 分鐘自動抓取台電資料，  
更新 `data.json`，由 GitHub Pages 靜態提供給前端顯示。

---

## 功能概覽

- **即時系統負載**：當前用電、可供電力、使用率
- **備轉狀態燈**：依備轉率顯示供電充裕 / 吃緊 / 警戒
- **互動式地圖**：Leaflet 深色地圖，四大分區圓形標記
- **能源結構面板**：12 種燃料類型橫條圖，顯示各類占比
- **自動重新整理**：每 10 分鐘倒數計時後自動刷新

---

## 檔案結構

```
Taiwan_Electricity_Map/
├── index.html                   前端儀表板（單一 HTML，無框架）
├── data.json                    台電資料（由 GitHub Actions 自動更新）
├── scripts/
│   └── fetch_data.py            資料抓取腳本
└── .github/workflows/
    └── fetch.yml                自動排程 Workflow
```

---

## 資料流程

```
GitHub Actions（每 10 分鐘）
    └─ fetch_data.py
        ├─ 優先：loadpara.txt（Referer + XHR headers）→ 解析 load / capacity
        ├─ 備援：d006001/001.json（台電開放資料 API）→ 從機組小計計算
        └─ 燃料：loadfueltype.csv → 12 種能源別發電量
    └─ 寫入 data.json
    └─ git commit & push
        └─ GitHub Pages 自動提供 data.json
            └─ index.html fetch('./data.json') 更新畫面
```

---

## data.json 格式

```json
{
  "fetchTime":  "2026-04-11T18:52:00+08:00",
  "error":      null,
  "load":       3033.1,
  "capacity":   4437.5,
  "utilRate":   68.3,
  "spareRate":  46.2,
  "updateTime": "18:50",
  "fuels": [
    { "name": "燃氣",   "mw": 1223.4 },
    { "name": "燃煤",   "mw":  843.8 },
    ...
  ],
  "regions": {
    "北區": 1273.9,
    "中區":  727.9,
    "南區":  879.6,
    "東區":  151.7
  }
}
```

| 欄位 | 單位 | 說明 |
|------|------|------|
| `load` | 萬瓩 | 系統當前負載 |
| `capacity` | 萬瓩 | 可供電力（或估算值） |
| `utilRate` | % | 使用率 = load / capacity × 100 |
| `spareRate` | % | 備轉率 = (capacity - load) / load × 100 |
| `fuels[].mw` | 萬瓩 | 各燃料類別發電量 |
| `regions` | 萬瓩 | 分區負載（依歷史比例估算） |

---

## 備轉率狀態對照

| 備轉率 | 狀態燈 | 標籤 |
|--------|--------|------|
| ≥ 10% | 🟢 綠 | 供電充裕 |
| 6 ~ 9.99% | 🟡 黃 | 供電吃緊 |
| < 6% | 🔴 紅 | 供電警戒 |

---

## 資料來源

### load / capacity（優先順序）

| 優先 | URL | 說明 |
|------|-----|------|
| 1 | `taipower.com.tw/.../loadpara.txt` | 台電即時備轉資料（需 Referer + XHR headers） |
| 2 | `service.taipower.com.tw/.../d006001/001.json` | 台電開放資料 — 發電機組資訊（每 10 分鐘更新） |

> loadpara.txt 備援說明：使用 d006001 時，備轉率為估計值（各燃料類型「小計」行的裝置容量加總），  
> 通常高於台電官方公告數字，但 load 數值仍精確。

### fuel breakdown

| URL | 說明 |
|-----|------|
| `taipower.com.tw/.../loadfueltype.csv` | 12 欄位燃料發電量（萬瓩），每 5 分鐘更新 |

### 分區負載

台電無公開分區即時 API，依歷史比例估算：
北區 42%、中區 24%、南區 29%、東區 5%

---

## FUEL_NAMES 欄位順序

`loadfueltype.csv` 的 12 個欄位（以 d006001 開放資料交叉驗證）：

| 欄位 | 名稱 |
|------|------|
| 1 | 燃氣 |
| 2 | 民營燃氣 |
| 3 | 燃煤 |
| 4 | 民營燃煤 |
| 5 | 汽電共生 |
| 6 | 重油 |
| 7 | 太陽能 |
| 8 | 風力 |
| 9 | 水力 |
| 10 | 儲能 |
| 11 | 其它再生能源 |
| 12 | 核能 |

---

## GitHub Actions Workflow（fetch.yml）

```yaml
on:
  schedule:
    - cron: '*/10 * * * *'   # 每 10 分鐘
  workflow_dispatch:           # 手動觸發
permissions:
  contents: write
```

**步驟：**
1. Checkout repo
2. Python 3.11
3. `pip install requests cloudscraper`
4. `python scripts/fetch_data.py`
5. `git add data.json && git commit && git push`（資料無變化時跳過 commit）

---

## 前端（index.html）

| 項目 | 說明 |
|------|------|
| 資料來源 | `./data.json`（cache-busting：`?_=Date.now()`） |
| 自動重整 | 每 600 秒（10 分鐘） |
| 地圖 | Leaflet.js，中心點 23.97°N / 120.97°E，zoom 7 |
| 底圖 | CartoDB Dark Matter |
| 四分區標記 | 北區、中區、南區、東區，圓形色碼依備轉壓力變色 |
| 能源面板 | 螢幕寬度 ≤ 900px 時隱藏 |
| 設計 | 純前端單一 HTML，無後端，無 npm，無 CDN 依賴以外框架 |

---

## 技術架構

- **前端**：純 HTML / CSS / JavaScript，Leaflet.js（地圖）
- **後端**：無（GitHub Pages 靜態托管）
- **資料更新**：GitHub Actions cron → Python → git push
- **Python 套件**：`requests`、`cloudscraper`（防 Cloudflare 備用）
