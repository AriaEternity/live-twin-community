/* ═══════════════════════════════════════════════════════════
   卡片条件筛选面板（站内搜卡页与卡组编辑器共用组件）
   用法：window.CardFilter.init(containerEl, onApply)
     - 在 containerEl 内渲染「🔍 条件筛选」折叠面板
     - 点「筛选」回调 onApply(paramsObject)，只含有值的键，
       直接 new URLSearchParams(params) 可拼；点「重置」回调 onApply({})
   样式自带注入（#card-filter-style，只注一次）；
   同一份样式还含 filter/search 结果卡的禁限角标 .cf-ban 与换页控件 .cf-pager。
   参数 key 与后端 /api/cards/filter 契约一致，不许改。
   ═══════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── 选项常量（key 照后端契约，label 简体中文） ── */
  var KINDS = [['', '全部'], ['monster', '怪兽'], ['spell', '魔法'], ['trap', '陷阱']];
  var SUBTYPES = {
    monster: [['effect', '效果'], ['normal', '通常'], ['fusion', '融合'], ['ritual', '仪式'],
              ['synchro', '同调'], ['xyz', '超量'], ['link', '连接'], ['pendulum', '灵摆'],
              ['tuner', '调整'], ['flip', '翻转'], ['union', '同盟'], ['gemini', '二重'],
              ['spirit', '灵魂'], ['toon', '卡通']],
    spell: [['s_normal', '通常'], ['s_quick', '速攻'], ['s_continuous', '永续'],
            ['s_equip', '装备'], ['s_field', '场地'], ['s_ritual', '仪式']],
    trap: [['t_normal', '通常'], ['t_continuous', '永续'], ['t_counter', '反击']]
  };
  var ATTRS = [['earth', '地'], ['water', '水'], ['fire', '炎'], ['wind', '风'],
               ['light', '光'], ['dark', '暗'], ['divine', '神']];
  var RACES = [
    ['warrior', '战士族'], ['spellcaster', '魔法师族'], ['fairy', '天使族'], ['fiend', '恶魔族'],
    ['zombie', '不死族'], ['machine', '机械族'], ['aqua', '水族'], ['pyro', '炎族'],
    ['rock', '岩石族'], ['winged_beast', '鸟兽族'], ['plant', '植物族'], ['insect', '昆虫族'],
    ['thunder', '雷族'], ['dragon', '龙族'], ['beast', '兽族'], ['beast_warrior', '兽战士族'],
    ['dinosaur', '恐龙族'], ['fish', '鱼族'], ['sea_serpent', '海龙族'], ['reptile', '爬虫族'],
    ['psychic', '念动力族'], ['divine_beast', '幻神兽族'], ['creator_god', '创造神族'],
    ['wyrm', '幻龙族'], ['cyberse', '电子界族'], ['illusion', '幻想魔族']
  ];
  var BANS = [['', '全部'], ['forbidden', '禁止'], ['limited', '限制'], ['semi', '准限制'], ['none', '无限制']];

  /* ── 组件样式（chips 圆角胶囊 / 选中粉蓝渐变；含 .cf-ban 禁限角标与 .cf-pager 换页） ── */
  var CSS = [
    '.cf-toggle{display:inline-flex;align-items:center;min-height:38px;padding:0 16px;border-radius:999px;',
    'border:1px solid rgba(238,91,166,.5);background:rgba(255,255,255,.85);color:var(--ksk-deep);',
    'font:700 13px var(--f-cn);cursor:pointer;transition:transform .2s var(--ease-out);}',
    '.cf-toggle.is-open{background:linear-gradient(135deg,var(--ksk-primary),var(--ksk-neon));color:#fff;border-color:transparent;}',

    '.cf-panel{margin-top:12px;padding:16px;border-radius:14px;',
    'background:rgba(255,255,255,.85);border:1px solid rgba(21,16,43,.1);}',
    /* 类选择器优先级高于 UA 的 [hidden] 规则，显式补一条保证 hidden 真的隐藏 */
    '.cf-panel[hidden],.cf-pager[hidden]{display:none;}',
    '.cf-row{display:flex;align-items:flex-start;gap:10px;margin-bottom:12px;}',
    '.cf-label{flex:0 0 62px;padding-top:7px;font-size:12px;font-weight:900;opacity:.78;}',
    '.cf-body{flex:1;min-width:0;display:flex;flex-wrap:wrap;gap:8px;align-items:center;}',

    '.cf-chip{min-height:32px;padding:0 14px;border-radius:999px;border:1px solid rgba(21,16,43,.18);',
    'background:rgba(255,255,255,.9);color:var(--ink-85);font:700 13px var(--f-cn);cursor:pointer;',
    'transition:transform .2s var(--ease-out);}',
    '.cf-chip.is-on{background:linear-gradient(135deg,var(--ksk-primary),var(--ksk-neon));color:#fff;border-color:transparent;}',

    '.cf-select{min-height:36px;padding:0 10px;border-radius:10px;border:1px solid rgba(21,16,43,.18);',
    'background:#fff;color:var(--ink-85);font:400 13px var(--f-cn);}',
    '.cf-num{width:78px;min-height:36px;padding:0 8px;box-sizing:border-box;border-radius:10px;',
    'border:1px solid rgba(21,16,43,.18);background:#fff;color:var(--ink-85);font:400 13px var(--f-cn);}',
    '.cf-tilde{opacity:.7;}',

    '.cf-actions{display:flex;gap:10px;margin-top:4px;}',
    '.cf-apply{min-height:38px;padding:0 22px;border:none;border-radius:999px;cursor:pointer;',
    'background:linear-gradient(135deg,var(--ksk-primary),var(--ksk-neon));color:#fff;font:900 14px var(--f-cn);',
    'transition:transform .2s var(--ease-out);}',
    '.cf-reset{min-height:38px;padding:0 18px;border-radius:999px;cursor:pointer;',
    'border:1px solid rgba(21,16,43,.18);background:rgba(255,255,255,.9);color:var(--ink-78);',
    'font:700 13px var(--f-cn);transition:transform .2s var(--ease-out);}',

    /* 禁限角标：卡图容器右上角，浮层显式 z-index:2 防被 img 盖住（踩坑 #17），纯装饰 */
    '.cf-ban{position:absolute;top:4px;right:4px;z-index:2;min-width:20px;height:20px;line-height:20px;',
    'padding:0 4px;box-sizing:border-box;border-radius:6px;font-size:11px;font-weight:900;text-align:center;',
    'pointer-events:none;}',
    '.cf-ban-0{background:var(--danger);color:#fff;}',
    '.cf-ban-1{background:#E8710A;color:#fff;}',
    '.cf-ban-2{background:#F2C200;color:#15102B;}',

    /* filter 端点换页控件（搜卡页与编辑器共用） */
    '.cf-pager{display:flex;align-items:center;justify-content:center;gap:12px;margin-top:18px;}',
    '.cf-pager button{min-height:36px;padding:0 16px;border-radius:999px;border:1px solid rgba(238,91,166,.5);',
    'background:rgba(255,255,255,.85);color:var(--ksk-deep);font:700 13px var(--f-cn);cursor:pointer;',
    'transition:transform .2s var(--ease-out);}',
    '.cf-pager button[disabled]{opacity:.6;cursor:default;}',
    '.cf-pager .cf-page-info{font-size:13px;font-weight:700;opacity:.78;}',

    /* hover 装饰只包鼠标设备，防触屏 sticky hover（踩坑 #19） */
    '@media (hover:hover) and (pointer:fine){',
    '.cf-toggle:hover,.cf-chip:hover,.cf-apply:hover,.cf-reset:hover{transform:translateY(-1px);}',
    '.cf-pager button:not([disabled]):hover{transform:translateY(-1px);}}',

    /* 移动端：触控目标 chips ≥36px / 按钮 ≥44px；标签独占一行，chip 组换行；
       输入/下拉字号提到 16px——iOS Safari 聚焦 <16px 输入框会自动放大页面且不缩回（踩坑 #18） */
    '@media (max-width:600px){',
    '.cf-chip{min-height:36px;}',
    '.cf-toggle,.cf-apply,.cf-reset,.cf-pager button{min-height:44px;}',
    '.cf-select,.cf-num{font-size:16px;}',
    '.cf-row{flex-wrap:wrap;}',
    '.cf-label{flex-basis:100%;padding-top:0;}}'
  ].join('');

  /* 样式只注入一次（一页多次 init 不重复） */
  function injectStyle() {
    if (document.getElementById('card-filter-style')) return;
    var el = document.createElement('style');
    el.id = 'card-filter-style';
    el.textContent = CSS;
    document.head.appendChild(el);
  }

  /* HTML 转义（label 均为内部常量，兜底防呆） */
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* 初始状态：全部为空即「不筛选」 */
  function freshState() {
    return {
      kind: '',        // '' | monster | spell | trap
      subs: {},        // 子类多选：key → true（切卡种时清空）
      attr: '',        // 属性单选（再点取消）
      race: '', level: '', link: '', scale: '',
      atkMin: '', atkMax: '', defMin: '', defMax: '',
      ban: ''          // '' | forbidden | limited | semi | none
    };
  }

  /* 数值输入归一化：空 → null；非法/负数 → null；向下取整 */
  function num(v) {
    if (v === '' || v === null || v === undefined) return null;
    var n = Math.floor(Number(v));
    return (isFinite(n) && n >= 0) ? n : null;
  }

  /* min/max 成对处理：都填且 min > max 时交换，少一次注定无结果的请求 */
  function range(lo, hi) {
    var a = num(lo), b = num(hi);
    if (a !== null && b !== null && a > b) { var t = a; a = b; b = t; }
    return [a, b];
  }

  function init(container, onApply) {
    injectStyle();
    var st = freshState();

    container.innerHTML =
      '<button type="button" class="cf-toggle" aria-expanded="false" aria-controls="cf-panel">🔍 条件筛选</button>' +
      '<div class="cf-panel" id="cf-panel" hidden></div>';
    var toggle = container.querySelector('.cf-toggle');
    var panel = container.querySelector('.cf-panel');

    /* ── 按当前状态拼查询参数（只含有值的键，key 照后端契约） ── */
    function buildParams() {
      var p = {};
      if (st.kind) p.kind = st.kind;
      var subs = Object.keys(st.subs);
      if (st.kind && subs.length) p.subtype = subs.join(',');
      if (st.kind === 'monster') {
        if (st.attr) p.attribute = st.attr;
        if (st.race) p.race = st.race;
        if (st.link) p.link = st.link;
        if (st.scale) p.scale = st.scale;
      }
      if (st.level) p.level = st.level;
      var atk = range(st.atkMin, st.atkMax);
      if (atk[0] !== null) p.atk_min = atk[0];
      if (atk[1] !== null) p.atk_max = atk[1];
      var def = range(st.defMin, st.defMax);
      if (def[0] !== null) p.def_min = def[0];
      if (def[1] !== null) p.def_max = def[1];
      if (st.ban) p.ban = st.ban;
      return p;
    }

    /* ── 渲染辅助 ── */
    function chipHtml(group, key, label, on) {
      return '<button type="button" class="cf-chip' + (on ? ' is-on' : '') + '"' +
        ' data-group="' + group + '" data-key="' + esc(key) + '"' +
        ' aria-pressed="' + (on ? 'true' : 'false') + '">' + esc(label) + '</button>';
    }

    function selectHtml(field, options, val) {
      return '<select class="cf-select" data-field="' + field + '">' +
        options.map(function (o) {
          return '<option value="' + esc(o[0]) + '"' +
            (String(val) === String(o[0]) ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
        }).join('') + '</select>';
    }

    function row(label, inner) {
      return '<div class="cf-row"><span class="cf-label">' + esc(label) + '</span>' +
        '<div class="cf-body">' + inner + '</div></div>';
    }

    function numPair(label, minField, maxField) {
      return row(label,
        '<input class="cf-num" type="number" min="0" step="50" data-field="' + minField + '"' +
          ' value="' + esc(st[minField]) + '" aria-label="' + esc(label) + '下限">' +
        '<span class="cf-tilde">~</span>' +
        '<input class="cf-num" type="number" min="0" step="50" data-field="' + maxField + '"' +
          ' value="' + esc(st[maxField]) + '" aria-label="' + esc(label) + '上限">');
    }

    /* ── 面板整体重绘（状态存 st，重绘不丢值；事件走容器委托不受重绘影响） ── */
    function renderPanel() {
      var h = row('卡种', KINDS.map(function (k) {
        return chipHtml('kind', k[0], k[1], st.kind === k[0]);
      }).join(''));

      // 子类：选定卡种后才出现，选项随卡种切换
      if (st.kind) {
        h += row('子类', SUBTYPES[st.kind].map(function (s) {
          return chipHtml('subtype', s[0], s[1], !!st.subs[s[0]]);
        }).join(''));
      }
      // 属性 / 种族 / 连接 / 灵摆刻度：仅怪兽时出现
      if (st.kind === 'monster') {
        h += row('属性', ATTRS.map(function (a) {
          return chipHtml('attr', a[0], a[1], st.attr === a[0]);
        }).join(''));
        h += row('种族', selectHtml('race', [['', '全部种族']].concat(RACES), st.race));
      }
      var levels = [['', '全部']];
      for (var i = 1; i <= 12; i++) levels.push([String(i), i + ' 星']);
      h += row('星级', selectHtml('level', levels, st.level));
      if (st.kind === 'monster') {
        var links = [['', '全部']];
        for (var j = 1; j <= 6; j++) links.push([String(j), 'LINK-' + j]);
        h += row('连接', selectHtml('link', links, st.link));
        var scales = [['', '全部']];
        for (var k = 0; k <= 13; k++) scales.push([String(k), String(k)]);
        h += row('灵摆刻度', selectHtml('scale', scales, st.scale));
      }
      h += numPair('攻击力', 'atkMin', 'atkMax');
      h += numPair('防御力', 'defMin', 'defMax');
      h += row('禁限', BANS.map(function (b) {
        return chipHtml('ban', b[0], b[1], st.ban === b[0]);
      }).join(''));
      h += '<div class="cf-actions">' +
        '<button type="button" class="cf-apply">筛选</button>' +
        '<button type="button" class="cf-reset">重置</button></div>';
      panel.innerHTML = h;
    }

    /* ── 展开/收起 ── */
    toggle.addEventListener('click', function () {
      var open = panel.hidden;
      panel.hidden = !open;
      toggle.setAttribute('aria-expanded', String(open));
      toggle.classList.toggle('is-open', open);
    });

    /* ── 事件委托：chip 单/多选、筛选、重置 ── */
    panel.addEventListener('click', function (e) {
      var chip = e.target.closest('.cf-chip');
      if (chip) {
        var g = chip.dataset.group, k = chip.dataset.key;
        if (g === 'kind') {
          if (st.kind === k) return;
          st.kind = k;
          st.subs = {}; // 换卡种清空子类（key 集合不同）
          if (k !== 'monster') { st.attr = ''; st.race = ''; st.link = ''; st.scale = ''; }
          renderPanel();
          return;
        }
        if (g === 'subtype') {
          if (st.subs[k]) { delete st.subs[k]; } else { st.subs[k] = true; }
          renderPanel();
          return;
        }
        if (g === 'attr') { st.attr = (st.attr === k) ? '' : k; renderPanel(); return; }
        if (g === 'ban') { st.ban = k; renderPanel(); return; }
        return;
      }
      if (e.target.closest('.cf-apply')) { onApply(buildParams()); return; }
      if (e.target.closest('.cf-reset')) {
        st = freshState();
        renderPanel();
        onApply({}); // 重置即清空条件并立即刷新结果
      }
    });

    /* 下拉与数字输入：值同步进 st（重绘后由 st 还原） */
    function syncField(e) {
      var f = e.target.dataset && e.target.dataset.field;
      if (f && f in st) st[f] = e.target.value;
    }
    panel.addEventListener('change', syncField);
    panel.addEventListener('input', syncField);

    renderPanel();
  }

  window.CardFilter = { init: init };
})();
