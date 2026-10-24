const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function page(clipboard) {
  let click, selected = false;
  const input = { value: 'https://flowers.test/r/private-code/', focus() {}, select() { selected = true; } };
  const status = {};
  const button = { addEventListener(type, handler) { click = handler; } };
  const section = { querySelector(selector) {
    return selector === '[data-referral-link]' ? input : selector === '[data-referral-copy]' ? button : status;
  } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/market/referral.js'), 'utf8'), {
    document: { querySelectorAll: () => [section] }, navigator: { clipboard },
  });
  return { click: () => click(), status, get selected() { return selected; } };
}

test('copies the visible personal link and announces success', async () => {
  let copied;
  const p = page({ async writeText(value) { copied = value; } });
  await p.click();
  assert.equal(copied, 'https://flowers.test/r/private-code/');
  assert.equal(p.status.textContent, 'Ссылка скопирована');
});

for (const [name, clipboard] of [
  ['unsupported', undefined],
  ['rejected', { async writeText() { throw Error('denied'); } }],
]) {
  test(`${name} clipboard selects link and explains manual copying`, async () => {
    const p = page(clipboard);
    await p.click();
    assert.equal(p.selected, true);
    assert.equal(p.status.textContent, 'Ссылка выделена. Скопируйте её вручную.');
  });
}
