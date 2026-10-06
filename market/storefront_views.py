from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST
from oscar.core.loading import get_model

from market.addresses import suggest_addresses
from market.availability import (
    available_slots,
    context_day,
    location_matches,
    receiving_options,
)
from market.catalogue import public_listings
from market.context import ContextForm, get_context
from market.forms import CheckoutContactForm, SignupForm
from market.models import Listing, Shop
from market.orders import basket_groups, cancel_shop_order, place_market_order
from market.pricing import quote_product


def safe_next(request, fallback="market:catalogue"):
    target = request.POST.get("next", "")
    return (
        target
        if url_has_allowed_host_and_scheme(
            target,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
        else reverse(fallback)
    )


def home(request):
    items = public_listings(get_context(request))
    return render(
        request,
        "market/home.html",
        {
            "listings": items[:8],
            "shop_count": Shop.objects.filter(
                status="active", settlement__region__in=["77", "50"]
            ).count(),
        },
    )


def catalogue(request):
    items = public_listings(get_context(request), request.GET)
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "market/catalogue.html",
        {
            "page": Paginator(items, 12).get_page(request.GET.get("page")),
            "total_count": len(items),
            "filters": request.GET,
            "query_string": query.urlencode(),
            "shops": Shop.objects.filter(
                status="active", settlement__region__in=["77", "50"]
            ),
            "flowers": Listing.objects.filter(
                shop__status="active", product__is_public=True
            )
            .exclude(flower_kind="")
            .values_list("flower_kind", flat=True)
            .distinct()
            .order_by("flower_kind"),
        },
    )


def product_detail(request, pk):
    listing = get_object_or_404(
        Listing.objects.select_related("shop__settlement", "product"),
        pk=pk,
        shop__status="active",
        product__is_public=True,
        shop__settlement__region__in=["77", "50"],
    )
    context = get_context(request)
    options = receiving_options(listing.shop, context)
    listing.display_price = (
        quote_product(listing.shop, listing.base_price, context.method).customer
        if listing.base_price is not None
        else None
    )
    return render(
        request,
        "market/product.html",
        {
            "listing": listing,
            "options": options,
            "recommendations": [
                row for row in public_listings(get_context(request)) if row.pk != pk
            ][:4],
        },
    )


def shop_detail(request, slug):
    shop = get_object_or_404(
        Shop.objects.select_related("settlement"),
        slug=slug,
        status="active",
        settlement__region__in=["77", "50"],
    )
    return render(
        request,
        "market/shop.html",
        {
            "shop": shop,
            "listings": public_listings(get_context(request), {"shop": slug}),
        },
    )


@require_POST
def set_context(request):
    if request.POST.get("clear"):
        request.session.pop("receiving", None)
        return redirect(safe_next(request))
    form = ContextForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        request.session["receiving"] = {
            "city": data["city"].pk if data.get("city") else None,
            "address_token": data.get("address_token", ""),
            "method": data["method"],
            "when": data["when"],
            "day": data["day"].isoformat() if data.get("day") else None,
            "hour": data.get("hour"),
        }
        if request.basket.num_lines:
            messages.info(
                request,
                "Условия получения изменились. Проверьте доступность товаров в корзине.",
            )
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect(safe_next(request))


@require_GET
def address_suggestions(request):
    import hashlib

    from django.core.cache import cache

    key = (
        "addr-limit:"
        + hashlib.sha256(request.META.get("REMOTE_ADDR", "local").encode()).hexdigest()
    )
    count = cache.get(key, 0)
    if count >= 120:
        return JsonResponse(
            {"results": [], "message": "Подождите немного перед следующим поиском."},
            status=429,
        )
    cache.set(key, count + 1, 60)
    results = suggest_addresses(request.GET.get("q", ""))
    response = JsonResponse(
        {
            "results": results,
            "message": "Уточните адрес до дома. Если подсказок нет, можно выбрать город и продолжить просмотр."
            if not results
            else "",
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@require_POST
@transaction.atomic
def basket_add(request, pk):
    listing = next(
        (row for row in public_listings(get_context(request)) if row.pk == pk), None
    )
    if listing is None:
        messages.error(
            request, "Этот товар недоступен по выбранным условиям получения."
        )
        return redirect(safe_next(request))
    try:
        basket = editable_basket(request)
        quantity = int(request.POST.get("quantity", "1"))
        current = sum(
            line.quantity
            for line in basket.all_lines()
            if line.product_id == listing.product_id
        )
        if (
            not 1 <= quantity <= 101
            or current + quantity > listing.stockrecord.net_stock_level
        ):
            raise ValueError
        basket.add_product(listing.product, quantity=quantity)
        messages.success(request, f"«{listing.product.title}» добавлен в корзину.")
    except (ValueError, TypeError):
        messages.error(request, "Проверьте количество и наличие товара.")
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    return redirect(safe_next(request))


@require_POST
@transaction.atomic
def basket_update(request, pk):
    try:
        basket = editable_basket(request)
        line = get_object_or_404(basket.lines, pk=pk)
        quantity = int(request.POST.get("quantity", "0"))
        if not 0 <= quantity <= 101 or (
            quantity
            and (not line.stockrecord or quantity > line.stockrecord.net_stock_level)
        ):
            raise ValueError
        if quantity == 0:
            line.delete()
        else:
            line.quantity = quantity
            line.save(update_fields=["quantity"])
        basket.reset_offer_applications()
    except (ValueError, TypeError):
        messages.error(request, "Проверьте количество и наличие товара.")
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    return redirect("market:basket")


def editable_basket(request):
    """All writes acquire the same basket lock as checkout, then recheck status."""
    Basket = get_model("basket", "Basket")
    if not request.basket.pk:
        request.basket.save()
    strategy = request.basket.strategy
    basket = Basket.objects.select_for_update().get(pk=request.basket.pk)
    expected_owner = request.user.pk if request.user.is_authenticated else None
    if basket.owner_id != expected_owner:
        raise PermissionDenied
    basket.strategy = strategy
    request.basket = basket
    if basket.status != Basket.OPEN:
        raise ValidationError(
            "Эта корзина уже оформляется. Обновите страницу перед изменением товаров."
        )
    return basket


def _basket_display(request):
    context, now = get_context(request), timezone.now()
    groups = basket_groups(request.basket)
    for group in groups:
        shop = group["shop"]
        group["base_total"] = 0
        group["delivery_goods_total"] = 0
        group["pickup_goods_total"] = 0
        for item in group["lines"]:
            stock = item["listing"].stockrecord
            if stock and stock.price is not None:
                delivery_quote = quote_product(shop, stock.price)
                pickup_quote = quote_product(shop, stock.price, "pickup")
                quantity = item["line"].quantity
                group["base_total"] += delivery_quote.base * quantity
                group["delivery_goods_total"] += delivery_quote.customer * quantity
                group["pickup_goods_total"] += pickup_quote.customer * quantity
                item["customer_price"] = (
                    pickup_quote if context.method == "pickup" else delivery_quote
                ).customer
        group["pickup_savings"] = (
            group["delivery_goods_total"] - group["pickup_goods_total"]
        )
        group["selected_method"] = request.POST.get(f"method_{shop.pk}", "")
        group["selected_slot"] = request.POST.get(f"slot_{shop.pk}", "")
        group["slot_options"] = {}
        for method in ["delivery", "pickup"]:
            if getattr(shop, f"{method}_enabled"):
                if (
                    method == "delivery"
                    and context.address
                    and not location_matches(shop, method, context)
                ):
                    continue
                slots = available_slots(
                    shop, method, now, context_day(context, shop, now), context.hour
                )
                group["slot_options"][method] = slots
        group["available"] = (
            shop.status == "active"
            and all(
                item["listing"].stockrecord
                and item["listing"].stockrecord.net_stock_level >= item["line"].quantity
                for item in group["lines"]
            )
            and any(group["slot_options"].values())
        )
    return groups


def basket_view(request):
    return render(request, "market/basket.html", {"groups": _basket_display(request)})


@login_required
def checkout(request):
    groups = _basket_display(request)
    form = CheckoutContactForm(
        request.POST or None,
        initial={"name": request.user.first_name or request.user.username},
    )
    if request.method == "POST" and form.is_valid():
        choices = {
            group["shop"].pk: {
                "method": request.POST.get(f"method_{group['shop'].pk}"),
                "slot": request.POST.get(f"slot_{group['shop'].pk}"),
            }
            for group in groups
        }
        try:
            # A duplicate POST may arrive after middleware has opened a fresh basket.
            basket_id = int(request.POST.get("basket_id", "0"))
            basket = get_object_or_404(
                get_model("basket", "Basket"), pk=basket_id, owner=request.user
            )
            if basket.pk != request.basket.pk:
                previous = (
                    get_model("order", "Order")
                    .objects.filter(basket=basket, user=request.user)
                    .first()
                )
                if previous:
                    return redirect("market:order", number=previous.number)
                raise ValidationError("Корзина изменилась. Откройте её ещё раз.")
            address = get_context(request).address
            order = place_market_order(
                request.user, basket, choices, address, form.cleaned_data
            )
            return redirect("market:order", number=order.number)
        except (ValidationError, ValueError) as error:
            form.add_error(
                None,
                "; ".join(error.messages)
                if isinstance(error, ValidationError)
                else "Проверьте корзину.",
            )
    return render(request, "market/checkout.html", {"groups": groups, "form": form})


@login_required
def account_orders(request):
    orders = (
        get_model("order", "Order")
        .objects.filter(user=request.user)
        .prefetch_related("shop_orders__shop")
        .order_by("-date_placed")
    )
    return render(request, "market/orders.html", {"orders": orders})


@login_required
def order_detail(request, number):
    order = get_object_or_404(
        get_model("order", "Order"), number=number, user=request.user
    )
    return render(
        request,
        "market/order.html",
        {"order": order, "parts": order.shop_orders.select_related("shop")},
    )


@login_required
@require_POST
def customer_cancel(request, pk):
    from market.models import ShopOrder

    part = get_object_or_404(
        ShopOrder.objects.select_related("order"), pk=pk, order__user=request.user
    )
    try:
        cancel_shop_order(part, request.user)
        messages.success(request, "Часть заказа отменена.")
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    return redirect("market:order", number=part.order.number)


def signup(request):
    if request.user.is_authenticated:
        return redirect("market:account")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("market:account")
    return render(
        request,
        "market/form.html",
        {"form": form, "heading": "Создать аккаунт", "button": "Зарегистрироваться"},
    )
