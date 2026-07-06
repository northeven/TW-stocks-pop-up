// 本服務已遷移到 Fly。這支只保留一個用途：把舊的 Render 網址
// （tw-stocks-pop-up.onrender.com）301 永久導向新網址，並保留路徑與查詢字串
// （/tsm、/nq 等深連結照樣落到對應頁）。原本的彈幕 App 已不在此執行——
// 完整程式碼在 https://github.com/j0214ack/TW-stocks-pop-up （部署於 Fly）。
//
// 用純 node:http、不依賴任何套件，確保 Render 一定啟得起來；也不再輪詢行情，
// 免得退休的免費實例還一直打 TWSE／Yahoo。

import http from 'node:http';

const TARGET = process.env.REDIRECT_TARGET || 'https://tw-stocks-pop-up.fly.dev';
const PORT = Number(process.env.PORT) || 3000;

const server = http.createServer((req, res) => {
  const location = TARGET + (req.url || '/');
  res.writeHead(301, {
    Location: location,
    'Content-Type': 'text/plain; charset=utf-8',
    // 適度快取，避免瀏覽器把 301 永久釘死（日後若要改導向仍留餘地）
    'Cache-Control': 'public, max-age=3600',
  });
  res.end(`已遷移，永久導向 → ${location}\n`);
});

server.listen(PORT, () => {
  console.log(`[redirect] 舊 Render 網址 301 → ${TARGET}（listening :${PORT}）`);
});
