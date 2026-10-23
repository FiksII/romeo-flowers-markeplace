from django.db import migrations


def register_photos(apps, schema_editor):
    Listing = apps.get_model("market", "Listing")
    Image = apps.get_model("catalogue", "ProductImage")
    alias = schema_editor.connection.alias
    for listing in Listing.objects.using(alias).exclude(photo="").iterator():
        images = Image.objects.using(alias).filter(product_id=listing.product_id)
        if not images.filter(original=listing.photo.name).exists():
            for image in images.order_by("-display_order"):
                image.display_order += 1
                image.save(using=alias, update_fields=["display_order"])
            Image.objects.using(alias).create(
                product_id=listing.product_id,
                original=listing.photo.name,
                display_order=0,
            )


class Migration(migrations.Migration):
    dependencies = [
        ("market", "0024_fulfillment_status_labels"),
        ("catalogue", "0032_category_exclude_from_menu_category_long_description"),
    ]
    operations = [migrations.RunPython(register_photos, migrations.RunPython.noop)]
