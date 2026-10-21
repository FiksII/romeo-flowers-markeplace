from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

TAGS = [
    ("march8", "8 марта"),
    ("feb23", "23 февраля"),
    ("birthday", "На день рождения"),
    ("funeral", "На похороны"),
    ("wedding", "На свадьбу"),
    ("man", "Мужчине"),
    ("woman", "Женщине"),
]
SIZES = [("standard", "Стандартный"), ("s", "S"), ("m", "M"), ("l", "L")]
SIZE_OPTION = "bouquet-size"


def validate_tags(value):
    if not isinstance(value, list) or any(
        not isinstance(tag, str) or tag not in dict(TAGS) for tag in value
    ):
        raise ValidationError("Выберите теги из списка.")


def validate_size_prices(value):
    if not isinstance(value, dict) or any(size not in dict(SIZES) for size in value):
        raise ValidationError("Выберите размеры из списка.")
    for size, prices in value.items():
        if not isinstance(prices, dict):
            raise ValidationError("Проверьте цены размеров.")
        if size == "standard":
            continue
        for key in ("pickup", "delivery"):
            raw = prices.get(key)
            if key == "delivery" and raw is None:
                continue
            try:
                amount = Decimal(raw)
                valid = (
                    amount.is_finite()
                    and 0 < amount <= Decimal("99999999.99")
                    and amount == amount.quantize(Decimal("0.01"))
                )
            except (InvalidOperation, TypeError, ValueError):
                valid = False
            if not valid:
                raise ValidationError(
                    "Для каждого размера нужна положительная цена до двух знаков после запятой."
                )


def line_size(line):
    for attribute in line.attributes.all():
        if attribute.option.code == SIZE_OPTION:
            return next(
                (code for code, label in SIZES if attribute.value == label), "invalid"
            )
    return "standard"


def validate_sale_prices(value):
    if not isinstance(value, dict) or any(size not in dict(SIZES) for size in value):
        raise ValidationError("Выберите размеры из списка.")
    for prices in value.values():
        if not isinstance(prices, dict) or set(prices) - {"pickup", "delivery"}:
            raise ValidationError("Проверьте цены продажи.")
        for raw in prices.values():
            if raw is None:
                continue
            try:
                amount = Decimal(raw)
                valid = (
                    amount.is_finite()
                    and 0 < amount <= Decimal("99999999.99")
                    and amount == amount.quantize(Decimal("0.01"))
                )
            except (InvalidOperation, TypeError, ValueError):
                valid = False
            if not valid:
                raise ValidationError("Цена продажи должна быть положительной.")
