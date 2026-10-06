from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Settlement(models.Model):
    REGIONS = [("77", "Москва"), ("50", "Московская область")]
    name = models.CharField("Населённый пункт", max_length=100)
    slug = models.SlugField(unique=True)
    region = models.CharField("Регион", max_length=2, choices=REGIONS)

    class Meta:
        ordering = ["region", "name"]
        verbose_name = "Населённый пункт"
        verbose_name_plural = "Населённые пункты"

    def clean(self):
        if self.region not in {"77", "50"}:
            raise ValidationError(
                {"region": "Доступны только Москва и Московская область."}
            )

    def __str__(self):
        return self.name


class Shop(models.Model):
    STATUSES = [
        ("draft", "Черновик"),
        ("review", "На проверке"),
        ("active", "Активен"),
        ("suspended", "Приостановлен"),
    ]
    partner = models.OneToOneField(
        "partner.Partner", on_delete=models.PROTECT, related_name="market_shop"
    )
    name = models.CharField("Название", max_length=128)
    slug = models.SlugField(unique=True)
    description = models.TextField("О магазине", blank=True)
    settlement = models.ForeignKey(
        Settlement, on_delete=models.PROTECT, verbose_name="Населённый пункт"
    )
    address = models.CharField("Адрес магазина", max_length=255)
    latitude = models.DecimalField(
        "Широта",
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        "Долгота",
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    timezone = models.CharField("Часовой пояс", max_length=64, default="Europe/Moscow")
    phone = models.CharField("Телефон", max_length=25, blank=True)
    status = models.CharField(
        "Статус", max_length=12, choices=STATUSES, default="review"
    )
    delivery_enabled = models.BooleanField("Доставка", default=True)
    pickup_enabled = models.BooleanField("Самовывоз", default=True)
    delivery_settlements = models.ManyToManyField(
        Settlement,
        related_name="delivering_shops",
        blank=True,
        verbose_name="Города доставки",
    )
    radius_km = models.DecimalField(
        "Радиус доставки, км",
        max_digits=6,
        decimal_places=2,
        default=10,
        validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(300)],
    )
    delivery_fee = models.DecimalField(
        "Доставка, ₽",
        max_digits=9,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    minimum_order = models.DecimalField(
        "Минимальная сумма товаров, ₽",
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    prep_minutes = models.PositiveIntegerField(
        "Подготовка до получения, минут",
        default=60,
        validators=[MaxValueValidator(10080)],
    )
    pickup_instructions = models.TextField("Как забрать заказ", blank=True)
    legal_name = models.CharField("Юридическое название", max_length=255, blank=True)
    inn = models.CharField("ИНН", max_length=12, blank=True)
    bank_account = models.CharField("Расчётный счёт", max_length=20, blank=True)
    bank_bik = models.CharField("БИК", max_length=9, blank=True)
    markup_percent = models.DecimalField(
        "Наценка платформы, % от цены магазина",
        max_digits=5,
        decimal_places=2,
        default=10,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    pickup_discount_percent = models.DecimalField(
        "Скидка за самовывоз, % от наценки",
        max_digits=5,
        decimal_places=2,
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Магазин"
        verbose_name_plural = "Магазины"

    def clean(self):
        if self.settlement_id and self.settlement.region not in {"77", "50"}:
            raise ValidationError(
                {
                    "settlement": "Магазин должен находиться в Москве или Московской области."
                }
            )
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValidationError({"timezone": "Укажите действительный часовой пояс."})
        if not self.delivery_enabled and not self.pickup_enabled:
            raise ValidationError("Включите доставку или самовывоз.")

    def __str__(self):
        return self.name


class Membership(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shop_memberships",
    )
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="memberships")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "shop"], name="market_unique_membership"
            )
        ]


class Flower(models.Model):
    name = models.CharField("Цветок", max_length=80, unique=True)
    rank = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["rank", "name"]
        verbose_name = "Цветок"
        verbose_name_plural = "Цветы"

    def __str__(self):
        return self.name


class Listing(models.Model):
    product = models.OneToOneField(
        "catalogue.Product", on_delete=models.CASCADE, related_name="market_listing"
    )
    shop = models.ForeignKey(Shop, on_delete=models.PROTECT, related_name="listings")
    flower_kind = models.CharField("Вид цветов", max_length=80, blank=True)
    flowers = models.ManyToManyField(
        Flower, blank=True, related_name="listings", verbose_name="Цветы в составе"
    )
    category = models.CharField(
        "Категория",
        max_length=20,
        choices=[
            ("bouquet", "Монобукеты"),
            ("composition", "Композиции"),
            ("basket", "Корзины"),
            ("box", "В коробке"),
        ],
        default="bouquet",
    )
    photo = models.ImageField("Фото", upload_to="listings/%Y/%m/", blank=True)
    seed_image = models.CharField(max_length=128, blank=True, editable=False)

    class Meta:
        verbose_name = "Предложение магазина"
        verbose_name_plural = "Предложения магазинов"

    def clean(self):
        if (
            self.product_id
            and self.shop_id
            and self.product.stockrecords.exclude(
                partner_id=self.shop.partner_id
            ).exists()
        ):
            raise ValidationError("Товар содержит цену или остатки другого магазина.")

    @property
    def stockrecord(self):
        return next(
            (
                stock
                for stock in self.product.stockrecords.all()
                if stock.partner_id == self.shop.partner_id
            ),
            None,
        )

    @property
    def base_price(self):
        stock = self.stockrecord
        return stock.price if stock else None

    @property
    def price(self):
        from market.pricing import quote_product

        return (
            quote_product(self.shop, self.base_price).customer
            if self.base_price is not None
            else None
        )

    @property
    def pickup_price(self):
        from market.pricing import quote_product

        return (
            quote_product(self.shop, self.base_price, "pickup").customer
            if self.base_price is not None
            else None
        )

    @property
    def image_url(self):
        from django.templatetags.static import static

        if self.photo:
            return self.photo.url
        return static(self.seed_image or "storefront/images/product-placeholder.svg")

    def __str__(self):
        return f"{self.product.title} · {self.shop.name}"


class WeeklyHours(models.Model):
    METHODS = [
        ("work", "Работа магазина"),
        ("delivery", "Доставка"),
        ("pickup", "Самовывоз"),
    ]
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="hours")
    weekday = models.PositiveSmallIntegerField(
        "День недели", validators=[MaxValueValidator(6)]
    )
    method = models.CharField("Расписание", max_length=8, choices=METHODS)
    start_minute = models.PositiveSmallIntegerField(
        "Начало, минут от полуночи", validators=[MaxValueValidator(1439)]
    )
    end_minute = models.PositiveSmallIntegerField(
        "Конец, минут от полуночи", validators=[MaxValueValidator(1440)]
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "weekday", "method"], name="market_weekly_hours"
            )
        ]

    def clean(self):
        if self.start_minute == self.end_minute:
            raise ValidationError("Для круглосуточного интервала задайте 00:00–24:00.")


class DateException(models.Model):
    shop = models.ForeignKey(
        Shop, on_delete=models.CASCADE, related_name="date_exceptions"
    )
    day = models.DateField("Дата")
    method = models.CharField("Расписание", max_length=8, choices=WeeklyHours.METHODS)
    closed = models.BooleanField("Выходной", default=True)
    start_minute = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(1439)]
    )
    end_minute = models.PositiveSmallIntegerField(
        default=1440, validators=[MaxValueValidator(1440)]
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "day", "method"], name="market_exception_day"
            )
        ]

    def clean(self):
        if not self.closed and self.start_minute == self.end_minute:
            raise ValidationError("Проверьте начало и конец интервала.")


class ShopOrder(models.Model):
    STATUSES = [
        ("accepted", "Принят"),
        ("preparing", "Собирается"),
        ("ready", "Готов к выдаче"),
        ("courier", "Передан курьеру"),
        ("completed", "Получен"),
        ("cancelled", "Отменён"),
    ]
    order = models.ForeignKey(
        "order.Order", on_delete=models.PROTECT, related_name="shop_orders"
    )
    shop = models.ForeignKey(Shop, on_delete=models.PROTECT, related_name="shop_orders")
    method = models.CharField(
        max_length=8, choices=[("delivery", "Доставка"), ("pickup", "Самовывоз")]
    )
    slot_start = models.DateTimeField()
    slot_end = models.DateTimeField()
    address = models.CharField(max_length=300)
    contact_name = models.CharField(max_length=100)
    contact_phone = models.CharField(max_length=25)
    instructions = models.TextField(blank=True)
    goods_total = models.DecimalField(max_digits=12, decimal_places=2)
    base_goods_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pickup_discount_total = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    delivery_total = models.DecimalField(max_digits=12, decimal_places=2)
    delivery_owner = models.CharField(
        max_length=8,
        default="platform",
        choices=[("platform", "Ромео"), ("partner", "Магазин")],
    )
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2)
    commission_total = models.DecimalField(max_digits=12, decimal_places=2)
    partner_total = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=12, choices=STATUSES, default="accepted")
    payment_status = models.CharField(
        max_length=12,
        default="pending",
        choices=[
            ("pending", "Ожидает оплаты"),
            ("paid", "Оплачен"),
            ("refunded", "Возвращён"),
        ],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "shop"], name="market_order_shop")
        ]
        ordering = ["-created_at"]

    @property
    def total(self):
        return self.goods_total + self.delivery_total

    @property
    def platform_delivery_total(self):
        return self.delivery_total if self.delivery_owner == "platform" else Decimal(0)

    @property
    def lines(self):
        return self.order.lines.filter(partner_id=self.shop.partner_id)


class AuditEntry(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    shop = models.ForeignKey(Shop, on_delete=models.PROTECT)
    action = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)


class DemoPayout(models.Model):
    """Fixture transfers for local dashboard previews; never a payment ledger."""

    part = models.OneToOneField(
        ShopOrder, on_delete=models.CASCADE, related_name="demo_payout"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_at = models.DateTimeField()

    class Meta:
        ordering = ["-paid_at", "-pk"]
        verbose_name = "Демонстрационная выплата"
        verbose_name_plural = "Демонстрационные выплаты"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="market_demo_payout_nonnegative"
            )
        ]
