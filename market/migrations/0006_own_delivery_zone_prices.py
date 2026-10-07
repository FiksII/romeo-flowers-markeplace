from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models

import market.zones


class Migration(migrations.Migration):
    """Schema additions for shop-set prices, own delivery and a drawn delivery zone.

    The old radius and pickup discount are removed by 0008, after 0007 has converted
    their values."""

    dependencies = [("market", "0005_flower_colour_variants")]
    operations = [
        migrations.RenameField(
            model_name="shop", old_name="markup_percent", new_name="commission_percent"
        ),
        migrations.AlterField(
            model_name="shop",
            name="commission_percent",
            field=models.DecimalField(
                default=10,
                max_digits=5,
                decimal_places=2,
                validators=[MinValueValidator(0), MaxValueValidator(100)],
                verbose_name="Комиссия платформы, % от цены букета",
            ),
        ),
        migrations.AlterField(
            model_name="shop",
            name="delivery_fee",
            field=models.DecimalField(
                default=0,
                max_digits=9,
                decimal_places=2,
                validators=[MinValueValidator(0)],
                verbose_name="Доставка Ромео, ₽",
            ),
        ),
        migrations.AddField(
            model_name="shop",
            name="own_delivery",
            field=models.BooleanField(default=False, verbose_name="Своя доставка"),
        ),
        migrations.AddField(
            model_name="shop",
            name="delivery_zone",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Многоугольник на карте: [[широта, долгота], ...].",
                validators=[market.zones.validate_zone],
                verbose_name="Зона доставки",
            ),
        ),
        migrations.AddField(
            model_name="listing",
            name="delivery_price",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Только для магазинов со своей доставкой.",
                max_digits=10,
                null=True,
                validators=[MinValueValidator(Decimal("0.01"))],
                verbose_name="Цена с доставкой магазина, ₽",
            ),
        ),
    ]
