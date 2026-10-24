from types import SimpleNamespace

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from market import loyalty
from market.availability import available_slots
from market.catalogue import catalogue_candidates
from market.context import get_context
from market.forms import CheckoutContactForm
from market.models import Listing


class GiftContactForm(CheckoutContactForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_method"].choices = [("gift", "Бесплатно")]
        self.fields["payment_method"].widget = forms.HiddenInput()
        self.fields["payment_method"].initial = "gift"

    def clean_payment_method(self):
        return "gift"


@login_required
def index(request):
    from market.referrals import share_url

    state = loyalty.progress(request.user)
    context = get_context(request)
    gifts = []
    if state["available"]:
        for listing in catalogue_candidates(context):
            methods = [
                method
                for method in ("pickup", "delivery")
                if method in listing.receiving_options
            ]
            listing.gift_sizes = []
            for option in listing.size_options(context.method):
                prices = [
                    listing.price_for(method, option["value"]) for method in methods
                ]
                if prices and min(prices) <= state["limit"]:
                    listing.gift_sizes.append({**option, "price": min(prices)})
            if listing.gift_sizes:
                gifts.append(listing)
    return render(
        request,
        "market/loyalty.html",
        {
            "loyalty_state": state,
            "stamp_slots": range(1, 11),
            "referral_url": share_url(request.user),
            "page": Paginator(gifts, 12).get_page(request.GET.get("page")),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def gift_checkout(request, pk):
    listing = get_object_or_404(
        Listing.objects.select_related("shop__settlement", "product"),
        pk=pk,
        shop__status="active",
        product__is_public=True,
        shop__settlement__region__in=["77", "50"],
    )
    # A replay can return an existing order even after the last entitlement was used.
    if request.method == "POST":
        from market.models import LoyaltyReward

        try:
            existing = LoyaltyReward.objects.filter(
                user=request.user, sequence=int(request.POST.get("sequence", ""))
            ).first()
        except (ValueError, TypeError):
            existing = None
        if existing and existing.order_id:
            return redirect("market:order", number=existing.order.number)
    state = loyalty.progress(request.user)
    if not state["available"]:
        return redirect("market:loyalty")
    size = (
        request.POST.get("size")
        if request.method == "POST"
        else request.GET.get("size")
    )
    size = size or listing.sizes[0]
    if size not in listing.sizes:
        return redirect("market:loyalty")
    from market.addresses import encode_address

    context = get_context(request)
    shop = listing.shop
    method = request.POST.get(f"method_{shop.pk}", "")
    form = GiftContactForm(
        request.POST or None,
        user=request.user,
        has_delivery=method == "delivery",
        initial={
            "customer_name": request.user.get_full_name(),
            "customer_phone": getattr(
                getattr(request.user, "customer_phone", None), "phone", ""
            ),
            "name": request.user.first_name or request.user.username,
            "address": context.address["value"] if context.address else "",
            "address_token": encode_address(context.address) if context.address else "",
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            reward = loyalty.claim(
                request.user,
                listing.pk,
                size,
                {"method": method, "slot": request.POST.get(f"slot_{shop.pk}")},
                form.cleaned_data["verified_address"],
                form.cleaned_data,
                sequence=request.POST.get("sequence"),
            )
            return redirect("market:order", number=reward.order.number)
        except ValidationError as error:
            form.add_error(None, error)
    options = {
        method: available_slots(shop, method, timezone.now())
        for method in ("delivery", "pickup")
        if getattr(shop, f"{method}_enabled")
        and listing.price_for(method, size) <= state["limit"]
    }
    group = {
        "shop": shop,
        "total": 0,
        "delivery_goods_total": 0,
        "pickup_goods_total": 0,
        "pickup_savings": 0,
        "own_delivery": shop.own_delivery,
        "lines": [{"listing": listing, "line": SimpleNamespace(quantity=1)}],
        "slot_options": options,
        "selected_method": method,
        "selected_slot": request.POST.get(f"slot_{shop.pk}", ""),
        "available": listing.can_supply(1) and any(options.values()),
    }
    return render(
        request,
        "market/checkout.html",
        {
            "is_gift": True,
            "form": form,
            "groups": [group],
            "size": size,
            "size_label": next(
                option["label"]
                for option in listing.size_options("pickup")
                if option["value"] == size
            ),
            "loyalty_state": state,
        },
    )
