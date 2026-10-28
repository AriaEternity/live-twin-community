/* Live☆Twin PWA Service Worker
   缓存版本：每次部署与 index.html 的 ?v= 一起 bump（踩坑 #34），activate 时清旧缓存 */
const CACHE = 'livetwin-public-20261028';

/* 应用壳：主站纯前端部分离线可完整打开（弹幕/特效/卡册全不依赖网络） */
const SHELL = [
  '/',
  '/index.html',
  '/offline.html',
  '/install.html',
  '/manifest.json',
  '/css/tokens.css',
  '/css/style.css',
  '/js/data.js',
  '/js/main.js',
  '/js/trail.js',
  '/js/starburst.js',
  '/img/icons/icon-192.png',
  '/img/icons/icon-512.png',
];

/* 静态资源目录：cache-first，匹配忽略 query。
   因此仅修改 HTML 的 ?v= 不足以清缓存；更新静态资源必须同步更换 CACHE。 */
const STATIC_RE = /^\/(css|js|cards|img|uploads)\//;

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return; // 只接管 GET（论坛发帖等 POST 直连网络）
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // 字体/GSAP CDN 外链不接管

  /* 导航请求：network-first，离线时回退缓存（首页命中应用壳）或 offline.html。
     不把动态页 HTML 写入缓存：论坛分页 URL 多且易过期，避免缓存无限膨胀 */
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).catch(() =>
        caches.match(req, { ignoreSearch: true })
          .then((hit) => hit || caches.match('/offline.html'))
      )
    );
    return;
  }

  /* 静态资源：cache-first（忽略 ?v= query），未命中走网络并回写缓存 */
  if (STATIC_RE.test(url.pathname)) {
    e.respondWith(
      caches.match(req, { ignoreSearch: true }).then((hit) =>
        hit || fetch(req).then((resp) => {
          if (resp.ok) {
            const copy = resp.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return resp;
        })
      )
    );
    return;
  }
  /* 其余（/api/ 搜卡等 XHR）：不接管，直连网络 */
});
