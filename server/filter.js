// 內容過濾：正規化（第 1 層）＋ 結構化廣告／聯絡方式偵測（第 2 層）。
// 命中即「靜默丟棄」——不回訊給發送者，不給對方測試規避的回饋（見 docs/moderation.md）。
// 這裡只做低誤判、零依賴的規則層；敏感詞詞庫（第 3–5 層）之後再視情況加。

// ---- 第 1 層：正規化（只用於比對，不動到顯示原文）----

// 零寬字元與雙向控制字元：常被拿來把關鍵字拆開躲過比對
const ZERO_WIDTH = /[​-‏‪-‮⁠﻿]/g;
// 字元之間插入的分隔符：空白、標點、符號——收掉才抓得到「賴 ｜ i d」「賺　錢」
const SEPARATORS = /[\s.。·、,，:：;；_＿\-–—~～!！|｜/\\*＊+＋()（）[\]【】]/g;

// NFKC 折疊全形→半形、轉小寫、去零寬。回傳供各規則比對的正規化字串。
export function normalize(text) {
  return text.normalize('NFKC').replace(ZERO_WIDTH, '').toLowerCase();
}

// 進一步把分隔符全部收掉，打回拆字規避的原形。
export function compact(norm) {
  return norm.replace(SEPARATORS, '');
}

// ---- 第 2 層：結構化廣告／聯絡方式規則 ----
// 廣告幾乎都要把人導出站（網址）或留聯絡方式（賴／微信／電話），擋掉這些就殺掉大宗廣告。
// 每條都盡量要求「聯絡意圖」共現，降低誤殺（例：姓「賴」、貼股價數字）。

// 網址與常見短網址服務
const URL_RE =
  /(https?:\/\/|www\.[a-z0-9]|(?:t\.me|reurl\.cc|lihi\d?\.[a-z]|pse\.is|bit\.ly|goo\.gl|tinyurl|is\.gd|ppt\.cc|1link\.tw)\/|\b[a-z0-9-]{2,}\.(?:com|net|org|cc|xyz|top|shop|vip|club|site|online|link|app|biz|info|me)\b)/i;

// 在「收掉分隔符」後的字串上比對聯絡方式，防 v x / 加 賴 這類拆字
const COMPACT_RES = [
  /加賴|徵賴|私賴|賴id|line[a-z0-9_.\-]{3,}|加line|@line/, // LINE
  /微信|加微|weixin|wechat|威信|薇信|vx[0-9a-z_.\-]{2,}/,  // 微信（vx 後接 id）
  /telegram|紙飛機|飛機群|@[a-z0-9_]{4,32}/,               // Telegram
  /09\d{8}/,                                                // 台灣手機號（已去分隔符）
];

// 高信心台股詐騙／賭博話術（單獨出現即幾乎確定是廣告）
const SCAM_RE =
  /娛樂城|百家樂|博弈|包牌|運彩|帶單|喊單|保證獲利|穩賺|內線明牌|飆股群|抱團群|老師帶|加入群組|加入我們|免費諮詢|穩定獲利/;

// 回傳 { ok:true } 或 { ok:false, reason }。text 應為已 sanitize（去控制字元、trim、截長）的字串。
export function screen(text) {
  const norm = normalize(text);
  const comp = compact(norm);

  if (URL_RE.test(norm) || URL_RE.test(comp)) return { ok: false, reason: 'url' };
  for (const re of COMPACT_RES) if (re.test(comp)) return { ok: false, reason: 'contact' };
  if (SCAM_RE.test(norm) || SCAM_RE.test(comp)) return { ok: false, reason: 'scam' };

  return { ok: true };
}
