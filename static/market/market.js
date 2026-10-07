"use strict";

document.querySelectorAll("[data-flower-picker]").forEach((picker) => {
  const search = picker.querySelector("[data-flower-search]");
  const available = picker.querySelector("[data-flower-available]");
  const selected = picker.querySelector("[data-flower-selected]");
  const selectedZone = picker.querySelector("[data-flower-selected-zone]");
  const count = picker.querySelector("[data-flower-count]");
  const empty = picker.querySelector("[data-flower-empty]");
  const tags = [...picker.querySelectorAll("[data-flower-tag]")];
  const normalize = (text) => text.toLocaleLowerCase("ru").replaceAll("ё", "е").trim();
  const refresh = () => {
    const focused = document.activeElement;
    const query = normalize(search.value);
    let chosen = 0, matches = 0;
    tags.forEach((tag) => {
      const checked = tag.querySelector('input[type="checkbox"]').checked;
      if (checked) {
        chosen += 1;
        tag.hidden = false;
        selected.append(tag);
      } else {
        tag.hidden = !normalize(tag.dataset.flowerName).includes(query);
        if (!tag.hidden) matches += 1;
        available.append(tag);
      }
    });
    count.textContent = String(chosen);
    selectedZone.hidden = chosen === 0;
    available.hidden = matches === 0;
    empty.hidden = matches > 0;
    empty.textContent = chosen === tags.length ? "Все цветы выбраны." : "Цветы не найдены. Попробуйте другое название.";
    if (picker.contains(focused) && document.activeElement !== focused) focused.focus({ preventScroll: true });
  };
  picker.querySelector("[data-flower-search-zone]").hidden = false;
  picker.addEventListener("change", (event) => {
    if (event.target.matches('input[type="checkbox"]')) refresh();
  });
  search.addEventListener("input", refresh);
  picker.closest("form")?.addEventListener("reset", () => setTimeout(refresh, 0));
  refresh();
});

document.querySelectorAll("[data-address-form]").forEach((form) => {
  const input = form.querySelector("[data-address-input]");
  const token = form.querySelector('[name="address_token"]');
  const results = form.querySelector(".address-results");
  const status = form.querySelector(".address-status");
  const city = form.querySelector('[name="city"]');
  if (!input || !token || !results) return;
  let timer, controller, sequence = 0;
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-autocomplete", "list");
  const close = () => { results.hidden = true; input.setAttribute("aria-expanded", "false"); };
  input.addEventListener("input", () => {
    token.value = "";
    close();
    clearTimeout(timer);
    if (controller) controller.abort();
    const current = ++sequence;
    if (input.value.trim().length < 2) return;
    timer = setTimeout(async () => {
      controller = new AbortController();
      status.textContent = "Ищем адрес…";
      try {
        const response = await fetch(`/addresses/?q=${encodeURIComponent(input.value.trim())}`, { signal: controller.signal, headers: { Accept: "application/json" } });
        const data = await response.json();
        if (current !== sequence) return;
        results.replaceChildren();
        (data.results || []).forEach((row) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = row.value;
          button.setAttribute("role", "option");
          button.addEventListener("click", () => {
            input.value = row.value;
            token.value = row.token;
            if (city) city.value = "";
            status.textContent = "Адрес выбран. Примените условия получения.";
            close();
            input.focus();
          });
          results.append(button);
        });
        results.hidden = !results.children.length;
        input.setAttribute("aria-expanded", String(!results.hidden));
        status.textContent = results.hidden ? (data.message || "Уточните адрес до дома.") : "Выберите адрес из списка.";
      } catch (error) {
        if (error.name !== "AbortError") status.textContent = "Подсказки сейчас недоступны. Можно выбрать город и продолжить просмотр.";
      }
    }, 280);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
    if (event.key === "ArrowDown" && !results.hidden) { event.preventDefault(); results.firstElementChild?.focus(); }
  });
  results.addEventListener("keydown", (event) => {
    const buttons = [...results.children], index = buttons.indexOf(document.activeElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); buttons[(index + (event.key === "ArrowDown" ? 1 : buttons.length - 1)) % buttons.length]?.focus(); }
    if (event.key === "Escape") { close(); input.focus(); }
  });
  document.addEventListener("click", (event) => { if (!form.contains(event.target)) close(); });
  city?.addEventListener("change", () => { if (city.value) { token.value = ""; input.value = ""; close(); } });
});

document.querySelectorAll(".receiving-form").forEach((form) => {
  const when = form.querySelector('[name="when"]'), dateField = form.querySelector("[data-date-field]");
  const update = () => { dateField.hidden = when.value !== "date"; };
  when.addEventListener("change", update);
  update();
});

const checkout = document.querySelector("[data-checkout]");
if (checkout) {
  const groups = [...checkout.querySelectorAll("[data-checkout-group]")];
  const money = (value) => new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 }).format(value);
  const totals = () => {
    let goods = 0, shipping = 0;
    groups.forEach((group) => {
      const selected = group.querySelector("[data-method]").selectedOptions[0];
      const amount = Math.round(Number(selected?.dataset.goods ?? group.dataset.goods) * 100);
      goods += amount;
      shipping += Math.round(Number(selected?.dataset.fee || 0) * 100);
      group.querySelector("[data-group-goods]").textContent = money(amount / 100);
    });
    checkout.querySelector("[data-goods-total]").textContent = money(goods / 100);
    checkout.querySelector("[data-shipping-total]").textContent = money(shipping / 100);
    checkout.querySelector("[data-order-total]").textContent = money((goods + shipping) / 100);
  };
  groups.forEach((group) => {
    const method = group.querySelector("[data-method]"), slot = group.querySelector("[data-slot]");
    const update = (resetSlot = false) => {
      if (resetSlot) slot.value = "";
      slot.querySelectorAll("optgroup").forEach((options) => { options.disabled = options.dataset.slotMethod !== method.value; options.hidden = options.disabled; });
      group.querySelector("[data-pickup-note]").hidden = method.value !== "pickup";
      group.querySelector("[data-delivery-note]").hidden = method.value !== "delivery";
      totals();
    };
    method.addEventListener("change", () => update(true));
    update();
  });
}

// The header place chip links to #receiving: open the collapsed panel and bring it into view.
const openReceivingPanel = () => {
  if (location.hash !== "#receiving") return;
  const panel = document.getElementById("receiving");
  if (!panel) return;
  panel.open = true;
  panel.scrollIntoView({ block: "start" });
};
window.addEventListener("hashchange", openReceivingPanel);
openReceivingPanel();

// On phones the catalogue filters start collapsed (unless some are applied) so the bouquets come first.
document.querySelectorAll("[data-filter-details]").forEach((details) => {
  const applied = [...new URLSearchParams(location.search).keys()].some((key) => key !== "page");
  if (window.matchMedia("(max-width: 759px)").matches && !applied) details.open = false;
});
