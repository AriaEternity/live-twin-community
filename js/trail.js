/* ═══════════ 鼠标拖尾 · 双子粉蓝光带（全站通用） ═══════════
   主站 index.html 与 Flask templates/base.html 双端引用（自包含 IIFE，无外部依赖）。
   仅桌面精确指针 + 非减少动效时启用（触屏/reduce 直接不建 canvas）。
   性能预算：低分辨率 canvas（不乘 DPR，光效无需 retina）、16ms 采样节流、
   24 点环形上限、3 点合并一段贝塞尔减少 stroke 次数、轨迹清空即停 rAF 不空转、
   后台标签页 rAF 由浏览器自动暂停。z-index 80：压弹幕层(70)，让位弹层(90)/BOOT(100)。 */
(function () {
  'use strict';
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (!matchMedia('(pointer: fine)').matches) return;

  /* 尾→头色带：姬丝吉尔粉 → 紫过渡 → 璃拉蓝（锚点取自 tokens.css ksk/lil 三色，插值 16 级） */
  const ANCHORS = [
    [255, 244, 250],  /* 尾端近白，拖出即散 */
    [255, 158, 208],
    [238, 91, 166],   /* --ksk-primary */
    [150, 110, 200],  /* 粉蓝过渡紫 */
    [74, 111, 208],   /* --lil-primary */
    [69, 224, 255],   /* --lil-neon，头部最亮 */
  ];
  const STEPS = 16;
  const PALETTE = [];
  for (let i = 0; i < STEPS; i++) {
    const pos = (i / (STEPS - 1)) * (ANCHORS.length - 1);
    const ai = Math.min(Math.floor(pos), ANCHORS.length - 2);
    const f = pos - ai;
    PALETTE.push([
      Math.round(ANCHORS[ai][0] + (ANCHORS[ai + 1][0] - ANCHORS[ai][0]) * f),
      Math.round(ANCHORS[ai][1] + (ANCHORS[ai + 1][1] - ANCHORS[ai][1]) * f),
      Math.round(ANCHORS[ai][2] + (ANCHORS[ai + 1][2] - ANCHORS[ai][2]) * f),
    ]);
  }

  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  canvas.style.cssText = 'position:fixed;left:0;top:0;pointer-events:none;z-index:80;';
  document.body.appendChild(canvas);

  const trail = [];
  const MAX_POINTS = 24;
  const RECORD_INTERVAL = 16; /* ms，约 60fps 采样节奏 */
  const STROKE_GROUP = 3;     /* 每 3 个采样点合并一段贝塞尔，减少 stroke 调用 */
  const MAX_AGE = 420;        /* ms，采样点存活期 */
  let W = 1, H = 1;
  let lastRecord = 0;
  let running = false;        /* 页面加载时无轨迹，不启动 rAF */
  let lastTime = 0;

  function resize() {
    W = innerWidth; H = innerHeight;
    canvas.width = W; canvas.height = H;
  }
  resize();
  addEventListener('resize', resize);

  document.addEventListener('mousemove', e => {
    const now = performance.now();
    if (now - lastRecord < RECORD_INTERVAL) return;
    lastRecord = now;
    trail.push({ x: e.clientX, y: e.clientY, age: 0 });
    while (trail.length > MAX_POINTS) trail.shift();
    /* 空闲时 rAF 已停止，鼠标再次移动时重启 */
    if (!running) { running = true; lastTime = now; requestAnimationFrame(render); }
  }, { passive: true });

  /* 把 trail[from..to] 描成一条平滑贝塞尔路径（不 stroke，供三层复用） */
  function tracePath(from, to) {
    ctx.beginPath();
    ctx.moveTo(trail[from].x, trail[from].y);
    for (let i = from; i < to; i++) {
      const last = i === to - 1;
      const mx = (trail[i].x + trail[i + 1].x) / 2;
      const my = (trail[i].y + trail[i + 1].y) / 2;
      ctx.quadraticCurveTo(trail[i].x, trail[i].y, last ? trail[i + 1].x : mx, last ? trail[i + 1].y : my);
    }
  }

  function render(now) {
    const dt = Math.min(now - lastTime, 33);
    lastTime = now;

    ctx.clearRect(0, 0, W, H);
    for (let i = trail.length - 1; i >= 0; i--) {
      trail[i].age += dt;
      if (trail[i].age > MAX_AGE) trail.splice(i, 1);
    }
    const n = trail.length;
    if (n < 2) { running = false; return; } /* 轨迹清空后停止 rAF，不再空转 */

    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    /* 第一层：粉色光晕（整条路径一次 stroke） */
    tracePath(0, n - 1);
    ctx.lineWidth = 6;
    ctx.strokeStyle = 'rgba(238, 91, 166, 0.16)';
    ctx.stroke();

    /* 第二层：渐变彩带——分段上色，越靠头部越亮越粗 */
    for (let k = STROKE_GROUP; k < n; k += STROKE_GROUP) {
      const start = k - STROKE_GROUP, end = Math.min(k, n - 1);
      if (end <= start) continue;
      const t = k / n;
      const ci = t * (STEPS - 1);
      const idx = Math.min(Math.floor(ci), STEPS - 2);
      const f = ci - idx;
      const c0 = PALETTE[idx], c1 = PALETTE[idx + 1];
      tracePath(start, end);
      ctx.lineWidth = 1.8 + t * 3.2;
      ctx.strokeStyle = 'rgba(' +
        Math.round(c0[0] + (c1[0] - c0[0]) * f) + ',' +
        Math.round(c0[1] + (c1[1] - c0[1]) * f) + ',' +
        Math.round(c0[2] + (c1[2] - c0[2]) * f) + ',' +
        (0.2 + t * 0.65).toFixed(3) + ')';
      ctx.stroke();
    }

    /* 第三层：内核高亮白线 */
    tracePath(0, n - 1);
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.7)';
    ctx.stroke();

    requestAnimationFrame(render);
  }
  /* rAF 不在页面加载时启动——由鼠标首次移动触发 */
})();
