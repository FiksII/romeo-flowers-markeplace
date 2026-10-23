"use strict";

document.querySelectorAll("[data-bouquet-gallery]").forEach((gallery) => {
  const main = gallery.querySelector("[data-gallery-main]");
  const original = gallery.querySelector("[data-gallery-original]");
  const counter = gallery.querySelector("[data-gallery-counter]");
  const thumbnails = [...gallery.querySelectorAll("[data-gallery-thumbnail]")];
  if (!main || thumbnails.length < 2) return;
  let index = 0, start = null, swiped = false;
  const show = (next, focus = false) => {
    index = (next + thumbnails.length) % thumbnails.length;
    const selected = thumbnails[index];
    main.srcset = selected.dataset.srcset || "";
    main.src = selected.dataset.src;
    original.href = selected.href;
    thumbnails.forEach((thumbnail, i) => {
      if (i === index) thumbnail.setAttribute("aria-current", "true");
      else thumbnail.removeAttribute("aria-current");
    });
    counter.textContent = `Фото ${index + 1} из ${thumbnails.length}`;
    if (focus) selected.focus();
    selected.scrollIntoView({ block: "nearest", inline: "nearest" });
  };
  thumbnails.forEach((thumbnail, i) => {
    thumbnail.addEventListener("click", (event) => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      show(i);
    });
    thumbnail.addEventListener("keydown", (event) => {
      const next = { ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: thumbnails.length - 1 }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      show(next, true);
    });
  });
  original.addEventListener("pointerdown", (event) => {
    swiped = false;
    if (event.isPrimary && event.button === 0) start = { x: event.clientX, y: event.clientY };
  });
  original.addEventListener("pointerup", (event) => {
    if (!start) return;
    const dx = event.clientX - start.x, dy = event.clientY - start.y;
    start = null;
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) {
      swiped = true;
      show(index + (dx < 0 ? 1 : -1));
    }
  });
  original.addEventListener("pointercancel", () => { start = null; });
  original.addEventListener("click", (event) => {
    if (swiped) { event.preventDefault(); swiped = false; }
  });
});

document.querySelectorAll("[data-photo-editor]").forEach((editor) => {
  const input = editor.querySelector("[data-photo-upload]");
  const previews = editor.querySelector("[data-photo-previews]");
  const status = editor.querySelector("[data-photo-status]");
  let urls = [], selectedFile = null;
  const release = () => { urls.forEach(url => URL.revokeObjectURL(url)); urls = []; };
  const ensurePrimary = () => {
    if (editor.querySelector("[data-photo-primary]:checked")) return;
    const next = [...editor.querySelectorAll("[data-photo-primary]")].find(radio =>
      !radio.closest(".bouquet-photo-tile").querySelector("[data-photo-remove]:checked"));
    if (next) next.checked = true;
  };
  const updateStatus = () => {
    const files = [...input.files];
    const removed = editor.querySelectorAll('input[name="remove_photos"]:checked').length;
    const total = Number(input.dataset.photoCount) - removed + files.length;
    let error = total > 20 ? "Можно сохранить не больше 20 фотографий букета." : "";
    if (files.some(file => file.size > 5 * 1024 * 1024)) error = "Каждая фотография должна быть не больше 5 МБ.";
    input.setCustomValidity(error);
    status.textContent = error || `Новых фото: ${files.length}. Всего после сохранения: ${total} из 20.`;
  };
  input.addEventListener("change", () => {
    release();
    previews.replaceChildren();
    const files = [...input.files];
    files.forEach((file, index) => {
      const tile = document.createElement("div");
      tile.className = "bouquet-photo-tile";
      const frame = document.createElement("div");
      frame.className = "photo-image-frame";
      const image = document.createElement("img");
      image.src = URL.createObjectURL(file);
      urls.push(image.src);
      image.alt = `Новое фото ${index + 1}`;
      image.width = 120; image.height = 150;
      const star = document.createElement("label");
      star.className = "photo-action photo-primary-toggle";
      star.title = `Сделать новое фото ${index + 1} главным`;
      const radio = document.createElement("input");
      radio.type = "radio"; radio.name = "primary_photo"; radio.value = `new:${index}`;
      radio.className = "visually-hidden"; radio.dataset.photoPrimary = "";
      radio.setAttribute("aria-label", star.title);
      radio.checked = selectedFile === file;
      star.append(radio);
      ["empty", "filled"].forEach((state, i) => {
        const icon = document.createElement("span");
        icon.className = `photo-star-${state}`; icon.textContent = i ? "★" : "☆";
        icon.setAttribute("aria-hidden", "true"); star.append(icon);
      });
      const remove = document.createElement("button");
      remove.type = "button"; remove.className = "photo-action photo-delete-new";
      remove.textContent = "×"; remove.setAttribute("aria-label", `Убрать новое фото ${index + 1}`);
      remove.addEventListener("click", () => {
        if (selectedFile === file) selectedFile = null;
        const transfer = new DataTransfer();
        files.filter((_, i) => i !== index).forEach(kept => transfer.items.add(kept));
        input.files = transfer.files;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      });
      const badge = document.createElement("span");
      badge.className = "photo-main-badge"; badge.textContent = "Главное";
      frame.append(image, star, remove, badge); tile.append(frame); previews.append(tile);
    });
    ensurePrimary();
    updateStatus();
  });
  editor.addEventListener("change", (event) => {
    const control = event.target;
    if (control.matches("[data-photo-primary]") && control.checked) {
      const remove = control.closest(".bouquet-photo-tile").querySelector("[data-photo-remove]");
      if (remove) remove.checked = false;
      selectedFile = control.value.startsWith("new:") ? input.files[Number(control.value.split(":")[1])] : null;
    }
    if (control.matches("[data-photo-remove]") && control.checked) {
      const primary = control.closest(".bouquet-photo-tile").querySelector("[data-photo-primary]");
      primary.checked = false;
    }
    ensurePrimary();
    updateStatus();
  });
  window.addEventListener("pagehide", release, { once: true });
});
