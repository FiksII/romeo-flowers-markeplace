from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from oscar.core.loading import get_model

from market.models import Listing, Membership, Settlement, Shop, WeeklyHours


class Command(BaseCommand):
    help = "Создать примеры магазинов и букетов только в демо-режиме."

    def add_arguments(self, parser):
        parser.add_argument(
            "--with-accounts",
            action="store_true",
            help="Локальные buyer, partner, operator с паролем Romeo-demo-2026.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.MARKET_DEMO:
            raise CommandError("Демо-данные доступны только с MARKET_DEMO=True.")
        cities = {}
        for slug, name, region in [
            ("moscow", "Москва", "77"),
            ("khimki", "Химки", "50"),
            ("balashikha", "Балашиха", "50"),
        ]:
            cities[slug], _ = Settlement.objects.get_or_create(
                slug=slug, defaults={"name": name, "region": region}
            )
        shops = []
        for slug, name, city, address, lat, lon, start, end, fee in [
            (
                "petal-studio",
                "Лепесток",
                "moscow",
                "Тверская улица, дом 1",
                "55.756700",
                "37.613700",
                0,
                1440,
                "350",
            ),
            (
                "garden-studio",
                "Тихий сад",
                "moscow",
                "Покровка, дом 10",
                "55.758400",
                "37.644500",
                540,
                1200,
                "450",
            ),
            (
                "morning-flowers",
                "Доброе утро",
                "khimki",
                "Московская улица, дом 1",
                "55.889700",
                "37.444300",
                540,
                1260,
                "300",
            ),
        ]:
            partner, _ = get_model("partner", "Partner").objects.get_or_create(
                code=slug, defaults={"name": name}
            )
            shop, created = Shop.objects.get_or_create(
                slug=slug,
                defaults={
                    "partner": partner,
                    "name": name,
                    "settlement": cities[city],
                    "address": address,
                    "latitude": lat,
                    "longitude": lon,
                    "status": "active",
                    "description": "Небольшая мастерская цветов. Собираем сезонные букеты с вниманием к оттенкам и каждой детали.",
                    "radius_km": 15,
                    "delivery_fee": fee,
                    "prep_minutes": 60,
                    "minimum_order": 1500,
                    "pickup_instructions": "Вход с улицы. Назовите номер заказа флористу.",
                    "markup_percent": Decimal(10),
                },
            )
            if created:
                shop.delivery_settlements.add(cities[city])
                for day in range(7):
                    for method in ["work", "delivery", "pickup"]:
                        WeeklyHours.objects.create(
                            shop=shop,
                            weekday=day,
                            method=method,
                            start_minute=start,
                            end_minute=end,
                        )
            shops.append(shop)
        cls, _ = get_model("catalogue", "ProductClass").objects.get_or_create(
            slug="market-flowers",
            defaults={"name": "Цветы", "track_stock": True, "requires_shipping": True},
        )
        names = [
            "Тихое счастье",
            "Розовое облако",
            "Солнечное утро",
            "Нежное признание",
            "Белая история",
            "Тёплые объятия",
            "Лавандовый вечер",
            "Просто любовь",
            "Садовая поэзия",
            "Лёгкое дыхание",
            "Светлый день",
            "Особенный момент",
        ]
        for index, title in enumerate(names, 1):
            shop = shops[(index - 1) // 4]
            product, created = get_model("catalogue", "Product").objects.get_or_create(
                upc=f"DEMO-ROMEO-{index:02}",
                defaults={
                    "title": title,
                    "product_class": cls,
                    "description": "Авторский букет в нежной палитре. Состав: розы, сезонные цветы и свежая зелень. Флорист бережно упакует цветы перед получением.",
                    "is_public": True,
                },
            )
            if created:
                get_model("partner", "StockRecord").objects.create(
                    product=product,
                    partner=shop.partner,
                    partner_sku=f"DEMO-{index:02}",
                    price_currency="RUB",
                    price=Decimal(1900 + index * 250),
                    num_in_stock=20,
                )
                listing = Listing.objects.create(
                    product=product,
                    shop=shop,
                    flower_kind="Розы" if index % 3 else "Сезонные цветы",
                    category="composition" if index % 4 == 0 else "bouquet",
                    seed_image=f"market/bouquets/bouquet-{index:02}.png",
                )
                if index % 3:
                    from market.models import Flower

                    listing.flowers.add(Flower.objects.get(name="Роза"))
        if options["with_accounts"]:
            for username, superuser in [
                ("buyer", False),
                ("partner", False),
                ("operator", True),
            ]:
                user, created = get_user_model().objects.get_or_create(
                    username=username,
                    defaults={
                        "email": f"{username}@example.invalid",
                        "is_superuser": superuser,
                        "is_staff": superuser,
                    },
                )
                if created:
                    user.set_password("Romeo-demo-2026")
                    user.save()
                if username == "partner":
                    Membership.objects.get_or_create(user=user, shop=shops[0])
        self.stdout.write(
            self.style.SUCCESS(
                "Демо-каталог готов: Москва и Московская область. Оплата отключена."
            )
        )
