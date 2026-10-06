from django.db import migrations


def add_variants(apps, schema_editor):
    Flower = apps.get_model("market", "Flower")
    for name, rank in [("Роза красная", 0), ("Роза белая", 0), ("Василёк", 20)]:
        Flower.objects.using(schema_editor.connection.alias).get_or_create(
            name=name, defaults={"rank": rank}
        )


class Migration(migrations.Migration):
    dependencies = [("market", "0004_flower_alter_listing_category_listing_flowers")]
    operations = [migrations.RunPython(add_variants, migrations.RunPython.noop)]
