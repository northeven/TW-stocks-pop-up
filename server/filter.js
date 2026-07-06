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

// ---- 第 4 層：露骨色情／性招攬詞（已逐詞審過財經/日常 FP，命中即靜默丟棄）----
// 收詞原則：只收「股市聊天幾乎不會出現、且無常見無辜母字串」的詞。
// 只比對 norm（保留分隔符），刻意不比對 compact——避免「外送 茶飲 → 外送茶」這類
// 分隔符收合造成的誤殺。以下詞已逐一審過中文子字串 FP：
//   一夜情 用 (?![勢懷況]) 擋掉「一夜情勢/情懷/情況」。
// FP 高、無法乾淨界定的詞刻意不收（見 docs/moderation.md「待審清單」）：
//   口爆(缺口爆量) 性交易/性服務(彈性/理性…) 援交(支援交流) 群交(客群交易)
//   內射(禁區內射) 做愛(做愛心) 肉棒(蟹肉棒) 性侵(個性侵略) 屌(好屌=讚美)
//   自慰(獨自慰問/各自慰勞) 援妹(支援妹妹) 叫小姐(叫小姐過來)…
const NSFW_RE =
  /約炮|約砲|外送茶|賣淫|嫖妓|嫖娼|可外約|顏射|插穴|裸聊|打手槍|應召站|幼交|戀童|迷姦|輪姦|強姦|一夜情(?![勢懷況])/;

// ---- 使用者指定封鎖片語（執行期可動態增刪，不必重啟／重部署）----
// 存 compact 形式（已去分隔符），比對時對 norm 與 comp 都測，連拆字空格也擋。
// 透過 index.js 的 admin API 即時增刪；預設種入 網球拍拍，另可由 BLOCKED_WORDS 環境變數帶入。
const MAX_BLOCKED = 500; // 上限，避免 regex 過大或被灌爆
const blockedWords = new Set();
let blockedRe = null; // 依 blockedWords 編譯；空集合為 null

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
function rebuildBlocked() {
  blockedRe = blockedWords.size ? new RegExp([...blockedWords].map(escapeRe).join('|')) : null;
}
// 把輸入詞正規化並去分隔符後當 key；空字串或過長回 null
function blockKey(raw) {
  const k = compact(normalize(String(raw))).trim();
  return k && k.length <= 50 ? k : null;
}

export function addBlockedWord(raw) {
  const k = blockKey(raw);
  if (!k || blockedWords.size >= MAX_BLOCKED) return false;
  blockedWords.add(k);
  rebuildBlocked();
  return true;
}
export function removeBlockedWord(raw) {
  const k = blockKey(raw);
  const ok = k ? blockedWords.delete(k) : false;
  rebuildBlocked();
  return ok;
}
export function listBlockedWords() {
  return [...blockedWords];
}
export function loadBlockedWords(words) {
  for (const w of words) {
    const k = blockKey(w);
    if (k && blockedWords.size < MAX_BLOCKED) blockedWords.add(k);
  }
  rebuildBlocked();
}

// 預設封鎖詞（已知騷擾／遊戲代練廣告；redeploy 後仍在）
loadBlockedWords(['網球拍拍', '楓之谷', '新楓之谷']);

// 回傳 { ok:true } 或 { ok:false, reason }。text 應為已 sanitize（去控制字元、trim、截長）的字串。
export function screen(text) {
  const norm = normalize(text);
  const comp = compact(norm);

  if (URL_RE.test(norm) || URL_RE.test(comp)) return { ok: false, reason: 'url' };
  for (const re of COMPACT_RES) if (re.test(comp)) return { ok: false, reason: 'contact' };
  if (SCAM_RE.test(norm) || SCAM_RE.test(comp)) return { ok: false, reason: 'scam' };
  if (NSFW_RE.test(norm)) return { ok: false, reason: 'nsfw' };
  if (blockedRe && (blockedRe.test(norm) || blockedRe.test(comp))) return { ok: false, reason: 'blocked' };

  return { ok: true };
}
