document.querySelectorAll('[data-referral-card]').forEach(card => {
  const input = card.querySelector('[data-referral-link]');
  const button = card.querySelector('[data-referral-copy]');
  const status = card.querySelector('[data-referral-status]');
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(input.value);
      status.textContent = 'Ссылка скопирована';
    } catch {
      input.focus();
      input.select();
      status.textContent = 'Ссылка выделена. Скопируйте её вручную.';
    }
  });
});
