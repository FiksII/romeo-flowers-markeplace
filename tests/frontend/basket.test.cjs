const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page(fetch) {
  let submit;
  const button = { disabled: false };
  const badge = { textContent: '0' };
  const link = { querySelector: () => badge, setAttribute() {}, getBoundingClientRect: () => ({ width: 0 }) };
  const notice = { classList: { toggle() {} } };
  const form = { action: '/basket/add/1/', querySelector: () => button, closest: () => null, setAttribute() {}, removeAttribute() {} };
  vm.runInNewContext(fs.readFileSync('static/market/basket.js', 'utf8'), {
    document: {
      addEventListener: (name, handler) => { submit = handler; },
      querySelector: () => notice, querySelectorAll: () => [link],
    }, fetch, FormData: class {}, setTimeout() {}, clearTimeout() {},
  });
  let prevented = false;
  return { button, badge, notice, submit: () => submit({ target: { closest: () => form }, preventDefault() { prevented = true; } }), prevented: () => prevented };
}

test('adding stays on the page and updates the basket after server confirmation', async () => {
  const calls = [];
  const ui = page(async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ ok: true, count: 3 }) };
  });
  await ui.submit();
  assert.equal(ui.prevented(), true);
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.headers.Accept, 'application/json');
  assert.equal(ui.badge.textContent, 3);
  assert.equal(ui.notice.textContent, 'Добавлено в корзину');
  assert.equal(ui.button.disabled, false);
});

test('stock errors stay visible without a false success count', async () => {
  const ui = page(async () => ({ ok: false, json: async () => ({ ok: false, message: 'Нет в наличии' }) }));
  await ui.submit();
  assert.equal(ui.badge.textContent, '0');
  assert.equal(ui.notice.textContent, 'Нет в наличии');
  assert.equal(ui.button.disabled, false);
});

test('repeated clicks while waiting send one request', async () => {
  let resolve;
  let calls = 0;
  const ui = page(() => { calls++; return new Promise((done) => { resolve = done; }); });
  const first = ui.submit();
  await ui.submit();
  assert.equal(calls, 1);
  resolve({ ok: true, json: async () => ({ ok: true, count: 1 }) });
  await first;
});

test('a lost response advises checking the basket without retrying the purchase', async () => {
  const ui = page(async () => { throw new Error('offline'); });
  await ui.submit();
  assert.match(ui.notice.textContent, /Проверьте корзину/);
  assert.equal(ui.badge.textContent, '0');
  assert.equal(ui.button.disabled, false);
});
