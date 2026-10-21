from django.db import migrations, models


def enable_existing_shops(apps, schema_editor):
    apps.get_model("market", "Shop").objects.using(
        schema_editor.connection.alias
    ).update(notify_admins=True)


class Migration(migrations.Migration):
    dependencies = [("market", "0019_shoporder_delivery_point")]
    operations = [
        migrations.AlterField(
            model_name="shop",
            name="notify_admins",
            field=models.BooleanField(
                default=True,
                verbose_name="Уведомлять супер администраторов о доставке Ромео",
            ),
        ),
        migrations.RunPython(enable_existing_shops, migrations.RunPython.noop),
    ]
