const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

test("operator board refreshes for address and status revisions without new orders", async () => {
  let poll;
  let revision = "first";
  let refreshes = 0;
  const board = { replaceWith: () => { refreshes++; } };
  const nav = { dataset: { partnerLive: "/operator/live/", ordersUrl: "/operator/orders/", newOrders: "0", latestOrder: "0", payouts: "0" }, querySelector: () => null };
  const document = {
    title: "Заказы", hidden: false,
    querySelectorAll: () => [], addEventListener: () => {},
    querySelector: (selector) => selector === "[data-partner-live]" ? nav : selector === "[data-order-board]" ? board : selector === ".toast-stack" ? {} : null,
  };
  const sandbox = {
    document, window: { location: { href: "https://flowers.test/operator/orders/" } },
    setInterval: (callback) => { poll = callback; return 1; }, setTimeout: () => {},
    DOMParser: class { parseFromString() { return { querySelector: () => ({}) }; } },
    fetch: async (url) => url === "/operator/live/"
      ? { ok: true, json: async () => ({ new_orders: 0, latest_order: 0, payouts: 0, revision }) }
      : { ok: true, text: async () => "<div data-order-board></div>" },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../static/market/partner.js"), "utf8"), sandbox);
  assert.equal(typeof poll, "function");
  await poll();
  assert.equal(refreshes, 1);
  await poll();
  assert.equal(refreshes, 1);
  revision = "address-updated";
  await poll();
  assert.equal(refreshes, 2);
  revision = "delivery-completed";
  await poll();
  assert.equal(refreshes, 3);
});
