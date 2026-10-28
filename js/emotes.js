/* 表情选择器 —— 论坛发帖/回帖/编辑共用
 *
 * 用法：模板里给「表情」按钮加 class="emote-btn" 和 data-target="<textarea id>"，
 * 并放一个 <div class="emote-modal" hidden> 容器（本脚本填充内容、控制显隐）。
 * 清单来自 /img/emotes/emotes.json（与服务端 emotes.py 同一份），首次打开时懒加载。
 *
 * 交互：点表情往 textarea 光标处插入 [em:id]，面板保持打开可连发；
 * × 按钮 / 点遮罩 / Esc 关闭；打开期间背景滚锁（踩坑 #20 同款 position:fixed 方案）。
 */
(function () {
  'use strict';

  var MANIFEST_URL = '/img/emotes/emotes.json';
  var modal = document.querySelector('.emote-modal');
  if (!modal) return;

  var packs = null;        // 清单缓存
  var loading = false;
  var currentBox = null;   // 当前目标 textarea
  var scrollY = 0;

  function lockScroll() {
    scrollY = window.scrollY;
    document.body.style.position = 'fixed';
    document.body.style.top = -scrollY + 'px';
    document.body.style.left = '0';
    document.body.style.right = '0';
  }
  function unlockScroll() {
    document.body.style.position = '';
    document.body.style.top = '';
    document.body.style.left = '';
    document.body.style.right = '';
    window.scrollTo(0, scrollY);
  }

  function openPanel(box) {
    currentBox = box;
    modal.hidden = false;
    lockScroll();
    if (!packs && !loading) loadManifest();
    var closeBtn = modal.querySelector('.emote-close');
    if (closeBtn) closeBtn.focus();
  }
  function closePanel() {
    modal.hidden = true;
    unlockScroll();
    if (currentBox) currentBox.focus();
  }

  /* 往 textarea 光标处插入占位符并复原选区（setRangeText 原生支持撤销栈） */
  function insertCode(id) {
    if (!currentBox) return;
    var code = '[em:' + id + ']';
    currentBox.focus();
    if (typeof currentBox.setRangeText === 'function') {
      var start = currentBox.selectionStart != null ? currentBox.selectionStart : currentBox.value.length;
      currentBox.setRangeText(code, start, currentBox.selectionEnd != null ? currentBox.selectionEnd : start, 'end');
    } else {
      currentBox.value += code; // 远古浏览器兜底：追加到末尾
    }
    currentBox.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function renderPacks() {
    var tabs = modal.querySelector('.emote-tabs');
    var grid = modal.querySelector('.emote-grid');
    var empty = modal.querySelector('.emote-empty');
    if (!tabs || !grid) return;
    if (!packs || !packs.length) {
      if (empty) empty.hidden = false;
      return;
    }

    function showPack(idx) {
      tabs.querySelectorAll('.emote-tab').forEach(function (t, i) {
        t.classList.toggle('is-active', i === idx);
        t.setAttribute('aria-selected', i === idx ? 'true' : 'false');
      });
      grid.innerHTML = '';
      packs[idx].stickers.forEach(function (st) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'emote-item';
        btn.title = st.name;
        btn.setAttribute('aria-label', '插入表情 ' + st.name);
        var img = document.createElement('img');
        img.src = '/img/emotes/' + st.file;
        img.alt = st.name;
        img.loading = 'lazy';
        if (st.w) img.width = st.w;
        if (st.h) img.height = st.h;
        btn.appendChild(img);
        btn.addEventListener('click', function () { insertCode(st.id); });
        grid.appendChild(btn);
      });
    }

    tabs.innerHTML = '';
    packs.forEach(function (p, i) {
      var tab = document.createElement('button');
      tab.type = 'button';
      tab.className = 'emote-tab';
      tab.textContent = p.name || p.id;
      tab.setAttribute('role', 'tab');
      tab.addEventListener('click', function () { showPack(i); });
      tabs.appendChild(tab);
    });
    tabs.hidden = packs.length < 2; // 只有一个包时不显示标签页
    showPack(0);
  }

  function loadManifest() {
    loading = true;
    fetch(MANIFEST_URL, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        packs = (data && data.packs) || [];
        renderPacks();
      })
      .catch(function () {
        var empty = modal.querySelector('.emote-empty');
        if (empty) { empty.hidden = false; empty.textContent = '表情包加载失败，请稍后重试'; }
      })
      .then(function () { loading = false; });
  }

  /* 事件绑定 */
  document.querySelectorAll('.emote-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var box = document.getElementById(btn.getAttribute('data-target'));
      if (box) openPanel(box);
    });
  });
  modal.querySelector('.emote-close').addEventListener('click', closePanel);
  modal.addEventListener('click', function (e) {
    if (e.target === modal) closePanel(); // 点遮罩关闭
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !modal.hidden) closePanel();
  });
})();
