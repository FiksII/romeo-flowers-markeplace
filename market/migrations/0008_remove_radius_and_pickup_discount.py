from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("market", "0007_convert_prices_and_zones")]
    operations = [
        migrations.RemoveField(model_name="shop", name="radius_km"),
        migrations.RemoveField(model_name="shop", name="pickup_discount_percent"),
    ]
