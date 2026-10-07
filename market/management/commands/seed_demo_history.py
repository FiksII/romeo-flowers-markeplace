from datetime import date, datetime, time, timedelta
from random import Random
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from oscar.core.loading import get_model

from market.availability import next_slot
from market.models import (
    AuditEntry,
    DateException,
    DemoPayout,
    Membership,
    Shop,
    ShopOrder,
)
from market.orders import cancel_shop_order, place_market_order, transition_shop_order
from market.strategy import MarketplaceStrategy

MOSCOW = ZoneInfo("Europe/Moscow")
PASSWORD = "Romeo-demo-2026"
SHOP_ACCOUNTS = {
    "petal-studio": "partner",
    "garden-studio": "partner_garden",
    "morning-flowers": "partner_morning",
}
NAMES = [
    ("Анна", "Соколова"),
    ("Михаил", "Орлов"),
    ("Елена", "Миронова"),
    ("Дмитрий", "Лебедев"),
    ("Мария", "Волкова"),
    ("Алексей", "Морозов"),
    ("Дарья", "Зайцева"),
    ("Илья", "Белов"),
    ("Полина", "Крылова"),
    ("Артём", "Фомин"),
    ("Ольга", "Никитина"),
    ("Сергей", "Павлов"),
    ("Ксения", "Громова"),
    ("Андрей", "Макаров"),
    ("София", "Лукина"),
    ("Николай", "Давыдов"),
    ("Валерия", "Тихонова"),
    ("Роман", "Иванов"),
    ("Вера", "Смирнова"),
    ("Павел", "Кузнецов"),
]


class Command(BaseCommand):
    help = "Заполнить локальное демо аккаунтами и историей заказов за несколько недель."

    def add_arguments(self, parser):
        parser.add_argument(
            "--weeks", type=int, default=6, help="1–52 недели; по умолчанию 6."
        )
        parser.add_argument(
            "--orders-per-day", type=int, default=4, help="1–20 общих заказов в день."
        )
        parser.add_argument(
            "--end-date",
            default=None,
            help="Последний день истории YYYY-MM-DD; по умолчанию сегодня.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=2026,
            help="Начальное значение генератора сценариев.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.MARKET_DEMO:
            raise CommandError(
                "Заполнение доступно только с MARKET_DEMO=True. Используйте romeo_market.demo_settings."
            )
        weeks, per_day = options["weeks"], options["orders_per_day"]
        if not 1 <= weeks <= 52 or not 1 <= per_day <= 20:
            raise CommandError("Укажите 1–52 недели и 1–20 заказов в день.")
        now = timezone.now().astimezone(MOSCOW)
        try:
            end = (
                date.fromisoformat(options["end_date"])
                if options["end_date"]
                else now.date()
            )
        except (ValueError, TypeError):
            raise CommandError("Дата должна иметь формат YYYY-MM-DD.")
        if end > now.date():
            raise CommandError("Последний день истории не может быть в будущем.")
        start = end - timedelta(days=weeks * 7 - 1)
        call_command("seed_marketplace", with_accounts=True, stdout=self.stdout)
        buyers, operator, shops = self.prepare_accounts(now)
        Order = get_model("order", "Order")
        existing = set(
            Order.objects.filter(number__startswith="DEMO-").values_list(
                "number", flat=True
            )
        )
        created = 0
        for offset in range(weeks * 7):
            day = start + timedelta(days=offset)
            for index in range(per_day):
                number = f"DEMO-{day:%Y%m%d}-{index + 1:02}"
                if number in existing:
                    continue
                rng = Random(f"{options['seed']}:{day}:{index}")
                current = day == now.date()
                placed = (
                    max(
                        datetime.combine(day, time.min, MOSCOW),
                        now - timedelta(minutes=5 if index % 8 < 3 else 240),
                    )
                    if current
                    else datetime.combine(day, time(9 + index % 6), MOSCOW)
                )
                # Alternating two-store Moscow orders and single-store regional orders.
                selected = (
                    [shops[0], shops[1]]
                    if index % 3 == 0
                    else [shops[2] if index % 3 == 1 else shops[index % 2]]
                )
                buyer = buyers[(offset * per_day + index) % len(buyers)]
                order = self.create_order(number, buyer, selected, placed, rng)
                self.finish_scenario(order, operator, index, current, rng, now)
                created += 1
        self.settle_due_payouts(now)
        self.stdout.write(
            self.style.SUCCESS(
                f"История {start:%d.%m.%Y}–{end:%d.%m.%Y}: добавлено {created} заказов; "
                f"всего демо-заказов {Order.objects.filter(number__startswith='DEMO-').count()}, "
                f"частей магазинов {ShopOrder.objects.filter(order__number__startswith='DEMO-').count()}."
            )
        )
        self.stdout.write(
            "Покупатели: buyer, buyer02 … buyer20. Партнёры: partner, partner_garden, partner_morning. Администратор: operator."
        )
        self.stdout.write(
            f"Пароль новых аккаунтов: {PASSWORD}. Существующие пароли сохранены."
        )
        self.stdout.write(
            "Оплата, возвраты и выплаты в истории имитированы. Реальные деньги не используются."
        )

    def prepare_accounts(self, now):
        User = get_user_model()
        buyers = []
        for index, (first, last) in enumerate(NAMES, 1):
            username = "buyer" if index == 1 else f"buyer{index:02}"
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": f"{username}@example.invalid",
                    "first_name": first,
                    "last_name": last,
                    "date_joined": now - timedelta(days=70 + index),
                },
            )
            if created:
                user.set_password(PASSWORD)
                user.save()
            else:
                # Fill only empty fields of the previously created demo profile.
                for field, value in [("first_name", first), ("last_name", last)]:
                    if not getattr(user, field):
                        setattr(user, field, value)
                        user.save(update_fields=[field])
            buyers.append(user)
        operator = User.objects.get(username="operator")
        if not operator.is_superuser or not operator.is_staff:
            raise CommandError(
                "Имя operator занято обычным аккаунтом. Оно не будет автоматически повышено до администратора."
            )
        for field, value in [("first_name", "Супер"), ("last_name", "Администратор")]:
            if not getattr(operator, field):
                setattr(operator, field, value)
                operator.save(update_fields=[field])
        shops = []
        for slug, username in SHOP_ACCOUNTS.items():
            shop = Shop.objects.get(slug=slug)
            if shop.status != "active":
                raise CommandError(
                    f"Демо-магазин «{shop.name}» неактивен. Его статус не будет изменён генератором."
                )
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": f"{username}@example.invalid",
                    "first_name": "Партнёр",
                    "last_name": shop.name,
                },
            )
            if created:
                user.set_password(PASSWORD)
                user.save()
            for field, value in [("first_name", "Партнёр"), ("last_name", shop.name)]:
                if not getattr(user, field):
                    setattr(user, field, value)
                    user.save(update_fields=[field])
            Membership.objects.get_or_create(user=user, shop=shop)
            values = {
                "legal_name": f"ДЕМО · ООО «{shop.name}»",
                "inn": "0000000000",
                "bank_account": "00000000000000000000",
                "bank_bik": "000000000",
                "phone": "+7 (000) 000-00-00",
            }
            changed = []
            for field, value in values.items():
                if not getattr(shop, field):
                    setattr(shop, field, value)
                    changed.append(field)
            if changed:
                shop.save(update_fields=changed)
            AuditEntry.objects.get_or_create(
                shop=shop,
                actor=operator,
                action="Демо: профиль и история магазина заполнены",
            )
            shops.append(shop)
        DateException.objects.get_or_create(
            shop=shops[1],
            day=now.date() + timedelta(days=7),
            method="pickup",
            defaults={"closed": True},
        )
        return buyers, operator, shops

    def create_order(self, number, buyer, shops, placed, rng):
        basket = get_model("basket", "Basket").objects.create(owner=buyer)
        basket.strategy = MarketplaceStrategy()
        choices = {}
        for index, shop in enumerate(shops):
            listings = list(
                shop.listings.filter(
                    product__is_public=True, product__upc__startswith="DEMO-ROMEO-"
                )
                .select_related("product")
                .order_by("product__upc")
            )
            if not listings:
                raise CommandError(f"У «{shop.name}» нет опубликованных демо-товаров.")
            for item in rng.sample(listings, k=rng.randint(1, min(2, len(listings)))):
                basket.add_product(item.product, quantity=rng.randint(1, 2))
            method = "pickup" if (index + rng.randint(0, 1)) % 2 else "delivery"
            slot = next_slot(shop, method, placed)
            if not slot:
                raise CommandError(
                    f"У «{shop.name}» нет доступных интервалов {method}."
                )
            choices[shop.pk] = {"method": method, "slot": slot.value}
        city = shops[0].settlement
        address = {
            "region": city.region,
            "city": city.name,
            "value": "Москва, Тверская улица, дом 1"
            if city.region == "77"
            else "Химки, Московская улица, дом 1",
            "latitude": "55.756700" if city.region == "77" else "55.889700",
            "longitude": "37.613700" if city.region == "77" else "37.444300",
        }
        order = place_market_order(
            buyer,
            basket,
            choices,
            address,
            {
                "name": buyer.get_full_name() or buyer.username,
                "phone": "+70000000000",
                "instructions": "Демонстрационный заказ: не собирать и не доставлять.",
            },
            now=placed,
        )
        get_model("order", "Order").objects.filter(pk=order.pk).update(
            number=number, date_placed=placed
        )
        ShopOrder.objects.filter(order=order).update(created_at=placed)
        order.number, order.date_placed = number, placed
        return order

    def finish_scenario(self, order, operator, index, current, rng, now):
        if current:
            scenario = [
                "pending",
                "accepted",
                "preparing",
                "courier",
                "ready",
                "completed",
                "cancelled",
                "refunded",
            ][index % 8]
        else:
            scenario = rng.choices(
                ["completed", "cancelled", "refunded"], weights=[8, 1, 1]
            )[0]
        for part in order.shop_orders.select_related("shop"):
            if scenario == "pending":
                continue
            if scenario == "cancelled":
                cancel_shop_order(part, operator)
                continue
            part.payment_status = "paid"
            part.save(update_fields=["payment_status"])
            # Early in the day some stores have not prepared these examples yet.
            # Keep the scenario at a plausible stage instead of receiving a future order.
            if (
                current
                and scenario in {"courier", "ready", "completed", "refunded"}
                and (
                    part.slot_start > now
                    or (scenario in {"completed", "refunded"} and part.slot_end > now)
                )
            ):
                continue
            if scenario == "accepted":
                continue
            transition_shop_order(part, operator, "preparing")
            if scenario == "preparing":
                continue
            transition_shop_order(
                part, operator, "ready" if part.method == "pickup" else "courier"
            )
            if scenario in {"courier", "ready"}:
                continue
            transition_shop_order(part, operator, "completed")
            # Historical sales get a matching demo replenishment, keeping the live catalogue usable.
            Stock = get_model("partner", "StockRecord")
            for line in part.lines:
                stock = Stock.objects.select_for_update().get(pk=line.stockrecord_id)
                if stock.num_in_stock is not None:
                    stock.num_in_stock += line.quantity
                    stock.save(update_fields=["num_in_stock"])
            if scenario == "refunded":
                part.payment_status = "refunded"
                part.status = "cancelled"
                part.save(update_fields=["payment_status", "status"])
        statuses = set(order.shop_orders.values_list("status", flat=True))
        order.status = (
            "Cancelled"
            if statuses == {"cancelled"}
            else "Completed"
            if statuses <= {"completed", "cancelled"}
            else "AwaitingPayment"
            if scenario == "pending"
            else "Processing"
        )
        order.save(update_fields=["status"])

    def settle_due_payouts(self, now):
        # Reruns also settle existing completed examples whose weekly due date arrived.
        due_parts = ShopOrder.objects.filter(
            order__number__startswith="DEMO-",
            status="completed",
            payment_status="paid",
            demo_payout__isnull=True,
        )
        for part in due_parts:
            due_day = part.slot_end.astimezone(MOSCOW).date() + timedelta(days=2)
            due_day += timedelta(days=(-due_day.weekday()) % 7)
            due = datetime.combine(due_day, time(12), MOSCOW)
            if due <= now:
                DemoPayout.objects.get_or_create(
                    part=part,
                    defaults={"amount": part.partner_total, "paid_at": due},
                )
