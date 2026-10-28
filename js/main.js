/* ═══════════════════════════════════════════════════════════
   直播☆双子 主题网站 — 交互逻辑
   BOOT序列 / 弹幕引擎 / 粒子 / 场景编排 / 卡册 / 彩蛋
   ═══════════════════════════════════════════════════════════ */
'use strict';

/* 纯文件打开（file:// 双击预览）时隐藏需要 Flask 服务的入口（论坛/社区）。
   放在 GSAP 守卫之前：即使 CDN 失败进入静态降级，file:// 下也不显示死链。 */
if (location.protocol === 'file:') {
  document.querySelectorAll('.needs-server').forEach(el => { el.style.display = 'none'; });
}

/* GSAP 可用性判定：CDN 任一文件失败即进入静态降级——无动画，但 BOOT 自动跳过、
   卡册/弹层/按钮等功能全部可用；下方所有 gsap 调用点均以 HAS_GSAP 守卫 */
const HAS_GSAP = !!(window.gsap && window.ScrollTrigger && window.ScrollToPlugin);
if (HAS_GSAP) {
  gsap.registerPlugin(ScrollTrigger, ScrollToPlugin);
  /* 掉帧补偿：低端机卡顿超过 500ms 时按 33ms 步进追赶，避免滚动 scrub 动画瞬移跳变 */
  gsap.ticker.lagSmoothing(500, 33);
} else {
  /* 先卸载 BOOT 覆盖层让静态内容可读（bootSequence 里会正式跳过开场），并给不显眼的小提示 */
  const _boot = document.getElementById('boot');
  if (_boot) _boot.style.display = 'none';
  document.body.classList.remove('boot-locked');
  const _hud = document.getElementById('live-hud');
  if (_hud) _hud.classList.remove('hidden');
  const _tip = document.createElement('div');
  _tip.className = 'anim-fallback-tip';
  _tip.textContent = '动画资源加载失败，已切换为静态模式';
  document.body.appendChild(_tip);
}

const IS_MOBILE = matchMedia('(max-width: 768px)').matches;
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const PARAMS = new URLSearchParams(location.search);
const STATIC = PARAMS.has('static'); // 静态审查模式：跳过所有滚动动画，内容直接可见
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const rand = (a, b) => a + Math.random() * (b - a);
const pick = arr => arr[Math.floor(Math.random() * arr.length)];

/* ═══════════ 弹幕引擎 ═══════════ */

/* 弹幕开关存取：localStorage 可能被禁用（隐私模式等），读写全部静默兜底 */
const danmakuStore = {
  key: 'lt-danmaku',
  load() { try { return localStorage.getItem(this.key); } catch (e) { return null; } },
  save(v) { try { localStorage.setItem(this.key, v); } catch (e) { /* 存储不可用时仅本次会话生效 */ } },
};
const _dkSaved = danmakuStore.load();

/* 弹幕轨道分布（vh，桌面端）：避开各场景中央内容带，轨道往上 1/4 与底部集中。
   移动端内容纵向堆叠、避障空间有限，保持 init() 里的均匀分布不动。
   原则：每屏顶部只留 6vh 单轨道，中部轨道必须落在元素间隙带；
   各场景表是「最大轨道集」，setTops 还会按实测标题安全线再过滤（见 filterBySafeTop）。 */
const DK_TOPS_DEFAULT = [6, 11, 16, 88, 93];
/* 动态避障候选轨道池：实测标题安全线后从中筛选，过滤结果再与场景手动最大集求交。
   基础档 [6,11,16,21,28,45,80,92,96] 之外并收录各场景手动表里的手调间隙带（10/15/86/88/91/93/95/97），
   否则求交会把这些与标题无关的底部轨道误删（实测发现 live 的 91、profiles 的 97 会被剥掉）。 */
const DK_TRACK_POOL = [6, 10, 11, 15, 16, 21, 28, 45, 80, 86, 88, 91, 92, 93, 95, 96, 97];
/* 轨道顶（含 ±2vh 抖动）的底部安全上限：弹幕本体高约 3-4vh（15px 字 + .3em 上下 padding，矮屏占比更高），
   轨道顶超过 95vh 时弹幕下半身会被视口底边裁掉 */
const DK_MAX_TOP = 95;
const DK_SCENE_TOPS = {
  live: [6, 11, 91, 96],       // 大标题 16~29vh、立绘卡 32~90vh，只留顶/底两带
  hijack: [6, 11, 80, 86, 92], // 内容居中，上下两带让开
  phantom: [6, 91, 96],        // 长滚动场景，内容随滚动变化，压顶/底两带
  profiles: [6, 92, 97],       // 顶部只留 6vh 单轨；档案卡占 20~90vh，底部两带保留
  sunny: [6, 45, 95],          // 顶部只留 6vh 单轨；45vh 是排行榜与介绍区之间的间隙带
  gallery: [6, 11, 15],        // 卡格占满中下部，轨道收进标题上方间隙带
  outro: [6, 10, 92, 96],      // 正文与页脚在 40~88vh
};

const Danmaku = {
  layer: $('#danmaku-layer'),
  pool: DANMAKU.live,
  tracks: [],
  sceneTops: null, // 当前场景的轨道分布（手动最大集）；null = 默认两带
  sceneEl: null,   // 当前场景元素，动态避障测量用
  timer: null,
  density: IS_MOBILE ? 2600 : 1500,
  maxTracks: IS_MOBILE ? 4 : 7,
  /* 开关状态：localStorage 记忆优先；prefers-reduced-motion 用户默认关闭（替代原来的 0.01s 瞬移残影） */
  enabled: _dkSaved !== null ? _dkSaved === '1' : !REDUCED,

  init() {
    // 桌面端轨道避开中央内容带（按场景取 DK_SCENE_TOPS + 动态避障，见 rebuildTracks）；移动端保持均匀分布
    const mob = matchMedia('(max-width: 768px)').matches; // 实时判断，横竖屏切换后重建轨道能拿到新断点
    if (mob) {
      this.tracks = Array.from({ length: this.maxTracks }, (_, i) => 10 + i * 11)
        .map(top => ({ top: Math.min(top + rand(-2, 2), DK_MAX_TOP), busy: false }));
    } else {
      this.rebuildTracks();
    }
  },

  /* 场景切换时更换轨道分布（仅桌面端重建；移动端保持均匀分布）。
     sceneEl 用于动态避障：实测场景标题安全线，在手动最大集上再过滤压标题带的轨道 */
  setTops(tops, sceneEl) {
    this.sceneTops = tops || null;
    this.sceneEl = sceneEl || null;
    if (matchMedia('(max-width: 768px)').matches) return;
    this.rebuildTracks();
  },

  /* 桌面端轨道重建：场景手动最大集 ∩ 实测标题安全线过滤后的候选池；加抖动后钳到 DK_MAX_TOP 防底边裁断 */
  rebuildTracks() {
    const tops = this.filterBySafeTop(this.sceneTops || DK_TOPS_DEFAULT);
    this.tracks = tops.slice(0, this.maxTracks).map(top => ({ top: Math.min(top + rand(-2, 2), DK_MAX_TOP), busy: false }));
  },

  /* 动态避障：实测当前场景 .scene-kicker/.section-title 的底部安全线（+3% 缓冲），
     候选池中落入 [0, safeTop] 的轨道剔除（6vh 顶轨永远保留），再与手动最大集求交（只缩不扩）。
     用「元素底部相对场景顶部的偏移」折 vh，与当前滚动位置无关，结果稳定；
     弹幕关闭/减弱动态/场景无标题元素时跳过测量，直接用手动集。 */
  filterBySafeTop(tops) {
    if (!this.enabled || REDUCED || !this.sceneEl) return tops;
    const sceneTop = this.sceneEl.getBoundingClientRect().top;
    let bottom = 0;
    this.sceneEl.querySelectorAll('.scene-kicker, .section-title').forEach(el => {
      bottom = Math.max(bottom, el.getBoundingClientRect().bottom - sceneTop);
    });
    if (bottom <= 0) return tops; // 标题未渲染（display:none 等）时放弃测量
    const safeTop = bottom / innerHeight * 100 + 3;
    const pool = DK_TRACK_POOL.filter(t => t === 6 || t > safeTop);
    return tops.filter(t => pool.includes(t));
  },

  setPool(pool) { this.pool = pool; this._bag = null; }, // 换场景换池时重置抽签袋

  /* 深夜档彩蛋取词：仅常态直播（live 池）混入，剧情场景（劫持/怪盗等）不打扰叙事。
     节日弹幕全天可混，深夜台词只在本地 22:00–次日 5:00 混入；两者各 25% 概率，节日优先。
     主池走抽签袋：整池打乱不放回抽取、抽完重置，同一句一轮内绝不重复（治「老看到重复弹幕」） */
  pickLine() {
    if (this.pool === DANMAKU.live) {
      const now = new Date();
      const mmdd = String(now.getMonth() + 1).padStart(2, '0') + '-' + String(now.getDate()).padStart(2, '0');
      const fes = DANMAKU.festival && DANMAKU.festival[mmdd];
      if (fes && Math.random() < 0.25) return pick(fes);
      const h = now.getHours();
      if ((h >= 22 || h < 5) && Math.random() < 0.25) return pick(DANMAKU.night);
    }
    if (!this._bag || !this._bag.length) {
      this._bag = this.pool.slice(); // Fisher-Yates 打乱
      for (let i = this._bag.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        const t = this._bag[i]; this._bag[i] = this._bag[j]; this._bag[j] = t;
      }
    }
    return this._bag.pop();
  },

  /* 弹幕开关：关闭时停止生成并隐藏弹幕层，再开即恢复 */
  setEnabled(on) {
    this.enabled = on;
    danmakuStore.save(on ? '1' : '0');
    if (this.layer) this.layer.style.display = on ? '' : 'none';
    if (on) this.start(); else this.stop();
  },

  fire(text, cls = '') {
    if (!this.enabled || !HAS_GSAP) return; // 开关关闭或无动画库时不生成弹幕
    cls = cls || this.poolExt || ''; // scene 全局标记的额外类（如 dk-evil）
    const free = this.tracks.filter(t => !t.busy);
    if (!free.length) return;
    const track = pick(free);
    track.busy = true;

    const el = document.createElement('div');
    el.className = 'danmaku ' + cls;
    el.textContent = text;
    el.style.top = track.top + 'vh';
    this.layer.appendChild(el);

    /* 宽度测量优化：不再逐条读 offsetWidth 强制 reflow。按 CJK/ASCII 分开估值（15px 字号经验值），
       每类弹幕仅首条实测一次校准 CJK 单字宽；+8% 余量只多不少，保证弹幕飞出屏外才移除、轨道释放时序不变 */
    const cache = this._wCache || (this._wCache = {});
    const key = cls || '_';
    const est = cache[key] || { c: 15, a: 8.5 };
    const cjk = (text.match(/[^\x00-\xff]/g) || []).length;
    let w = Math.ceil((cjk * est.c + (text.length - cjk) * est.a + 28) * 1.08); // 28 ≈ .9em×2 padding
    if (!cache[key]) {
      const real = el.offsetWidth;
      if (real > 28 && cjk > 0) cache[key] = { c: (real - 28 - (text.length - cjk) * est.a) / cjk, a: est.a };
      if (real > 0) w = real; // 首条直接用实测值（已付出 reflow 成本），估值只服务后续弹幕
    }
    const dist = innerWidth + w + 40;
    const dur = dist / (IS_MOBILE ? 90 : 130); // 速度 px/s

    gsap.fromTo(el, { x: 0 }, {
      x: -dist, duration: dur, ease: 'none',
      onComplete: () => { el.remove(); track.busy = false; },
    });
    // 轨道提前释放（弹幕尾部离开屏幕右缘后）
    gsap.delayedCall(Math.min(dur * (w + 120) / dist, dur * 0.5), () => { track.busy = false; });
    return el;
  },

  start() {
    if (this.timer || !this.enabled || !HAS_GSAP) return; // 已启动/开关关闭/无动画库时不启动
    const loop = () => {
      const r = Math.random();
      if (r < 0.06) this.fire(pick(DANMAKU.gift), 'dk-sc');
      else this.fire(this.pickLine());
      this.timer = setTimeout(loop, this.density * rand(0.5, 1.5));
    };
    loop();
  },
  stop() { clearTimeout(this.timer); this.timer = null; },
  burst(texts, cls = '', n = 12) {
    for (let i = 0; i < n; i++) {
      setTimeout(() => this.fire(texts[i % texts.length], cls), i * 160);
    }
  },
};

/* ═══════════ SC 置顶横幅 ═══════════
   真实直播间 SuperChat 形态：金色横幅置顶居中悬停 6-8 秒后淡出。
   容器挂在弹幕层内（随弹幕开关整体显隐，z-index 低于 HUD 高于普通弹幕），
   最多 2 条堆叠，新来顶替最旧；reduced-motion 只做静态淡入淡出 */
const SCPin = {
  items: [],
  GAP: 76,   // 堆叠纵向间距（px）：横幅高约 56px + 间隙
  layer: null,
  ensureLayer() {
    if (!this.layer) {
      this.layer = document.createElement('div');
      this.layer.id = 'dk-sc-pin';
      this.layer.setAttribute('aria-hidden', 'true');
      this.layer.style.cssText = 'position:absolute; inset:0; z-index:1; pointer-events:none;';
      (Danmaku.layer || document.body).appendChild(this.layer);
    }
    return this.layer;
  },
  push(text) {
    if (!Danmaku.enabled) return; // 弹幕关闭时 SC 横幅同样不出现
    const layer = this.ensureLayer();
    if (this.items.length >= 2) this.remove(this.items[0], true); // 最多 2 条，新来顶替最旧
    const el = document.createElement('div');
    el.className = 'sc-banner';
    el.textContent = text;
    el.style.top = '9%';
    layer.appendChild(el);
    this.items.push(el);
    const slotY = (this.items.length - 1) * this.GAP;
    if (HAS_GSAP && !REDUCED) {
      gsap.fromTo(el, { xPercent: -50, y: slotY - 70, opacity: 0 },
        { xPercent: -50, y: slotY, opacity: 1, duration: .55, ease: 'back.out(1.7)' });
      gsap.delayedCall(rand(6, 8), () => this.remove(el));
    } else {
      el.style.top = `calc(9% + ${slotY}px)`; // 无动画/reduced：改用 top 堆叠，transform 保持静态
      if (HAS_GSAP) { // reduced-motion：仅 opacity 淡入淡出
        gsap.fromTo(el, { opacity: 0 }, { opacity: 1, duration: .4 });
        gsap.delayedCall(rand(6, 8), () => this.remove(el));
      } else {
        setTimeout(() => this.remove(el), rand(6000, 8000)); // 无动画库：静态展示 6-8 秒后移除
      }
    }
  },
  remove(el, instant = false) {
    const i = this.items.indexOf(el);
    if (i === -1) return; // 已被顶替清理，忽略迟到的定时器
    this.items.splice(i, 1);
    if (HAS_GSAP) gsap.killTweensOf(el);
    if (instant || !HAS_GSAP) el.remove();
    else if (REDUCED) gsap.to(el, { opacity: 0, duration: .35, onComplete: () => el.remove() });
    else gsap.to(el, { opacity: 0, y: '-=24', duration: .3, ease: 'power2.in', onComplete: () => el.remove() });
    // 下方横幅补位上堆
    this.items.forEach((item, idx) => {
      if (!HAS_GSAP || REDUCED) item.style.top = `calc(9% + ${idx * this.GAP}px)`;
      else gsap.to(item, { y: idx * this.GAP, duration: .35, ease: 'power2.out', overwrite: 'auto' });
    });
  },
};

/* ═══════════ 粒子系统 ═══════════ */

const Particles = {
  cv: $('#particles'), ctx: null, items: [], mode: 'off', raf: null,
  init() {
    this.ctx = this.cv.getContext('2d');
    // 初始值必须为 0：否则首次 resize() 判断恒假，canvas 永远停在默认 300×150 被 CSS 拉伸（桌面粒子变模糊巨块）
    let lastW = 0, lastH = 0;
    const resize = () => {
      if (innerWidth !== lastW || Math.abs(innerHeight - lastH) > 120) {
        lastW = innerWidth; lastH = innerHeight;
        this.cv.width = innerWidth; this.cv.height = innerHeight;
      }
    };
    addEventListener('resize', resize); resize();
  },
  setMode(mode) {
    if (this.mode === mode) return;
    this.mode = mode;
    this.items = [];
    if (mode === 'off') { cancelAnimationFrame(this.raf); this.raf = null; this.clear(); return; }
    const n = REDUCED ? 0 : (matchMedia('(max-width: 768px)').matches ? 26 : 60); // 实时判断，横竖屏切换后重建粒子能拿到新档位
    for (let i = 0; i < n; i++) this.items.push(this.spawn(mode, true));
    if (!this.raf) this.loop();
  },
  spawn(mode, init = false) {
    const W = this.cv.width, H = this.cv.height;
    if (mode === 'confetti') {
      return {
        mode, x: rand(0, W), y: init ? rand(0, H) : -20,
        vy: rand(.4, 1.2), vx: rand(-.3, .3), r: rand(2, 5),
        rot: rand(0, 6.28), vr: rand(-.04, .04),
        color: pick(['#EE5BA6', '#4A6FD0', '#FFD447', '#7CE85A', '#FF5CE1', '#45E0FF']),
        shape: Math.random() < .5 ? 'circle' : 'star',
      };
    }
    // diamond
    return {
      mode, x: rand(0, W), y: init ? rand(0, H) : -30,
      vy: rand(.5, 1.6), vx: rand(-.2, .2), r: rand(3, 8),
      rot: rand(0, 6.28), vr: rand(-.03, .03),
      color: pick(['#45E0FF', '#FF5CE1', '#E8F8FF', '#FFD447']),
      tw: rand(0, 6.28),
    };
  },
  drawStar(c, r) {
    c.beginPath();
    for (let i = 0; i < 10; i++) {
      const rad = i % 2 ? r * .45 : r;
      const a = i * Math.PI / 5 - Math.PI / 2;
      c[i ? 'lineTo' : 'moveTo'](Math.cos(a) * rad, Math.sin(a) * rad);
    }
    c.closePath(); c.fill();
  },
  drawDiamond(c, r) {
    c.beginPath();
    c.moveTo(0, -r); c.lineTo(r * .7, 0); c.lineTo(0, r); c.lineTo(-r * .7, 0);
    c.closePath(); c.fill();
    c.globalAlpha = .5;
    c.beginPath(); c.moveTo(0, -r); c.lineTo(r * .7, 0); c.lineTo(0, 0); c.closePath();
    c.fillStyle = '#fff'; c.fill();
  },
  loop() {
    this.raf = requestAnimationFrame(() => this.loop());
    if (this.mode === 'off') return;
    const { ctx: c, cv } = this;
    c.clearRect(0, 0, cv.width, cv.height);
    for (let i = this.items.length - 1; i >= 0; i--) {
      const p = this.items[i];
      p.y += p.vy; p.x += p.vx; p.rot += p.vr;
      if (p.y > cv.height + 40) this.items[i] = this.spawn(p.mode);
      c.save();
      c.translate(p.x, p.y); c.rotate(p.rot);
      if (p.mode === 'diamond') {
        p.tw += .05;
        c.globalAlpha = .5 + Math.sin(p.tw) * .4;
        c.fillStyle = p.color;
        this.drawDiamond(c, p.r);
      } else {
        c.globalAlpha = .8;
        c.fillStyle = p.color;
        if (p.shape === 'star') this.drawStar(c, p.r);
        else { c.beginPath(); c.arc(0, 0, p.r, 0, 6.28); c.fill(); }
      }
      c.restore();
    }
  },
  clear() { this.ctx && this.ctx.clearRect(0, 0, this.cv.width, this.cv.height); },
};

/* ═══════════ 图片预热器（进入直播间后后台限速预取） ═══════════ */

const Preloader = {
  started: false,
  // 延迟启动：等 window load + 浏览器空闲（requestIdleCallback）再预取，
  // 避免与 BOOT 结束后用户滚动触发的懒加载图争抢带宽；无 rIC 的浏览器 1s 兜底
  start() {
    if (this.started) return;
    this.started = true;
    const kickoff = () => {
      const idle = window.requestIdleCallback || ((cb) => setTimeout(cb, 1000));
      idle(() => this._run());
    };
    if (document.readyState === 'complete') kickoff();
    else window.addEventListener('load', kickoff, { once: true });
  },
  _run() {
    // 流量敏感用户跳过预热
    if (navigator.connection && (navigator.connection.saveData || /2g/.test(navigator.connection.effectiveType))) return;
    // 卡图已全部本地化（data.js IMG_*/FANDOM_IMG 本地路径）；卡册网格用 small 图，整图不再预热
    // 批次1后列表已很短：移动端仅 5 张关键图，桌面端追加 7 张翻卡区裁切图
    const all = IS_MOBILE ? [] : [36326160, 9205573, 54257392, 73810864, 36609518, 81078880, 82699999].map(IMG_CROP);
    const urls = [
      IMG_CROP(62098216),
      FANDOM_IMG.md_kisikil,
      FANDOM_IMG.md_lilla,
      IMG_CROP(37582948),
      IMG_CROP(35487920),
      ...all,
    ];
    let i = 0;
    const next = () => {
      if (i >= urls.length) return;
      const img = new Image();
      img.onload = img.onerror = () => setTimeout(next, 400); // 列表已短，节奏更佛系
      img.src = urls[i++];
    };
    next(); next(); next(); // 3并发限速
  },
};

/* ═══════════ BOOT 序列 ═══════════ */

/* 进场待机弹幕爆发：进入直播间后来一波「观众进场」（5-8 条、间隔 200-300ms 错开），
   正常进场与 ?skip-boot/?static 路径共用，只触发一次；reduced-motion/弹幕关闭时不触发 */
let _entryBurstDone = false;
function entryDanmakuBurst() {
  if (_entryBurstDone || REDUCED || !HAS_GSAP || !Danmaku.enabled) return;
  _entryBurstDone = true;
  const texts = ['来了来了！', '前排！', '从魔界追过来的老粉了', '开播了开播了！', '蹲点成功！', '双子贴贴！'];
  const n = 5 + Math.floor(rand(0, 4)); // 5-8 条
  for (let i = 0; i < n; i++) {
    setTimeout(() => Danmaku.fire(pick(texts)), 500 + i * rand(200, 300));
  }
}

function bootSequence() {
  document.body.classList.add('boot-locked');

  /* 完成开场：卸载 BOOT、解锁滚动、显示 HUD/徽章/导航，启动弹幕粒子与图片预热 */
  const revealSite = () => {
    $('#boot').style.display = 'none';
    document.body.classList.remove('boot-locked');
    $('#live-hud').classList.remove('hidden');
    $('#mode-badge').classList.remove('hidden');
    $('#scene-nav').classList.remove('hidden');
    Danmaku.init(); Danmaku.start();
    Particles.setMode(HAS_GSAP ? 'confetti' : 'off'); // 无动画库时粒子不启动
    Preloader.start();
    entryDanmakuBurst();
  };

  // 调试参数 ?skip-boot / ?static，或 GSAP 加载失败：直接跳过开场，全部内容静态可见
  if (PARAMS.has('skip-boot') || STATIC || !HAS_GSAP) {
    revealSite();
    return;
  }

  // 噪点背景
  const noise = $('#boot-noise');
  const glyphs = '⍜⌇⊑⊾⋈⍙⌿⏧⏃⌇⟟☌⋏⏃⌰∴∵◬◭◮';
  noise.textContent = Array.from({ length: 30 }, () =>
    Array.from({ length: 90 }, () => glyphs[Math.floor(Math.random() * glyphs.length)]).join('')
  ).join('\n');

  // 打字机
  const lines = ['SIGNAL CONNECTING…', '接入信号源：魔界', 'Live☆Twin Channel 待机中'];
  const typedEl = $('#boot-typed');
  let li = 0, ci = 0;
  let bootSkipped = false; // 「跳过」按钮置位后中止打字机，避免后台定时器空跑
  const type = () => {
    if (bootSkipped) return;
    if (li >= lines.length) { showLogo(); return; }
    typedEl.textContent = lines[li].slice(0, ++ci);
    if (ci >= lines[li].length) { li++; ci = 0; setTimeout(type, 420); }
    else setTimeout(type, rand(30, 90));
  };

  const showLogo = () => {
    gsap.to('#boot-logo', { opacity: 1, y: 0, duration: 1.2, ease: 'power3.out' });
    gsap.fromTo('#boot-logo', { y: 26 }, { y: 0, duration: 1.2, ease: 'power3.out' });
    $('#enter-btn').classList.add('ready');
  };

  setTimeout(type, 500);

  /* 常驻「跳过」按钮：打字机期间随时可点，与 ?skip-boot 同效 */
  $('#boot-skip').addEventListener('click', () => {
    bootSkipped = true;
    revealSite();
    focusTop();
  }, { once: true });

  /* BOOT 消失后把焦点移到页面顶部（HUD 第一个链接）：被 display:none 移除的焦点元素
     会让浏览器从原 DOM 位置续走 Tab——不移焦的话下一次 Tab 直接跳到幻影场景卡片。
     file:// 预览时 .hud-forum 带 needs-server 被隐藏，聚焦隐藏元素静默失败，需回退到弹幕开关 */
  const focusTop = () => {
    const a = $('.hud-forum');
    const el = (a && a.getClientRects().length) ? a : $('#danmaku-toggle');
    if (el) el.focus({ preventScroll: true });
  };

  /* BOOT 期间焦点圈禁已内联在 index.html <head>（先行注册，覆盖 main.js 未加载的早期窗口），此处不再重复 */

  $('#enter-btn').addEventListener('click', () => {
    const tl = gsap.timeline();
    tl.to('#boot', { opacity: 0, scale: 1.04, duration: .8, ease: 'power2.inOut' })
      .set('#boot', { display: 'none' })
      .add(() => {
        document.body.classList.remove('boot-locked');
        $('#live-hud').classList.remove('hidden');
        $('#mode-badge').classList.remove('hidden');
        $('#scene-nav').classList.remove('hidden');
        Danmaku.init(); Danmaku.start();
        Particles.setMode('confetti');
        Preloader.start();
        entryDanmakuBurst();
        ScrollTrigger.refresh();
        focusTop();
      });
  }, { once: true });
}

/* ═══════════ 观众数模拟 ═══════════ */

function viewerSimulation() {
  const el = $('#viewer-count');
  let count = 13542, timer = null;
  const tick = () => {
    count += Math.floor(rand(-30, 120));
    count = Math.max(9000, count);
    el.textContent = count.toLocaleString();
  };
  const start = () => { if (!timer) timer = setInterval(tick, 1600); };
  const stop = () => { clearInterval(timer); timer = null; };
  /* 后台标签页暂停定时器，回前台重启并补一次 tick */
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop(); else { tick(); start(); }
  });
  start();
}

/* 北京时间实时时钟 */
function beijingClock() {
  const el = $('#beijing-time');
  const tick = () => {
    const now = new Date();
    el.textContent = now.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };
  let timer = null;
  const start = () => { if (!timer) timer = setInterval(tick, 1000); };
  const stop = () => { clearInterval(timer); timer = null; };
  /* 后台标签页暂停定时器，回前台重启并补一次 tick */
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop(); else { tick(); start(); }
  });
  tick();
  start();
}

/* ═══════════ 场景编排 ═══════════ */

function sceneOrchestration() {
  const badge = $('#mode-badge'), star = $('#mode-star');
  const glitch = $('#glitch-overlay');

  if (STATIC || REDUCED || !HAS_GSAP) return; // 静态模式/减弱动态/无动画库：不做任何滚动编排，内容 CSS 默认可见（未被 fromTo 隐藏）

  // 场景进入动画（通用）
  $$('.scene').forEach(sec => {
    const items = sec.querySelectorAll('.scene-kicker, .section-title, .live-title-wrap, .twin-sticker, .phantom-hero, .md-art, .evil-cards, .profile-card, .ranking-ui, .sunny-duo, .outro-inner > *');
    if (!items.length) return;
    gsap.fromTo(items,
      { y: 46, opacity: 0 },
      {
        y: 0, opacity: 1, duration: 1, stagger: .12, ease: 'power3.out',
        scrollTrigger: { trigger: sec, start: 'top 62%' },
      });
  });

  // 各场景状态切换
  const setScene = (name) => {
    $$('#scene-nav a').forEach(a => a.classList.toggle('active', a.dataset.scene === name));
    badge.classList.toggle('gone', name === 'outro'); // 下播场景隐藏徽章，避免遮挡页脚/与 SuperChat 按钮打架
    Danmaku.poolExt = ''; // 默认重置弹幕样式标记，仅 phantom 覆盖为 dk-evil
    Danmaku.setTops(DK_SCENE_TOPS[name], document.getElementById(name)); // 按场景切换弹幕轨道（手动最大集 + 实测标题避障）
    switch (name) {
      case 'live':
        Danmaku.setPool(DANMAKU.live); Danmaku.density = IS_MOBILE ? 2600 : 1500;
        Danmaku.poolExt = ''; Particles.setMode('confetti');
        document.body.classList.remove('mode-dark-active');
        badge.classList.remove('evil'); star.textContent = '☆';
        break;
      case 'hijack':
        Danmaku.setPool(DANMAKU.hijack); Danmaku.poolExt = ''; Danmaku.density = 900;
        Particles.setMode('off');
        // glitch 爆发（重复15次=16迭代→偶数→终点回x:0，避免残留偏移）
        glitch.classList.add('active');
        gsap.fromTo('#main', { x: 0 }, { x: 8, duration: .06, repeat: 15, yoyo: true, ease: 'none', overwrite: 'auto',
          onComplete() { gsap.set('#main', { clearProps: 'x' }); } }); // clearProps 清掉内联 transform：残留 translate(0,0) 会让 #main 成为 fixed 后代（#boot）的包含块
        setTimeout(() => glitch.classList.remove('active'), 1600);
        break;
      case 'phantom':
        Danmaku.setPool(DANMAKU.evil); Danmaku.density = IS_MOBILE ? 2400 : 1400;
        Danmaku.poolExt = 'dk-evil';
        Particles.setMode('diamond');
        document.body.classList.add('mode-dark-active');
        badge.classList.add('evil'); star.textContent = '★';
        gsap.fromTo(badge, { rotateY: 180, scale: .4 }, { rotateY: 0, scale: 1, duration: .8, ease: 'back.out(2)' });
        break;
      case 'profiles':
        Danmaku.density = 3400; // 弹幕稀疏，让阅读
        Particles.setMode('off');
        break;
      case 'sunny':
        Danmaku.setPool(DANMAKU.sunny); Danmaku.density = 2200;
        break;
      case 'gallery':
        Danmaku.density = 5000;
        Particles.setMode('off');
        break;
      case 'outro':
        Danmaku.setPool(DANMAKU.outro); Danmaku.density = 1800;
        Particles.setMode('confetti');
        break;
    }
  };

  ['live', 'hijack', 'phantom', 'profiles', 'sunny', 'gallery', 'outro'].forEach(name => {
    ScrollTrigger.create({
      trigger: '#' + name, start: 'top 55%', end: 'bottom 45%',
      onEnter: () => setScene(name),
      onEnterBack: () => setScene(name),
    });
  });

  // hijack 标题逐词闪现
  gsap.fromTo('.hijack-title span',
    { opacity: 0, scale: 1.4, filter: 'blur(8px)' },
    {
      opacity: 1, scale: 1, filter: 'blur(0px)', stagger: .35, duration: .5, ease: 'power4.out',
      scrollTrigger: { trigger: '#hijack', start: 'top 40%' },
    });

  // live 场景 SC 横幅条（#live-sc-bar，可选装饰元素）：scaleX 0→1 入场；元素不存在则跳过
  const scBar = $('#live-sc-bar');
  if (scBar) {
    gsap.fromTo(scBar, { scaleX: 0, transformOrigin: '50% 50%' }, {
      scaleX: 1, duration: .9, ease: 'power3.out',
      scrollTrigger: { trigger: '#live', start: 'top 62%' },
    });
  }

  // 直播间贴纸视差
  gsap.to('.st-kisikil', {
    y: -60, scrollTrigger: { trigger: '#live', start: 'top top', end: 'bottom top', scrub: 1 },
  });
  gsap.to('.st-lilla', {
    y: -20, scrollTrigger: { trigger: '#live', start: 'top top', end: 'bottom top', scrub: 1 },
  });

  // 怪盗 hero 微推进
  gsap.fromTo('.phantom-hero', { scale: .92 }, {
    scale: 1, scrollTrigger: { trigger: '.phantom-hero', start: 'top 85%', end: 'top 30%', scrub: 1 },
  });

  // 导航点击平滑滚动
  $$('#scene-nav a').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      gsap.to(window, { scrollTo: { y: a.getAttribute('href'), offsetY: 0 }, duration: 1.1, ease: 'power3.inOut' });
    });
  });
}

/* ═══════════ Tilt 视差 ═══════════ */

function tiltEffect() {
  if (IS_MOBILE || REDUCED || !HAS_GSAP) return;
  $$('[data-tilt]').forEach(el => {
    gsap.set(el, { transformPerspective: 800 });
    /* quickTo 复用内部 tween，避免 mousemove 高频新建 gsap.to 且无 overwrite 导致 tween 堆积。
       mouseleave 的弹性回位会 overwrite 掉 quickTo 的内部 tween，故置空后下次移动时重建 */
    let qRX = null, qRY = null;
    const ensureQuick = () => {
      if (qRX) return;
      qRX = gsap.quickTo(el, 'rotationX', { duration: .4, ease: 'power2.out' });
      qRY = gsap.quickTo(el, 'rotationY', { duration: .4, ease: 'power2.out' });
    };
    ensureQuick();
    el.addEventListener('mousemove', e => {
      ensureQuick();
      const r = el.getBoundingClientRect();
      qRX(((e.clientY - r.top) / r.height - .5) * -9);
      qRY(((e.clientX - r.left) / r.width - .5) * 9);
    });
    el.addEventListener('mouseleave', () => {
      qRX = qRY = null;
      gsap.to(el, { rotationX: 0, rotationY: 0, duration: .7, ease: 'elastic.out(1,.5)', overwrite: 'auto' });
    });
  });
}

/* ═══════════ 卡册 ═══════════ */

function buildGallery() {
  const grid = $('#gallery-grid');
  grid.innerHTML = CARDS.map(c => `
    <figure class="g-card g-${c.cat}" data-id="${c.id}" role="button" tabindex="0" aria-label="${c.cn}">
      <img loading="lazy" src="${IMG_SMALL(c.id)}" alt="${c.cn}" width="268" height="391">
      <figcaption class="g-label">${c.cn}</figcaption>
    </figure>`).join('');

  // 标题与筛选计数都取自同一份卡片资料，新增卡片时无需手改 HTML 数字。
  $$('[data-card-count]').forEach(el => { el.textContent = CARDS.length; });
  const counts = CARDS.reduce((m, c) => (m[c.cat] = (m[c.cat] || 0) + 1, m), {});
  $$('.gf-btn').forEach(btn => {
    const f = btn.dataset.filter;
    let i = btn.querySelector('i');
    if (!i) {
      i = document.createElement('i');
      btn.appendChild(document.createTextNode(' '));
      btn.appendChild(i);
    }
    i.textContent = f === 'all' ? CARDS.length : (counts[f] || 0);
  });

  // 筛选结果计数行由卡片总数与本次可见数生成。
  const countLine = document.createElement('p');
  countLine.className = 'gf-count dotgothic';
  countLine.style.cssText = 'opacity:.6; font-size:12px; text-align:center; margin:-28px 0 16px;';
  const updateCount = n => { countLine.textContent = `${CARDS.length} 枚中 ${n} 枚表示中`; };
  updateCount(CARDS.length);
  $('.gallery-filters').insertAdjacentElement('afterend', countLine);

  // 键盘事件委托：Enter/Space 点击打开弹层
  grid.addEventListener('keydown', e => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.closest('.g-card')) {
      e.preventDefault();
      openModal(+e.target.closest('.g-card').dataset.id);
    }
  });

  // 筛选
  let gfDelayTimer = null; // stagger 延迟清理定时器（快速连点 tab 时只保留最后一次）
  $$('.gf-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      $$('.gf-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const f = btn.dataset.filter;
      let shown = 0;
      $$('.g-card').forEach(card => {
        const c = CARDS.find(x => x.id == card.dataset.id);
        if (f !== 'all' && c.cat !== f) {
          card.style.transitionDelay = ''; // 隐藏侧保持现有 .g-hide 过渡，清掉可能残留的延迟
          card.classList.add('g-hide');
        } else {
          // 可见侧 stagger 淡入：30ms 递增 transitionDelay 后移除 .g-hide
          // （JS 控制延时、过渡结束即清理，不用 CSS fill，避开踩坑 #26；减弱动态用户直接显示）
          card.style.transitionDelay = REDUCED ? '' : (shown * 30) + 'ms';
          shown++;
          card.classList.remove('g-hide');
        }
      });
      updateCount(shown);
      // 全部过渡完成后清理 transitionDelay，避免残留延迟拖慢后续 hover/筛选过渡（.g-card 最长过渡 .4s）
      clearTimeout(gfDelayTimer);
      gfDelayTimer = setTimeout(() => {
        $$('.g-card').forEach(card => { card.style.transitionDelay = ''; });
      }, shown * 30 + 450);
      if (HAS_GSAP) ScrollTrigger.refresh();
    });
  });

  // 弹层
  grid.addEventListener('click', e => {
    const card = e.target.closest('.g-card');
    if (card) openModal(+card.dataset.id);
  });
  $$('[data-card]').forEach(el => {
    el.addEventListener('click', () => openModal(+el.dataset.card));
  });
}

/* ═══════════ 卡片弹层 ═══════════ */

/* iOS Safari 兼容滚动锁：body 改 fixed 定位并记住 scrollY（旧 iOS 不认 body overflow:hidden），关闭时还原 */
let _scrollLockY = null;
function lockScroll() {
  if (_scrollLockY !== null) return;
  _scrollLockY = window.scrollY;
  document.body.style.position = 'fixed';
  document.body.style.top = `-${_scrollLockY}px`;
  document.body.style.left = '0';
  document.body.style.right = '0';
}
function unlockScroll() {
  if (_scrollLockY === null) return;
  document.body.style.position = '';
  document.body.style.top = '';
  document.body.style.left = '';
  document.body.style.right = '';
  window.scrollTo(0, _scrollLockY);
  _scrollLockY = null;
}

/* 弹层卡图切换：先透明再换 src，加载完成淡入，避免弱网残留上一张图；加载期在 wrap 挂 .loading 显示缓冲占位 */
function setModalImg(src, alt) {
  const img = $('.modal-img');
  const wrap = img.closest('.modal-img-wrap');
  wrap.classList.add('loading');
  img.style.opacity = '0';
  const done = () => { img.style.opacity = '1'; wrap.classList.remove('loading'); };
  img.onload = done;
  img.onerror = done; // 加载失败也要恢复可见，交给全局 error 兜底显示占位
  img.src = src;
  img.alt = alt;
  if (img.complete && img.naturalWidth) done(); // 命中缓存时 load 事件可能不触发
}

function openModal(id) {
  const c = CARDS.find(x => x.id === id);
  if (!c) return;
  const m = $('#card-modal');
  _lastFocused = document.activeElement; // 焦点管理：关闭后还原
  setModalImg(IMG_FULL(c.id), c.cn);
  $('.modal-name').textContent = c.cn;
  $('.modal-en').textContent = c.en;
  const bits = [c.type, c.attr && `${c.attr}属性`, c.lv, c.atk != null && `ATK/${c.atk}`, c.def != null && `DEF/${c.def}`].filter(Boolean);
  $('.modal-meta').textContent = bits.join(' · ');
  $('.modal-desc').textContent = c.desc;
  $('.modal-tag').textContent = c.tag;
  if (HAS_GSAP) gsap.killTweensOf('.modal-body'); // 关闭中的tween立即停止，避免竞态
  m.classList.remove('hidden');
  lockScroll();
  if (HAS_GSAP) gsap.fromTo('.modal-body', { scale: .85, opacity: 0, rotateX: 8 }, { scale: 1, opacity: 1, rotateX: 0, duration: .45, ease: 'back.out(1.6)' });
  $('.modal-close').focus();
}
let _lastFocused = null;
function closeModal() {
  unlockScroll(); // 同步还原，防止关闭动画被 killTweensOf 打断后滚动锁残留
  const finish = () => {
    $('#card-modal').classList.add('hidden');
    if (_lastFocused && _lastFocused.focus) _lastFocused.focus(); // 焦点还原
  };
  if (!HAS_GSAP) { finish(); return; } // 无动画库：直接显隐
  gsap.to('.modal-body', {
    scale: .9, opacity: 0, duration: .25, ease: 'power2.in',
    onComplete: finish,
  });
}

/* ═══════════ 彩蛋 ═══════════ */

function easterEggs() {
  // 关注按钮
  $('#follow-btn').addEventListener('click', function () {
    const followed = this.classList.toggle('followed');
    this.textContent = followed ? '★ 已关注' : '☆ 关注';
    if (followed) Danmaku.burst(['关注了！', '欢迎新粉！', '☆☆☆', '舰长上船！'], 'dk-sc', 8);
  });

  // 弹幕开关：状态存 localStorage，关闭时停止生成新弹幕并隐藏弹幕层，再开即恢复
  const dkBtn = $('#danmaku-toggle');
  const syncDkBtn = () => {
    dkBtn.classList.toggle('off', !Danmaku.enabled);
    dkBtn.setAttribute('aria-pressed', Danmaku.enabled ? 'true' : 'false');
    dkBtn.textContent = Danmaku.enabled ? '💬 弹幕' : '🚫 弹幕';
  };
  dkBtn.addEventListener('click', () => {
    Danmaku.setEnabled(!Danmaku.enabled);
    syncDkBtn();
  });
  if (!Danmaku.enabled && Danmaku.layer) Danmaku.layer.style.display = 'none'; // 初始关闭（记忆或减弱动态）时隐藏弹幕层
  syncDkBtn();

  // SuperChat（5 秒冷却防止快速连点导致金币/弹幕堆积卡顿；SC 置顶横幅复用此冷却）
  let scCooldown = false;
  $('#super-chat-btn').addEventListener('click', () => {
    if (scCooldown) return;
    scCooldown = true;
    const btn = $('#super-chat-btn');
    btn.textContent = '⏳ 冷却中…';
    setTimeout(() => { scCooldown = false; btn.textContent = '💰 发送 SuperChat'; }, 5000);

    const layer = $('#sc-layer');
    // 金币雨
    const n = IS_MOBILE ? 18 : 40;
    for (let i = 0; i < n; i++) {
      const coin = document.createElement('div');
      coin.className = 'sc-coin';
      coin.textContent = pick(['🪙', '💰', '⭐', '💎']);
      coin.style.left = rand(2, 96) + 'vw';
      coin.style.fontSize = rand(20, 44) + 'px';
      coin.style.animationDuration = rand(1.2, 2.6) + 's';
      coin.style.animationDelay = rand(0, .9) + 's';
      layer.appendChild(coin);
      setTimeout(() => coin.remove(), 4200);
    }
    // 置顶横幅：金色 SC 悬顶停留 6-8 秒（最多 2 条堆叠，弹幕关闭时不出现；复用 .sc-banner 样式）
    SCPin.push(pick([
      '💰 SuperChat ¥1000 —「小钱钱也喜欢我！」',
      '💰 SuperChat ¥500 —「给双子买糖吃！」',
      '💰 SuperChat ¥100 —「璃拉说唱再来一段！」',
    ]));
    // 弹幕爆发
    Danmaku.burst(DANMAKU.gift.concat(['老板大气！', '1000金币！', '平台抽成-500', '璃拉狂喜']), 'dk-sc', 16);
  });

  // 翻转卡：点击弹出大图+介绍（与卡册弹层统一交互），hover 仅做装饰性翻转
  $$('.flip-card').forEach(card => {
    card.addEventListener('click', e => {
      // 如果用户点的是弹层关闭按钮（不太可能但防御），不触发
      if (e.target.closest('.modal-close')) return;
      // 取前图和后文
      const frontImg = card.querySelector('.flip-front img');
      const backB = card.querySelector('.flip-back b');
      const backP = card.querySelector('.flip-back p');
      if (!frontImg) return;
      // 复用现有弹层结构：图=高清裁切正面，名=背面标题，描述=背面正文
      const m = $('#card-modal');
      setModalImg(frontImg.src, frontImg.alt);
      $('.modal-name').textContent = backB ? backB.textContent : frontImg.alt;
      $('.modal-en').textContent = '';
      $('.modal-meta').textContent = '';
      $('.modal-desc').textContent = backP ? backP.textContent : '';
      $('.modal-tag').textContent = '点击背景或 ✕ 关闭';
      // 显示弹层
      if (HAS_GSAP) gsap.killTweensOf('.modal-body');
      m.classList.remove('hidden');
      lockScroll();
      if (HAS_GSAP) gsap.fromTo('.modal-body', { scale: .85, opacity: 0 }, { scale: 1, opacity: 1, duration: .45, ease: 'back.out(1.6)' });
      $('.modal-close').focus();
    });
    card.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); card.click(); }
    });
  });

  // 弹层关闭
  $('.modal-close').addEventListener('click', closeModal);
  $('.modal-backdrop').addEventListener('click', closeModal);
  addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#card-modal').classList.contains('hidden')) closeModal(); });

  // 键盘委托：Enter/Space 打开 data-card 指向的卡片弹层（mini-card/sunny-card）
  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const el = e.target.closest('[data-card]');
    if (el) { e.preventDefault(); openModal(+el.dataset.card); }
  });

  // 图片加载失败兜底
  $$('img').forEach(img => {
    img.addEventListener('error', () => {
      img.style.background = 'linear-gradient(135deg,#2A2050,#14102B)';
      img.style.minHeight = '120px';
      img.alt = (img.alt || '图片') + '（加载失败）';
    }, { once: true });
  });
}

/* 切页标题彩蛋：离开标签页随机换成双子喊话，回来恢复原标题 */
function tabTitleTrick() {
  const LEAVE_TITLES = [
    '☆ 信号丢失，双子找人中…',
    '姬丝吉尔：喂——你去哪了？！',
    '璃拉：快回来嘛…☆',
    'BUFFERING…观众 -1',
    '☆ 直播还在继续哦~',
    '（双子盯——）别走开哦',
    '予告状：马上回来逮捕你☆',
    '双子星待机中…zzz',
  ];
  const original = document.title;
  document.addEventListener('visibilitychange', () => {
    document.title = document.hidden ? pick(LEAVE_TITLES) : original;
  });
}

/* ═══════════ 启动 ═══════════ */

document.addEventListener('DOMContentLoaded', () => {
  Particles.init();
  bootSequence();
  viewerSimulation();
  beijingClock();
  sceneOrchestration();
  tiltEffect();
  buildGallery();
  easterEggs();
  tabTitleTrick();
  /* 异步字体加载完成后刷新 ScrollTrigger 触发点（字体会改变标题行高→场景总高） */
  if (document.fonts && HAS_GSAP && !STATIC && !REDUCED) {
    document.fonts.ready.then(() => ScrollTrigger.refresh());
  }

  /* matchMedia 变化监听：横竖屏/窗口缩放跨 768 断点时同步弹幕轨道/密度并重建粒子（数量档位随断点变化） */
  const mqMobile = matchMedia('(max-width: 768px)');
  mqMobile.addEventListener('change', () => {
    Danmaku.density = mqMobile.matches ? 2600 : 1500;
    Danmaku.maxTracks = mqMobile.matches ? 4 : 7;
    Danmaku.init(); // 重建轨道
    const pm = Particles.mode;
    if (pm !== 'off') { Particles.setMode('off'); Particles.setMode(pm); } // 按新断点重建粒子
  });
  /* 视口尺寸变化时重算弹幕避障安全线（vh 换算随视口高度变化；防抖避免拖动期间频繁重建轨道） */
  let _dkResizeTimer = null;
  addEventListener('resize', () => {
    clearTimeout(_dkResizeTimer);
    _dkResizeTimer = setTimeout(() => {
      if (!matchMedia('(max-width: 768px)').matches) Danmaku.rebuildTracks();
    }, 200);
  });
  matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', () => {
    location.reload(); // 动效已硬编码进 ScrollTrigger/粒子，切换后最安全的方式是刷新
  });

  /* 后台标签页暂停弹幕调度（粒子 rAF 由浏览器自动暂停，无需处理）；
     回前台时若之前在跑则重启——start() 的 loop 自带一发，相当于补 tick；
     用 wasRunning 快照区分「BOOT 尚未进场」与「确在运行」，避免后台加载后回前台提前启动弹幕 */
  let _dkWasRunning = false;
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { _dkWasRunning = !!Danmaku.timer; Danmaku.stop(); }
    else if (_dkWasRunning) { _dkWasRunning = false; Danmaku.start(); }
  });
});
