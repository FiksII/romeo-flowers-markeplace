// T-Bank SpeedPay buttons on the order page:
// https://developer.tbank.ru/eacq/intro/developer/setup_js/setup_speedpay
(function () {
  const card = document.querySelector("[data-tbank-payment]");
  if (!card) return;
  const container = card.querySelector("#paymentContainer");
  const loading = card.querySelector("[data-pay-loading]");
  const errorBox = card.querySelector("[data-pay-error]");
  const csrf = card.querySelector("[name=csrfmiddlewaretoken]").value;

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

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = !message;
  }

  // The bank's status can be ahead of its notification to the server; ask the server
  // to check it and reload once the order is paid.
  function checkPaid(attempt) {
    post(card.dataset.statusUrl)
      .then((data) => {
        if (!data.waiting) window.location.reload();
        else if (attempt < 10) setTimeout(() => checkPaid(attempt + 1), 3000);
      })
      .catch(() => {});
  }

  if (card.querySelector("[data-pay-progress]")) checkPaid(0);

  window.onTbankIntegrationLoad = function () {
    PaymentIntegration.init({
      terminalKey: card.dataset.terminalKey,
      product: "eacq",
      features: {
        payment: {
          container: container,
          paymentStartCallback: async () => {
            showError("");
            const data = await post(card.dataset.payUrl);
            return data.paymentUrl;
          },
          config: {
            loadedCallback: () => {
              if (loading) loading.hidden = true;
            },
            status: {
              changedCallback: async (status) => {
                if (status === "SUCCESS") checkPaid(0);
              },
            },
            payment: {
              failedPaymentStartCallback: async (error) => {
                showError((error && error.message) || "Не удалось начать оплату.");
              },
            },
          },
        },
      },
    }).catch(() => {
      if (loading) loading.hidden = true;
      showError("Не удалось загрузить способы оплаты. Обновите страницу.");
    });
  };

  const script = document.createElement("script");
  script.src = "https://integrationjs.t-static.ru/integration.js";
  script.async = true;
  script.onload = window.onTbankIntegrationLoad;
  script.onerror = () => {
    if (loading) loading.hidden = true;
    showError("Не удалось загрузить способы оплаты. Обновите страницу.");
  };
  document.body.appendChild(script);
})();
