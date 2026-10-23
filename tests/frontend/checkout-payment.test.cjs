const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function page(methods, onlineAvailable = true, gift = false) {
  const radios = (gift ? ['gift'] : onlineAvailable ? ['cash', 'online'] : ['cash']).map(value => {
    const label = {};
    return { value, checked: value === 'cash', label, closest: () => gift ? null : label };
  });
  const controls = methods.map(value => ({ value, selectedOptions: [], addEventListener(event, callback) { this.change = callback; } }));
  const groups = controls.map(method => ({ dataset: {}, querySelector: key => {
    if (key === '[data-method]') return method;
    if (key === '[data-slot]') return { querySelectorAll: () => [] };
    return {};
  }}));
  const notice = {}, submit = {};
  const form = {
    hasAttribute: key => gift && key === 'data-gift-checkout',
    querySelectorAll: key => key === '[data-checkout-group]' ? groups : key === '[name="payment_method"]' ? radios : [],
    querySelector: key => key === '[data-payment-help]' ? notice : key === '[data-checkout-submit]' ? submit : {},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/market/market.js'), 'utf8'), {
    document: { querySelectorAll: () => [], querySelector: key => key === '[data-checkout]' ? form : null, getElementById: () => null },
    window: { addEventListener: () => {} }, location: { hash: '' },
  });
  return { radios, controls, notice, submit };
}

test('one delivery in a mixed order hides cash and selects online', () => {
  const p = page(['pickup', 'delivery']);
  assert.equal(p.radios[0].disabled, true);
  assert.equal(p.radios[0].label.hidden, true);
  assert.equal(p.radios[0].checked, false);
  assert.equal(p.radios[1].checked, true);
  p.controls[1].value = 'pickup'; p.controls[1].change();
  assert.equal(p.radios[0].disabled, false);
  assert.equal(p.radios[0].label.hidden, false);
});

test('disabled online payment blocks delivery without cash fallback', () => {
  const p = page(['delivery'], false);
  assert.equal(p.submit.disabled, true);
  assert.equal(p.radios[0].disabled, true);
  assert.match(p.notice.textContent, /временно недоступна/);
  p.controls[0].value = 'pickup'; p.controls[0].change();
  assert.equal(p.submit.disabled, false);
  assert.equal(p.radios[0].disabled, false);
});

test('free gift delivery can be submitted without online payment or a payment label', () => {
  const p = page(['delivery'], false, true);
  assert.equal(p.submit.disabled, false);
  assert.equal(p.radios[0].disabled, false);
});
