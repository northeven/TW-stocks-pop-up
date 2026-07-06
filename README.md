# 台股彈幕 📈🚀

即時股市彈幕系統——一邊看行情，一邊發匿名彈幕。純粹為了好玩。

兩個頻道：

- `/` 台股大盤（發行量加權股價指數，證交所 MIS API）
- `/tsm` 美股台積電 ADR（Yahoo Finance API），台股休市時可以來這邊玩

## 功能

- **即時行情**：伺服器每 5 秒輪詢，紅漲綠跌（台股慣例）
- **即時走勢圖**：原生 Canvas 手繪、零前端依賴，畫在彈幕區背景，彈幕直接從線圖上飛過；新連線會收到當日完整歷史（TSM 頻道還會用 Yahoo 分線資料秒補全日走勢）
- **匿名彈幕**：完全匿名，訊息飛過去就沒了，不落地、不存資料庫
- **自動分房**：每頻道獨立分房，每房上限 500 人（可調），滿了自動開新房；彈幕只在房內廣播，控制伺服器頻寬成本
- **防洗版**：同 IP 節流（每 1.2 秒才能再發，前端按鈕同步冷卻、後端依 IP 把關，擋多分頁繞過）＋ 令牌桶限流（可連發 3 則，之後每 2 秒補一則額度）＋ 洗同句偵測（10 秒內同一句超過 5 次就冷靜 30 秒），單則最長 50 字
- **防廣告／不當內容**：後端內容過濾（正規化破解拆字／全形規避 → 網址、加賴、微信、Telegram、電話、詐騙話術、露骨色情詞靜默丟棄），命中不回饋發送者；擋下量在 `/stats` 觀測。詞庫已逐一審過財經／台語黑話 FP。策略與待審清單見 [`docs/moderation.md`](docs/moderation.md)
- **模擬行情**：收盤時間或連不到行情來源時自動切換隨機漫步假行情，開發不用等開盤

## 快速開始

```bash
npm install
npm start          # 正式模式：抓證交所即時行情
npm run dev        # 開發模式：SIMULATE=1，用模擬行情
```

打開 <http://localhost:3000> 就能玩。開兩個分頁可以看到彼此的彈幕。

## 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `PORT` | `3000` | 伺服器埠號 |
| `ROOM_CAPACITY` | `500` | 每個房間的人數上限 |
| `SIMULATE` | 關 | 設 `1` 強制使用模擬行情 |
| `ADMIN_TOKEN` | 關 | 設了才開放臨時封鎖詞 admin API（見下） |
| `BLOCKED_WORDS` | 關 | 啟動時帶入的封鎖詞（逗號分隔），比執行期動態增刪更持久 |

## 架構

```
server/
  index.js    Express + Socket.IO 主程式（頻道、分房、限流、廣播、走勢歷史、admin API）
  market.js   行情來源（證交所 / Yahoo Finance）+ 模擬行情備援
  rooms.js    房間管理（滿房自動開新房、空房回收）
  filter.js   內容過濾（正規化、廣告／色情、可動態增刪的封鎖詞）
public/
  index.html  單頁前端（/ 與 /tsm 共用，依路徑決定頻道）
  style.css   深色主題、彈幕動畫
  app.js      Socket.IO 客戶端、Canvas 走勢圖、彈幕軌道排程
```

### 成本控制設計

- 彈幕用 `socket.to(room)` 只對同房間廣播，一則訊息最多扇出 99 個連線
- 行情是每頻道共用的單一輪詢（不管幾個使用者，對行情來源都只有一條輪詢），每 5 秒對該頻道廣播一次
- 走勢歷史只在連線時送一次全量，之後每 5 秒只推最新一點
- `maxHttpBufferSize` 壓到 4 KB，異常大封包直接擋掉
- 沒有資料庫、沒有訊息歷史，記憶體佔用只跟在線人數成正比

## 監控

`GET /stats` 回傳各頻道目前房間數、在線人數、行情來源與被過濾擋下的彈幕數：

```json
{
  "capacity": 500,
  "channels": {
    "taiex": { "rooms": 2, "users": 137, "marketSource": "twse", "blocked": 23 },
    "tsm": { "rooms": 1, "users": 42, "marketSource": "yahoo", "blocked": 4 }
  }
}
```

## 臨時封鎖詞（admin API）

不必改 code 或重部署，就能即時增刪封鎖片語（命中即靜默丟棄，含拆字空格）。需先設環境變數
`ADMIN_TOKEN`（沒設時 API 回 404、等於關閉）。詞會存在記憶體，重啟／重部署會回到程式內建的預設清單；
要持久就寫進 `BLOCKED_WORDS` 環境變數或原始碼預設清單。

```bash
TOKEN=你的ADMIN_TOKEN
BASE=https://你的網址/admin/blocklist

# 列出目前封鎖詞
curl "$BASE?token=$TOKEN"

# 新增（建議用 JSON body，中文不必手動 URL-encode）
curl -X POST "$BASE?token=$TOKEN" -H 'content-type: application/json' -d '{"word":"要擋的詞"}'

# 移除
curl -X DELETE "$BASE?token=$TOKEN" -H 'content-type: application/json' -d '{"word":"要擋的詞"}'
```

內建預設封鎖：`網球拍拍`、`楓之谷`、`新楓之谷`。

## 免費部署（Render）

不需要自己的伺服器，用 [Render](https://render.com) 免費方案就能上線：

1. 用 GitHub 帳號註冊/登入 Render（免信用卡）
2. Dashboard → **New → Web Service**，選這個 repo
3. Render 會自動讀取 `render.yaml`，直接按 **Deploy** 即可
4. 完成後會拿到一個 `https://tw-stocks-pop-up.onrender.com` 之類的網址，分享給朋友就能一起發彈幕

注意事項：

- 免費方案 **15 分鐘沒流量會休眠**，下一個訪客要等約 30–60 秒冷啟動（好玩專案可接受）
- 免費額度每月 750 小時，單一服務 24 小時開著也夠用（一個月最多 744 小時）
- 免費方案只有單一實例，剛好符合本專案設計（Socket.IO 不需要 Redis adapter）
- Render 的伺服器連證交所 API 沒問題，部署後就是真實大盤行情

## 資料來源

- 大盤指數：[臺灣證券交易所基本市況報導網站（MIS）](https://mis.twse.com.tw/)公開 API
- 台積電 ADR：Yahoo Finance 公開 chart API

均僅供個人趣味用途；請保持合理的輪詢頻率。
