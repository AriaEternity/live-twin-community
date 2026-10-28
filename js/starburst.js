/* ═══════════ 点击爆星 · 双子粉蓝双色（全站通用） ═══════════
   主站 index.html 与 Flask templates/base.html 双端引用（自包含 IIFE，无外部依赖）。
   与拖尾不同：爆星对触屏同样开放（移动端点按也该有反馈），仅「减少动效」用户禁用。
   用户友好：pointerdown 被动监听，不 preventDefault、不拦截点击/滑动/文本选择；
   80ms 触发节流防连点刷屏（踩坑 #12 同款思路）；星星纯装饰，z-index 80 让位弹层(90)/BOOT(100)。
   性能预算：canvas 懒创建（首次点击才建）；星星用预渲染精灵图（drawImage 比每帧
   画路径/文字便宜一个量级）；粒子总量封顶 60；rAF 空闲即停不空转；后台标签页自动暂停。 */
(function () {
  'use strict';
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  /* ── 精灵图预渲染：粉/蓝/白三色五角星，开局一次性画好 ── */
  var SPRITE_SIZE = 36;
  function makeSprite(fill) {
    var c = document.createElement('canvas');
    c.width = c.height = SPRITE_SIZE;
    var g = c.getContext('2d');
    var cx = SPRITE_SIZE / 2, cy = SPRITE_SIZE / 2;
    var R = 13, r = 5.6; /* 外/内半径，五角星比例 */
    g.beginPath();
    for (var i = 0; i < 10; i++) {
      var rad = (i % 2 === 0) ? R : r;
      var ang = -Math.PI / 2 + i * Math.PI / 5;
      var px = cx + rad * Math.cos(ang), py = cy + rad * Math.sin(ang);
      if (i === 0) g.moveTo(px, py); else g.lineTo(px, py);
    }
    g.closePath();
    g.shadowColor = fill;
    g.shadowBlur = 6; /* 烘焙进精灵的一次性辉光，运行期零成本 */
    g.fillStyle = fill;
    g.fill();
    return c;
  }
  var SPRITES = [
    makeSprite('#EE5BA6'), /* --ksk-primary 姬丝吉尔粉 */
    makeSprite('#FF5CE1'), /* --ksk-neon 粉高光 */
    makeSprite('#4A6FD0'), /* --lil-primary 璃拉蓝 */
    makeSprite('#45E0FF'), /* --lil-neon 蓝高光 */
    makeSprite('#FFFFFF'), /* 白星点缀 */
  ];

  var canvas = null, ctx = null;
  var W = 1, H = 1;
  /* 半分辨率渲染：画布只建 0.5×，CSS 拉伸回全屏。纹理上传量降 4 倍——
     2K/高缩放屏上全屏 canvas 每帧整幅上传会在点击瞬间造成卡顿，辉光星星肉眼不辨差异 */
  var SCALE = 0.5;
  var parts = [];
  var MAX_PARTS = 60;      /* 粒子总量硬上限，到顶丢弃新粒子 */
  var BURST_MIN_GAP = 80;  /* ms，两次爆发最小间隔（防连点刷屏） */
  var lastBurst = 0;
  var running = false;
  var lastTime = 0;

  function ensureCanvas() {
    if (canvas) return;
    canvas = document.createElement('canvas');
    ctx = canvas.getContext('2d');
    canvas.style.cssText = 'position:fixed;left:0;top:0;pointer-events:none;z-index:80;';
    document.body.appendChild(canvas);
    resize();
    addEventListener('resize', resize);
  }
  function resize() {
    W = innerWidth; H = innerHeight;
    canvas.width = Math.round(W * SCALE);
    canvas.height = Math.round(H * SCALE);
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
  }

  function burst(x, y) {
    var n = 8 + (Math.random() * 3 | 0); /* 每次 8-10 颗 */
    for (var i = 0; i < n; i++) {
      if (parts.length >= MAX_PARTS) break;
      var ang = Math.random() * Math.PI * 2;
      var spd = 90 + Math.random() * 210; /* px/s 初速 */
      parts.push({
        x: x, y: y,
        vx: Math.cos(ang) * spd,
        vy: Math.sin(ang) * spd - 60, /* 整体略向上飘 */
        rot: Math.random() * Math.PI * 2,
        vr: (Math.random() - 0.5) * 9, /* rad/s 自旋 */
        size: 0.5 + Math.random() * 0.75, /* 精灵缩放 0.5-1.25 */
        age: 0,
        life: 550 + Math.random() * 350, /* ms 存活 0.55-0.9s */
        sprite: SPRITES[(Math.random() * SPRITES.length) | 0],
      });
    }
  }

  addEventListener('pointerdown', function (e) {
    var now = performance.now();
    if (now - lastBurst < BURST_MIN_GAP) return;
    lastBurst = now;
    ensureCanvas();
    burst(e.clientX, e.clientY);
    if (!running) { running = true; lastTime = now; requestAnimationFrame(render); }
  }, { passive: true });

  var GRAVITY = 520; /* px/s² 轻微下坠，星星有抛物线手感 */

  function render(now) {
    var dt = Math.min((now - lastTime) / 1000, 0.05);
    lastTime = now;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (var i = parts.length - 1; i >= 0; i--) {
      var p = parts[i];
      p.age += dt * 1000;
      if (p.age >= p.life) { parts.splice(i, 1); continue; }
      p.vy += GRAVITY * dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.rot += p.vr * dt;
      var t = p.age / p.life;
      var alpha = t < 0.7 ? 1 : 1 - (t - 0.7) / 0.3; /* 前 70% 满亮，末段淡出 */
      var size = SPRITE_SIZE * p.size * (1 - t * 0.35); /* 边飞边缩 */
      ctx.globalAlpha = alpha;
      ctx.setTransform(SCALE, 0, 0, SCALE, p.x * SCALE, p.y * SCALE);
      ctx.rotate(p.rot);
      ctx.drawImage(p.sprite, -size / 2, -size / 2, size, size);
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;

    if (parts.length === 0) { running = false; return; } /* 粒子清空即停 rAF */
    requestAnimationFrame(render);
  }
})();
