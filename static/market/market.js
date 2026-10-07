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

// Catalogue filters on the home page. Every change refreshes the results in place: the page is
// fetched again and the regions marked data-live-region are swapped, so an open menu stays open
// while several flowers are picked. Without JavaScript the form and links still work.
const catalogue = document.querySelector("[data-catalogue-live]");
if (catalogue) {
  const form = catalogue.querySelector("[data-filter-form]");
  const phone = window.matchMedia("(max-width: 900px)");
  let controller, typingTimer;
  const openPop = () => catalogue.querySelector("details.pop[open]");
  const syncScrollLock = () => document.body.classList.toggle("has-dialog", phone.matches && Boolean(openPop()));
  const closePops = (except) => {
    catalogue.querySelectorAll("details.pop[open]").forEach((pop) => { if (pop !== except) pop.open = false; });
    syncScrollLock();
  };
  const formUrl = () => {
    const params = new URLSearchParams();
    for (const [name, value] of new FormData(form)) if (value !== "") params.append(name, value);
    const query = params.toString();
    return query ? `${location.pathname}?${query}` : location.pathname;
  };
  const filterTags = (input) => {
    const term = input.value.trim().toLocaleLowerCase("ru"), menu = input.closest(".menu");
    let shown = 0;
    menu.querySelectorAll(".filter-tag").forEach((tag) => {
      const match = tag.dataset.flowerName.toLocaleLowerCase("ru").includes(term);
      tag.hidden = !match;
      if (match) shown += 1;
    });
    menu.querySelector("[data-flower-empty]").hidden = shown > 0;
  };
  const refresh = async (url, { keepOpen = false, scroll = false } = {}) => {
    controller?.abort();
    controller = new AbortController();
    const reopen = keepOpen ? openPop()?.dataset.pop : null;
    const tagScroll = catalogue.querySelector(".tags")?.scrollTop ?? 0;
    const search = catalogue.querySelector("[data-flower-search]");
    const searchText = search?.value ?? "", searchHadFocus = Boolean(search) && document.activeElement === search;
    catalogue.classList.add("is-loading");
    try {
      const response = await fetch(url, { signal: controller.signal, headers: { "X-Requested-With": "fetch" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const next = new DOMParser().parseFromString(await response.text(), "text/html").querySelector("[data-catalogue-live]");
      if (!next) throw new Error("catalogue not found");
      catalogue.querySelectorAll("[data-live-region]").forEach((region) => {
        const fresh = next.querySelector(`[data-live-region="${region.dataset.liveRegion}"]`);
        if (fresh) region.innerHTML = fresh.innerHTML;
      });
      if (reopen) {
        const pop = catalogue.querySelector(`details.pop[data-pop="${reopen}"]`);
        if (pop) {
          pop.open = true;
          const tags = pop.querySelector(".tags"), freshSearch = pop.querySelector("[data-flower-search]");
          if (tags) tags.scrollTop = tagScroll;
          if (freshSearch && searchText) { freshSearch.value = searchText; filterTags(freshSearch); }
          if (freshSearch && searchHadFocus) freshSearch.focus({ preventScroll: true });
        }
      }
      syncScrollLock();
      history.replaceState(null, "", url);
      if (scroll) catalogue.scrollIntoView({ block: "start", behavior: "smooth" });
    } catch (error) {
      if (error.name !== "AbortError") location.assign(url);
    } finally {
      catalogue.classList.remove("is-loading");
    }
  };

  catalogue.addEventListener("change", (event) => {
    if (event.target.matches('input[name="category"]')) { closePops(); refresh(formUrl()); }
    else if (event.target.matches('input[name="flower"]')) refresh(formUrl(), { keepOpen: true });
  });
  form.addEventListener("input", (event) => {
    if (!event.target.matches("#filter-q")) return;
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => refresh(formUrl()), 350);
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    clearTimeout(typingTimer);
    closePops();
    refresh(formUrl());
  });
  catalogue.addEventListener("input", (event) => { if (event.target.matches("[data-flower-search]")) filterTags(event.target); });
  catalogue.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-live-link]");
    if (link && event.button === 0 && !(event.metaKey || event.ctrlKey || event.shiftKey)) {
      event.preventDefault();
      const keepOpen = link.hasAttribute("data-keep-open");
      if (!keepOpen) closePops();
      refresh(link.href, { keepOpen, scroll: link.hasAttribute("data-scroll") });
    } else if (event.target.closest("[data-close-pop]")) {
      closePops();
    }
  });
  // one menu at a time (also where <details name> is not supported), a click outside or Escape closes it
  catalogue.addEventListener("toggle", (event) => { if (event.target.open) closePops(event.target); syncScrollLock(); }, true);
  document.addEventListener("click", (event) => {
    const pop = openPop();
    if (pop && !pop.contains(event.target)) closePops();
  });
  document.addEventListener("keydown", (event) => {
    const pop = openPop();
    if (event.key === "Escape" && pop) { pop.open = false; pop.querySelector("summary")?.focus(); syncScrollLock(); }
  });
  phone.addEventListener("change", () => closePops());
}
