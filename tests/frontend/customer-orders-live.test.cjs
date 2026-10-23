const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function page(fetch) {
  const handlers = {}, windowHandlers = {};
  const chip = { dataset: { customerOrderStatus: "7" }, textContent: "Собирается" };
  const cancel = { dataset: { customerCancel: "7" }, hidden: false };
  const root = {
    dataset: { customerOrdersLive: "/account/orders/live/" },
    querySelectorAll: selector => selector === "[data-customer-order-status]" ? [chip] : [cancel],
  };
  const document = {
    hidden: false, querySelector: () => root,
    addEventListener: (name, handler) => { handlers[name] = handler; },
  };
  let poll, timeout, cleared = false, navigationCalls = 0;
  const location = { reload: () => { navigationCalls++; }, assign: () => { navigationCalls++; } };
  const window = {
    location, scrollTo: () => { navigationCalls++; },
    addEventListener: (name, handler) => { windowHandlers[name] = handler; },
  };
  const sandbox = {
    document, window, fetch, AbortController,
    setInterval: (callback, delay) => { assert.equal(delay, 5000); poll = callback; return 1; },
    clearInterval: () => { cleared = true; },
    setTimeout: callback => { timeout = callback; return 2; }, clearTimeout: () => {},
    location,
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../static/market/customer-orders.js"), "utf8"), sandbox);
  return { document, chip, cancel, handlers, windowHandlers, tick: () => poll(), expire: () => timeout(), stopped: () => cleared, navigationCalls: () => navigationCalls };
}

const response = (label = "Готов", can_cancel = false) => ({
  ok: true, status: 200, redirected: false,
  json: async () => ({ parts: [{ id: 7, status: "ready", label, can_cancel }] }),
});

test("poll updates text and cancellation in place without reload or scrolling", async () => {
  const p = page(async (url, options) => {
    assert.equal(url, "/account/orders/live/");
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
    return response();
  });
  const sameChip = p.chip;
  await p.tick();
  assert.equal(p.chip, sameChip);
  assert.equal(p.chip.textContent, "Готов");
  assert.equal(p.cancel.hidden, true);
  assert.equal(p.navigationCalls(), 0);
});

test("hidden tabs skip requests and check immediately when visible", async () => {
  let calls = 0;
  const p = page(async () => { calls++; return response(); });
  p.document.hidden = true;
  await p.tick();
  assert.equal(calls, 0);
  p.document.hidden = false;
  await p.handlers.visibilitychange();
  assert.equal(calls, 1);
});

test("concurrent polls do not overlap and transient failures retry", async () => {
  let resolve, calls = 0;
  const p = page(async () => { calls++; if (calls === 1) return new Promise(done => { resolve = done; }); return response("Доставлен"); });
  const first = p.tick();
  await p.tick();
  assert.equal(calls, 1);
  resolve({ ok: false, status: 503 });
  await first;
  assert.equal(p.chip.textContent, "Собирается");
  await p.tick();
  assert.equal(p.chip.textContent, "Доставлен");
});

test("timeout aborts a stuck request and a later poll can retry", async () => {
  let calls = 0;
  const p = page(async (_url, options) => {
    calls++;
    if (calls > 1) return response();
    return new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(Error("aborted"))));
  });
  const first = p.tick();
  p.expire();
  await first;
  await p.tick();
  assert.equal(p.chip.textContent, "Готов");
});

test("expired login stops polling while preserving current page", async () => {
  const p = page(async () => ({ ok: true, redirected: true }));
  await p.tick();
  assert.equal(p.stopped(), true);
  assert.equal(p.chip.textContent, "Собирается");
});

test("pagehide stops polling and pageshow resumes after browser back", async () => {
  let calls = 0;
  const p = page(async () => { calls++; return response(); });
  p.windowHandlers.pagehide();
  assert.equal(p.stopped(), true);
  await p.windowHandlers.pageshow();
  assert.equal(calls, 1);
});
