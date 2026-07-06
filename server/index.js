import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';
import { Server } from 'socket.io';
import { RoomManager } from './rooms.js';
import { startMarketFeed, fetchTaiex, fetchYahoo } from './market.js';

const PORT = Number(process.env.PORT) || 3000;
const ROOM_CAPACITY = Number(process.env.ROOM_CAPACITY) || 100;
const SIMULATE = process.env.SIMULATE === '1';

const MAX_TEXT_LENGTH = 50;
const RATE_BURST = 3; // 令牌桶：最多連發 3 則
const RATE_REFILL_MS = 2000; // 之後每 2 秒補一則的額度
const HISTORY_MAX = 4000; // 每頻道走勢歷史點數上限

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ---- 頻道：每個頻道有自己的行情來源、走勢歷史、房間 ----

const CHANNEL_CONFIG = {
  taiex: {
    fetcher: fetchTaiex,
    // TWSE MIS 無盤中歷史，改用 Yahoo ^TWII 回補今日 9:00 至今的分線價格線
    seed: () => fetchYahoo('^TWII', '發行量加權股價指數'),
    sim: { base: 23000, name: '發行量加權股價指數' },
  },
  tsm: {
    fetcher: () => fetchYahoo('TSM', '台積電 ADR (TSM)'),
    sim: { base: 250, name: '台積電 ADR (TSM)' },
  },
};

const channels = {};
for (const [key, cfg] of Object.entries(CHANNEL_CONFIG)) {
  channels[key] = {
    key,
    cfg,
    rooms: new RoomManager({ capacity: ROOM_CAPACITY, prefix: `${key}:` }),
    history: [], // [timestamp, price, 該筆成交量]
    prevClose: null,
    lastCumVolume: null, // 上一筆累積成交量，用來換算單筆量
    latest: null,
  };
}

function recordQuote(ch, q) {
  if (ch.prevClose !== q.prevClose) {
    // 昨收變了代表換交易日，重新開始畫
    ch.prevClose = q.prevClose;
    ch.history.length = 0;
    ch.lastCumVolume = null;
  }
  // Yahoo 會附當日分線歷史（含各分鐘成交量），冷啟動直接整段填入
  if (ch.history.length === 0 && q.series?.length) {
    ch.history.push(...q.series);
  }
  const last = ch.history[ch.history.length - 1];
  if (!last || last[0] !== q.time) {
    // 來源給的是累積量，換算成這一筆的單筆量（首筆或跨日先記 0）
    let barVolume = 0;
    if (q.cumVolume != null) {
      barVolume = ch.lastCumVolume == null ? 0 : Math.max(0, q.cumVolume - ch.lastCumVolume);
      ch.lastCumVolume = q.cumVolume;
    }
    q.barVolume = barVolume; // 隨 market 事件送給前端即時附加
    ch.history.push([q.time, q.price, barVolume]); // 收盤後同一筆會重複，去重
  } else {
    q.barVolume = last[2] ?? 0;
  }
  if (ch.history.length > HISTORY_MAX) {
    ch.history.splice(0, ch.history.length - HISTORY_MAX);
  }
}

// ---- HTTP ----

const app = express();
app.use(express.static(path.join(__dirname, '..', 'public')));
// /tsm 也是同一份前端，客戶端依路徑決定頻道
app.get('/tsm', (_req, res) => {
  res.sendFile(path.join(__dirname, '..', 'public', 'index.html'));
});

const server = http.createServer(app);
const io = new Server(server, {
  // 同源部署不需要開 CORS；若前後端分離再自行加上
  maxHttpBufferSize: 4096, // 彈幕很短，縮小上限擋掉異常大封包
});

app.get('/stats', (_req, res) => {
  const perChannel = {};
  for (const ch of Object.values(channels)) {
    perChannel[ch.key] = {
      rooms: ch.rooms.totalRooms(),
      users: ch.rooms.totalUsers(),
      marketSource: ch.latest?.source ?? null,
    };
  }
  res.json({ capacity: ROOM_CAPACITY, channels: perChannel });
});

// ---- 歷史回補：先把今日 9:00 至今的分線價格填進來，再開始接即時 tick ----
// 指數在 Yahoo 沒有成交量，故僅回補價格線；量能副圖自伺服器啟動後才累積。

async function seedHistory(ch) {
  if (!ch.cfg.seed) return;
  try {
    const q = await ch.cfg.seed();
    if (q.series?.length && ch.history.length === 0) {
      ch.prevClose = q.prevClose;
      ch.history = q.series.slice();
      console.log(`[seed] ${ch.key} 回補 ${ch.history.length} 筆分線（起 ${new Date(ch.history[0][0]).toLocaleTimeString('zh-TW')}）`);
    }
  } catch (err) {
    console.warn(`[seed] ${ch.key} 歷史回補失敗: ${err.message}`);
  }
}

// 先回補再開輪詢，避免第一筆即時 tick 搶先寫入導致略過回補
if (!SIMULATE) await Promise.all(Object.values(channels).map(seedHistory));

// ---- 行情輪詢：每個頻道一條，全站共用，對該頻道所有連線廣播 ----

const stoppers = Object.values(channels).map((ch) =>
  startMarketFeed(
    (quote) => {
      ch.latest = quote;
      recordQuote(ch, quote);
      // series 很大只在連線時隨歷史送；cumVolume 前端用不到（改用 barVolume）
      const { series, cumVolume, ...lean } = quote;
      io.to(`chan:${ch.key}`).emit('market', lean);
    },
    { simulate: SIMULATE, fetcher: ch.cfg.fetcher, sim: ch.cfg.sim },
  ),
);

// ---- Socket.IO ----

function sanitizeText(raw) {
  if (typeof raw !== 'string') return null;
  // 去頭尾空白、移除控制字元，限制長度
  const text = raw.replace(/[\u0000-\u001f\u007f]/g, '').trim();
  if (!text) return null;
  return text.slice(0, MAX_TEXT_LENGTH);
}

io.on('connection', (socket) => {
  const chKey = Object.hasOwn(channels, socket.handshake.query.channel)
    ? socket.handshake.query.channel
    : 'taiex';
  const ch = channels[chKey];

  const room = ch.rooms.assign();
  socket.join(room);
  socket.join(`chan:${chKey}`);
  socket.data.channel = chKey;
  socket.data.room = room;
  socket.data.tokens = RATE_BURST;
  socket.data.lastRefill = Date.now();

  socket.emit('joined', {
    channel: chKey,
    room,
    count: ch.rooms.count(room),
    capacity: ROOM_CAPACITY,
  });
  if (ch.latest) {
    const { series, cumVolume, ...lean } = ch.latest;
    socket.emit('market', lean);
  }
  socket.emit('market-history', { points: ch.history, prevClose: ch.prevClose });
  io.to(room).emit('room-count', { room, count: ch.rooms.count(room) });

  socket.on('barrage', (payload) => {
    // 令牌桶限流：擋洗版，也保護伺服器頻寬
    const now = Date.now();
    const refill = Math.floor((now - socket.data.lastRefill) / RATE_REFILL_MS);
    if (refill > 0) {
      socket.data.tokens = Math.min(RATE_BURST, socket.data.tokens + refill);
      socket.data.lastRefill += refill * RATE_REFILL_MS;
    }
    if (socket.data.tokens <= 0) {
      socket.emit('rate-limited');
      return;
    }

    const text = sanitizeText(payload?.text);
    if (!text) return;

    socket.data.tokens -= 1;
    // 完全匿名：不帶任何身分識別。發送者已在本地顯示，只廣播給房間其他人
    socket.to(socket.data.room).emit('barrage', { text });
  });

  socket.on('disconnect', () => {
    ch.rooms.leave(socket.data.room);
    io.to(socket.data.room).emit('room-count', {
      room: socket.data.room,
      count: ch.rooms.count(socket.data.room),
    });
  });
});

server.listen(PORT, () => {
  console.log(`台股彈幕伺服器啟動 → http://localhost:${PORT}`);
  console.log(`頻道：${Object.keys(channels).join('、')}；每房上限 ${ROOM_CAPACITY} 人；行情模式：${SIMULATE ? '模擬' : '即時'}`);
});

process.on('SIGINT', () => {
  stoppers.forEach((stop) => stop());
  io.close();
  server.close(() => process.exit(0));
});
