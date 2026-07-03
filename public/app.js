/* global io */
(() => {
  const socket = io();

  // ---- DOM ----
  const stage = document.getElementById('danmaku-stage');
  const priceEl = document.getElementById('index-price');
  const changeEl = document.getElementById('index-change');
  const highEl = document.getElementById('index-high');
  const lowEl = document.getElementById('index-low');
  const prevEl = document.getElementById('index-prev');
  const nameEl = document.getElementById('index-name');
  const sourceBadge = document.getElementById('source-badge');
  const roomBadge = document.getElementById('room-badge');
  const form = document.getElementById('barrage-form');
  const input = document.getElementById('barrage-input');
  const toast = document.getElementById('toast');

  let roomInfo = { room: '—', count: 0, capacity: 100 };

  // ---- 大盤行情 ----

  const fmt = (n) =>
    n == null ? '—' : n.toLocaleString('zh-Hant-TW', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  socket.on('market', (q) => {
    nameEl.textContent = q.name;
    priceEl.textContent = fmt(q.price);
    highEl.textContent = fmt(q.high);
    lowEl.textContent = fmt(q.low);
    prevEl.textContent = fmt(q.prevClose);

    const dir = q.change > 0 ? 'up' : q.change < 0 ? 'down' : 'flat';
    const sign = q.change > 0 ? '▲' : q.change < 0 ? '▼' : '—';
    changeEl.className = dir;
    priceEl.className = dir;
    changeEl.textContent = `${sign} ${fmt(Math.abs(q.change))} (${q.changePercent >= 0 ? '+' : ''}${q.changePercent.toFixed(2)}%)`;

    sourceBadge.hidden = q.source !== 'simulated';
  });

  // ---- 房間 ----

  function renderRoomBadge() {
    roomBadge.textContent = `${roomInfo.room}｜${roomInfo.count}/${roomInfo.capacity} 人`;
  }

  socket.on('joined', (info) => {
    roomInfo = { ...roomInfo, ...info };
    renderRoomBadge();
  });

  socket.on('room-count', ({ count }) => {
    roomInfo.count = count;
    renderRoomBadge();
  });

  socket.on('disconnect', () => {
    roomBadge.textContent = '斷線，重連中…';
  });

  // ---- 彈幕 ----

  const MAX_ON_SCREEN = 120; // 保護前端：同時在畫面上的彈幕上限
  const PALETTE = ['#f6465d', '#2ebd85', '#f0b90b', '#58a6ff', '#d2a8ff', '#ff9f43', '#e6edf3', '#7ee787'];
  const LANES = 12;
  const laneNextFree = new Array(LANES).fill(0); // 每條軌道下一個可用時間，減少重疊

  function pickLane(now) {
    for (let i = 0; i < LANES; i++) {
      const lane = Math.floor(Math.random() * LANES);
      if (laneNextFree[lane] <= now) return lane;
    }
    return Math.floor(Math.random() * LANES); // 全滿就隨機疊
  }

  function spawnDanmaku(text, { mine = false } = {}) {
    if (stage.childElementCount >= MAX_ON_SCREEN) stage.firstElementChild.remove();

    const el = document.createElement('div');
    el.className = mine ? 'danmaku mine' : 'danmaku';
    el.textContent = text;
    el.style.color = PALETTE[Math.floor(Math.random() * PALETTE.length)];

    const now = performance.now();
    const lane = pickLane(now);
    const laneHeight = stage.clientHeight / LANES;
    el.style.top = `${lane * laneHeight + laneHeight * 0.15}px`;

    stage.appendChild(el);
    const distance = stage.clientWidth + el.offsetWidth;
    const duration = 7000 + Math.random() * 4000; // 7–11 秒飛完
    el.style.setProperty('--fly-distance', `-${distance}px`);
    el.style.animationDuration = `${duration}ms`;

    // 佔用軌道到「尾巴完全進場」為止，後面的彈幕才不會追撞
    const speed = distance / duration;
    laneNextFree[lane] = now + el.offsetWidth / speed + 300;

    el.addEventListener('animationend', () => el.remove());
  }

  socket.on('barrage', ({ text }) => spawnDanmaku(text));

  socket.on('rate-limited', () => showToast('發太快了，休息一下再發 🙏'));

  // ---- 輸入 ----

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    socket.emit('barrage', { text });
    spawnDanmaku(text.slice(0, 50), { mine: true });
    input.value = '';
    input.focus();
  });

  let toastTimer;
  function showToast(msg) {
    toast.textContent = msg;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast.hidden = true), 2500);
  }
})();
