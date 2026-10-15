from django.conf import settings
from django.templatetags.static import static
from django.utils import dateformat

from market.access import shops_for_user
from market.addresses import demo_address_search
from market.context import ContextForm, context_initial, get_context
from market.models import Listing, Settlement

DESIGN_IMAGES = "storefront/images/design/"
# Catalogue categories: value, illustration. Labels come from the model choices.
RAIL_CATEGORY_IMAGES = {
    "bouquet": "category-monobukety",
    "composition": "category-kompozitsii",
    "basket": "category-korziny",
    "box": "category-v-korobke",
}


WHEN_LABELS = {"asap": "как можно скорее", "today": "сегодня", "tomorrow": "завтра"}
METHOD_LABELS = {"delivery": "доставка", "pickup": "самовывоз"}


def receiving_details(context):
    """When and how for the place-and-time bar: «сегодня, 14:00–15:00 · самовывоз»."""
    when = WHEN_LABELS.get(context.when, "любое время")
    if context.when == "date" and context.day:
        when = dateformat.format(context.day, "j E")
    if context.hour is not None:
        hour = int(context.hour)
        when += f", {hour:02}:00–{hour + 1:02}:00"
    return " · ".join(
        part for part in (when, METHOD_LABELS.get(context.method)) if part
    )


def rail_categories():
    labels = dict(Listing._meta.get_field("category").choices)
    return [
        {
            "value": value,
            "label": labels[value],
            "image": f"{DESIGN_IMAGES}{image}.webp",
        }
        for value, image in RAIL_CATEGORY_IMAGES.items()
        if value in labels
    ]


def market_context(request):
    context = get_context(request)
    city = (
        Settlement.objects.filter(pk=context.city_id).first()
        if context.city_id
        else None
    )
    return {
        "receiving": context,
        "receiving_form": ContextForm(initial=context_initial(request)),
        "receiving_label": context.address["value"]
        if context.address
        else (city.name if city else "Москва и область"),
        "receiving_details": receiving_details(context),
        "rail_categories": rail_categories(),
        "demo_mode": settings.MARKET_DEMO,
        "address_demo": demo_address_search(),
        "dadata_addresses": bool(settings.DADATA_TOKEN) and not demo_address_search(),
        "yandex_tiles_api_key": settings.YANDEX_TILES_API_KEY,
        "shop_map_config": {
            "yandexTilesKey": settings.YANDEX_TILES_API_KEY,
            "yandexLogo": static("vendor/yandex-tiles/yandex-logo.svg"),
        },
        "has_shops": request.user.is_authenticated
        and shops_for_user(request.user).exists(),
    }
