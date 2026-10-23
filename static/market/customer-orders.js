// Customer fulfillment updates. Keep the document, forms, focus and scroll intact.
(() => {
  const root = document.querySelector("[data-customer-orders-live]");
  if (!root) return;
  const PERIOD = 5000;
  let timer = null, controller = null, busy = false, signedOut = false, active = true;

  function stop() {
    clearInterval(timer);
    timer = null;
    controller?.abort();
  }

  async function poll() {
    if (busy || document.hidden || signedOut || !active) return;
    busy = true;
    const request = new AbortController();
    controller = request;
    const timeout = setTimeout(() => request.abort(), 10000);
    try {
      const response = await fetch(root.dataset.customerOrdersLive, {
        credentials: "same-origin", cache: "no-store",
        headers: { Accept: "application/json" }, signal: request.signal,
      });
      if (response.redirected || response.status === 401 || response.status === 403) {
        signedOut = true;
        stop();
        return;
      }
      if (!response.ok) return;
      const data = await response.json();
      if (request.signal.aborted || !active || !Array.isArray(data.parts)) return;
      const parts = new Map(data.parts.map(part => [String(part.id), part]));
      root.querySelectorAll("[data-customer-order-status]").forEach(chip => {
        const part = parts.get(chip.dataset.customerOrderStatus);
        if (part && typeof part.label === "string" && chip.textContent !== part.label) {
          chip.textContent = part.label;
        }
      });
      root.querySelectorAll("[data-customer-cancel]").forEach(form => {
        const part = parts.get(form.dataset.customerCancel);
        if (part) form.hidden = part.can_cancel !== true;
      });
    } catch (error) {
      // Offline/timeouts retain the last known state and retry on the next tick.
    } finally {
      clearTimeout(timeout);
      if (controller === request) controller = null;
      busy = false;
    }
  }

  function start() {
    if (!timer && !signedOut && active && !document.hidden) timer = setInterval(poll, PERIOD);
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
    else { start(); return poll(); }
  });
  window.addEventListener("pagehide", () => { active = false; stop(); });
  window.addEventListener("pageshow", () => { active = true; start(); return poll(); });
  start();
})();
