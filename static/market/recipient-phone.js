"use strict";

document.querySelectorAll("[data-recipient-phone]").forEach((input) => {
  const digits = (value) => value.replace(/\D/g, "");
  const national = (value) => {
    let number = digits(value);
    if (value.startsWith("+") || (number.length === 11 && /^[78]/.test(number))) number = number.slice(1);
    return number.slice(0, 10);
  };
  const format = (number) => {
    if (!number) return "";
    let value = "+7(" + number.slice(0, 3);
    if (number.length >= 3) value += ")";
    if (number.length > 3) value += number.slice(3, 6);
    if (number.length > 6) value += "-" + number.slice(6, 8);
    if (number.length > 8) value += "-" + number.slice(8, 10);
    return value;
  };
  const caret = (count) => {
    let seen = -1;
    for (let position = 0; position < input.value.length; position += 1) {
      if (/\d/.test(input.value[position])) seen += 1;
      if (seen === count && count > 0) return position + 1;
    }
    return count === 0 ? Math.min(3, input.value.length) : input.value.length;
  };
  const update = () => {
    const raw = input.value;
    const position = input.selectionStart ?? raw.length;
    let count = digits(raw.slice(0, position)).length;
    if (raw.startsWith("+") || (digits(raw).length === 11 && /^[78]/.test(raw))) count = Math.max(0, count - 1);
    if (/^[78]$/.test(raw)) {
      input.value = "+7";
    } else {
      input.value = format(national(raw));
    }
    input.setSelectionRange(caret(count), caret(count));
  };
  input.addEventListener("input", update);
  input.addEventListener("keydown", (event) => {
    if (!["Backspace", "Delete"].includes(event.key) || input.selectionStart !== input.selectionEnd) return;
    const position = input.selectionStart;
    const adjacent = event.key === "Backspace" ? position - 1 : position;
    if (adjacent < 0 || adjacent >= input.value.length || /\d/.test(input.value[adjacent])) return;
    const count = Math.max(0, digits(input.value.slice(0, position)).length - 1);
    const index = event.key === "Backspace" ? count - 1 : count;
    if (index < 0) return;
    event.preventDefault();
    const number = national(input.value);
    input.value = format(number.slice(0, index) + number.slice(index + 1));
    input.setSelectionRange(caret(index), caret(index));
  });
  if (input.value) update();
});
