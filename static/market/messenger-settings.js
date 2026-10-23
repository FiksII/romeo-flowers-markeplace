"use strict";

document.querySelectorAll("[data-messenger-test]").forEach((form) => {
  const button = form.querySelector('[value="test"]');
  const result = form.querySelector("[data-messenger-result]");
  let busy = false;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    button.disabled = true;
    result.hidden = false;
    result.textContent = "Проверяем подключение…";
    try {
      const data = new FormData(form);
      data.set("action", "test");
      const response = await fetch(form.action, {
        method: "POST", body: data, credentials: "same-origin", mode: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (response.redirected) throw new Error("Войдите в кабинет заново и повторите проверку.");
      const feedback = await response.json();
      result.textContent = feedback.message || "Не удалось выполнить проверку. Попробуйте ещё раз.";
    } catch (error) {
      result.textContent = "Не удалось выполнить проверку. Попробуйте ещё раз.";
    } finally {
      busy = false;
      button.disabled = false;
    }
  });
});
