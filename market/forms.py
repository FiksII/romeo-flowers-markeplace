import uuid
from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from oscar.core.loading import get_model

from market.addresses import decode_address, encode_address
from market.auth_adapter import RussianPhoneField
from market.bouquet_options import SIZES, TAGS
from market.delivery_areas import normalized, preset_city_ids
from market.models import (
    Flower,
    Listing,
    Membership,
    MessengerLink,
    Settlement,
    Shop,
    ShopOrder,
    WeeklyHours,
)
from market.photos import MAX_PHOTOS, MultiplePhotoField, gallery_items, save_photos
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


class ShopNumberForm(forms.Form):
    """A number on the shop's notification list (a label for the invitation link)."""

    channel = forms.ChoiceField(
        choices=MessengerLink.CHANNELS, widget=forms.HiddenInput
    )
    phone = RussianPhoneField(
        label="Номер телефона",
        widget=forms.TextInput(
            attrs={
                "placeholder": "+7(***)***-**-**",
                "inputmode": "tel",
                "autocomplete": "off",
                "data-recipient-phone": "",
            }
        ),
    )


class ContactAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label="Телефон или email",
        max_length=254,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Проверьте телефон или email и пароль.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].max_length = 254
        self.fields["username"].widget.attrs["maxlength"] = 254

    def clean(self):
        identifier = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if identifier and password:
            User = get_user_model()
            candidates = User.objects.none()
            if "@" in identifier:
                candidates = User.objects.filter(email__iexact=identifier)
            else:
                from market.auth_adapter import RussianPhoneField

                try:
                    phone = RussianPhoneField().clean(identifier)
                except ValidationError:
                    pass
                else:
                    candidates = User.objects.filter(
                        customer_phone__phone=phone, customer_phone__verified=True
                    )
            users = list(candidates[:2])
            if len(users) != 1:
                # Match Django's password-hashing work for unknown identifiers.
                User().set_password(password)
                raise self.get_invalid_login_error()
            self.cleaned_data["username"] = users[0].get_username()
        return super().clean()




class ProductForm(forms.Form):
    title = forms.CharField(label="Название", max_length=255)
    description = forms.CharField(
        label="Описание и состав", widget=forms.Textarea, required=False
    )
    category = forms.ChoiceField(
        label="Категория", choices=Listing._meta.get_field("category").choices
    )
    tags = forms.MultipleChoiceField(
        label="Теги", choices=TAGS, required=False, widget=forms.CheckboxSelectMultiple
    )
    size_config = forms.CharField(initial="1", required=False, widget=forms.HiddenInput)
    sizes = forms.MultipleChoiceField(
        label="Размеры",
        choices=SIZES,
        required=False,
        initial=["standard"],
        widget=forms.CheckboxSelectMultiple,
        help_text="Отметьте доступные варианты и задайте цену каждого. Количество в наличии общее для всех размеров.",
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
    photo = MultiplePhotoField(
        label="Фотографии букета", required=False,
        help_text="До 20 фотографий JPEG, PNG или WebP, каждая до 5 МБ. Можно выбрать несколько файлов сразу.",
        widget=MultiplePhotoField.widget(attrs={"accept": "image/jpeg,image/png,image/webp", "data-photo-upload": ""}),
    )
    remove_photos = forms.MultipleChoiceField(label="Удалить фотографии", required=False)
    primary_photo = forms.ChoiceField(label="Главное фото", required=False)
    is_public = forms.BooleanField(
        label="Показывать в каталоге", required=False, initial=True
    )

    def __init__(self, *args, shop=None, listing=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.shop = shop
        self.listing = listing
        self.photo_items = gallery_items(listing)
        choices = [(row["id"], f"Фото {index + 1}") for index, row in enumerate(self.photo_items)]
        self.fields["remove_photos"].choices = choices
        self.fields["primary_photo"].choices = [("", "Сохранить текущее главное фото"), *choices, ("new", "Первое из новых фото")]
        self.fields["photo"].widget.attrs["data-photo-count"] = len(choices)
        self.fields["pickup_price"].required = False
        self.fields["pickup_price"].label = "Стандартный — цена самовывозом, ₽"
        self.fields["pickup_price"].widget.attrs["data-size-price"] = "standard"
        self.fields["delivery_price"].required = False
        self.fields["delivery_price"].label = "Стандартный — цена с доставкой, ₽"
        self.fields["delivery_price"].widget.attrs["data-size-price"] = "standard"
        for code, label in SIZES[1:]:
            for method, text in [
                ("pickup", "самовывозом"),
                ("delivery", "с доставкой"),
            ]:
                if method == "delivery" and shop is not None and not shop.own_delivery:
                    continue
                self.fields[f"{method}_price_{code}"] = forms.DecimalField(
                    label=f"{label} — цена {text}, ₽",
                    required=False,
                    min_value=Decimal("0.01"),
                    max_digits=10,
                    decimal_places=2,
                    widget=forms.NumberInput(attrs={"data-size-price": code}),
                )
        if shop is not None:
            self.fields["flowers"].queryset = Flower.objects.annotate(
                usage=Count("listings", filter=Q(listings__shop=shop))
            ).order_by("-usage", "rank", "name")
            if not shop.own_delivery:
                del self.fields["delivery_price"]
        self.fields["stock"].widget.attrs["data-stock-count"] = ""
        self.fields["always_in_stock"].widget.attrs["data-stock-always"] = ""
        names = list(self.fields)
        price_names = [
            name
            for name in names
            if name.startswith(("pickup_price", "delivery_price"))
        ]
        names = [name for name in names if name not in price_names]
        index = names.index("sizes") + 1
        self.order_fields(names[:index] + price_names + names[index:])

    def _clean_sizes(self, data):
        # Older clients submit only the original single-price fields.
        sizes = data.get("sizes") or (
            [] if self.data.get("size_config") else ["standard"]
        )
        if not sizes:
            self.add_error("sizes", "Выберите хотя бы один размер.")
            return
        prices = {}
        for code in sizes:
            if code not in dict(SIZES):
                continue
            suffix = "" if code == "standard" else f"_{code}"
            values = {}
            for method in ("pickup", "delivery"):
                name = f"{method}_price{suffix}"
                if name not in self.fields:
                    continue
                price = data.get(name)
                if price is None and name not in self.errors:
                    self.add_error(name, "Укажите цену выбранного размера.")
                values[method] = str(price) if price is not None else None
            prices[code] = {} if code == "standard" else values
        data["size_prices"] = prices if sizes != ["standard"] else {}
        if "standard" not in sizes and prices:
            first = next(iter(prices.values()))
            data["pickup_price"] = (
                Decimal(first["pickup"]) if first.get("pickup") else None
            )
            if "delivery_price" in self.fields:
                data["delivery_price"] = (
                    Decimal(first["delivery"]) if first.get("delivery") else None
                )

    def clean(self):
        data = super().clean()
        self._clean_sizes(data)
        photos = data.get("photo", [])
        remaining = len(self.photo_items) - len(data.get("remove_photos", []))
        if remaining + len(photos) > MAX_PHOTOS:
            self.add_error("photo", "Можно сохранить не больше 20 фотографий букета.")
        if data.get("primary_photo") == "new" and not photos:
            self.add_error("primary_photo", "Загрузите новое фото или выберите существующее главным.")
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
        listing = Listing.objects.select_for_update().get(pk=listing.pk)
        listing.product = product
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
    listing.tags = data.get("tags", [])
    listing.size_prices = data.get("size_prices", {})
    listing.flower_kind = data.get("flower_kind", "")
    if "delivery_price" in data:
        listing.delivery_price = data["delivery_price"]
    listing.full_clean()
    if listing.pk:
        # Romeo's own sale prices are the administrator's; the seller never overwrites them.
        listing.save(
            update_fields=[
                "category",
                "tags",
                "size_prices",
                "flower_kind",
                "delivery_price",
                "photo",
            ]
        )
    else:
        listing.save()
    if "flowers" in data:
        listing.flowers.set(data["flowers"])
    save_photos(listing, data.get("photo"), data.get("remove_photos", ()), data.get("primary_photo"))
    return listing


class ShopFormBase(forms.ModelForm):
    """Shared by every form that edits a part of a shop.

    Saving writes only the fields of the form, so a seller who had the page open cannot
    overwrite a status or markup the administrator changed meanwhile."""

    OPERATOR_FIELDS = ("status", "markup_percent", "delivery_fee")
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
            self.fields["markup_percent"] = forms.DecimalField(
                label="Наценка Ромео, % к цене магазина",
                help_text="Покупатель платит цену магазина плюс наценку; магазин получает свою цену полностью. Свою цену продажи букета можно задать на витрине.",
                min_value=0,
                max_value=1000,
                max_digits=6,
                decimal_places=2,
                initial=self.instance.markup_percent,
            )
            self.fields["delivery_fee"] = forms.DecimalField(
                label="Доставка Ромео из этого магазина, ₽",
                min_value=0,
                max_digits=9,
                decimal_places=2,
                initial=self.instance.delivery_fee,
            )
        if "own_delivery" in self.fields:
            self.fields[
                "own_delivery"
            ].help_text = "Курьер ваш: покупатель платит цену с доставкой, которую вы задаёте для каждого букета. Если галочка снята, доставку выполняет Ромео."
        self.operator = operator

    def clean(self):
        data = super().clean()
        if "own_delivery" in self.fields:
            self.instance.delivery_enabled = True
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
                or normalized(city.name) != normalized(address["city"])
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
                if "own_delivery" in self.fields:
                    fields += ["delivery_enabled"]
                if "address" in self.fields:
                    fields += ["latitude", "longitude"]
                if self.operator:
                    fields += list(self.OPERATOR_FIELDS)
                instance.save(update_fields=fields)
            else:
                instance.save()
            self.save_m2m()
        return instance


class ShopDeliveryForm(ShopFormBase):
    delivery_zone = forms.JSONField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "delivery_settlements" in self.fields:
            self.fields["delivery_settlements"].widget = forms.MultipleHiddenInput()

    def clean(self):
        data = super().clean()
        zone = data.get("delivery_zone")
        if zone and "delivery_settlements" in self.fields:
            cities = self.fields["delivery_settlements"].queryset
            previous = data.get("delivery_settlements")
            if not previous:
                previous = (
                    self.instance.delivery_settlements.all()
                    if self.instance.pk
                    else cities.filter(pk=getattr(data.get("settlement"), "pk", None))
                )
            ids = preset_city_ids(zone, cities, previous.values_list("pk", flat=True))
            if ids is not None:
                data["delivery_settlements"] = cities.filter(pk__in=ids)
            elif not data.get("delivery_settlements"):
                data["delivery_settlements"] = (
                    self.instance.delivery_settlements.all()
                    if self.instance.pk
                    else cities.filter(pk=getattr(data.get("settlement"), "pk", None))
                )
        return data

    def clean_delivery_zone(self):
        zone = clean_zone(self.cleaned_data.get("delivery_zone"))
        if not zone:
            raise ValidationError(
                "Нарисуйте зону доставки на карте или выберите готовую территорию."
            )
        return zone


class ShopForm(ShopDeliveryForm):
    """The whole profile at once: connecting a shop and the administrator's page."""

    class Meta:
        model = Shop
        fields = [
            "name",
            "description",
            "settlement",
            "address",
            "phone",
            "pickup_enabled",
            "own_delivery",
            "delivery_zone",
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


class ShopMemberChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, user):
        return (
            f"{user.get_username()} · {user.email}"
            if user.email
            else user.get_username()
        )


class OperatorShopForm(ShopForm):
    members = ShopMemberChoiceField(
        label="Владельцы и сотрудники магазина",
        queryset=get_user_model().objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Выбранные пользователи получат доступ к кабинету этого магазина. Снимите отметку, чтобы отозвать доступ. Суперадминистраторы имеют доступ ко всем магазинам автоматически.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, operator=True, **kwargs)
        self.fields["members"].queryset = get_user_model().objects.order_by("username")
        self.initial["members"] = (
            list(self.instance.memberships.values_list("user_id", flat=True))
            if self.instance.pk
            else []
        )

    def save_memberships(self):
        """Called alongside saving the shop, within the view's transaction."""
        selected = {user.pk for user in self.cleaned_data["members"]}
        self.instance.memberships.exclude(user_id__in=selected).delete()
        for user_id in selected:
            Membership.objects.get_or_create(shop=self.instance, user_id=user_id)


class ShopInfoForm(ShopDeliveryForm):
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
            "own_delivery",
            "delivery_zone",
            "delivery_settlements",
            "prep_minutes",
            "pickup_instructions",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "pickup_instructions": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "own_delivery": "Курьер ваш: покупатель платит цену с доставкой, которую вы задаёте для каждого букета. Без галочки доставку организует Ромео за отдельную плату.",
        }


class ShopPayoutForm(ShopDeliveryForm):
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
    customer_name = forms.CharField(
        label="Ваше имя",
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    customer_phone = RussianPhoneField(
        label="Ваш телефон",
        max_length=25,
        widget=forms.TextInput(
            attrs={
                "type": "tel",
                "inputmode": "tel",
                "autocomplete": "tel",
                "placeholder": "+7(***)***-**-**",
                "data-recipient-phone": "",
            }
        ),
    )
    name = forms.CharField(label="Имя получателя", max_length=100)
    phone = forms.RegexField(
        label="Телефон",
        regex=r"^\+?[0-9 ()-]{10,24}$",
        max_length=25,
        error_messages={"invalid": "Проверьте номер телефона."},
        widget=forms.TextInput(
            attrs={
                "type": "tel",
                "inputmode": "tel",
                "autocomplete": "tel",
                "placeholder": "+7(***)***-**-**",
                "data-recipient-phone": "",
            }
        ),
    )
    delivery_contact = forms.ChoiceField(
        label="Доставка",
        choices=[
            ("call", "Уточнить у получателя"),
            ("no_call", "Не звонить получателю"),
        ],
        initial="call",
        required=False,
        widget=forms.RadioSelect,
    )
    address = forms.CharField(
        label="Точный адрес доставки",
        max_length=300,
        required=False,
        widget=forms.TextInput(
            attrs={
                "data-address-input": "",
                "autocomplete": "off",
                "placeholder": "Улица и номер дома",
            }
        ),
    )
    address_token = forms.CharField(required=False, widget=forms.HiddenInput)
    address_details = forms.CharField(
        label="Квартира, подъезд, этаж и домофон",
        max_length=300,
        required=False,
    )
    instructions = forms.CharField(
        label="Комментарий",
        max_length=1000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    payment_method = forms.ChoiceField(
        label="Оплата",
        choices=[("cash", "Наличными при получении"), ("online", "Онлайн")],
        initial="cash",
        required=False,
        widget=forms.RadioSelect,
        error_messages={"invalid_choice": "Выберите способ оплаты."},
    )

    def clean_payment_method(self):
        method = self.cleaned_data["payment_method"] or (
            "online" if self.has_delivery else "cash"
        )
        if self.has_delivery:
            if not settings.MARKET_ONLINE_PAYMENTS:
                raise ValidationError("Онлайн-оплата доставки временно недоступна. Выберите самовывоз.")
            if method != "online":
                raise ValidationError("Доставка оплачивается только онлайн.")
        return method

    def __init__(self, *args, has_delivery=False, user=None, **kwargs):
        self.has_delivery = has_delivery
        super().__init__(*args, **kwargs)
        if not settings.MARKET_ONLINE_PAYMENTS:
            self.fields["payment_method"].choices = [
                ("cash", "Наличными при получении")
            ]
        if user is not None and not user.email.strip():
            self.fields["email"] = forms.EmailField(
                label="Ваш email",
                max_length=254,
                help_text="Сохраним в аккаунте для информации о ваших заказах. В следующий раз спрашивать не будем.",
                widget=forms.EmailInput(attrs={"autocomplete": "email"}),
            )

    def clean_phone(self):
        number = "".join(
            character for character in self.cleaned_data["phone"] if character.isdigit()
        )
        if len(number) == 10:
            number = "7" + number
        elif len(number) == 11 and number.startswith("8"):
            number = "7" + number[1:]
        if len(number) != 11 or not number.startswith("7"):
            raise ValidationError("Введите телефон полностью: +7(***)***-**-**.")
        return "+" + number

    def clean(self):
        data = super().clean()
        data["delivery_contact"] = data.get("delivery_contact") or "no_call"
        data["verified_address"] = None
        if self.has_delivery and data["delivery_contact"] == "no_call":
            if not data.get("address_token"):
                self.add_error("address", "Выберите точный адрес из подсказок.")
            else:
                try:
                    address = decode_address(data["address_token"])
                    if data.get("address") != address["value"]:
                        raise ValidationError(
                            "Выберите точный адрес из подсказок заново."
                        )
                    data["verified_address"] = address
                except ValidationError as error:
                    self.add_error("address", error)
        return data


class DeliveryDestinationForm(forms.Form):
    address = forms.CharField(
        label="Конечный адрес доставки",
        max_length=300,
        widget=forms.TextInput(attrs={"data-address-input": "", "autocomplete": "off"}),
    )
    address_token = forms.CharField(required=False, widget=forms.HiddenInput)
    address_details = forms.CharField(
        label="Квартира, подъезд, этаж и домофон", max_length=300, required=False
    )
    slot_start = forms.DateTimeField(
        label="Начало согласованного интервала",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        ),
    )
    slot_end = forms.DateTimeField(
        label="Конец согласованного интервала",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        ),
    )

    def __init__(self, *args, part, **kwargs):
        self.part = part
        initial = {"address": part.address, "address_details": part.address_details}
        if part.delivery_point:
            initial["address_token"] = encode_address(part.delivery_point)
        super().__init__(*args, initial=initial, **kwargs)
        if part.slot_start and part.slot_end:
            del self.fields["slot_start"]
            del self.fields["slot_end"]

    def full_clean(self):
        with timezone.override(self.part.shop.timezone):
            super().full_clean()

    def clean(self):
        data = super().clean()
        if data.get("address_token"):
            try:
                address = decode_address(data["address_token"])
                if data.get("address") != address["value"]:
                    raise ValidationError("Выберите точный адрес из подсказок заново.")
                data["verified_address"] = address
            except ValidationError as error:
                self.add_error("address", error)
        else:
            self.add_error("address", "Выберите точный адрес из подсказок.")
        start, end = data.get("slot_start"), data.get("slot_end")
        if start and start <= timezone.now():
            self.add_error("slot_start", "Выберите время доставки в будущем.")
        if start and end and end <= start:
            self.add_error("slot_end", "Конец интервала должен быть позже начала.")
        return data


class PayoutForm(forms.Form):
    amount = forms.DecimalField(
        label="Сумма, ₽",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(
            attrs={"step": "0.01", "placeholder": "Сумма, ₽", "aria-label": "Сумма, ₽"}
        ),
    )
    note = forms.CharField(
        label="Комментарий",
        max_length=255,
        required=False,
        widget=forms.TextInput(
            attrs={"placeholder": "Комментарий", "aria-label": "Комментарий"}
        ),
    )


class ListingPriceForm(forms.Form):
    """Romeo's sale price of a bouquet, per size; the shop's own prices stay as they are."""

    def __init__(self, *args, listing, **kwargs):
        super().__init__(*args, **kwargs)
        from market.pricing import quote_product

        self.listing = listing
        self.rows = []
        labels = dict(SIZES)
        methods = ["pickup", "delivery"] if listing.shop.own_delivery else ["pickup"]
        for code in listing.sizes:
            pickup, delivery = listing.prices_for_size(code)
            for method in methods:
                name = f"sale_{method}_{code}"
                current = listing.sale_price_for(code, method)
                quote = quote_product(listing.shop, pickup, delivery, method)
                title = labels[code] if listing.has_size_choice else ""
                if listing.shop.own_delivery:
                    way = "самовывоз" if method == "pickup" else "с доставкой магазина"
                    title = f"{title} · {way}" if title else way.capitalize()
                self.fields[name] = forms.DecimalField(
                    label=title or "Цена продажи",
                    required=False,
                    min_value=Decimal("0.01"),
                    max_digits=10,
                    decimal_places=2,
                    initial=current,
                    widget=forms.NumberInput(
                        attrs={"step": "0.01", "placeholder": f"{quote.customer:.0f}"}
                    ),
                )
                self.rows.append(
                    {
                        "field": name,
                        "label": title or "Букет",
                        "partner": quote.payout,
                        "by_markup": quote.customer,
                        "current": current,
                        "selling": current if current is not None else quote.customer,
                    }
                )

    def bound_rows(self):
        return [{**row, "bound": self[row["field"]]} for row in self.rows]

    def sale_prices(self):
        """The ``Listing.sale_prices`` value; an empty field means «by the markup»."""
        result = {}
        for code in self.listing.sizes:
            entry = {}
            for method in ("pickup", "delivery"):
                value = self.cleaned_data.get(f"sale_{method}_{code}")
                if value is not None:
                    entry[method] = str(value)
            if entry:
                result[code] = entry
        return result


def update_sale_prices(listing, sale_prices):
    """Set Romeo's sale prices without touching anything the shop set.

    Orders already placed keep the prices they were bought at (``ShopOrderItem``)."""
    Stock = get_model("partner", "StockRecord")
    with transaction.atomic():
        # Sellers edit a listing while holding its stock row; take it first as they do.
        Stock.objects.select_for_update().filter(
            product_id=listing.product_id, partner=listing.shop.partner
        ).first()
        listing = Listing.objects.select_for_update().get(pk=listing.pk)
        listing.sale_prices = sale_prices
        listing.full_clean()
        listing.save(update_fields=["sale_prices"])
    return listing
