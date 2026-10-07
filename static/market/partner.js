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
