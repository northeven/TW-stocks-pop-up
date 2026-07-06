/* global io */
(() => {
  // 依路徑決定頻道：/tsm → 美股台積電 ADR，其他 → 台股大盤
  const channel = location.pathname.replace(/\/+$/, '') === '/tsm' ? 'tsm' : 'taiex';
  document.title = channel === 'tsm' ? '美股 TSM 彈幕' : '台股彈幕';
  document.querySelectorAll('#channel-nav a').forEach((a) => {
    a.classList.toggle('active', a.dataset.channel === channel);
  });

  const socket = io({ query: { channel } });

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

  // ---- 即時走勢圖（畫在彈幕區背景的 Canvas）----

  const canvas = document.getElementById('chart');
  const ctx = canvas.getContext('2d');
  let chartPoints = []; // [timestamp, price]
  let chartPrevClose = null;

  const UP = '#f6465d';
  const DOWN = '#2ebd85';
  const timeFmt = new Intl.DateTimeFormat('zh-Hant-TW', {
    timeZone: 'Asia/Taipei', hour: '2-digit', minute: '2-digit', hour12: false,
  });

  function drawChart() {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (!w || !h) return;
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (chartPoints.length < 2 || chartPrevClose == null) return;

    const pad = { top: 24, right: 64, bottom: 26, left: 12 };
    const iw = w - pad.left - pad.right;
    const ih = h - pad.top - pad.bottom;
    // 右側留一小段空白，表示還在等未來的 tick
    const futureGap = Math.min(iw * 0.24, 168);
    const plotW = iw - futureGap;

    const t0 = chartPoints[0][0];
    const t1 = chartPoints[chartPoints.length - 1][0];
    let pMin = chartPrevClose;
    let pMax = chartPrevClose;
    for (const [, p] of chartPoints) {
      if (p < pMin) pMin = p;
      if (p > pMax) pMax = p;
    }
    const span = Math.max(pMax - pMin, chartPrevClose * 0.001); // 避免整條平的除以零
    pMin -= span * 0.08;
    pMax += span * 0.08;

    const x = (t) => pad.left + ((t - t0) / Math.max(t1 - t0, 1)) * plotW;
    const y = (p) => pad.top + (1 - (p - pMin) / (pMax - pMin)) * ih;

    const last = chartPoints[chartPoints.length - 1][1];
    const color = last >= chartPrevClose ? UP : DOWN;
    const dec = pMax - pMin < 10 ? 2 : 0; // 個股區間小要顯示小數，指數不用

    // 水平格線 + 右側價位標籤
    ctx.font = '11px sans-serif';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    const gridN = 4;
    for (let i = 0; i <= gridN; i++) {
      const p = pMin + ((pMax - pMin) * i) / gridN;
      const gy = y(p);
      ctx.strokeStyle = 'rgba(139, 148, 158, 0.12)';
      ctx.beginPath();
      ctx.moveTo(pad.left, gy);
      ctx.lineTo(pad.left + iw, gy);
      ctx.stroke();
      ctx.fillStyle = 'rgba(139, 148, 158, 0.7)';
      ctx.fillText(p.toFixed(dec), pad.left + iw + 8, gy);
    }

    // 昨收基準虛線
    if (chartPrevClose >= pMin && chartPrevClose <= pMax) {
      const by = y(chartPrevClose);
      ctx.strokeStyle = 'rgba(230, 237, 243, 0.35)';
      ctx.setLineDash([5, 5]);
      ctx.beginPath();
      ctx.moveTo(pad.left, by);
      ctx.lineTo(pad.left + iw, by);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = 'rgba(230, 237, 243, 0.6)';
      ctx.fillText(`昨收 ${chartPrevClose.toFixed(dec)}`, pad.left + 4, by - 9);
    }

    // 走勢線 + 底下漸層填色
    ctx.beginPath();
    for (let i = 0; i < chartPoints.length; i++) {
      const [t, p] = chartPoints[i];
      i === 0 ? ctx.moveTo(x(t), y(p)) : ctx.lineTo(x(t), y(p));
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.8;
    ctx.lineJoin = 'round';
    ctx.stroke();

    const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + ih);
    grad.addColorStop(0, color + '2e');
    grad.addColorStop(1, color + '00');
    ctx.lineTo(x(t1), pad.top + ih);
    ctx.lineTo(x(t0), pad.top + ih);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // 最新價光點
    ctx.beginPath();
    ctx.arc(x(t1), y(last), 3.5, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();

    // 時間軸（起訖）
    ctx.fillStyle = 'rgba(139, 148, 158, 0.7)';
    ctx.textBaseline = 'bottom';
    ctx.textAlign = 'left';
    ctx.fillText(timeFmt.format(new Date(t0)), pad.left, h - 6);
    ctx.textAlign = 'right';
    ctx.fillText(timeFmt.format(new Date(t1)), x(t1), h - 6);
  }

  new ResizeObserver(drawChart).observe(canvas);

  socket.on('market-history', ({ points, prevClose }) => {
    chartPoints = points || [];
    if (prevClose != null) chartPrevClose = prevClose;
    drawChart();
  });

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

    // 餵給走勢圖（昨收變了代表換交易日，重畫）
    if (chartPrevClose !== q.prevClose) {
      chartPrevClose = q.prevClose;
      chartPoints = [];
    }
    const lastPt = chartPoints[chartPoints.length - 1];
    if (!lastPt || lastPt[0] !== q.time) {
      chartPoints.push([q.time, q.price]);
      if (chartPoints.length > 4000) chartPoints.shift();
      drawChart();
    }
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
    const existing = stage.getElementsByClassName('danmaku');
    if (existing.length >= MAX_ON_SCREEN) existing[0].remove();

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

  // 分頁在背景時不渲染彈幕：CSS 動畫會被節流、animationend 可能不觸發，
  // 徒增游離節點與 GPU 負擔。回到前景後的新彈幕才繼續顯示。
  socket.on('barrage', ({ text }) => {
    if (document.hidden) return;
    spawnDanmaku(text);
  });

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
