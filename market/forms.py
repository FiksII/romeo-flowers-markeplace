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
from market.models import Flower, Listing, Settlement, Shop, WeeklyHours
from market.widgets import FlowerTagSelect


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
    price = forms.DecimalField(
        label="Цена магазина, ₽",
        min_value=Decimal("0.01"),
        max_digits=10,
        decimal_places=2,
        help_text="Эта сумма за товар причитается магазину. Ромео автоматически добавит свою наценку для покупателя.",
    )
    stock = forms.IntegerField(
        label="Количество в наличии", min_value=0, max_value=1000000
    )
    photo = forms.ImageField(label="Фото", required=False)
    is_public = forms.BooleanField(
        label="Показывать в каталоге", required=False, initial=True
    )

    def __init__(self, *args, shop=None, **kwargs):
        super().__init__(*args, **kwargs)
        if shop is not None:
            self.fields["flowers"].queryset = Flower.objects.annotate(
                usage=Count("listings", filter=Q(listings__shop=shop))
            ).order_by("-usage", "rank", "name")

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and (
            photo.size > 5 * 1024 * 1024
            or photo.image.format not in {"JPEG", "PNG", "WEBP"}
        ):
            raise ValidationError("Используйте JPEG, PNG или WebP до 5 МБ.")
        return photo


@transaction.atomic
def save_listing(shop, data, listing=None):
    if listing and listing.shop_id != shop.pk:
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied
    Product, Stock, ProductClass = (
        get_model("catalogue", "Product"),
        get_model("partner", "StockRecord"),
        get_model("catalogue", "ProductClass"),
    )
    if listing:
        stock = Stock.objects.select_for_update().get(
            product_id=listing.product_id, partner=shop.partner
        )
        product = Product.objects.select_for_update().get(pk=listing.product_id)
        if data["stock"] < (stock.num_allocated or 0):
            raise ValidationError(
                "Наличие не может быть меньше количества, зарезервированного в заказах."
            )
    else:
        cls, _ = ProductClass.objects.get_or_create(
            slug="market-flowers",
            defaults={"name": "Цветы", "track_stock": True, "requires_shipping": True},
        )
        product = Product(product_class=cls)
        stock = None
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
    stock.price, stock.price_currency, stock.num_in_stock = (
        data["price"],
        "RUB",
        data["stock"],
    )
    stock.save()
    listing = listing or Listing(product=product, shop=shop)
    listing.category = data["category"]
    listing.flower_kind = data.get("flower_kind", "")
    if data.get("photo"):
        listing.photo = data["photo"]
    listing.full_clean()
    listing.save()
    if "flowers" in data:
        listing.flowers.set(data["flowers"])
    return listing


class ShopForm(forms.ModelForm):
    address_token = forms.CharField(required=False, widget=forms.HiddenInput)

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
            "delivery_settlements",
            "radius_km",
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

    def __init__(self, *args, operator=False, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = Settlement.objects.filter(region__in=["77", "50"])
        self.fields["settlement"].queryset = allowed
        self.fields["delivery_settlements"].queryset = allowed
        self.fields["address"].widget.attrs.update(
            {"data-address-input": "", "autocomplete": "off"}
        )
        if operator:
            self.fields["status"] = forms.ChoiceField(
                label="Статус", choices=Shop.STATUSES, initial=self.instance.status
            )
            self.fields["markup_percent"] = forms.DecimalField(
                label="Наценка платформы, % от цены магазина",
                min_value=0,
                max_value=100,
                max_digits=5,
                decimal_places=2,
                initial=self.instance.markup_percent,
            )
            self.fields["pickup_discount_percent"] = forms.DecimalField(
                label="Скидка за самовывоз, % от нашей наценки",
                min_value=0,
                max_value=100,
                max_digits=5,
                decimal_places=2,
                initial=self.instance.pickup_discount_percent,
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
        for name, lengths in [
            ("inn", {10, 12}),
            ("bank_account", {20}),
            ("bank_bik", {9}),
        ]:
            value = data.get(name)
            if value and (not value.isdigit() or len(value) not in lengths):
                self.add_error(name, "Проверьте число цифр в реквизитах.")
        return data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.operator:
            instance.status = self.cleaned_data["status"]
            instance.markup_percent = self.cleaned_data["markup_percent"]
            instance.pickup_discount_percent = self.cleaned_data[
                "pickup_discount_percent"
            ]
            instance.delivery_fee = self.cleaned_data["delivery_fee"]
        if commit:
            instance.full_clean()
            if instance.pk:
                fields = [
                    name
                    for name in self.Meta.fields
                    if not instance._meta.get_field(name).many_to_many
                ]
                fields += ["latitude", "longitude"]
                if self.operator:
                    fields += [
                        "status",
                        "markup_percent",
                        "pickup_discount_percent",
                        "delivery_fee",
                    ]
                instance.save(update_fields=fields)
            else:
                instance.save()
            self.save_m2m()
        return instance


class HoursForm(forms.Form):
    weekday = forms.TypedChoiceField(
        label="День недели",
        coerce=int,
        choices=list(
            enumerate(
                [
                    "Понедельник",
                    "Вторник",
                    "Среда",
                    "Четверг",
                    "Пятница",
                    "Суббота",
                    "Воскресенье",
                ]
            )
        ),
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
                hours, minutes = map(int, data.get(field, "").split(":"))
                if (
                    not 0 <= minutes <= 59
                    or not 0 <= hours <= 24
                    or (hours == 24 and (minutes or field == "start"))
                ):
                    raise ValueError
                data[field + "_minute"] = hours * 60 + minutes
            except (ValueError, AttributeError):
                self.add_error(field, "Используйте время в формате ЧЧ:ММ.")
        if not data.get("closed") and data.get("start_minute") == data.get(
            "end_minute"
        ):
            self.add_error("end", "Для круглосуточной работы задайте 00:00–24:00.")
        return data


class ExceptionForm(HoursForm):
    day = forms.DateField(
        label="Дата исключения", widget=forms.DateInput(attrs={"type": "date"})
    )
    weekday = None


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
