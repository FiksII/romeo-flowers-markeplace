import uuid
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from oscar.core.loading import get_model

from market.addresses import decode_address
from market.models import Flower, Listing, Settlement, Shop, ShopOrder, WeeklyHours
from market.widgets import FlowerTagSelect
from market.zones import clean_zone

WEEKDAYS = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]
SHORT_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
UNLIMITED_CLASS = "market-flowers-unlimited"


class SignupForm(UserCreationForm):
    email = forms.EmailField(label="Email")

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ["username", "email"]


class ProductForm(forms.Form):
    title = forms.CharField(label="Название", max_length=255)
    description = forms.CharField(
        label="Описание и состав", widget=forms.Textarea, required=False
    )
    category = forms.ChoiceField(
        label="Категория", choices=Listing._meta.get_field("category").choices
    )
    flowers = forms.ModelMultipleChoiceField(
        label="Цветы в составе",
        queryset=Flower.objects.all(),
        required=False,
        widget=FlowerTagSelect,
        help_text="Выберите все цветы в составе. Часто используемые вами цветы показаны первыми.",
    )
    pickup_price = forms.DecimalField(
        label="Цена самовывозом, ₽",
        min_value=Decimal("0.01"),
        max_digits=10,
        decimal_places=2,
        help_text="Столько платит покупатель, который забирает букет сам. Если у магазина нет своей доставки, эту же цену увидит и покупатель с доставкой Ромео; доставка добавляется отдельно.",
    )
    delivery_price = forms.DecimalField(
        label="Цена с доставкой, ₽",
        min_value=Decimal("0.01"),
        max_digits=10,
        decimal_places=2,
        help_text="Столько платит покупатель, когда букет привозит ваш курьер. Доставку Ромео в этом случае не добавляем.",
    )
    always_in_stock = forms.BooleanField(label="Всегда в наличии", required=False)
    stock = forms.IntegerField(
        label="Количество в наличии",
        min_value=0,
        max_value=1000000,
        required=False,
        help_text="0 — букет распродан и не показывается в каталоге.",
    )
    photo = forms.ImageField(label="Фото", required=False)
    is_public = forms.BooleanField(
        label="Показывать в каталоге", required=False, initial=True
    )

    def __init__(self, *args, shop=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.shop = shop
        if shop is not None:
            self.fields["flowers"].queryset = Flower.objects.annotate(
                usage=Count("listings", filter=Q(listings__shop=shop))
            ).order_by("-usage", "rank", "name")
            if not shop.own_delivery:
                del self.fields["delivery_price"]
        self.fields["stock"].widget.attrs["data-stock-count"] = ""
        self.fields["always_in_stock"].widget.attrs["data-stock-always"] = ""

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and (
            photo.size > 5 * 1024 * 1024
            or photo.image.format not in {"JPEG", "PNG", "WEBP"}
        ):
            raise ValidationError("Используйте JPEG, PNG или WebP до 5 МБ.")
        return photo

    def clean(self):
        data = super().clean()
        if (
            not data.get("always_in_stock")
            and data.get("stock") is None
            and "stock" not in self.errors
        ):
            self.add_error(
                "stock", "Укажите количество или отметьте «Всегда в наличии»."
            )
        return data


def _open_orders(listing):
    return (
        ShopOrder.objects.filter(
            shop_id=listing.shop_id, order__lines__product_id=listing.product_id
        )
        .exclude(status__in=["completed", "cancelled"])
        .exists()
    )


def _product_class(track):
    ProductClass = get_model("catalogue", "ProductClass")
    if track:
        cls, _ = ProductClass.objects.get_or_create(
            slug="market-flowers",
            defaults={"name": "Цветы", "track_stock": True, "requires_shipping": True},
        )
    else:
        cls, _ = ProductClass.objects.get_or_create(
            slug=UNLIMITED_CLASS,
            defaults={
                "name": "Цветы без учёта остатков",
                "track_stock": False,
                "requires_shipping": True,
            },
        )
    return cls


@transaction.atomic
def save_listing(shop, data, listing=None):
    if listing and listing.shop_id != shop.pk:
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied
    Product, Stock = (
        get_model("catalogue", "Product"),
        get_model("partner", "StockRecord"),
    )
    track = not data.get("always_in_stock", False)
    change_class = listing is None
    if listing:
        stock = Stock.objects.select_for_update().get(
            product_id=listing.product_id, partner=shop.partner
        )
        product = Product.objects.select_for_update().get(pk=listing.product_id)
        if product.get_product_class().track_stock != track:
            # Oscar reserves and releases stock only for classes that track it, so the
            # mode cannot change while an order for this bouquet is still open.
            if _open_orders(listing):
                raise ValidationError(
                    "Пока есть незавершённые заказы с этим букетом, нельзя менять режим «Всегда в наличии»."
                )
            change_class = True
        elif track and data["stock"] < (stock.num_allocated or 0):
            raise ValidationError(
                "Наличие не может быть меньше количества, зарезервированного в заказах."
            )
    else:
        stock, product = None, Product()
    if change_class:
        product.product_class = _product_class(track)
    product.title, product.description, product.is_public = (
        data["title"],
        data["description"],
        data["is_public"],
    )
    product.save()
    if stock is None:
        stock = Stock(
            product=product, partner=shop.partner, partner_sku=uuid.uuid4().hex
        )
    stock.price, stock.price_currency = data["pickup_price"], "RUB"
    if track:
        stock.num_in_stock = data["stock"]
    else:
        stock.num_in_stock, stock.num_allocated = None, 0
    stock.save()
    listing = listing or Listing(product=product, shop=shop)
    listing.category = data["category"]
    listing.flower_kind = data.get("flower_kind", "")
    if "delivery_price" in data:
        listing.delivery_price = data["delivery_price"]
    if data.get("photo"):
        listing.photo = data["photo"]
    listing.full_clean()
    listing.save()
    if "flowers" in data:
        listing.flowers.set(data["flowers"])
    return listing


class ShopFormBase(forms.ModelForm):
    """Shared by every form that edits a part of a shop.

    Saving writes only the fields of the form, so a seller who had the page open cannot
    overwrite a status or commission the administrator changed meanwhile."""

    OPERATOR_FIELDS = ("status", "commission_percent", "delivery_fee")
    address_token = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, operator=False, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = Settlement.objects.filter(region__in=["77", "50"])
        if "settlement" in self.fields:
            self.fields["settlement"].queryset = allowed
        if "delivery_settlements" in self.fields:
            self.fields["delivery_settlements"].queryset = allowed
        if "address" in self.fields:
            self.fields["address"].widget.attrs.update(
                {"data-address-input": "", "autocomplete": "off"}
            )
        else:
            del self.fields["address_token"]
        if operator:
            self.fields["status"] = forms.ChoiceField(
                label="Статус", choices=Shop.STATUSES, initial=self.instance.status
            )
            self.fields["commission_percent"] = forms.DecimalField(
                label="Комиссия платформы, % от цены букета",
                min_value=0,
                max_value=100,
                max_digits=5,
                decimal_places=2,
                initial=self.instance.commission_percent,
            )
            self.fields["delivery_fee"] = forms.DecimalField(
                label="Доставка Ромео из этого магазина, ₽",
                min_value=0,
                max_digits=9,
                decimal_places=2,
                initial=self.instance.delivery_fee,
            )
        self.operator = operator

    def clean(self):
        data = super().clean()
        if "address" in self.fields:
            self._clean_address(data)
        for name, lengths in [
            ("inn", {10, 12}),
            ("bank_account", {20}),
            ("bank_bik", {9}),
        ]:
            value = data.get(name)
            if value and (not value.isdigit() or len(value) not in lengths):
                self.add_error(name, "Проверьте число цифр в реквизитах.")
        return data

    def _clean_address(self, data):
        token = data.get("address_token")
        changed = (
            not self.instance.pk
            or data.get("address") != self.instance.address
            or data.get("settlement") != self.instance.settlement
        )
        if token:
            address = decode_address(token)
            city = data.get("settlement")
            if city and (
                city.region != address["region"]
                or city.name.casefold() != address["city"].casefold()
            ):
                raise ValidationError(
                    "Адрес должен соответствовать выбранному населённому пункту."
                )
            self.instance.latitude = address["latitude"]
            self.instance.longitude = address["longitude"]
            data["address"] = address["value"]
        elif changed:
            self.add_error("address", "Выберите точный адрес магазина из подсказок.")

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.operator:
            for name in self.OPERATOR_FIELDS:
                setattr(instance, name, self.cleaned_data[name])
        if commit:
            instance.full_clean()
            if instance.pk:
                fields = [
                    name
                    for name in self.Meta.fields
                    if not instance._meta.get_field(name).many_to_many
                ]
                if "address" in self.fields:
                    fields += ["latitude", "longitude"]
                if self.operator:
                    fields += list(self.OPERATOR_FIELDS)
                instance.save(update_fields=fields)
            else:
                instance.save()
            self.save_m2m()
        return instance


class ShopForm(ShopFormBase):
    """The whole profile at once: connecting a shop and the administrator's page."""

    class Meta:
        model = Shop
        fields = [
            "name",
            "description",
            "settlement",
            "address",
            "phone",
            "delivery_enabled",
            "pickup_enabled",
            "own_delivery",
            "delivery_settlements",
            "minimum_order",
            "prep_minutes",
            "pickup_instructions",
            "legal_name",
            "inn",
            "bank_account",
            "bank_bik",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "pickup_instructions": forms.Textarea(attrs={"rows": 3}),
        }


class ShopInfoForm(ShopFormBase):
    """Cabinet section «Информация о магазине» (the schedule is a separate form)."""

    class Meta:
        model = Shop
        fields = [
            "name",
            "description",
            "settlement",
            "address",
            "phone",
            "pickup_enabled",
            "delivery_enabled",
            "own_delivery",
            "prep_minutes",
            "pickup_instructions",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "pickup_instructions": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "delivery_enabled": "Покупатели могут заказать доставку из вашего магазина.",
            "own_delivery": "Курьер ваш: покупатель платит цену с доставкой, которую вы задаёте для каждого букета. Без галочки доставку организует Ромео за отдельную плату.",
        }


class ShopPayoutForm(ShopFormBase):
    """Cabinet section «Реквизиты и зона доставки»."""

    delivery_zone = forms.JSONField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Shop
        fields = [
            "legal_name",
            "inn",
            "bank_account",
            "bank_bik",
            "delivery_zone",
            "delivery_settlements",
            "minimum_order",
        ]
        widgets = {"delivery_settlements": forms.CheckboxSelectMultiple}
        help_texts = {
            "delivery_settlements": "Покупатель, выбравший только город, увидит ваш магазин, если город отмечен здесь. Точный адрес проверяется по зоне на карте.",
        }

    def clean_delivery_zone(self):
        return clean_zone(self.cleaned_data.get("delivery_zone"))


def clock_text(minute):
    return f"{minute // 60:02}:{minute % 60:02}"


def parse_clock(text, *, end):
    """``ЧЧ:ММ`` to minutes from midnight; ``24:00`` is allowed only as an end."""
    hours, minutes = map(int, text.split(":"))
    if (
        not 0 <= minutes <= 59
        or not 0 <= hours <= 24
        or (hours == 24 and (minutes or not end))
    ):
        raise ValueError
    return hours * 60 + minutes


class WeeklyHoursForm(forms.Form):
    """Work, delivery and pickup hours for every weekday in one grid.

    An empty pair of fields is a day off; ``00:00``–``24:00`` is round the clock."""

    def __init__(self, *args, shop, **kwargs):
        super().__init__(*args, **kwargs)
        self.shop = shop
        current = {(row.weekday, row.method): row for row in shop.hours.all()}
        for method, _ in WeeklyHours.METHODS:
            for day in range(7):
                row = current.get((day, method))
                for part, minute in [
                    ("start", row.start_minute if row else None),
                    ("end", row.end_minute if row else None),
                ]:
                    self.fields[f"{method}_{day}_{part}"] = forms.CharField(
                        required=False,
                        max_length=5,
                        initial="" if minute is None else clock_text(minute),
                        widget=forms.TextInput(
                            attrs={
                                "placeholder": "09:00" if part == "start" else "20:00",
                                "inputmode": "numeric",
                                "pattern": r"([01]?\d|2[0-3]):[0-5]\d|24:00",
                                "maxlength": "5",
                                "size": "5",
                                "aria-label": f"{WEEKDAYS[day]}: {'начало' if part == 'start' else 'конец'}",
                            }
                        ),
                    )

    @property
    def methods(self):
        return WeeklyHours.METHODS

    @property
    def rows(self):
        return [
            {
                "label": WEEKDAYS[day],
                "short": SHORT_WEEKDAYS[day],
                "day": day,
                "cells": [
                    {
                        "method": method,
                        "start": self[f"{method}_{day}_start"],
                        "end": self[f"{method}_{day}_end"],
                    }
                    for method, _ in WeeklyHours.METHODS
                ],
            }
            for day in range(7)
        ]

    def clean(self):
        data = super().clean()
        self.intervals = {}
        for method, _ in WeeklyHours.METHODS:
            for day in range(7):
                start_name, end_name = f"{method}_{day}_start", f"{method}_{day}_end"
                start = (data.get(start_name) or "").strip()
                end = (data.get(end_name) or "").strip()
                if not start and not end:
                    continue
                try:
                    if not start or not end:
                        raise ValueError
                    interval = (
                        parse_clock(start, end=False),
                        parse_clock(end, end=True),
                    )
                except ValueError:
                    self.add_error(
                        end_name if start else start_name,
                        "Укажите время в формате ЧЧ:ММ или оставьте день пустым.",
                    )
                    continue
                if interval[0] == interval[1]:
                    self.add_error(
                        end_name, "Для круглосуточной работы задайте 00:00–24:00."
                    )
                    continue
                self.intervals[(day, method)] = interval
        return data

    @transaction.atomic
    def save(self):
        for (day, method), (start, end) in self.intervals.items():
            WeeklyHours.objects.update_or_create(
                shop=self.shop,
                weekday=day,
                method=method,
                defaults={"start_minute": start, "end_minute": end},
            )
        for row in self.shop.hours.all():
            if (row.weekday, row.method) not in self.intervals:
                row.delete()


class ExceptionForm(forms.Form):
    """A single date that differs from the usual week."""

    day = forms.DateField(
        label="Дата исключения", widget=forms.DateInput(attrs={"type": "date"})
    )
    method = forms.ChoiceField(label="Расписание", choices=WeeklyHours.METHODS)
    start = forms.CharField(
        label="Начало", initial="09:00", help_text="ЧЧ:ММ, например 09:00"
    )
    end = forms.CharField(
        label="Конец",
        initial="20:00",
        help_text="24:00 — конец суток; конец раньше начала — ночной интервал",
    )
    closed = forms.BooleanField(label="Не работает в этот день", required=False)

    def clean(self):
        data = super().clean()
        for field in ["start", "end"]:
            try:
                data[field + "_minute"] = parse_clock(
                    data.get(field, ""), end=field == "end"
                )
            except (ValueError, AttributeError):
                self.add_error(field, "Используйте время в формате ЧЧ:ММ.")
        if not data.get("closed") and data.get("start_minute") == data.get(
            "end_minute"
        ):
            self.add_error("end", "Для круглосуточной работы задайте 00:00–24:00.")
        return data


class CheckoutContactForm(forms.Form):
    name = forms.CharField(label="Имя получателя", max_length=100)
    phone = forms.RegexField(
        label="Телефон",
        regex=r"^\+?[0-9 ()-]{10,24}$",
        max_length=25,
        error_messages={"invalid": "Проверьте номер телефона."},
    )
    instructions = forms.CharField(
        label="Квартира, подъезд и комментарий",
        max_length=1000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
