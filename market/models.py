from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from market.bouquet_options import (
    SIZES,
    TAGS,
    validate_sale_prices,
    validate_size_prices,
    validate_tags,
)
from market.zones import validate_zone


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
    own_delivery = models.BooleanField("Своя доставка", default=False)
    delivery_settlements = models.ManyToManyField(
        Settlement,
        related_name="delivering_shops",
        blank=True,
        verbose_name="Города доставки",
    )
    delivery_zone = models.JSONField(
        "Зона доставки",
        default=list,
        blank=True,
        validators=[validate_zone],
        help_text="Многоугольник на карте: [[широта, долгота], ...].",
    )
    delivery_fee = models.DecimalField(
        "Доставка Ромео, ₽",
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
        "Наценка Ромео, % к цене магазина",
        max_digits=6,
        decimal_places=2,
        default=4,
        validators=[MinValueValidator(0), MaxValueValidator(1000)],
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
        if self.own_delivery and not self.delivery_enabled:
            raise ValidationError(
                {
                    "own_delivery": "Своя доставка работает только при включённой доставке."
                }
            )

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

    @property
    def tag_palette(self):
        from market.flower_colours import FLOWER_PALETTES

        return FLOWER_PALETTES.get(self.name, "blush")


class Listing(models.Model):
    tags = models.JSONField(
        "Теги", default=list, blank=True, validators=[validate_tags]
    )
    size_prices = models.JSONField(
        "Размеры и цены", default=dict, blank=True, validators=[validate_size_prices]
    )
    # Prices Romeo sells at, set by the administrator: {size: {"pickup": "...",
    # "delivery": "..."}}. Without one the buyer pays the shop's price plus the markup.
    sale_prices = models.JSONField(
        "Цены продажи Ромео",
        default=dict,
        blank=True,
        validators=[validate_sale_prices],
    )
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
    delivery_price = models.DecimalField(
        "Цена с доставкой магазина, ₽",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Только для магазинов со своей доставкой.",
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
    def pickup_price(self):
        """The price the shop set for a bouquet picked up in the shop."""
        stock = self.stockrecord
        return stock.price if stock else None

    @property
    def tag_labels(self):
        return [label for code, label in TAGS if code in self.tags]

    @property
    def sizes(self):
        return [
            code for code, _ in SIZES if code in (self.size_prices or {"standard": {}})
        ]

    @property
    def has_size_choice(self):
        return len(self.sizes) > 1

    def prices_for_size(self, size):
        if size not in self.sizes:
            raise ValidationError(
                "Этот размер больше недоступен. Выберите другой размер букета."
            )
        if size == "standard":
            return self.pickup_price, self.delivery_price
        prices = self.size_prices[size]
        return Decimal(prices["pickup"]), Decimal(prices["delivery"]) if prices.get(
            "delivery"
        ) is not None else None

    def sale_key(self, method):
        """Which of Romeo's prices applies: shops without their own delivery have one."""
        return "delivery" if method != "pickup" and self.shop.own_delivery else "pickup"

    def sale_price_for(self, size, method):
        value = ((self.sale_prices or {}).get(size) or {}).get(self.sale_key(method))
        return Decimal(value) if value not in (None, "") else None

    def size_options(self, method):
        return [
            {"value": code, "label": label, "price": self.price_for(method, code)}
            for code, label in SIZES
            if code in self.sizes
        ]

    @property
    def price_variants(self):
        return [
            {
                "label": label,
                "pickup": self.price_for("pickup", code),
                "delivery": self.price_for("delivery", code),
                "delivery_set": self.prices_for_size(code)[1] is not None,
            }
            for code, label in SIZES
            if code in self.sizes
        ]

    def price_for(self, method, size=None):
        """What the buyer pays for one bouquet with this way of receiving it."""
        from market.pricing import customer_price

        if size is None:
            prices = [self.price_for(method, code) for code in self.sizes]
            return min((price for price in prices if price is not None), default=None)
        pickup, delivery = self.prices_for_size(size)
        return (
            customer_price(
                self.shop, pickup, delivery, method, self.sale_price_for(size, method)
            )
            if pickup is not None
            else None
        )

    @property
    def price(self):
        return self.price_for("delivery")

    @property
    def buyer_pickup_price(self):
        """What a buyer pays for pickup; ``pickup_price`` is the shop's own price."""
        return self.price_for("pickup")

    @property
    def always_in_stock(self):
        """Oscar's own switch: classes that do not track stock are always available."""
        return not self.product.get_product_class().track_stock

    @property
    def stock_left(self):
        """Bouquets that can still be bought; ``None`` means no limit."""
        if self.always_in_stock:
            return None
        stock = self.stockrecord
        return max(0, stock.net_stock_level) if stock else 0

    @property
    def sold_out(self):
        return self.stock_left == 0

    def can_supply(self, quantity=1):
        left = self.stock_left
        return left is None or left >= quantity

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
    slot_start = models.DateTimeField(null=True, blank=True)
    slot_end = models.DateTimeField(null=True, blank=True)
    address = models.CharField(max_length=300, blank=True)
    delivery_contact = models.CharField(
        max_length=8,
        default="no_call",
        choices=[
            ("call", "Уточнить у получателя"),
            ("no_call", "Не звонить получателю"),
        ],
    )
    address_details = models.CharField(max_length=300, blank=True)
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
    # Romeo's part of the goods (markup or its own price minus the shop's price) and the
    # markup percent at the time of the order. The shop gets ``partner_total``.
    commission_percent = models.DecimalField(max_digits=6, decimal_places=2)
    commission_total = models.DecimalField(max_digits=12, decimal_places=2)
    partner_total = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=12, choices=STATUSES, default="accepted")
    payment_method = models.CharField(
        max_length=8,
        default="online",
        choices=[
            ("cash", "Наличными при получении"),
            ("online", "Онлайн"),
        ],
    )
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

    @property
    def can_be_worked(self):
        return self.payment_method == "cash" or self.payment_status == "paid"

    @property
    def next_step(self):
        """(status, button label) the shop moves this part to next, or ``None``."""
        if not self.can_be_worked:
            return None
        pickup = self.method == "pickup"
        done = (
            "completed",
            "Получен, наличные приняты" if self.payment_method == "cash" else "Получен",
        )
        return {
            "accepted": ("preparing", "Начать сборку"),
            "preparing": ("ready", "Готов к выдаче")
            if pickup
            else ("courier", "Передан курьеру"),
            "ready": done if pickup else None,
            "courier": None if pickup else done,
        }.get(self.status)

    @property
    def receiving_label(self):
        if self.method == "pickup":
            return "Самовывоз"
        return (
            "Доставка магазина"
            if self.delivery_owner == "partner"
            else "Доставка Ромео"
        )

    @property
    def platform_collects(self):
        """Whether Romeo, not the shop, is the one who receives the customer's money."""
        return self.payment_method == "online" or (
            self.method == "delivery" and self.delivery_owner == "platform"
        )

    @property
    def balance_effect(self):
        """How this part moves the shop's balance once the money has been received."""
        return self.partner_total if self.platform_collects else -self.commission_total


class ShopOrderItem(models.Model):
    """What one bouquet cost when it was bought.

    The values are copied at checkout and never recalculated, so a later change of the
    listing's price (or the listing's removal) does not touch past orders."""

    part = models.ForeignKey(ShopOrder, on_delete=models.CASCADE, related_name="items")
    listing = models.ForeignKey(
        Listing, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    title = models.CharField(max_length=255)
    size = models.CharField(max_length=8, blank=True)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit_commission = models.DecimalField(max_digits=12, decimal_places=2)
    unit_payout = models.DecimalField(max_digits=12, decimal_places=2)
    commission_percent = models.DecimalField(max_digits=6, decimal_places=2)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="market_item_quantity"
            )
        ]

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    @property
    def payout_total(self):
        """What the shop gets for this line: its own price times the quantity."""
        return self.unit_payout * self.quantity

    @property
    def size_label(self):
        return dict(SIZES).get(self.size, "")


class Payout(models.Model):
    """Money Romeo sends a shop to settle its balance.

    Romeo records the transfer; the shop then confirms that it arrived."""

    STATUSES = [
        ("sent", "Отправлена, ждёт подтверждения"),
        ("confirmed", "Получена"),
        ("disputed", "Магазин не получил"),
        ("cancelled", "Отменена"),
    ]
    shop = models.ForeignKey(Shop, on_delete=models.PROTECT, related_name="payouts")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUSES, default="sent")
    note = models.CharField("Комментарий", max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "Выплата магазину"
        verbose_name_plural = "Выплаты магазинам"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="market_payout_positive"
            )
        ]


class BalanceEntry(models.Model):
    """One movement on a shop's balance with Romeo.

    Positive amounts are what Romeo owes the shop, negative ones what the shop owes
    Romeo. The balance is the sum of entries; entries are never edited or deleted."""

    KINDS = [
        ("platform_collected", "Оплата получена Ромео"),
        ("shop_collected", "Наличные получил магазин"),
        ("refund", "Возврат оплаты"),
        ("payout", "Выплата магазину"),
        ("payout_cancelled", "Выплата отменена"),
    ]
    shop = models.ForeignKey(
        Shop, on_delete=models.PROTECT, related_name="balance_entries"
    )
    part = models.ForeignKey(
        ShopOrder,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="balance_entries",
    )
    payout = models.ForeignKey(
        Payout,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="balance_entries",
    )
    kind = models.CharField(max_length=20, choices=KINDS)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "Движение по балансу"
        verbose_name_plural = "Баланс магазинов"
        constraints = [
            models.UniqueConstraint(
                fields=["part", "kind"],
                condition=models.Q(part__isnull=False),
                name="market_balance_once_per_part",
            ),
            models.UniqueConstraint(
                fields=["payout", "kind"],
                condition=models.Q(payout__isnull=False),
                name="market_balance_once_per_payout",
            ),
            models.CheckConstraint(
                condition=~models.Q(amount=0), name="market_balance_nonzero"
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Движения по балансу не изменяются.")
        super().save(*args, **kwargs)


class OnlinePayment(models.Model):
    """One attempt to pay an order through T-Bank internet acquiring.

    Every Init call gets its own ``order_ref`` (T-Bank's OrderId must be unique per
    payment); ``status`` mirrors the bank's payment status."""

    PAID = "CONFIRMED"
    # The customer can still pay through the link of a payment in these statuses.
    OPEN = ("NEW", "FORM_SHOWED")
    # The bank has the money or is taking it; a new attempt must not start.
    IN_PROGRESS = (
        "AUTHORIZING",
        "3DS_CHECKING",
        "3DS_CHECKED",
        "AUTHORIZED",
        "CONFIRMING",
    )

    order = models.ForeignKey(
        "order.Order", on_delete=models.PROTECT, related_name="online_payments"
    )
    order_ref = models.CharField(max_length=50, unique=True)
    payment_id = models.CharField(max_length=20, blank=True, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, default="NEW")
    payment_url = models.URLField(max_length=500, blank=True)
    expires_at = models.DateTimeField()
    # Money taken for parts that were cancelled before the payment arrived.
    returned_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "Онлайн-оплата"
        verbose_name_plural = "Онлайн-оплаты"

    def __str__(self):
        return f"{self.order_ref} · {self.status}"


class AuditEntry(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    shop = models.ForeignKey(Shop, on_delete=models.PROTECT)
    action = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)


class CustomerPhone(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="customer_phone",
    )
    phone = models.CharField(max_length=16, unique=True)
    verified = models.BooleanField(default=False)


class MessengerLink(models.Model):
    """A person's Telegram chat or VK dialog that receives order notifications.

    The row appears with a one-time ``code``; the person sends it to the bot (or the VK
    community), which fills ``chat_id`` in."""

    TELEGRAM, VK = "telegram", "vk"
    CHANNELS = [(TELEGRAM, "Telegram"), (VK, "ВК")]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="messenger_links",
    )
    channel = models.CharField(max_length=10, choices=CHANNELS)
    chat_id = models.CharField(max_length=32, blank=True)
    title = models.CharField(max_length=100, blank=True)
    enabled = models.BooleanField(default=True)
    code = models.CharField(max_length=12, unique=True, null=True, blank=True)
    code_created_at = models.DateTimeField(null=True, blank=True)
    linked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "channel"], name="market_messenger_link_channel"
            )
        ]
        verbose_name = "Привязка мессенджера"
        verbose_name_plural = "Привязки мессенджеров"

    def __str__(self):
        return f"{self.get_channel_display()} · {self.title or self.chat_id or '—'}"

    @property
    def is_linked(self):
        return bool(self.chat_id)


class MessengerMessage(models.Model):
    """A notification waiting for (or after) delivery to one messenger link."""

    PENDING, SENT, FAILED = "pending", "sent", "failed"

    link = models.ForeignKey(
        MessengerLink, on_delete=models.CASCADE, related_name="messages"
    )
    part = models.ForeignKey(
        ShopOrder,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="messenger_messages",
    )
    text = models.TextField()
    status = models.CharField(
        max_length=8,
        default=PENDING,
        choices=[(PENDING, "Ждёт"), (SENT, "Отправлено"), (FAILED, "Не отправлено")],
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # A recipient hears about an order part once, even if payment is confirmed twice.
            models.UniqueConstraint(
                fields=["link", "part"], name="market_messenger_message_once"
            )
        ]
        indexes = [models.Index(fields=["status", "created_at"])]
        ordering = ["-created_at", "-pk"]
        verbose_name = "Сообщение в мессенджер"
        verbose_name_plural = "Сообщения в мессенджеры"
