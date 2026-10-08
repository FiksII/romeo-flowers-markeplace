"use strict";

// Same slide, dot and swipe interaction as the shop storefront carousel.
document.querySelectorAll("[data-carousel]").forEach((carousel) => {
  const slides = [...carousel.querySelectorAll("[data-carousel-slide]")];
  const dots = [...carousel.querySelectorAll("[data-carousel-dot]")];
  const track = carousel.querySelector("[data-carousel-track]");
  const pause = carousel.querySelector("[data-carousel-pause]");
  if (slides.length < 2) {
    carousel.querySelector(".hero-controls").hidden = true;
    return;
  }
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let index = 0, timer, paused = false, hovered = false, start = null;
  const show = (next) => {
    index = (next + slides.length) % slides.length;
    slides.forEach((slide, i) => {
      slide.hidden = false;
      slide.classList.toggle("is-active", i === index);
      slide.setAttribute("aria-hidden", String(i !== index));
      slide.inert = i !== index;
    });
    dots.forEach((dot, i) => {
      dot.classList.toggle("is-active", i === index);
      dot.setAttribute("aria-pressed", String(i === index));
    });
  };
  const schedule = () => {
    clearInterval(timer);
    if (paused || hovered || document.hidden || motion.matches || carousel.contains(document.activeElement)) return;
    timer = setInterval(() => show(index + 1), 6500);
  };
  dots.forEach((dot, i) => dot.addEventListener("click", () => { show(i); schedule(); }));
  pause.addEventListener("click", () => {
    paused = !paused;
    pause.textContent = paused ? "Продолжить" : "Пауза";
    pause.setAttribute("aria-label", paused ? "Продолжить смену слайдов" : "Приостановить смену слайдов");
    schedule();
  });
  track.addEventListener("pointerdown", (event) => {
    if (event.isPrimary && event.button === 0) start = { x: event.clientX, y: event.clientY };
  });
  track.addEventListener("pointerup", (event) => {
    if (!start) return;
    const dx = event.clientX - start.x, dy = event.clientY - start.y;
    start = null;
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) { show(dx < 0 ? index + 1 : index - 1); schedule(); }
  });
  track.addEventListener("pointercancel", () => { start = null; });
  carousel.addEventListener("mouseenter", () => { hovered = true; schedule(); });
  carousel.addEventListener("mouseleave", () => { hovered = false; start = null; schedule(); });
  carousel.addEventListener("focusin", () => clearInterval(timer));
  carousel.addEventListener("focusout", () => setTimeout(schedule, 0));
  document.addEventListener("visibilitychange", schedule);
  motion.addEventListener("change", schedule);
  show(0);
  schedule();
});
