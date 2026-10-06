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
