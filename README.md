# Epoch PINP 骨骼健康監測系統

> 結合 LINE Bot、LIFF 相機介面與 AI 影像判讀，讓家屬在家即可完成 PINP 骨鬆標誌物定量檢測。

---

## 目錄

- [系統架構](#系統架構)
- [功能說明](#功能說明)
- [快速開始](#快速開始)
- [環境變數設定](#環境變數設定)
- [啟動伺服器](#啟動伺服器)
- [LINE 後台設定](#line-後台設定)
- [API 端點說明](#api-端點說明)
- [專案結構](#專案結構)
- [開發進度與待辦](#開發進度與待辦)

---

## 系統架構

```
照顧者 (LINE App)
     │
     ├── 傳送試紙照片  ──▶  LINE 伺服器  ──▶  /callback (Webhook)
     │                                              │
     │                                        processor.py
     │                                        (OpenCV AI 判讀)
     │                                              │
     │                                        epoch.db (SQLite)
     │                                              │
     └── LIFF 拍照介面 ──▶  /api/upload  ──────────┘
                                │
                           /api/history
                                │
                         trends.html (圖表)
```

- **前端**：LINE LIFF (HTML5) — 步驟引導相機、倒數計時、結果顯示
- **後端**：Python FastAPI — Webhook 處理、AI 影像分析、REST API
- **資料庫**：SQLite (SQLAlchemy ORM) — 儲存使用者、病患、PINP 歷史紀錄
- **AI**：OpenCV — 白平衡校正、T/C 線灰階強度提取、T/C 比值換算濃度

---

## 功能說明

### LINE Bot（Webhook 觸發）
- 接收家屬傳送的試紙照片
- 執行 AI 影像判讀（白平衡校正 → C 線有效驗證 → T/C 比值換算）
- 自動建立使用者與病患檔案
- 回覆彩色 **Flex Message 骨骼動力報告卡**（含 PINP 數值、三色狀態、骨骼動力條）

### LIFF 拍照介面（`/static/camera/`）
| 步驟 | 說明 |
|------|------|
| 1. 準備耗材 | 確認試紙、採血筆、酒精棉片、30 µL 採血管 |
| 2. 反應計時 | 15 分鐘 SVG 倒數計時，避免過早拍照導致數值偏差 |
| 3. 拍照判讀 | 試紙對準框 + 自動亮度偵測（提示開燈） |
| 4. 查看結果 | 即時顯示 PINP 濃度、三色狀態、骨骼動力條 |

### 歷史趨勢圖表（`/static/chart/trends.html`）
- 從 `/api/history/{line_user_id}` 動態載入最近 12 筆紀錄
- Chart.js 折線圖，每個數據點依綠/黃/紅三色著色
- 下方骨骼動力條 + 狀態摘要文字

### 三色骨骼狀態判斷
| 顏色 | PINP 濃度 | 意義 |
|------|-----------|------|
| 🟢 綠色 | > 50 ng/mL | 骨骼生長良好，藥效反應佳 |
| 🟡 黃色 | 25–50 ng/mL | 骨骼狀態穩定，持續觀察 |
| 🔴 紅色 | < 25 ng/mL | 需警示，建議諮詢醫師 |

---

## 快速開始

### 1. 複製專案

```bash
git clone https://github.com/HiOliver0029/pinp-liff.git
cd pinp-liff
```

### 2. 建立 Python 虛擬環境並安裝套件

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 3. 設定環境變數

複製範本後填入實際金鑰：

```bash
cp .env.example .env
```

編輯 `.env`（請勿將此檔案上傳至 Git）：

```
LINE_CHANNEL_ACCESS_TOKEN=你的_Channel_Access_Token
LINE_CHANNEL_SECRET=你的_Channel_Secret
```

---

## 環境變數設定

| 變數名稱 | 說明 | 取得位置 |
|----------|------|----------|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API 存取金鑰 | LINE Developers Console → Messaging API → Channel access token |
| `LINE_CHANNEL_SECRET` | LINE Channel 密鑰 | LINE Developers Console → Basic settings → Channel secret |

---

## 啟動伺服器

### 方法一：PowerShell 腳本（Windows）

```powershell
.\start.ps1
```

### 方法二：直接執行

```bash
python main.py
```

伺服器預設運行於 `http://0.0.0.0:8000`，並開啟 auto-reload。

### 開發階段 Webhook 測試（使用 ngrok）

```bash
ngrok http 8000
```

將產生的 `https://xxxx.ngrok-free.app/callback` 填入 LINE Developers Console 的 Webhook URL，並開啟 **Use webhook** 開關。

---

## LINE 後台設定

### 1. LINE Developers Console
1. 前往 [LINE Developers Console](https://developers.line.biz/)
2. 建立 **Messaging API** Channel
3. 取得 `Channel Secret` 與 `Channel access token`
4. 將 Webhook URL 設定為 `https://你的網域/callback`

### 2. LIFF 設定
1. 在 Console 的 **LIFF** 頁籤新增應用程式
2. Size 選擇 `Full`
3. Endpoint URL 填入 `https://你的網域/static/camera/index.html`
4. Scopes 勾選 `openid`、`profile`
5. 取得 LIFF ID 後，填入以下兩個檔案的 `YOUR_LIFF_ID`：
   - `static/camera/app.js` → 第 1 行 `const LIFF_ID`
   - `static/chart/trends.html` → script 區塊的 `const LIFF_ID`

### 3. 圖文選單（建議格式）
| 位置 | 功能 | 動作 |
|------|------|------|
| 左側大按鈕 | 開始骨骼檢測 | 連結 → LIFF 相機 URL |
| 右上按鈕 | 歷史數據 / 醫師報告 | 連結 → LIFF 趨勢圖 URL |
| 右下按鈕 | 購買監測套裝包 | 連結 → 購買頁面 |

---

## API 端點說明

| 方法 | 路徑 | 說明 |
|------|------|------|
| `POST` | `/callback` | LINE Webhook 接收端點 |
| `POST` | `/api/upload` | LIFF 直接上傳試紙圖片（multipart/form-data） |
| `GET` | `/api/history/{line_user_id}` | 取得使用者最近 12 筆 PINP 紀錄 |
| `GET` | `/health` | 服務健康檢查 |
| `GET` | `/static/*` | LIFF 靜態頁面 |

### `POST /api/upload` 參數

| 欄位 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `file` | File | ✅ | 試紙圖片（image/*） |
| `line_user_id` | string | ❌ | LINE User ID，有填才會儲存至資料庫 |
| `device_info` | string | ❌ | User-Agent，供 AI 校準分析使用 |

### `GET /api/history/{line_user_id}` 回傳格式

```json
[
  { "date": "2026-01-15", "concentration": 35.2, "color": "red" },
  { "date": "2026-02-10", "concentration": 48.1, "color": "yellow" },
  { "date": "2026-03-10", "concentration": 65.5, "color": "green" }
]
```

---

## 專案結構

```
pinp-liff/
├── main.py                  # 入口點（uvicorn 啟動）
├── fastapi_webhook.py       # FastAPI 主程式（Webhook + REST API）
├── processor.py             # AI 影像判讀核心（OpenCV）
├── models.py                # SQLAlchemy 資料模型
├── database.py              # 資料庫連線設定（SQLite）
├── requirements.txt         # Python 套件清單
├── start.ps1                # Windows 一鍵啟動腳本
├── .env.example             # 環境變數範本（請勿暴露 .env）
├── .gitignore
├── images/                  # 使用者上傳的試紙照片（自動建立，不入版控）
└── static/
    ├── camera/
    │   ├── index.html       # LIFF 拍照引導介面（4 步驟流程）
    │   └── app.js           # LIFF 邏輯（倒數、亮度偵測、上傳、結果顯示）
    └── chart/
        └── trends.html      # 歷史趨勢圖表（Chart.js + LIFF 動態載入）
```

### 資料庫 Schema

```
User (照顧者)
 └── Patient (受照護長輩，1 對多)
      └── DetectionRecord (PINP 檢測紀錄，1 對多)
           ├── concentration  PINP 濃度 (ng/mL)
           ├── gray_value     T 線原始灰階強度
           ├── status_color   green / yellow / red
           ├── image_path     試紙圖片儲存路徑
           ├── device_info    拍攝裝置資訊
           └── doctor_notes   醫師備註（可選）
```

---

## 開發進度與待辦

- [x] LINE Bot Webhook 接收與 Flex Message 回覆
- [x] OpenCV AI 影像判讀（白平衡校正 + C 線有效驗證 + T/C 比值換算）
- [x] LIFF 4 步驟拍照引導介面（倒數計時、亮度偵測）
- [x] `/api/upload` 直接上傳端點
- [x] `/api/history` 歷史趨勢 API
- [x] 歷史趨勢圖表（動態三色、骨骼動力條）
- [x] SQLite 資料持久化（User / Patient / DetectionRecord）
- [ ] 填入真實 LIFF ID（`static/camera/app.js`、`static/chart/trends.html`）
- [ ] 以長庚標準校準數據調整 AI 迴歸係數（processor.py）
- [ ] 部署至雲端伺服器（GCP / AWS）並設定正式 Webhook URL
- [ ] 診間摘要報告 PDF 產生功能
- [ ] 病患管理介面（多病患支援、藥物紀錄）

---

## 授權

本專案為 Epoch 新創團隊 MVP 開發階段原型，技術支援來自長庚醫療研究團隊。

> **注意**：本系統目前為研究原型，尚未通過 TFDA 醫療器材許可，不得作為臨床診斷依據。
