"use strict";

// Product form: «Всегда в наличии» replaces the quantity.
document.querySelectorAll("[data-product-form]").forEach((form) => {
  const always = form.querySelector("[data-stock-always]");
  const count = form.querySelector("[data-stock-count]");
  if (!always || !count) return;
  const field = count.closest(".field");
  const update = () => {
    count.disabled = always.checked;
    count.required = !always.checked;
    if (field) field.hidden = always.checked;
  };
  always.addEventListener("change", update);
  update();
});

// Weekly schedule grid: shortcuts that copy times between days and columns.
document.querySelectorAll("[data-hours-table]").forEach((table) => {
  const tools = document.querySelector("[data-hours-tools]");
  if (!tools) return;
  tools.hidden = false;
  const cell = (day, method) => table.querySelector(`tr[data-day="${day}"] td[data-method="${method}"]`);
  const inputs = (td) => td.querySelectorAll("input");
  const copy = (from, to) => {
    const source = [...inputs(from)].map((input) => input.value);
    inputs(to).forEach((input, index) => { input.value = source[index]; });
  };
  tools.querySelector("[data-copy-monday]")?.addEventListener("click", () => {
    ["work", "delivery", "pickup"].forEach((method) => {
      const monday = cell(0, method);
      for (let day = 1; day < 7; day += 1) copy(monday, cell(day, method));
    });
  });
  tools.querySelector("[data-copy-work]")?.addEventListener("click", () => {
    for (let day = 0; day < 7; day += 1) {
      const work = cell(day, "work");
      ["delivery", "pickup"].forEach((method) => copy(work, cell(day, method)));
    }
  });
});

// Settings: copy an invitation link.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const input = document.getElementById(button.dataset.copy);
  if (!input) return;
  input.select();
  const done = () => {
    const label = button.textContent;
    button.textContent = "Скопировано";
    setTimeout(() => { button.textContent = label; }, 1500);
  };
  if (navigator.clipboard) navigator.clipboard.writeText(input.value).then(done, () => document.execCommand("copy") && done());
  else if (document.execCommand("copy")) done();
});

document.addEventListener("submit", event => {
  const form = event.target.closest("form[data-confirm]");
  if (form && !window.confirm(form.dataset.confirm)) event.preventDefault();
});


// Live cabinet: poll for new orders and payouts, update the Orders badge, show a toast,
// refresh the order cards and, when allowed, a system notification.
(() => {
  const nav = document.querySelector("[data-partner-live]");
  if (!nav) return;
  const PERIOD = 15000;
  const baseTitle = document.title.replace(/^\(\d+\)\s*/, "");
  let state = {
    new_orders: Number(nav.dataset.newOrders) || 0,
    latest_order: Number(nav.dataset.latestOrder) || 0,
    payouts: Number(nav.dataset.payouts) || 0,
  };
  let timer = null;
  let busy = false;

  const toasts = (() => {
    let box = document.querySelector(".toast-stack");
    if (!box) {
      box = document.createElement("div");
      box.className = "toast-stack";
      box.setAttribute("role", "status");
      box.setAttribute("aria-live", "polite");
      document.body.append(box);
    }
    return box;
  })();

  function toast(text, href, label) {
    const item = document.createElement("div");
    item.className = "toast";
    const message = document.createElement("span");
    message.textContent = text;
    item.append(message);
    if (href) {
      const link = document.createElement("a");
      link.href = href;
      link.className = "text-link";
      link.textContent = label;
      item.append(link);
    }
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "Закрыть");
    close.textContent = "×";
    close.addEventListener("click", () => item.remove());
    item.append(close);
    toasts.append(item);
    setTimeout(() => item.remove(), 20000);
  }

  function system(text, href) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    try {
      const note = new Notification("Цветы от Ромео", { body: text, tag: "romeo-orders" });
      note.addEventListener("click", () => { window.focus(); window.location.href = href; });
    } catch (error) { /* some browsers only allow notifications from a service worker */ }
  }

  function badge(count) {
    const tab = nav.querySelector("[data-orders-tab]");
    if (!tab) return;
    let mark = tab.querySelector(".nav-badge");
    if (!count) { mark?.remove(); return; }
    if (!mark) {
      mark = document.createElement("span");
      mark.className = "nav-badge";
      tab.append(" ", mark);
    }
    mark.textContent = count;
    mark.setAttribute("aria-label", `Новых заказов: ${count}`);
    document.title = `(${count}) ${baseTitle}`;
  }

  async function refreshBoard() {
    const board = document.querySelector("[data-order-board]");
    if (!board) return;
    const response = await fetch(window.location.href, { credentials: "same-origin" });
    if (!response.ok) return;
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const fresh = page.querySelector("[data-order-board]");
    if (fresh) board.replaceWith(fresh);
  }

  async function poll() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const response = await fetch(nav.dataset.partnerLive, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const next = await response.json();
      if (next.latest_order > state.latest_order) {
        const added = Math.max(1, next.new_orders - state.new_orders);
        const text = added > 1 ? `Новые заказы: ${added}` : "Новый заказ";
        toast(text, nav.dataset.ordersUrl, "Открыть заказы");
        system(text, nav.dataset.ordersUrl);
      }
      if (next.payouts > state.payouts) {
        const text = "Ромео отправил выплату — подтвердите получение";
        toast(text, nav.dataset.balanceUrl, "Открыть баланс");
        system(text, nav.dataset.balanceUrl);
      }
      const changed = next.new_orders !== state.new_orders || next.latest_order !== state.latest_order
        || (next.revision !== undefined && next.revision !== state.revision);
      badge(next.new_orders);
      if (!next.new_orders) document.title = baseTitle;
      state = next;
      if (changed) await refreshBoard();
    } catch (error) {
      /* offline or signed out: try again on the next tick */
    } finally {
      busy = false;
    }
  }

  const start = () => { if (!timer) timer = setInterval(poll, PERIOD); };
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  if (state.new_orders) document.title = `(${state.new_orders}) ${baseTitle}`;
  start();
})();

// Settings: let the seller allow system notifications in this browser.
document.querySelectorAll("[data-browser-notify]").forEach((section) => {
  if (!("Notification" in window)) return;
  section.hidden = false;
  const stateText = section.querySelector("[data-notify-state]");
  const button = section.querySelector("[data-notify-enable]");
  const show = () => {
    const permission = Notification.permission;
    stateText.textContent = permission === "granted"
      ? "Включены в этом браузере."
      : permission === "denied"
        ? "Запрещены в настройках браузера — разрешите уведомления для этого сайта."
        : "Выключены.";
    button.hidden = permission !== "default";
  };
  button.addEventListener("click", async () => { await Notification.requestPermission(); show(); });
  show();
});
