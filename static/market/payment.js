// Bank-hosted payment form: Init on our server, then navigate to its PaymentURL.
// https://developer.tbank.ru/eacq/scenarios/payments/nonPCI
// Without JavaScript the POST redirects to the same form.
(function () {
  const card = document.querySelector("[data-tbank-payment]");
  if (!card) return;
  const csrf = card.querySelector("[name=csrfmiddlewaretoken]").value;
  const form = card.querySelector("[data-pay-form]");
  const errorBox = card.querySelector("[data-pay-error]");
  const DECLINED = ["REJECTED", "AUTH_FAIL", "CANCELED", "DEADLINE_EXPIRED"];

  function post(url) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrf, Accept: "application/json" },
    }).then((response) =>
      response.json().then((data) => {
        if (!response.ok) throw new Error(data.error || "Не удалось начать оплату.");
        return data;
      }),
    );
  }

  // The form does not always report the result and the bank's notification may be
  // late, so ask the server (it checks with T-Bank) and reload once the payment has
  // finished. Checks stop after about 15 minutes.
  let watching = false;
  function watchPayment() {
    if (watching) return;
    watching = true;
    let left = 180;
    const check = () =>
      post(card.dataset.statusUrl)
        .then((data) => {
          if (!data.waiting) window.location.reload();
          // Leave the bank's own message on screen for a moment.
          else if (DECLINED.includes(data.status)) setTimeout(() => window.location.reload(), 4000);
          else if (--left > 0) setTimeout(check, 5000);
        })
        .catch(() => {
          if (--left > 0) setTimeout(check, 5000);
        });
    setTimeout(check, 3000);
  }

  if (card.hasAttribute("data-pay-watch")) watchPayment();
  if (!form) return;
  const button = form.querySelector("button");
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      button.disabled = false;
      watchPayment();
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    errorBox.hidden = true;
    try {
      const data = await post(form.action);
      window.location.assign(data.paymentUrl);
    } catch (error) {
      button.disabled = false;
      errorBox.textContent = (error && error.message) || "Не удалось открыть форму оплаты.";
      errorBox.hidden = false;
    }
  });
})();
