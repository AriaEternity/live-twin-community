/* 在 VM 中执行真实 SW，验证缓存契约；不替代浏览器注册/安装/离线实测。 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = fs.readFileSync(new URL('../sw.js', import.meta.url), 'utf8');
function harness() {
  const handlers = {}, state = {shell: [], deleted: [], matches: [], puts: []};
  const storage = {
    addAll: async paths => { state.shell = paths; },
    put: async (...args) => { state.puts.push(args); },
  };
  const scope = {
    URL,
    self: { location: {origin: 'http://127.0.0.1:18080'},
      addEventListener: (name, fn) => { handlers[name] = fn; },
      skipWaiting: async () => { state.waiting = true; },
      clients: {claim: async () => { state.claimed = true; }} },
    caches: {
      open: async name => { state.opened = name; return storage; },
      keys: async () => ['livetwin-old', 'livetwin-public-20261028'],
      delete: async name => { state.deleted.push(name); },
      match: async (request, options) => {
        state.matches.push({request, options});
        return request === '/offline.html' ? 'offline-shell' : null;
      },
    },
    fetch: async () => ({ok: true, clone: () => 'response-copy'}),
  };
  vm.runInNewContext(source, scope);
  function event(method='GET', path='/api/cards/search', mode='cors') {
    const ev = {request: {method, url: new URL(path, scope.self.location.origin).href, mode},
      respondWith: value => { ev.result = value; }, waitUntil: value => { ev.wait = value; }};
    return ev;
  }
  return {handlers, scope, state, event};
}

test('安装预缓存完整应用壳并使用冻结版本', async () => {
  const {handlers, event, state} = harness();
  const ev = event(); handlers.install(ev); await ev.wait;
  assert.equal(state.opened, 'livetwin-public-20261028');
  assert.ok(state.shell.length >= 12);
  for (const name of ['/', '/js/data.js', '/js/main.js', '/css/style.css', '/offline.html']) {
    assert.ok(state.shell.includes(name));
  }
  assert.equal(state.waiting, true);
});
test('激活清理旧版本并接管客户端', async () => {
  const {handlers, event, state} = harness();
  const ev=event(); handlers.activate(ev); await ev.wait;
  assert.deepEqual(state.deleted, ['livetwin-old']); assert.equal(state.claimed, true);
});
test('POST、跨域和卡片 API 均绕过 SW 缓存', () => {
  const {handlers, event} = harness();
  for (const ev of [event('POST','/decks/new'), event('GET','https://example.invalid/js/x.js'), event()]) {
    handlers.fetch(ev); assert.equal(ev.result, undefined);
  }
});
test('导航优先网络且不缓存动态 HTML', async () => {
  const {handlers, event, state} = harness();
  const ev=event('GET','/forum/','navigate'); handlers.fetch(ev);
  assert.equal((await ev.result).ok, true); assert.equal(state.puts.length,0);
});
test('网络失败的导航降级到离线说明页', async () => {
  const {handlers, event, scope} = harness();
  scope.fetch=async()=>{throw new Error('合成断网');};
  const ev=event('GET','/forum/','navigate'); handlers.fetch(ev);
  assert.equal(await ev.result, 'offline-shell');
});
test('静态资源兼容版本参数且未命中时回写当前缓存', async () => {
  const {handlers, event, state} = harness();
  const ev=event('GET','/js/data.js?v=20261027'); handlers.fetch(ev); await ev.result;
  assert.equal(state.matches[0].options.ignoreSearch,true);
  assert.equal(state.opened,'livetwin-public-20261028'); assert.equal(state.puts.length,1);
});

const base = process.argv.find(value => /^http:\/\/127\.0\.0\.1:\d+$/.test(value));
if (base) test('隔离 HTTP 环境所有应用壳和 manifest 图标存在', async () => {
  const {handlers, event, state} = harness();
  const ev=event(); handlers.install(ev); await ev.wait;
  for (const path of state.shell) {
    const response=await fetch(base+path);
    assert.equal(response.status,200,path);
  }
  const manifest=await (await fetch(base+'/manifest.json')).json();
  assert.equal(manifest.start_url,'/?source=pwa'); assert.equal(manifest.icons.length,3);
  for (const icon of manifest.icons) assert.equal((await fetch(base+icon.src)).status,200,icon.src);
  console.log(`HTTP 壳资源 ${state.shell.length} 项及 manifest 三图标可达`);
});
