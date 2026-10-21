"use strict";

(() => {
  let pending = false;
  let noticeTimer;
  function notify(message, error = false) {
    let notice = document.querySelector(".basket-notice");
    if (!notice) {
      notice = document.createElement("div");
      notice.className = "basket-notice";
      notice.setAttribute("role", "status");
      notice.setAttribute("aria-live", "polite");
      document.body.append(notice);
    }
    clearTimeout(noticeTimer);
    notice.textContent = message;
    notice.classList.toggle("basket-notice-error", error);
    notice.hidden = false;
    noticeTimer = setTimeout(() => { notice.hidden = true; }, error ? 7000 : 3000);
  }

  function celebrate(form) {
    const links = Array.from(document.querySelectorAll(".basket-link"));
    const target = links.find((link) => {
      const rect = link.getBoundingClientRect();
      return rect.width && rect.height && rect.top >= 0 && rect.bottom <= innerHeight;
    });
    if (!target || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    target.animate?.([{ transform: "scale(1)" }, { transform: "scale(1.2)" }, { transform: "scale(1)" }], { duration: 400, delay: 450 });
    const image = form.closest(".product-card, .product-detail")?.querySelector("img");
    if (!image || !image.animate) return;
    const start = image.getBoundingClientRect();
    const end = target.getBoundingClientRect();
    const flyer = document.createElement("img");
    flyer.src = image.currentSrc || image.src;
    flyer.alt = "";
    flyer.className = "basket-flyer";
    flyer.style.left = `${Math.max(0, Math.min(innerWidth - 64, start.left + start.width / 2 - 32))}px`;
    flyer.style.top = `${Math.max(0, Math.min(innerHeight - 64, start.top + start.height / 2 - 32))}px`;
    document.body.append(flyer);
    const origin = flyer.getBoundingClientRect();
    const dx = end.left + end.width / 2 - origin.left - 32;
    const dy = end.top + end.height / 2 - origin.top - 32;
    const animation = flyer.animate([
      { transform: "translate(0, 0) scale(1)", opacity: 1 },
      { transform: `translate(${dx * 0.5}px, ${dy * 0.5 - 60}px) scale(.8)`, opacity: .9, offset: .5 },
      { transform: `translate(${dx}px, ${dy}px) scale(.15)`, opacity: 0 },
    ], { duration: 650, easing: "ease-in-out" });
    animation.finished.then(() => flyer.remove(), () => flyer.remove());
  }

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-basket-add]");
    if (!form) return;
    event.preventDefault();
    if (pending) return;
    pending = true;
    const button = form.querySelector('button[type="submit"], button:not([type])');
    if (button) button.disabled = true;
    form.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(form.action, {
        method: "POST", body: new FormData(form), credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        notify(data.message || "Не удалось добавить букет. Попробуйте ещё раз.", true);
        return;
      }
      document.querySelectorAll(".basket-link").forEach((link) => {
        let badge = link.querySelector(".basket-count");
        if (!badge) {
          badge = document.createElement("b");
          badge.className = "basket-count";
          link.append(badge);
        }
        badge.textContent = data.count;
        link.setAttribute("aria-label", `Корзина, товаров: ${data.count}`);
      });
      form.closest("dialog")?.close();
      notify("Добавлено в корзину");
      celebrate(form);
    } catch {
      notify("Не удалось подтвердить добавление. Проверьте корзину перед повтором.", true);
    } finally {
      pending = false;
      if (button) button.disabled = false;
      form.removeAttribute("aria-busy");
    }
  });
})();
