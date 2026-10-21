from django.db import migrations


def enable_delivery(apps, schema_editor):
    apps.get_model("market", "Shop").objects.using(schema_editor.connection.alias).filter(
        delivery_enabled=False
    ).update(delivery_enabled=True)


class Migration(migrations.Migration):
    dependencies = [("market", "0020_shop_notify_admins_default")]
    operations = [migrations.RunPython(enable_delivery, migrations.RunPython.noop)]
