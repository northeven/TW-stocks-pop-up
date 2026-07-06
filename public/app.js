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
  const composer = document.getElementById('composer');
  const toast = document.getElementById('toast');

  let roomInfo = { room: '—', count: 0, capacity: 500 };

  // ---- 即時走勢圖（畫在彈幕區背景的 Canvas）----

  const canvas = document.getElementById('chart');
  const ctx = canvas.getContext('2d');
  let chartPoints = []; // [timestamp, price]
  let chartPrevClose = null;

  const UP = '#f6465d';
  const DOWN = '#2ebd85';
  const VOL_COLOR = 'rgba(88, 166, 255, 0.55)'; // 交易量用淺藍，與紅漲綠跌區隔
  const volFmt = new Intl.NumberFormat('zh-Hant-TW', { notation: 'compact', maximumFractionDigits: 1 });
  // 交易時段（當地時間、以分鐘計）：時間軸固定攤開整段，尚未成交的時間留白。
  const SESSION = channel === 'tsm'
    ? { tz: 'America/New_York', open: 9 * 60 + 30, close: 16 * 60 } // 09:30–16:00 ET
    : { tz: 'Asia/Taipei', open: 9 * 60, close: 13 * 60 + 30 };     // 09:00–13:30
  const hmFmt = new Intl.DateTimeFormat('en-US', {
    timeZone: SESSION.tz, hour12: false, hour: '2-digit', minute: '2-digit',
  });
  function minutesOfDay(t) {
    let hh = 0;
    let mm = 0;
    for (const part of hmFmt.formatToParts(new Date(t))) {
      if (part.type === 'hour') hh = Number(part.value);
      else if (part.type === 'minute') mm = Number(part.value);
    }
    if (hh === 24) hh = 0; // 某些環境午夜回傳 24
    return hh * 60 + mm;
  }

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
    // 下方切一條副圖畫交易量，價格線只用上半部
    const volH = Math.round(ih * 0.16);
    const volGap = 8;
    const priceH = ih - volH - volGap;
    const volBase = pad.top + ih; // 量能副圖的底線

    const t1 = chartPoints[chartPoints.length - 1][0];
    // 時間軸固定攤開整段交易時段，資料只填到「現在」；右側空白＝尚未到來的盤中時間，
    // tick 因此維持原本密度、不被拉伸。以最後一筆為基準換算當地分鐘，避免逐點做時區轉換。
    const lastMin = minutesOfDay(t1);
    const localMin = (t) => lastMin + (t - t1) / 60000;
    // 左邊界從第一筆資料起（Render 冷啟動可能盤中才開始收），昨收起點就貼齊最左；
    // 右邊界固定為收盤時間，留白＝尚未到來的盤中時間。
    const firstMin = localMin(chartPoints[0][0]);
    const domainMax = SESSION.close;
    const leftMargin = Math.max((domainMax - firstMin) * 0.02, 1); // 給昨收起點一點寬度畫初始漲跌
    const domainMin = firstMin - leftMargin;
    // 第一個 tick 預留為昨收，畫在最左邊，一開始就能看到初始漲跌折線
    const openT = chartPoints[0][0] - leftMargin * 60000;
    const pts = [[openT, chartPrevClose], ...chartPoints];

    // 垂直範圍以昨收為中心，預設預留上下 1.3%；當日振幅超過就對稱動態拉高，
    // 讓昨收始終落在線圖正中央。
    let maxDev = 0;
    for (const [, p] of chartPoints) {
      const dev = Math.abs(p - chartPrevClose);
      if (dev > maxDev) maxDev = dev;
    }
    const half = Math.max(chartPrevClose * 0.013, maxDev * 1.08);
    const pMin = chartPrevClose - half;
    const pMax = chartPrevClose + half;

    const x = (t) => {
      const frac = (localMin(t) - domainMin) / (domainMax - domainMin);
      return pad.left + Math.min(Math.max(frac, 0), 1) * iw;
    };
    const y = (p) => pad.top + (1 - (p - pMin) / (pMax - pMin)) * priceH;

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

    // 垂直時間格線 + 整點標籤（攤開整段交易時間）
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    for (let hr = Math.ceil((domainMin + 1) / 60); hr * 60 <= domainMax; hr++) {
      const gx = pad.left + ((hr * 60 - domainMin) / (domainMax - domainMin)) * iw;
      ctx.strokeStyle = 'rgba(139, 148, 158, 0.12)';
      ctx.beginPath();
      ctx.moveTo(gx, pad.top);
      ctx.lineTo(gx, pad.top + ih);
      ctx.stroke();
      ctx.fillStyle = 'rgba(139, 148, 158, 0.7)';
      ctx.fillText(String(hr), gx, h - 6);
    }
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';

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

    // 走勢線（含開盤昨收起點）：以昨收為界，之上紅、之下綠，用裁切自動切色
    const byLevel = y(chartPrevClose); // 昨收在畫面上的 y（因置中，約在中央）
    const linePath = new Path2D();
    for (let i = 0; i < pts.length; i++) {
      const [t, p] = pts[i];
      i === 0 ? linePath.moveTo(x(t), y(p)) : linePath.lineTo(x(t), y(p));
    }
    // 面積填色：線與昨收基準線之間，之上淡紅、之下淡綠
    const areaPath = new Path2D(linePath);
    areaPath.lineTo(x(t1), byLevel);
    areaPath.lineTo(x(openT), byLevel);
    areaPath.closePath();

    const clipTop = () => { ctx.beginPath(); ctx.rect(pad.left, pad.top, iw, byLevel - pad.top); ctx.clip(); };
    const clipBottom = () => { ctx.beginPath(); ctx.rect(pad.left, byLevel, iw, pad.top + priceH - byLevel); ctx.clip(); };

    ctx.save(); clipTop(); ctx.fillStyle = UP + '22'; ctx.fill(areaPath); ctx.restore();
    ctx.save(); clipBottom(); ctx.fillStyle = DOWN + '22'; ctx.fill(areaPath); ctx.restore();

    ctx.lineWidth = 1.1;
    ctx.lineJoin = 'round';
    ctx.save(); clipTop(); ctx.strokeStyle = UP; ctx.stroke(linePath); ctx.restore();
    ctx.save(); clipBottom(); ctx.strokeStyle = DOWN; ctx.stroke(linePath); ctx.restore();

    // 最新價光點
    ctx.beginPath();
    ctx.arc(x(t1), y(last), 2.2, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();

    // ---- 交易量副圖 ----
    let maxVol = 0;
    for (const pt of chartPoints) {
      if (pt[2] > maxVol) maxVol = pt[2];
    }
    if (maxVol > 0) {
      for (let i = 0; i < chartPoints.length; i++) {
        const v = chartPoints[i][2];
        if (!v) continue;
        const bx = x(chartPoints[i][0]);
        const nx = i + 1 < chartPoints.length ? x(chartPoints[i + 1][0]) : bx + 1;
        const bw = Math.max(1, nx - bx);
        const bh = (v / maxVol) * volH;
        ctx.fillStyle = VOL_COLOR;
        ctx.fillRect(bx, volBase - bh, bw, bh);
      }
      // 量能最大值標籤
      ctx.fillStyle = 'rgba(139, 148, 158, 0.7)';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      ctx.fillText(volFmt.format(maxVol), pad.left + 2, volBase - volH);
    }
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
      chartPoints.push([q.time, q.price, q.barVolume ?? 0]);
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

  const sendBtn = form.querySelector('button[type="submit"]');
  const SEND_COOLDOWN_MS = 600; // 與後端同 IP 節流一致：每 0.6 秒才能再發
  let onCooldown = false;
  let cooldownTimer;

  function startCooldown() {
    onCooldown = true;
    sendBtn.disabled = true;
    clearTimeout(cooldownTimer);
    cooldownTimer = setTimeout(() => {
      onCooldown = false;
      sendBtn.disabled = false;
    }, SEND_COOLDOWN_MS);
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (onCooldown) return;
    const text = input.value.trim();
    if (!text) return;
    socket.emit('barrage', { text });
    spawnDanmaku(text.slice(0, 50), { mine: true });
    input.value = '';
    startCooldown();
    input.focus();
  });

  // ---- 手機鍵盤：讓輸入列緊貼鍵盤上緣 ----
  // iOS Safari 開鍵盤時不會縮小版面（layout viewport 維持滿高），輸入列會被留在
  // 鍵盤下方而露出大片空白。改用 visualViewport 量出鍵盤遮住的高度，把輸入列上移貼齊。
  const vv = window.visualViewport;
  if (vv) {
    const stickComposer = () => {
      const inset = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      composer.style.transform = inset ? `translateY(-${inset}px)` : '';
    };
    vv.addEventListener('resize', stickComposer);
    vv.addEventListener('scroll', stickComposer);
  }

  let toastTimer;
  function showToast(msg) {
    toast.textContent = msg;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast.hidden = true), 2500);
  }
})();
