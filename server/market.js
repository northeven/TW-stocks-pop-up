// 大盤（發行量加權股價指數，TAIEX）即時行情來源。
//
// 正常模式：輪詢證交所 MIS API（免費、無需金鑰），約每 5 秒一次。
// 模擬模式：SIMULATE=1 啟動，或連續抓不到證交所資料時自動切換，
//           用隨機漫步產生假行情，方便收盤時間與本機開發。

const TWSE_URL =
  'https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0';

const POLL_MS = 5000;
const FAILS_BEFORE_FALLBACK = 3;

function num(v) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

async function fetchTaiex() {
  const res = await fetch(TWSE_URL, {
    headers: { 'User-Agent': 'Mozilla/5.0', Accept: 'application/json' },
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) throw new Error(`TWSE HTTP ${res.status}`);
  const data = await res.json();
  const row = data?.msgArray?.[0];
  if (!row) throw new Error('TWSE 回應缺少 msgArray');

  const price = num(row.z) ?? num(row.o); // 盤中最新價；偶爾回 '-'，退回開盤價
  const prevClose = num(row.y);
  if (price === null || prevClose === null) throw new Error('TWSE 回應缺少價格欄位');

  const change = price - prevClose;
  return {
    source: 'twse',
    name: row.n || '發行量加權股價指數',
    price,
    prevClose,
    change,
    changePercent: (change / prevClose) * 100,
    high: num(row.h),
    low: num(row.l),
    open: num(row.o),
    time: num(row.tlong) ?? Date.now(),
  };
}

// ---- 模擬行情（隨機漫步）----

function createSimulator() {
  const prevClose = 23000 + Math.round(Math.random() * 500);
  let price = prevClose * (1 + (Math.random() - 0.5) * 0.01);
  let high = price;
  let low = price;
  const open = price;

  return function tick() {
    // 帶一點向昨收回歸的力道，避免漂太遠
    const drift = (prevClose - price) * 0.001;
    price = Math.max(1, price + drift + (Math.random() - 0.5) * 25);
    high = Math.max(high, price);
    low = Math.min(low, price);
    const change = price - prevClose;
    return {
      source: 'simulated',
      name: '發行量加權股價指數（模擬）',
      price: Math.round(price * 100) / 100,
      prevClose,
      change: Math.round(change * 100) / 100,
      changePercent: Math.round((change / prevClose) * 10000) / 100,
      high: Math.round(high * 100) / 100,
      low: Math.round(low * 100) / 100,
      open: Math.round(open * 100) / 100,
      time: Date.now(),
    };
  };
}

/**
 * 啟動行情輪詢。每次拿到新報價就呼叫 onQuote(quote)。
 * 回傳 stop() 以便關閉。
 */
export function startMarketFeed(onQuote, { simulate = false } = {}) {
  let simTick = simulate ? createSimulator() : null;
  let consecutiveFails = 0;
  let stopped = false;

  async function poll() {
    if (stopped) return;
    if (simTick) {
      onQuote(simTick());
    } else {
      try {
        onQuote(await fetchTaiex());
        consecutiveFails = 0;
      } catch (err) {
        consecutiveFails += 1;
        console.warn(`[market] 抓取證交所資料失敗（${consecutiveFails} 次）: ${err.message}`);
        if (consecutiveFails >= FAILS_BEFORE_FALLBACK) {
          console.warn('[market] 連續失敗，切換為模擬行情');
          simTick = createSimulator();
        }
      }
    }
    if (!stopped) setTimeout(poll, POLL_MS);
  }

  poll();
  return () => {
    stopped = true;
  };
}
