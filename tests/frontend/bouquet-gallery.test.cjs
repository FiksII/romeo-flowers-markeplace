const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page() {
  const listeners = new Map();
  function element(key) {
    return { dataset: {}, attrs: {}, addEventListener(type, fn) { listeners.set(`${key}:${type}`, fn); },
      setAttribute(name, value) { this.attrs[name] = value; }, removeAttribute(name) { delete this.attrs[name]; },
      focus() { this.focused = true; }, scrollIntoView() {} };
  }
  const main = element('main'), original = element('original'), counter = element('counter');
  const thumbs = [0, 1, 2].map(i => Object.assign(element(`thumb${i}`), { href: `/original-${i}.jpg`, dataset: { src: `/detail-${i}.webp`, srcset: `image-${i} 640w` } }));
  const gallery = element('gallery');
  gallery.querySelectorAll = () => thumbs;
  gallery.querySelector = selector => ({ '[data-gallery-main]': main, '[data-gallery-original]': original, '[data-gallery-counter]': counter }[selector]);
  vm.runInNewContext(fs.readFileSync('static/market/bouquet-gallery.js', 'utf8'), {
    document: { querySelectorAll: selector => selector === '[data-bouquet-gallery]' ? [gallery] : [] },
  });
  function event(extra = {}) { return { button: 0, preventDefault() { this.prevented = true; }, ...extra }; }
  return { main, original, counter, thumbs, fire: (key, type, extra) => { const e = event(extra); listeners.get(`${key}:${type}`)(e); return e; } };
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
  assert.equal(ui.original.href, undefined);
});

test('arrow navigation wraps and moves focus with selected photograph', () => {
  const ui = page();
  ui.fire('thumb0', 'keydown', { key: 'ArrowLeft' });
  assert.equal(ui.main.src, '/detail-2.webp');
  assert.equal(ui.thumbs[2].focused, true);
});
