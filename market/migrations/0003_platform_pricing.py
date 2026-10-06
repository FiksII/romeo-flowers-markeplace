from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


def keep_existing_order_snapshots(apps, schema_editor):
    Part = apps.get_model("market", "ShopOrder")
    Part.objects.using(schema_editor.connection.alias).update(
        base_goods_total=models.F("goods_total") - models.F("commission_total"),
        delivery_owner="partner",
    )


class Migration(migrations.Migration):
    dependencies = [("market", "0002_demopayout")]
    operations = [
        migrations.RenameField(
            model_name="shop", old_name="commission_percent", new_name="markup_percent"
        ),
        migrations.AlterField(
            model_name="shop",
            name="markup_percent",
            field=models.DecimalField(
                default=10,
                max_digits=5,
                decimal_places=2,
                validators=[MinValueValidator(0), MaxValueValidator(100)],
                verbose_name="Наценка платформы, % от цены магазина",
            ),
        ),
        migrations.AddField(
            model_name="shop",
            name="pickup_discount_percent",
            field=models.DecimalField(
                default=50,
                max_digits=5,
                decimal_places=2,
                validators=[MinValueValidator(0), MaxValueValidator(100)],
                verbose_name="Скидка за самовывоз, % от наценки",
            ),
        ),
        migrations.AddField(
            model_name="shoporder",
            name="base_goods_total",
            field=models.DecimalField(default=0, max_digits=12, decimal_places=2),
        ),
        migrations.AddField(
            model_name="shoporder",
            name="pickup_discount_total",
            field=models.DecimalField(default=0, max_digits=12, decimal_places=2),
        ),
        migrations.AddField(
            model_name="shoporder",
            name="delivery_owner",
            field=models.CharField(
                choices=[("platform", "Ромео"), ("partner", "Магазин")],
                default="platform",
                max_length=8,
            ),
        ),
        migrations.RunPython(keep_existing_order_snapshots, migrations.RunPython.noop),
    ]
