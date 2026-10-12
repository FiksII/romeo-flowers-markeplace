const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function field() {
  const handlers = {};
  const input = {
    value: '', selectionStart: 0,
    addEventListener: (name, handler) => { handlers[name] = handler; },
    setSelectionRange: (start) => { input.selectionStart = start; },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/market/recipient-phone.js'), 'utf8'), {
    document: { querySelectorAll: () => [input] },
  });
  return { input, handlers };
}

test('formats pasted Russian numbers with either country or trunk prefix', () => {
  for (const value of ['+79991234567', '89991234567', '9991234567']) {
    const { input, handlers } = field();
    input.value = value;
    input.selectionStart = value.length;
    handlers.input();
    assert.equal(input.value, '+7(999)123-45-67');
  }
});

test('keeps an incomplete number visible and can clear the field', () => {
  const { input, handlers } = field();
  input.value = '99912';
  input.selectionStart = 5;
  handlers.input();
  assert.equal(input.value, '+7(999)12');
  input.value = '';
  input.selectionStart = 0;
  handlers.input();
  assert.equal(input.value, '');
});

test('backspace at a separator removes the previous digit', () => {
  const { input, handlers } = field();
  input.value = '+7(999)123-45-67';
  input.selectionStart = input.selectionEnd = 7;
  let prevented = false;
  handlers.keydown({ key: 'Backspace', preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(input.value, '+7(991)234-56-7');
});

test('formats a number typed digit by digit without moving digits around separators', () => {
  for (const number of ['9991234567', '79991234567', '89991234567', '+79991234567']) {
    const { input, handlers } = field();
    for (const digit of number) {
      const position = input.selectionStart;
      input.value = input.value.slice(0, position) + digit + input.value.slice(position);
      input.selectionStart = position + 1;
      handlers.input();
    }
    assert.equal(input.value, '+7(999)123-45-67');
  }
});

test('plus, seven and eight activate the country prefix immediately', () => {
  for (const value of ['+', '7', '8', '+7']) {
    const { input, handlers } = field();
    input.value = value;
    input.selectionStart = value.length;
    handlers.input();
    assert.equal(input.value, '+7');
    assert.equal(input.selectionStart, 2);
  }
});

test('backspace can clear an activated prefix', () => {
  const { input, handlers } = field();
  input.value = '+';
  input.selectionStart = 1;
  handlers.input();
  input.selectionEnd = input.selectionStart;
  let prevented = false;
  handlers.keydown({ key: 'Backspace', preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(input.value, '');
});
