# Vitaslow Freshbox™ 團購結帳系統

EatAsia 辦公室團購生鮮品牌的完整訂購系統:**編輯上架 → 前台下單 → 收款核帳 → 出貨取貨** 一站完成。
以 LINE 官方帳號作為客戶互動窗口,系統自動處理訂單通知、到貨提醒與訂單查詢。

## 功能總覽

### 前台(給客人用)
- 依分類瀏覽商品(分類跳轉導覽列、人氣/限量標籤、劃線價)
- 購物車:調整數量、移除、最低訂購金額檢查(未達門檻顯示還差多少)
- 結帳:姓名/手機/取貨點/備註 + 付款方式(轉帳、取貨付款、綠界線上付款)
- 訂單完成頁:付款指示(轉帳資訊)、轉帳末五碼回報
- 訂單查詢:訂單編號 + 手機號碼即可查進度,不需註冊帳號

### 後台(`/admin`)
- 營運總覽:今日訂單、待確認、待核帳、待取貨、本月營收、熱銷 TOP5、庫存告急
- 商品管理:CRUD、多圖上傳(自動裁切封面)、標籤、庫存、開賣/截單日、排序
- 分類、取貨點管理;系統設定(最低訂購金額、預購天數、轉帳資訊…改規則不用改程式)
- 訂單管理:8 種狀態流轉、批次變更(整批標記到貨+自動 LINE 通知)、轉帳核帳、內部備註、異動紀錄
- CSV 批次匯入商品(範本下載、分類自動建立、Big5/UTF-8 自動判斷、錯誤列回報)
- 對帳報表:依日期/取貨點/付款方式統計,一鍵匯出 CSV(Excel 可直接開)

### 安全性(相較原型的重點升級)
- 後台密碼 PBKDF2 雜湊儲存(不再明碼寫死),含預設密碼強制修改提醒、登入防爆破鎖定
- 所有表單 CSRF 保護;上傳檔案類型/大小白名單;後台 noindex
- 綠界付款通知「伺服器端」驗證 CheckMacValue 簽章;LINE Webhook 驗證 X-Line-Signature
- 下單交易含庫存檢查,防止超賣;取消訂單自動回補庫存

## 快速開始(本機)

```bash
cd freshbox
pip3 install -r requirements.txt
python3 app.py
```

- 前台:http://127.0.0.1:5000
- 後台:http://127.0.0.1:5000/admin/login(帳號 `admin`,預設密碼 `eatasia2026`,**登入後請立即修改**)

第一次啟動會自動建立 `data/freshbox.db`,內含 7 大分類、5 個取貨點(南港/汐止/汐科/內湖/淡水)與幾筆示範商品。

### 沿用舊原型的資料庫

把舊的 `freshbox.db` 複製到 `freshbox/data/freshbox.db` 再啟動即可 —— 系統啟動時會自動補齊新欄位,不會覆蓋既有的商品與訂單。也可以用後台的 CSV 匯入功能重新匯入商品總表。

## 環境變數(正式上線設定)

| 變數 | 說明 |
|---|---|
| `SECRET_KEY` | Session 簽章金鑰,正式環境必設(隨機長字串) |
| `ADMIN_INIT_PASSWORD` | 首次啟動建立 admin 帳號的密碼(不設則用預設值並提醒修改) |
| `DATA_DIR` | 資料庫與上傳圖片目錄(掛載持久磁碟時指到掛載點) |
| `PUBLIC_BASE_URL` | 對外網址,例如 `https://shop.eatasia.com.tw`(金流回呼、LINE 連結用) |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API Token(設定後自動啟用 LINE 通知) |
| `LINE_CHANNEL_SECRET` | LINE Channel Secret(Webhook 簽章驗證) |
| `LINE_ADMIN_USER_ID` | 你的 LINE userId,新訂單/轉帳回報即時推播給你 |
| `ECPAY_MERCHANT_ID` / `ECPAY_HASH_KEY` / `ECPAY_HASH_IV` | 綠界金鑰(設定後結帳自動出現線上付款) |
| `ECPAY_STAGE` | `1`=綠界測試環境(預設),`0`=正式環境 |

### 綠界測試(還沒申請正式帳號前就能試刷)

用綠界公開測試金鑰即可測整條線上付款流程:

```bash
export ECPAY_MERCHANT_ID=3002607
export ECPAY_HASH_KEY=pwFHCqoQZGmho4w6
export ECPAY_HASH_IV=EkRm7iFT261dpevs
export ECPAY_STAGE=1
```

測試卡號 `4311-9522-2222-2222`,有效月年任填未來日期,安全碼 `222`。
(注意:回呼 `ReturnURL` 需要對外可連的網址,本機測試可搭配 ngrok 並設定 `PUBLIC_BASE_URL`。)

### LINE 官方帳號串接步驟

1. 到 [LINE Developers](https://developers.line.biz/) 建立 Messaging API Channel
2. 把 Channel Access Token / Channel Secret 設成環境變數
3. Webhook URL 填 `https://你的網域/webhook/line`,開啟「Use webhook」
4. 客人加好友後傳「`查詢 訂單編號 手機末三碼`」→ 機器人回覆訂單狀態並**自動綁定**,之後到貨會主動 LINE 通知
5. 想收新訂單推播:用自己的帳號跟機器人講一句話,從 Webhook 事件紀錄取得你的 userId,設到 `LINE_ADMIN_USER_ID`

## 部署(Render 範例)

repo 根目錄的 `render.yaml` 已含 `vitaslow-freshbox` 服務設定,在 Render 選 **New → Blueprint** 指向本 repo 即可。

SQLite 資料存在磁碟上,**務必掛載 Persistent Disk**(Render 免費方案磁碟不持久,重新部署會清空資料):
Service → Disks → 新增磁碟掛載到 `/var/data`,並設環境變數 `DATA_DIR=/var/data`。

流量成長後建議升級 PostgreSQL(資料層集中在 `db.py`,替換成本低)。

## 訂單狀態流

```
待確認 → 已確認 → 備貨中 → 已出貨 → 待取貨 → 已完成
                                      ↘ 逾期未取
任一階段可 → 已取消(自動回補庫存)
付款狀態獨立追蹤:未付款 → 待核對(客人回報末五碼)→ 已付款 / 已退款
```

## 專案結構

```
app.py        主程式:前台路由 + 綠界/LINE Webhook
admin.py      後台管理(Blueprint)
store.py      商業邏輯:訂單狀態機、購物車、防超賣下單
db.py         SQLite schema + 寬容式升級 + 預設資料
config.py     環境變數集中管理
security.py   PBKDF2 密碼雜湊、CSRF token
lineapi.py    LINE Messaging API(推播/回覆/簽章驗證)
ecpay.py      綠界金流(CheckMacValue 產生與驗證)
templates/    前台 shop/ + 後台 admin/ 頁面
static/       樣式(手機優先)
data/         資料庫與上傳圖片(gitignore,不進版控)
```
