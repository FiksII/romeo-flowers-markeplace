from django.conf import settings
from django.templatetags.static import static

from market.access import shops_for_user
from market.addresses import demo_address_search
from market.context import ContextForm, context_initial, get_context
from market.models import Listing, Settlement

DESIGN_IMAGES = "storefront/images/design/"
# Category circles in the header rail: value, illustration. Labels come from the model choices.
RAIL_CATEGORY_IMAGES = {
    "bouquet": "category-monobukety",
    "composition": "category-kompozitsii",
    "basket": "category-korziny",
    "box": "category-v-korobke",
}


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
