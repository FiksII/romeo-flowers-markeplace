from dataclasses import dataclass
from datetime import date

from django import forms

from market.models import Settlement


@dataclass
class FulfillmentContext:
    city_id: int | None = None
    address: dict | None = None
    method: str = "any"
    when: str = "any"
    day: date | None = None
    hour: int | None = None


class ContextForm(forms.Form):
    city = forms.ModelChoiceField(
        label="Город",
        queryset=Settlement.objects.filter(region__in=["77", "50"]),
        required=False,
        empty_label="Москва и область",
    )
    address_token = forms.CharField(required=False, widget=forms.HiddenInput)
    method = forms.ChoiceField(
        label="Получение",
        choices=[
            ("any", "Доставка и самовывоз"),
            ("delivery", "Доставка"),
            ("pickup", "Самовывоз"),
        ],
    )
    when = forms.ChoiceField(
        label="Когда получить",
        choices=[
            ("any", "Выбрать позже"),
            ("asap", "Как можно скорее"),
            ("today", "Сегодня"),
            ("tomorrow", "Завтра"),
            ("date", "Выбрать дату"),
        ],
    )
    day = forms.DateField(
        label="Дата", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    hour = forms.TypedChoiceField(
        label="Время",
        required=False,
        coerce=int,
        empty_value=None,
        choices=[("", "Любое время")]
        + [(str(hour), f"{hour:02}:00–{hour + 1:02}:00") for hour in range(24)],
    )

    def clean(self):
        from datetime import timedelta

        from django.utils import timezone

        from market.addresses import decode_address

        data = super().clean()
        if data.get("when") == "date":
            day = data.get("day")
            today = timezone.localdate()
            if not day or not today <= day <= today + timedelta(days=30):
                self.add_error("day", "Выберите дату в ближайшие 30 дней.")
        if data.get("address_token"):
            data["address"] = decode_address(data["address_token"])
        return data


def get_context(request):
    from django.core.exceptions import ValidationError

    from market.addresses import decode_address

    data = request.session.get("receiving", {})
    try:
        address = (
            decode_address(data["address_token"]) if data.get("address_token") else None
        )
    except ValidationError:
        address = None
    try:
        day = date.fromisoformat(data["day"]) if data.get("day") else None
    except (TypeError, ValueError):
        day = None
    return FulfillmentContext(
        city_id=data.get("city"),
        address=address,
        method=data.get("method", "any"),
        when=data.get("when", "any"),
        day=day,
        hour=data.get("hour"),
    )


def context_initial(request):
    return request.session.get("receiving", {})
