import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';
import { Server } from 'socket.io';
import { RoomManager } from './rooms.js';
import { startMarketFeed } from './market.js';

const PORT = Number(process.env.PORT) || 3000;
const ROOM_CAPACITY = Number(process.env.ROOM_CAPACITY) || 100;
const SIMULATE = process.env.SIMULATE === '1';

const MAX_TEXT_LENGTH = 50;
const RATE_BURST = 3; // 令牌桶：最多連發 3 則
const RATE_REFILL_MS = 2000; // 之後每 2 秒補一則的額度

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const app = express();
app.use(express.static(path.join(__dirname, '..', 'public')));

const server = http.createServer(app);
const io = new Server(server, {
  // 同源部署不需要開 CORS；若前後端分離再自行加上
  maxHttpBufferSize: 4096, // 彈幕很短，縮小上限擋掉異常大封包
});

const rooms = new RoomManager({ capacity: ROOM_CAPACITY });
let latestQuote = null;

app.get('/stats', (_req, res) => {
  res.json({
    rooms: rooms.totalRooms(),
    users: rooms.totalUsers(),
    capacity: ROOM_CAPACITY,
    marketSource: latestQuote?.source ?? null,
  });
});

// 行情是全站共用的（大家都看大盤），直接對所有連線廣播
const stopMarket = startMarketFeed((quote) => {
  latestQuote = quote;
  io.emit('market', quote);
}, { simulate: SIMULATE });

function sanitizeText(raw) {
  if (typeof raw !== 'string') return null;
  // 去頭尾空白、移除控制字元，限制長度
  const text = raw.replace(/[\u0000-\u001f\u007f]/g, '').trim();
  if (!text) return null;
  return text.slice(0, MAX_TEXT_LENGTH);
}

io.on('connection', (socket) => {
  const room = rooms.assign();
  socket.join(room);
  socket.data.room = room;
  socket.data.tokens = RATE_BURST;
  socket.data.lastRefill = Date.now();

  socket.emit('joined', {
    room,
    count: rooms.count(room),
    capacity: ROOM_CAPACITY,
  });
  if (latestQuote) socket.emit('market', latestQuote);
  io.to(room).emit('room-count', { room, count: rooms.count(room) });

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
    rooms.leave(socket.data.room);
    io.to(socket.data.room).emit('room-count', {
      room: socket.data.room,
      count: rooms.count(socket.data.room),
    });
  });
});

server.listen(PORT, () => {
  console.log(`台股彈幕伺服器啟動 → http://localhost:${PORT}`);
  console.log(`每房上限 ${ROOM_CAPACITY} 人；行情模式：${SIMULATE ? '模擬' : '證交所即時'}`);
});

process.on('SIGINT', () => {
  stopMarket();
  io.close();
  server.close(() => process.exit(0));
});
