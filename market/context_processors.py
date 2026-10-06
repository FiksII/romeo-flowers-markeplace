from django.conf import settings

from market.access import shops_for_user
from market.context import ContextForm, context_initial, get_context
from market.models import Settlement


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
        "demo_mode": settings.MARKET_DEMO,
        "has_shops": request.user.is_authenticated
        and shops_for_user(request.user).exists(),
    }
