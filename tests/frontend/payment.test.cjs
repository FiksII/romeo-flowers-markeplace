const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function paymentPage(fetch) {
  const button = { disabled: false };
  const errorBox = { hidden: true };
  let submit;
  const form = {
    action: '/account/orders/123/pay/',
    querySelector: () => button,
    addEventListener: (event, callback) => { submit = callback; },
  };
  const card = {
    dataset: { statusUrl: '/account/orders/123/pay/status/' },
    hasAttribute: () => false,
    querySelector: (selector) => ({
      '[name=csrfmiddlewaretoken]': { value: 'csrf-token' },
      '[data-pay-form]': form,
      '[data-pay-error]': errorBox,
    })[selector],
  };
  const navigations = [];
  const events = {};
  const timers = [];
  const reloads = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/market/payment.js'), 'utf8'), {
    document: { querySelector: () => card, createElement: () => ({}), body: { appendChild() {} } },
    window: { location: { assign: (url) => navigations.push(url), reload: () => reloads.push(true) }, addEventListener: (name, callback) => { events[name] = callback; } },
    fetch,
    setTimeout: (callback) => timers.push(callback),
  });
  return { button, errorBox, navigations, events, timers, reloads, submit: () => submit({ preventDefault() {} }) };
}

test('paying navigates to the bank PaymentURL without loading an iframe SDK', async () => {
  const calls = [];
  const page = paymentPage(async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ paymentUrl: 'https://pay.tbank-online.com/example' }) };
  });
  await page.submit();
  assert.deepEqual(page.navigations, ['https://pay.tbank-online.com/example']);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/account/orders/123/pay/');
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.headers['X-CSRFToken'], 'csrf-token');
  assert.equal(page.button.disabled, true);
});

test('a failed payment start displays the error and lets the customer retry', async () => {
  const page = paymentPage(async () => ({ ok: false, json: async () => ({ error: 'Время на оплату истекло.' }) }));
  await page.submit();
  assert.equal(page.errorBox.textContent, 'Время на оплату истекло.');
  assert.equal(page.errorBox.hidden, false);
  assert.equal(page.button.disabled, false);
  assert.deepEqual(page.navigations, []);
});

test('a network failure lets the customer retry', async () => {
  const page = paymentPage(async () => { throw new Error('Сеть недоступна.'); });
  await page.submit();
  assert.equal(page.errorBox.textContent, 'Сеть недоступна.');
  assert.equal(page.button.disabled, false);
  assert.deepEqual(page.navigations, []);
});

test('repeated clicks while starting a payment send only one request', async () => {
  let finish;
  let calls = 0;
  const page = paymentPage(() => {
    calls++;
    return new Promise((resolve) => { finish = resolve; });
  });
  const first = page.submit();
  await page.submit();
  assert.equal(calls, 1);
  finish({ ok: true, json: async () => ({ paymentUrl: 'https://pay.tbank-online.com/example' }) });
  await first;
  assert.equal(page.navigations.length, 1);
});

test('returning from the bank with Back restores the button and checks payment status', async () => {
  const page = paymentPage(async (url) => ({ ok: true, json: async () => url.endsWith('/status/') ? { waiting: false, status: 'CONFIRMED' } : { paymentUrl: 'https://pay.tbank-online.com/example' } }));
  await page.submit();
  assert.equal(page.button.disabled, true);
  page.events.pageshow({ persisted: true });
  assert.equal(page.button.disabled, false);
  assert.equal(page.timers.length, 1);
  await page.timers.shift()();
  assert.equal(page.reloads.length, 1);
});
