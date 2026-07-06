// 行情來源模組。
//
// 台股大盤：輪詢證交所 MIS API（免費、無需金鑰）。
// 美股個股：輪詢 Yahoo Finance chart API（免費、無需金鑰），
//           順便回傳當日分線歷史（series），新連線的線圖立刻是完整的。
// 模擬模式：SIMULATE=1 啟動，或連續抓不到真實資料時自動切換，
//           用隨機漫步產生假行情，方便收盤時間與本機開發。

const TWSE_URL =
  'https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0';

const POLL_MS = 5000;
const FAILS_BEFORE_FALLBACK = 3;

function num(v) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

export async function fetchTaiex() {
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
    cumVolume: num(row.v), // 當日累積成交量（張），交易量副圖用
  };
}

export async function fetchYahoo(symbol, displayName) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?interval=1m&range=1d&includePrePost=false`;
  const res = await fetch(url, {
    headers: { 'User-Agent': 'Mozilla/5.0', Accept: 'application/json' },
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) throw new Error(`Yahoo HTTP ${res.status}`);
  const data = await res.json();
  const result = data?.chart?.result?.[0];
  if (!result?.meta) throw new Error('Yahoo 回應缺少 chart.result');

  const meta = result.meta;
  const price = num(meta.regularMarketPrice);
  const prevClose = num(meta.chartPreviousClose) ?? num(meta.previousClose);
  if (price === null || prevClose === null) throw new Error('Yahoo 回應缺少價格欄位');

  // 當日分線歷史（給線圖秒載入用）：[時間, 收盤價, 該分鐘成交量]
  const ts = result.timestamp || [];
  const closes = result.indicators?.quote?.[0]?.close || [];
  const volumes = result.indicators?.quote?.[0]?.volume || [];
  const series = [];
  for (let i = 0; i < ts.length; i++) {
    if (num(closes[i]) !== null) series.push([ts[i] * 1000, closes[i], num(volumes[i]) ?? 0]);
  }

  const change = price - prevClose;
  return {
    source: 'yahoo',
    name: displayName || meta.shortName || symbol,
    price,
    prevClose,
    change,
    changePercent: (change / prevClose) * 100,
    high: num(meta.regularMarketDayHigh),
    low: num(meta.regularMarketDayLow),
    open: series.length ? series[0][1] : null,
    time: (num(meta.regularMarketTime) ?? Math.floor(Date.now() / 1000)) * 1000,
    currency: meta.currency || 'USD',
    marketState: meta.marketState, // PRE / REGULAR / POST / CLOSED
    cumVolume: num(meta.regularMarketVolume), // 當日累積成交量，交易量副圖用
    series,
  };
}

// ---- 模擬行情（隨機漫步）----

function createSimulator({ base = 23000, name = '模擬指數' } = {}) {
  const prevClose = base * (1 + (Math.random() - 0.5) * 0.02);
  let price = prevClose * (1 + (Math.random() - 0.5) * 0.01);
  let high = price;
  let low = price;
  const open = price;
  const step = base * 0.001; // 波動幅度跟價位成比例，指數和個股都合理
  let cumVolume = 0;
  let ticks = 0;

  return function tick() {
    // 帶一點向昨收回歸的力道，避免漂太遠
    const drift = (prevClose - price) * 0.001;
    price = Math.max(base * 0.5, price + drift + (Math.random() - 0.5) * step);
    high = Math.max(high, price);
    low = Math.min(low, price);
    // 假成交量：開盤爆量、之後逐漸收斂，貼近真實盤中量能分布
    ticks += 1;
    cumVolume += Math.round(base * Math.random() * (1 + 4 / ticks));
    const change = price - prevClose;
    const r = (v) => Math.round(v * 100) / 100;
    return {
      source: 'simulated',
      name: `${name}（模擬）`,
      price: r(price),
      prevClose: r(prevClose),
      change: r(change),
      changePercent: r((change / prevClose) * 100),
      high: r(high),
      low: r(low),
      open: r(open),
      time: Date.now(),
      cumVolume,
    };
  };
}

/**
 * 啟動行情輪詢。每次拿到新報價就呼叫 onQuote(quote)。
 * fetcher：真實行情來源；sim：{ base, name } 模擬行情參數。
 * 回傳 stop() 以便關閉。
 */
export function startMarketFeed(onQuote, { simulate = false, fetcher = fetchTaiex, sim = {} } = {}) {
  let simTick = simulate ? createSimulator(sim) : null;
  let consecutiveFails = 0;
  let stopped = false;

  async function poll() {
    if (stopped) return;
    if (simTick) {
      onQuote(simTick());
    } else {
      try {
        onQuote(await fetcher());
        consecutiveFails = 0;
      } catch (err) {
        consecutiveFails += 1;
        console.warn(`[market] 抓取行情失敗（${consecutiveFails} 次）: ${err.message}`);
        if (consecutiveFails >= FAILS_BEFORE_FALLBACK) {
          console.warn('[market] 連續失敗，切換為模擬行情');
          simTick = createSimulator(sim);
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
