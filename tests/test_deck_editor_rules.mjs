/* 离线运行真实编辑器脚本，验证规则失败/重试、异画计数与卡组保留。 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../js/deck_editor.js', import.meta.url), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const ordinaryMain = () => Array.from({ length: 40 }, (_, i) => ({
  id: 900000001 + i, name: '合成卡' + i, types: '怪兽卡'
}));
const card = id => ({ id, name: '合成卡' + id, types: '怪兽卡' });
const rules = (cards = { 123: 0 }, aliases = {}, stale = false) => ({
  cards, aliases, meta: { available: true, effective_date: '2026-10-01',
    fetched: '2026-10-27', count: Object.keys(cards).length, stale }
});

function editor(main = ordinaryMain(), extra = []) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', className: '', innerHTML: '',
      hidden: false, disabled: false, dataset: {}, listeners: {},
      action: '/decks/new',
      addEventListener(type, handler) { this.listeners[type] = handler; },
      click() { if (!this.disabled && this.listeners.click) this.listeners.click(); },
      querySelector() { return element('synthetic-search-button'); },
      scrollIntoView() {}
    });
    return elements.get(id);
  };
  element('f-title').value = '合成卡组标题';
  const pending = [];
  const notices = [];
  let reloads = 0;
  let applyFilters;
  const window = {
    __DECK_INITIAL__: { main, extra, side: [] },
    CardFilter: { init(_element, callback) { applyFilters = callback; } },
    toast: text => notices.push(text),
    location: { reload() { reloads++; }, assign() { reloads++; } }
  };
  vm.runInNewContext(source, {
    document: { getElementById: element }, window, URLSearchParams, AbortController,
    setTimeout: () => 1, clearTimeout() {},
    FormData: class { constructor(form) { this.form = form; } },
    alert: text => notices.push(text),
    fetch(url, options) {
      return new Promise((resolve, reject) => pending.push({ url, options, resolve, reject }));
    }
  }, { filename: 'deck_editor.js' });
  return { element, pending, notices, applyFilters: params => applyFilters(params),
    get reloads() { return reloads; } };
}

async function supplyRules(harness, data = rules()) {
  const request = harness.pending.shift();
  assert.equal(request.url, '/decks/api/banlist');
  request.resolve({ ok: true, status: 200, json: async () => data });
  await flush();
}

let checks = 0;
async function check(name, test) {
  await test();
  checks++;
  process.stdout.write('通过：' + name + '\n');
}

await check('加载中和请求失败均禁止保存，重试成功恢复且保留卡组', async () => {
  const h = editor();
  const initial = h.element('de-zones').innerHTML;
  assert.equal(h.element('de-save').disabled, true);
  h.pending.shift().reject(new Error('合成网络错误'));
  await flush();
  assert.equal(h.element('de-save').disabled, true);
  assert.equal(h.element('de-ban-retry').hidden, false);
  assert.match(h.element('de-ban-status').textContent, /待核验/);
  assert.equal(h.element('de-zones').innerHTML, initial);
  h.element('de-ban-retry').listeners.click();
  await supplyRules(h);
  assert.equal(h.element('de-save').disabled, false);
  assert.match(h.element('de-ban-status').textContent, /生效日期：2026-10-01.*抓取日期：2026-10-27/);
  assert.equal(h.element('de-zones').innerHTML, initial);
});

await check('最后有效规则明确标注更新失败和待核验', async () => {
  const h = editor();
  await supplyRules(h, rules({ 123: 0 }, {}, true));
  assert.equal(h.element('de-save').disabled, false);
  assert.equal(h.element('de-ban-retry').hidden, false);
  assert.match(h.element('de-ban-status').textContent, /更新失败.*上次有效规则.*待核验/);
});

await check('规则未就绪时回车不会原生提交或丢失卡片', async () => {
  const h = editor();
  const initial = h.element('de-zones').innerHTML;
  let prevented = false;
  h.element('deck-form').listeners.submit({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(h.pending.length, 1);
  assert.match(h.element('de-errors').innerHTML, /规则尚未就绪/);
  assert.equal(h.element('de-zones').innerHTML, initial);
  await supplyRules(h);
});

await check('原密码与主图号跨分区混搭仍受同一张限制卡上限约束', async () => {
  const main = ordinaryMain();
  main[0] = card(18144506);
  const h = editor(main, [card(18144507)]);
  await supplyRules(h, rules({ 18144507: 1 }, { 18144506: 18144507 }));
  const initial = h.element('de-zones').innerHTML;
  h.element('de-save').listeners.click();
  assert.match(h.element('de-errors').innerHTML, /最多投入 1 张/);
  assert.equal(h.pending.length, 0);
  assert.equal(h.element('de-zones').innerHTML, initial);
});

await check('未禁限同卡异画也合计最多三张', async () => {
  const main = ordinaryMain();
  main.splice(0, 3, card(456), card(456), card(789));
  const h = editor(main, [card(789)]);
  await supplyRules(h, rules({ 123: 0 }, { 456: 789 }));
  h.element('de-save').listeners.click();
  assert.match(h.element('de-errors').innerHTML, /最多投入 3 张/);
  assert.equal(h.pending.length, 0);
});

await check('点击加入主图号时计入原密码已占用的限额', async () => {
  const main = ordinaryMain();
  main[0] = card(18144506);
  const h = editor(main);
  await supplyRules(h, rules({ 18144507: 1 }, { 18144506: 18144507 }));
  h.element('de-q').value = '合成搜索';
  h.element('de-search-form').listeners.submit({ preventDefault() {} });
  h.pending.shift().resolve({ ok: true, status: 200,
    json: async () => ({ cards: [{ id: 18144507, sc_name: '合成卡', ban: 1 }] }) });
  await flush();
  const initial = h.element('de-zones').innerHTML;
  h.element('de-results').listeners.click({
    target: { closest: () => ({ dataset: { idx: '0', zone: 'main' } }) }
  });
  assert.match(h.notices.at(-1), /最多投入 1 张/);
  assert.equal(h.element('de-zones').innerHTML, initial);
});

await check('服务端保存时规则不可用会保留标题和卡片，不刷新页面', async () => {
  const h = editor();
  await supplyRules(h);
  const initial = h.element('de-zones').innerHTML;
  h.element('de-save').listeners.click();
  const save = h.pending.shift();
  assert.equal(save.options.method, 'POST');
  save.resolve({ ok: false, status: 503 });
  await flush();
  assert.equal(h.reloads, 0);
  assert.equal(h.element('de-save').disabled, true);
  assert.equal(h.element('f-title').value, '合成卡组标题');
  assert.equal(h.element('de-zones').innerHTML, initial);
  assert.match(h.element('de-errors').innerHTML, /当前卡片和标题均已保留/);
});

await check('本地快照提示在搜索及分页结果中可见且不覆盖数量', async () => {
  const h = editor();
  await supplyRules(h);
  h.element('de-q').value = '合成快照';
  h.element('de-search-form').listeners.submit({ preventDefault() {} });
  const snapshotNotice = '本地文本快照（抓取日期：2026-10-27）';
  h.pending.shift().resolve({ ok: true, status: 200,
    json: async () => ({ cards: [{ id: 68059897, sc_name: '合成卡' }],
      local_snapshot: true, notice: snapshotNotice }) });
  await flush();
  assert.equal(h.element('de-status').textContent, snapshotNotice);
  // 由筛选回调驱动真实 filter 端点提示分支，保留总数并追加快照来源。
  h.element('de-q').value = '';
  h.applyFilters({ kind: 'spell' });
  const filtered = h.pending.shift();
  assert.match(filtered.url, /^\/api\/cards\/filter\?/);
  filtered.resolve({ ok: true, status: 200,
    json: async () => ({ cards: [{ id: 68059897, sc_name: '合成卡' }],
      total: 42, page: 1, pages: 2, notice: snapshotNotice }) });
  await flush();
  assert.match(h.element('de-status').textContent, /^共 42 张符合条件的卡片/);
  assert.ok(h.element('de-status').textContent.includes(snapshotNotice));
});

process.stdout.write(`编辑器规则回归：${checks}/${checks} 通过\n`);
