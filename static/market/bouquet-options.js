"use strict";

document.querySelectorAll("[data-product-form]").forEach((form) => {
  const selected = () => new Set(Array.from(form.querySelectorAll('[name="sizes"]:checked'), (input) => input.value));
  const update = () => {
    const sizes = selected();
    form.querySelectorAll("[data-size-price]").forEach((input) => {
      const enabled = sizes.has(input.dataset.sizePrice);
      input.closest(".field").hidden = !enabled;
      input.disabled = !enabled;
      input.required = enabled;
    });
  };
  form.querySelectorAll('[name="sizes"]').forEach((input) => input.addEventListener("change", update));
  update();
});

document.addEventListener("click", (event) => {
  const open = event.target.closest("[data-size-dialog-open]");
  if (open) {
    const dialog = document.getElementById(open.dataset.sizeDialogOpen);
    if (dialog && typeof dialog.showModal === "function") {
      event.preventDefault();
      dialog.showModal();
    }
  }
  const close = event.target.closest("[data-size-dialog-close]");
  if (close) close.closest("dialog").close();
});

document.querySelectorAll("[data-size-picker]").forEach((picker) => {
  const price = picker.closest("[data-product-detail]")?.querySelector(".detail-price");
  if (!price) return;
  const update = () => {
    const chosen = picker.querySelector("input:checked");
    if (chosen) price.textContent = chosen.closest("label").querySelector("strong").textContent;
  };
  picker.addEventListener("change", update);
  update();
});
