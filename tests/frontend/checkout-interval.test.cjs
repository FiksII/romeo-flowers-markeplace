const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function checkout(ownDelivery) {
  const slot = { querySelectorAll: () => [] };
  const slotField = {};
  const method = { value: 'delivery', selectedOptions: [{ dataset: { goods: '2500' } }], addEventListener: () => {} };
  const groupNodes = { '[data-method]': method, '[data-slot]': slot, '[data-slot-field]': slotField };
  const group = { dataset: { ownDelivery: String(ownDelivery), goods: '2500' }, querySelector: (key) => groupNodes[key] ?? {} };
  const nodes = { '[name="delivery_contact"]:checked': { value: 'call' } };
  const form = {
    querySelectorAll: (key) => key === '[data-checkout-group]' ? [group] : [],
    querySelector: (key) => nodes[key] ?? {},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/market/market.js'), 'utf8'), {
    document: { querySelectorAll: () => [], querySelector: (key) => key === '[data-checkout]' ? form : null, getElementById: () => null },
    window: { addEventListener: () => {} }, location: { hash: '' },
  });
  return { slot, slotField };
}

test('Romeo interval stays visible and required when calling the recipient', () => {
  const { slot, slotField } = checkout(0);
  assert.equal(slotField.hidden, false);
  assert.equal(slot.disabled, false);
  assert.equal(slot.required, true);
});

test('shop delivery can still clarify the interval with the recipient', () => {
  const { slot, slotField } = checkout(1);
  assert.equal(slotField.hidden, true);
  assert.equal(slot.disabled, true);
  assert.equal(slot.required, false);
});
