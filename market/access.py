from django.shortcuts import get_object_or_404
from django.urls import reverse

from market.models import Shop


def shops_for_user(user):
    if not user.is_authenticated:
        return Shop.objects.none()
    if user.is_superuser:
        return Shop.objects.all()
    return Shop.objects.filter(memberships__user=user).distinct()


def partner_dashboard_url(user):
    if not user.is_authenticated or user.is_superuser:
        return None
    shops = list(shops_for_user(user).values_list("slug", flat=True)[:2])
    if len(shops) == 1:
        return reverse("market:partner-shop", kwargs={"slug": shops[0]})
    if shops:
        return reverse("market:partner")
    return None


def get_shop_for_user(user, slug):
    return get_object_or_404(
        shops_for_user(user).select_related("settlement", "partner"), slug=slug
    )


def may_complete_order(user, part):
    """Romeo confirms its deliveries; shops confirm their delivery and pickup."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if part.method == "delivery" and part.delivery_owner == "platform":
        return False
    return shops_for_user(user).filter(pk=part.shop_id).exists()


def next_order_step(user, part):
    step = part.next_step
    if step and step[0] == "completed" and not may_complete_order(user, part):
        return None
    return step


def may_set_delivery_destination(user, part):
    return user.is_authenticated and (
        user.is_superuser
        or (
            part.delivery_owner == "partner"
            and shops_for_user(user).filter(pk=part.shop_id).exists()
        )
    )
