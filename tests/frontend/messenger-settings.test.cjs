const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function page(fetch) {
  const button = {}, result = {};
  let submit;
  const form = {
    action: 'https://flowers.test/partner/shop/settings/',
    querySelector: key => key === '[value="test"]' ? button : result,
    addEventListener: (event, handler) => { submit = handler; },
  };
  class FormData {
    constructor(target) { assert.equal(target, form); this.values = { csrfmiddlewaretoken: 'masked-token', link: '42' }; }
    set(key, value) { this.values[key] = value; }
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/market/messenger-settings.js'), 'utf8'), {
    document: { querySelectorAll: () => [form] }, FormData, fetch,
    // Any navigation or scrolling is a failure; this action must stay in place.
    window: new Proxy({}, { get() { assert.fail('Window/navigation access'); } }),
    location: new Proxy({}, { get() { assert.fail('Location/navigation access'); } }),
  });
  return { button, result, submit: () => submit({ preventDefault() {} }) };
}

test('test button sends CSRF and shows inline feedback without navigation', async () => {
  const p = page(async (url, options) => {
    assert.equal(options.method, 'POST');
    assert.equal(options.headers.Accept, 'application/json');
    assert.equal(options.body.values.csrfmiddlewaretoken, 'masked-token');
    assert.equal(options.body.values.action, 'test');
    assert.equal(options.body.values.link, '42');
    return { json: async () => ({ ok: true, message: 'Проверочное сообщение отправлено.' }) };
  });
  await p.submit();
  assert.equal(p.result.textContent, 'Проверочное сообщение отправлено.');
  assert.equal(p.result.hidden, false);
  assert.equal(p.button.disabled, false);
});

test('server failure is shown beside the button and permits a retry', async () => {
  const p = page(async () => ({ json: async () => ({ ok: false, message: 'Бот заблокирован.' }) }));
  await p.submit();
  assert.equal(p.result.textContent, 'Бот заблокирован.');
  assert.equal(p.button.disabled, false);
});

test('double click sends a single request and network errors permit retry', async () => {
  let release, calls = 0;
  const p = page(async () => { calls++; await new Promise(resolve => { release = resolve; }); throw Error('offline'); });
  const pending = p.submit();
  await p.submit();
  assert.equal(calls, 1);
  assert.equal(p.button.disabled, true);
  release(); await pending;
  assert.equal(p.button.disabled, false);
  assert.match(p.result.textContent, /Попробуйте ещё раз/);
});
