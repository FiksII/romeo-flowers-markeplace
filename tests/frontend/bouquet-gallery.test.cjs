const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page(count = 3) {
  const listeners = new Map();
  function element(key) {
    return { dataset: {}, attrs: {}, addEventListener(type, fn) { listeners.set(`${key}:${type}`, fn); },
      setAttribute(name, value) { this.attrs[name] = value; }, removeAttribute(name) { delete this.attrs[name]; },
      focus() { this.focused = true; }, scrollIntoView() {} };
  }
  const main = element('main'), original = element('original'), counter = element('counter');
  original.href = '/original-0.jpg';
  const dialog = element('dialog'), full = element('full'), close = element('close'), previous = element('previous'), next = element('next'), status = element('status'), retry = element('retry'), zoom = element('zoom');
  dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; listeners.get('dialog:close')?.(); };
  dialog.classList = { remove() {}, toggle() { return true; } };
  const thumbs = Array.from({length: count > 1 ? count : 0}, (_, i) => Object.assign(element(`thumb${i}`), { href: `/original-${i}.jpg`, dataset: { src: `/detail-${i}.webp`, srcset: `image-${i} 640w` } }));
  const gallery = element('gallery');
  gallery.querySelectorAll = () => thumbs;
  gallery.querySelector = selector => ({ '[data-gallery-main]': main, '[data-gallery-original]': original, '[data-gallery-counter]': counter, '[data-gallery-dialog]': dialog }[selector]);
  dialog.querySelector = selector => ({ '[data-gallery-full]': full, '[data-gallery-close]': close, '[data-gallery-previous]': previous, '[data-gallery-next]': next, '[data-gallery-status]': status, '[data-gallery-retry]': retry, '[data-gallery-zoom]': zoom }[selector]);
  vm.runInNewContext(fs.readFileSync('static/market/bouquet-gallery.js', 'utf8'), {
    document: { body: {classList: {toggle() {}}}, querySelectorAll: selector => selector === '[data-bouquet-gallery]' ? [gallery] : [] },
  });
  function event(extra = {}) { return { button: 0, preventDefault() { this.prevented = true; }, ...extra }; }
  return { main, original, counter, thumbs, dialog, full, status, fire: (key, type, extra) => { const e = event(extra); listeners.get(`${key}:${type}`)(e); return e; } };
}

test('thumbnail switches main photograph, full-size link, and selected state', () => {
  const ui = page();
  assert.equal(ui.fire('thumb1', 'click').prevented, true);
  assert.equal(ui.main.src, '/detail-1.webp');
  assert.equal(ui.main.srcset, 'image-1 640w');
  assert.equal(ui.original.href, '/original-1.jpg');
  assert.equal(ui.thumbs[1].attrs['aria-current'], 'true');
  assert.equal(ui.thumbs[0].attrs['aria-current'], undefined);
  assert.match(ui.counter.textContent, /2.*3/);
});

test('modified thumbnail clicks keep browser link behavior', () => {
  const ui = page();
  assert.equal(ui.fire('thumb1', 'click', { ctrlKey: true }).prevented, undefined);
  assert.equal(ui.original.href, '/original-0.jpg');
});

test('full-size photograph opens in a modal without navigating away', () => {
  const ui = page();
  assert.equal(ui.full.src, undefined, 'original is loaded only when requested');
  assert.equal(ui.fire('original', 'click').prevented, true);
  assert.equal(ui.dialog.open, true);
  assert.equal(ui.full.src, '/original-0.jpg');
  ui.fire('next', 'click');
  assert.equal(ui.full.src, '/original-1.jpg');
  ui.fire('dialog', 'keydown', {key: 'ArrowLeft'});
  assert.equal(ui.full.src, '/original-0.jpg');
  ui.fire('close', 'click');
  assert.equal(ui.dialog.open, false);
  assert.equal(ui.original.focused, true);
});

test('a single legacy photograph also opens in the modal', () => {
  const ui = page(1);
  assert.equal(ui.fire('original', 'click').prevented, true);
  assert.equal(ui.dialog.open, true);
  assert.equal(ui.full.src, '/original-0.jpg');
});

test('swiping switches photograph without opening a modal accidentally', () => {
  const ui = page();
  ui.fire('original', 'pointerdown', {isPrimary: true, clientX: 200, clientY: 100});
  ui.fire('original', 'pointerup', {clientX: 100, clientY: 105});
  assert.equal(ui.fire('original', 'click').prevented, true);
  assert.equal(ui.dialog.open, undefined);
  assert.equal(ui.original.href, '/original-1.jpg');
});

test('arrow navigation wraps and moves focus with selected photograph', () => {
  const ui = page();
  ui.fire('thumb0', 'keydown', { key: 'ArrowLeft' });
  assert.equal(ui.main.src, '/detail-2.webp');
  assert.equal(ui.thumbs[2].focused, true);
});
