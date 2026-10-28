/* ═══════════════════════════════════════════════════════════
   卡组编辑器（templates/decks/edit.html 专用）
   状态：window.__DECK_INITIAL__ 注入的三区卡条目（每张卡一条 {id, name, types}）。
   交互：搜卡 → 点「主/额/副」加入 → 右侧点缩略图移除一张 → 保存时客户端校验 +
   序列化进隐藏字段 → fetch POST（redirect:'manual'，防 follow 消费 flash，踩坑 #29）。
   禁限卡表：启动时拉一次 /decks/api/banlist（0=禁止/1=限制/2=准限制），加卡即时拦截 +
   搜索结果角标 + 保存校验上限；展示规则日期，未就绪时保留卡组并暂停保存。
   ═══════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // 三区定义：key=状态键/隐藏字段前缀，label=显示名，rule=计数旁的规则文案，min/max=合法性范围
  var ZONES = [
    { key: 'main', label: '主卡组', rule: '40-60', min: 40, max: 60 },
    { key: 'extra', label: '额外卡组', rule: '0-15', min: 0, max: 15 },
    { key: 'side', label: '副卡组', rule: '0-15', min: 0, max: 15 }
  ];
  // 这些类别通常进额外卡组：点「主」时提示但不强制拦截
  var EXTRA_HINT_TYPES = ['融合', '同调', '超量', '连接'];
  var CARD_IMG = 'https://images.ygoprodeck.com/images/cards_small/';

  var form = document.getElementById('deck-form');
  var titleInput = document.getElementById('f-title');
  var hiddenMain = document.getElementById('f-main');
  var hiddenExtra = document.getElementById('f-extra');
  var hiddenSide = document.getElementById('f-side');
  var searchForm = document.getElementById('de-search-form');
  var searchInput = document.getElementById('de-q');
  var status = document.getElementById('de-status');
  var results = document.getElementById('de-results');
  var pager = document.getElementById('de-pager');      // filter 端点换页控件容器
  var filterEl = document.getElementById('de-filter'); // 条件筛选面板容器
  var zonesRoot = document.getElementById('de-zones');
  var errorsBox = document.getElementById('de-errors');
  var saveBtn = document.getElementById('de-save');
  var banStatus = document.getElementById('de-ban-status');
  var banRetry = document.getElementById('de-ban-retry');

  // 当前卡组状态：三区各为 [{id, name, types}, ...]，一张卡一条
  var state = { main: [], extra: [], side: [] };
  var filterParams = {}; // 筛选面板最近应用的条件（key 照后端契约）
  var lastKey = '';      // 上次成功请求的完整 URL，防重复提交
  var lastCards = []; // 最近一次搜索结果，供加卡按钮按索引取卡

  /* ── OCG 禁限卡表：卡id(Number) → 0禁止/1限制/2准限制；未收录按 3 张上限 ── */
  var banlist = new Map();
  var banAliases = new Map();
  var banlistReady = false;
  var banlistLoading = false;
  var isSaving = false;

  function canonicalId(id) {
    var number = Number(id);
    return banAliases.has(number) ? banAliases.get(number) : number;
  }

  /* 某 id 的有效投入上限（禁→0、限→1、准→2、未收录→3） */
  function effectiveLimit(id) {
    if (!banlistReady) return null;
    var lim = banlist.get(canonicalId(id));
    return lim === undefined ? 3 : lim;
  }

  /* 超限提示文案：加卡 toast 与保存前校验共用，与服务端口径一致 */
  function limitText(name, lim) {
    if (lim === 0) return '「' + name + '」是禁止卡，不能投入卡组';
    if (lim === 1) return '「' + name + '」是限制卡，最多投入 1 张';
    if (lim === 2) return '「' + name + '」是准限制卡，最多投入 2 张';
    return '「' + name + '」最多投入 3 张';
  }

  /* 该 id 当前在三区的合计张数 */
  function countAll(id) {
    var n = 0;
    ['main', 'extra', 'side'].forEach(function (k) {
      state[k].forEach(function (e) { if (canonicalId(e.id) === canonicalId(id)) n += 1; });
    });
    return n;
  }

  function showBanStatus(text, error) {
    banStatus.textContent = text;
    banStatus.className = 'de-ban-status' + (error ? ' error' : '');
  }

  /* 日期和状态来自同一份服务端有效规则；请求失败不清空用户当前卡组。 */
  function loadBanlist() {
    if (banlistLoading) return;
    banlistLoading = true;
    banlistReady = false;
    setSaving(isSaving);
    showBanStatus('正在加载 OCG 禁限规则，规则就绪后可保存卡组…', false);
    banRetry.hidden = true;
    banRetry.disabled = true;
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 10000);
    fetch('/decks/api/banlist', {
      credentials: 'same-origin', cache: 'no-store', signal: controller.signal
    })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        var cards = d && d.cards;
        var aliases = (d && d.aliases) || {};
        var meta = d && d.meta;
        if (!meta || !meta.available || !cards || Array.isArray(cards)
            || typeof cards !== 'object' || !Object.keys(cards).length
            || typeof aliases !== 'object' || Array.isArray(aliases)) {
          throw new Error('禁限规则尚未就绪');
        }
        var nextBanlist = new Map();
        var nextAliases = new Map();
        Object.keys(cards).forEach(function (k) {
          var id = Number(k);
          var lim = cards[k];
          if (!Number.isSafeInteger(id) || id <= 0 || String(id) !== k
              || (lim !== 0 && lim !== 1 && lim !== 2)) {
            throw new Error('禁限规则数据异常');
          }
          nextBanlist.set(id, lim);
        });
        Object.keys(aliases).forEach(function (k) {
          var id = Number(k);
          var target = aliases[k];
          if (!Number.isSafeInteger(id) || id <= 0 || String(id) !== k
              || !Number.isSafeInteger(target) || target <= 0
              || Object.prototype.hasOwnProperty.call(aliases, String(target))) {
            throw new Error('异画映射数据异常');
          }
          nextAliases.set(id, target);
        });
        banlist = nextBanlist;
        banAliases = nextAliases;
        banlistReady = true;
        var text = 'OCG 禁限规则 · 生效日期：' + (meta.effective_date || '待核验')
          + ' · 抓取日期：' + (meta.fetched || '未记录')
          + ' · ' + nextBanlist.size + ' 项';
        if (meta.stale) text += ' · 更新失败，使用上次有效规则，待核验';
        showBanStatus(text, !!meta.stale);
        banRetry.hidden = !meta.stale;
        showErrors([]);
        // 表加载晚于搜索时，补渲染一次结果让角标出现
        if (lastCards.length) renderResults(lastCards);
      })
      .catch(function () {
        banlistReady = false;
        showBanStatus('禁限规则加载失败，状态待核验。当前卡组已保留，请重试加载后再保存。', true);
        banRetry.hidden = false;
      })
      .then(function () {
        clearTimeout(timeout);
        banlistLoading = false;
        banRetry.disabled = false;
        setSaving(isSaving);
      });
  }
  banRetry.addEventListener('click', loadBanlist);


  /* HTML 转义（卡名来自上游 API，进 innerHTML 前必须转义） */
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* 轻提示：优先用全局 toast（base.html 提供），兜底 alert */
  function notify(msg) {
    if (window.toast) { window.toast(msg, 'info'); } else { alert(msg); }
  }

  /* ── 初始状态注入 ── */
  function loadInitial() {
    var init = window.__DECK_INITIAL__ || {};
    ZONES.forEach(function (z) {
      var list = Array.isArray(init[z.key]) ? init[z.key] : [];
      state[z.key] = list.map(function (e) {
        return { id: e.id, name: e.name || '', types: e.types || '' };
      });
    });
  }

  /* ── 搜索 ── */
  function setLoading(loading) {
    var btn = searchForm.querySelector('button[type="submit"]');
    btn.disabled = loading;
    btn.textContent = loading ? '搜索中…' : '搜卡';
  }

  function showStatus(text, kind) {
    status.textContent = text || '';
    status.hidden = !text;
    status.className = 'de-status' + (kind ? ' ' + kind : '');
  }

  /* 禁限角标与投入限额共用当前加载的规则，避免旧搜索结果残留旧档位。 */
  function banBadge(card) {
    if (!banlistReady) return '';
    var lim = banlist.get(canonicalId(card.id));
    if (lim === undefined) return '';
    var label = lim === 0 ? '禁' : (lim === 1 ? '限' : '准');
    var desc = lim === 0 ? '禁止卡' : (lim === 1 ? '限制卡' : '准限制卡');
    return '<span class="dr-ban dr-ban-' + lim + '" title="OCG 禁限卡表：' + desc + '">' + label + '</span>';
  }

  function renderResults(cards) {
    lastCards = cards;
    results.innerHTML = cards.map(function (c, i) {
      return '<div class="de-result">' +
        '<div class="dr-img">' +
          '<img src="' + CARD_IMG + esc(c.id) + '.jpg" alt="' + esc(c.sc_name) + '" loading="lazy" width="268" height="391" onerror="this.onerror=null;this.src=\'/img/card_pre_release.png\'">' +
          banBadge(c) +
        '</div>' +
        '<div class="dr-name">' + esc(c.sc_name) + '</div>' +
        '<div class="dr-add">' +
          '<button type="button" class="add-main" data-idx="' + i + '" data-zone="main">主</button>' +
          '<button type="button" class="add-extra" data-idx="' + i + '" data-zone="extra">额</button>' +
          '<button type="button" class="add-side" data-idx="' + i + '" data-zone="side">副</button>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  /* 拼请求：有文字 → /api/cards/search（q + 筛选参数，无分页）；
     无文字有条件 → /api/cards/filter（筛选参数 + page）；都无 → null */
  function buildRequest(page) {
    var q = searchInput.value.trim();
    var p = {};
    Object.keys(filterParams).forEach(function (k) { p[k] = filterParams[k]; });
    if (q) {
      p.q = q;
      return { url: '/api/cards/search?' + new URLSearchParams(p), paged: false, q: q };
    }
    if (!Object.keys(p).length) return null;
    p.page = page || 1;
    return { url: '/api/cards/filter?' + new URLSearchParams(p), paged: true, q: '' };
  }

  /* filter 端点换页控件渲染（search 端点无分页不显示） */
  function renderPager(ps) {
    if (!pager) return;
    if (!ps || ps.pages <= 1) { pager.innerHTML = ''; pager.hidden = true; return; }
    pager.hidden = false;
    pager.innerHTML =
      '<button type="button" data-page="' + (ps.page - 1) + '"' + (ps.page <= 1 ? ' disabled' : '') + '>‹ 上一页</button>' +
      '<span class="cf-page-info">第 ' + ps.page + ' / ' + ps.pages + ' 页</span>' +
      '<button type="button" data-page="' + (ps.page + 1) + '"' + (ps.page >= ps.pages ? ' disabled' : '') + '>下一页 ›</button>';
  }
  if (pager) {
    pager.addEventListener('click', function (e) {
      var btn = e.target.closest('button[data-page]');
      if (!btn || btn.disabled) return;
      runSearch(+btn.dataset.page);
    });
  }

  /* 执行搜索：文字搜索按钮与筛选面板共用，page 仅 filter 端点有效 */
  function runSearch(page) {
    var req = buildRequest(page);
    if (!req) {
      results.innerHTML = '';
      renderPager(null);
      showStatus('输入卡名或选择筛选条件', '');
      return;
    }
    // 与上次成功请求完全相同（含页码）则跳过；失败不计入，可重试
    if (req.url === lastKey) return;
    results.innerHTML = '';
    renderPager(null);
    showStatus('信号接入中…', '');
    setLoading(true);

    fetch(req.url)
      .then(function (r) { return r.json().then(function (d) { return { status: r.status, ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          // 429/503/502 等失败：优先用后端 error 文案，并允许同条件重试（不记 lastKey）
          var msg = res.data.error ||
            (res.status === 429 ? '搜索太频繁了，稍等片刻再试' :
             res.status === 503 ? '卡库数据维护中，稍后再试' : '卡库信号异常，稍后再试');
          showStatus(msg + '，可再次点击搜卡重试', 'error');
          return;
        }
        lastKey = req.url;
        var cards = res.data.cards || [];
        var notice = typeof res.data.notice === 'string' ? res.data.notice : '';
        if (!cards.length) {
          var emptyText = req.q ? '没有找到「' + req.q + '」相关的卡片' : '没有符合条件的卡片';
          showStatus(emptyText + (notice ? ' · ' + notice : ''), '');
          return;
        }
        if (req.paged) {
          showStatus('共 ' + (res.data.total || cards.length) + ' 张符合条件的卡片'
            + (notice ? ' · ' + notice : ''), '');
          renderResults(cards);
          renderPager({ page: res.data.page || 1, pages: res.data.pages || 1 });
        } else {
          showStatus(notice, '');
          renderResults(cards);
        }
      })
      .catch(function () { showStatus('网络错误，请稍后再试，可再次点击搜卡重试', 'error'); })
      .then(function () { setLoading(false); });
  }

  searchForm.addEventListener('submit', function (e) {
    e.preventDefault();
    runSearch(1);
  });

  /* 条件筛选面板：应用条件后立即按「当前搜索词 + 条件」从第 1 页查询；重置时 params 为 {} */
  if (filterEl && window.CardFilter) {
    window.CardFilter.init(filterEl, function (params) {
      filterParams = params || {};
      runSearch(1);
    });
  }

  /* ── 加卡：点结果卡下的「主/额/副」 ── */
  results.addEventListener('click', function (e) {
    var btn = e.target.closest('button[data-zone]');
    if (!btn) return;
    var card = lastCards[+btn.dataset.idx];
    if (!card) return;
    if (!banlistReady) {
      notify('禁限规则尚未就绪，请先重试加载规则；当前卡组已保留');
      return;
    }
    var zone = btn.dataset.zone;
    // 禁限/同名上限即时拦截：该 id 三区合计已达有效上限则不加入（服务端保存时同样拦截兜底）
    var lim = effectiveLimit(card.id);
    if (countAll(card.id) >= lim) {
      notify(limitText(card.sc_name, lim));
      return;
    }
    // 额外卡组类别点「主」时提示，但不强制（玩家有自己的理解）
    if (zone === 'main' && EXTRA_HINT_TYPES.some(function (t) { return (card.types || '').indexOf(t) !== -1; })) {
      notify('「' + card.sc_name + '」通常放额外卡组（仍已加入主卡组）');
    }
    state[zone].push({ id: card.id, name: card.sc_name, types: card.types || '' });
    renderZones();
  });

  /* ── 右侧三区渲染：同 id 合并计数 + ×N 角标，点缩略图移除一张 ── */
  function zoneIsInvalid(z, count) {
    return count < z.min || count > z.max;
  }

  function renderZones() {
    zonesRoot.innerHTML = ZONES.map(function (z) {
      var entries = state[z.key];
      // 按 id 分组，保持首次出现顺序
      var groups = [];
      var byId = {};
      entries.forEach(function (e) {
        if (byId[e.id]) { byId[e.id].count += 1; }
        else { var g = { id: e.id, name: e.name, count: 1 }; byId[e.id] = g; groups.push(g); }
      });
      var invalid = zoneIsInvalid(z, entries.length);
      var body = groups.length
        ? '<div class="de-zone-grid">' + groups.map(function (g) {
            return '<button type="button" class="de-thumb" data-zone="' + z.key + '" data-id="' + esc(g.id) + '"' +
              ' title="' + esc(g.name) + '（点击移除一张）">' +
              '<img src="' + CARD_IMG + esc(g.id) + '.jpg" alt="' + esc(g.name) + '" loading="lazy" width="268" height="391" onerror="this.onerror=null;this.src=\'/img/card_pre_release.png\'">' +
              (g.count > 1 ? '<span class="dt-x">×' + g.count + '</span>' : '') +
            '</button>';
          }).join('') + '</div>'
        : '<p class="de-zone-empty">（空）</p>';
      return '<section class="de-zone">' +
        '<div class="de-zone-head">' +
          '<span class="de-zone-title">' + z.label + '</span>' +
          '<span class="de-zone-count' + (invalid ? ' is-invalid' : '') + '">' + entries.length + '/' + z.rule + '</span>' +
        '</div>' + body +
      '</section>';
    }).join('');
  }

  /* 点缩略图：从对应区移除一张该 id 的卡 */
  zonesRoot.addEventListener('click', function (e) {
    var thumb = e.target.closest('.de-thumb');
    if (!thumb) return;
    var list = state[thumb.dataset.zone];
    var id = +thumb.dataset.id;
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) { list.splice(i, 1); break; }
    }
    renderZones();
  });

  /* ── 保存：客户端校验 → 序列化 → fetch POST ── */
  function showErrors(errs) {
    if (!errs.length) { errorsBox.hidden = true; errorsBox.innerHTML = ''; return; }
    errorsBox.innerHTML = errs.map(esc).join('<br>');
    errorsBox.hidden = false;
    errorsBox.scrollIntoView({ block: 'nearest' });
  }

  function validate() {
    var errs = [];
    if (!banlistReady) return ['禁限规则尚未就绪，请重试加载规则后再保存；当前卡组已保留'];
    if (!titleInput.value.trim()) errs.push('卡组标题不能为空');
    var mainN = state.main.length, extraN = state.extra.length, sideN = state.side.length;
    if (mainN < 40 || mainN > 60) errs.push('主卡组需要 40-60 张（当前 ' + mainN + ' 张）');
    if (extraN > 15) errs.push('额外卡组最多 15 张（当前 ' + extraN + ' 张）');
    if (sideN > 15) errs.push('副卡组最多 15 张（当前 ' + sideN + ' 张）');
    // 同名卡三个区合计 ≤ 有效上限（禁限表：禁0/限1/准2，未收录 3）
    var total = {};
    ['main', 'extra', 'side'].forEach(function (k) {
      state[k].forEach(function (e) {
        var id = canonicalId(e.id);
        total[id] = (total[id] || 0) + 1;
      });
    });
    Object.keys(total).forEach(function (id) {
      var lim = effectiveLimit(id);
      if (total[id] > lim) {
        var name = '';
        ['main', 'extra', 'side'].forEach(function (k) {
          var hit = state[k].filter(function (e) { return String(canonicalId(e.id)) === id; })[0];
          if (hit && !name) name = hit.name;
        });
        errs.push(limitText(name || id, lim) + '（当前 ' + total[id] + ' 张）');
      }
    });
    return errs;
  }

  function setSaving(saving) {
    isSaving = saving;
    saveBtn.disabled = saving || !banlistReady;
    saveBtn.textContent = saving ? '保存中…' : '保存卡组';
    saveBtn.title = banlistReady ? '' : '请先等待禁限规则加载，失败时可点击重试';
  }

  // 回车也走同一保存入口，避免原生表单提交丢失尚未序列化的卡片。
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (!banlistReady) {
      showErrors(['禁限规则尚未就绪，请重试加载规则后再保存；当前卡组已保留']);
      return;
    }
    if (!isSaving) saveBtn.click();
  });

  saveBtn.addEventListener('click', function () {
    var errs = validate();
    if (errs.length) { showErrors(errs); return; }
    showErrors([]);
    // 校验通过：三区状态序列化进隐藏字段（FormData 会带上 title/description/_csrf_token）
    hiddenMain.value = JSON.stringify(state.main);
    hiddenExtra.value = JSON.stringify(state.extra);
    hiddenSide.value = JSON.stringify(state.side);
    setSaving(true);

    fetch(form.action, {
      method: 'POST',
      body: new FormData(form),
      // 必须 manual：follow 重定向会消费掉 flash（踩坑 #29）
      redirect: 'manual',
      credentials: 'same-origin'
    })
      .then(function (res) {
        if (res.status === 503) {
          banlistReady = false;
          showBanStatus('禁限规则暂不可用，当前卡组已保留，请重试加载后再保存。', true);
          banRetry.hidden = false;
          showErrors(['禁限规则暂不可用，本次未保存。请重试加载规则，当前卡片和标题均已保留。']);
          setSaving(false);
          return;
        }
        if (res.type === 'opaqueredirect' || (res.status >= 300 && res.status < 400)) {
          // 保存成功被重定向：manual 模式下同源也通常拿不到 Location，回退到刷新/广场
          var loc = res.headers.get('Location');
          if (loc) { window.location.assign(loc); return; }
          if (window.__DECK_IS_EDIT__) { window.location.reload(); return; }
          window.location.assign(window.__DECK_FALLBACK__ || '/decks/');
          return;
        }
        // 200 = 服务端校验未通过，页面内含错误 flash，刷新让它呈现
        window.location.reload();
      })
      .catch(function () {
        showErrors(['网络错误，保存失败，请稍后再试']);
        setSaving(false);
      });
  });

  /* 启动 */
  loadInitial();
  renderZones();
  loadBanlist(); // 可以查看与移除卡片；规则就绪后才允许加卡和保存
})();
