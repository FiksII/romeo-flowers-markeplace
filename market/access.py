from django.shortcuts import get_object_or_404

from market.models import Shop


def shops_for_user(user):
    if not user.is_authenticated:
        return Shop.objects.none()
    if user.is_superuser:
        return Shop.objects.all()
    return Shop.objects.filter(memberships__user=user).distinct()


def get_shop_for_user(user, slug):
    return get_object_or_404(
        shops_for_user(user).select_related("settlement", "partner"), slug=slug
    )


def may_set_delivery_destination(user, part):
    return user.is_authenticated and (
        user.is_superuser
        or (
            part.delivery_owner == "partner"
            and shops_for_user(user).filter(pk=part.shop_id).exists()
        )
    )
