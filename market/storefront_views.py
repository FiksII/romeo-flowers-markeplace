from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponsePermanentRedirect, JsonResponse
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
from market.catalogue import (
    ListingFilter,
    catalogue_candidates,
    public_listings,
    sort_listings,
)
from market.context import ContextForm, get_context
from market.context_processors import rail_categories
from market.filters import (
    filter_chips,
    flower_label,
    price_label,
    price_range_links,
    toggle_category_url,
    toggle_flower_url,
    without_url,
)
from market.forms import CheckoutContactForm, SignupForm
from market.models import Flower, Listing, Shop
from market.orders import basket_groups, cancel_shop_order, place_market_order

# Flower circles under the headings: directory name, plural label, illustration.
FLOWER_RAIL = (
    ("Роза", "Розы", "rose"),
    ("Пион", "Пионы", "peony"),
    ("Ромашка", "Ромашки", "daisy"),
    ("Тюльпан", "Тюльпаны", "tulip"),
    ("Гортензия", "Гортензии", "hydrangea"),
    ("Лилия", "Лилии", "lily"),
    ("Калла", "Каллы", "calla"),
    ("Орхидея", "Орхидеи", "orchid"),
    ("Гвоздика", "Гвоздики", "carnation"),
)


def flower_rail(params):
    """Flower circles; each one adds its flower to the filter or removes it."""
    chosen = set(params.getlist("flower"))
    pks = dict(
        Flower.objects.filter(
            name__in=[name for name, _, _ in FLOWER_RAIL]
        ).values_list("name", "pk")
    )
    return [
        {
            "pk": pks[name],
            "label": label,
            "image": f"storefront/images/design/{image}.webp",
            "url": toggle_flower_url(params, pks[name]),
            "active": str(pks[name]) in chosen,
        }
        for name, label, image in FLOWER_RAIL
        if name in pks
    ]


def safe_next(request, fallback="market:home"):
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


class MarketLoginView(LoginView):
    template_name = "market/login.html"

    def get_default_redirect_url(self):
        from market.access import shops_for_user

        user = self.request.user
        if user.is_superuser:
            return reverse("market:operator")
        shops = list(shops_for_user(user)[:2])
        if len(shops) == 1:
            return reverse("market:partner-shop", kwargs={"slug": shops[0].slug})
        if shops:
            return reverse("market:partner")
        return super().get_default_redirect_url()


def home(request):
    """Main page: banner, entry points and the whole bouquet listing with its filters."""
    params = request.GET
    candidates = catalogue_candidates(get_context(request), params)
    spec = ListingFilter.parse(params)
    items = sort_listings(
        [listing for listing in candidates if spec.matches(listing)],
        params.get("sort", "newest"),
    )
    query = params.copy()
    query.pop("page", None)
    categories = Listing._meta.get_field("category").choices
    shops = list(
        Shop.objects.filter(status="active", settlement__region__in=["77", "50"])
        .select_related("settlement")
        .order_by("name")
    )
    flowers = list(Flower.objects.all())
    chosen = set(spec.flowers)
    category_options = [
        {
            **option,
            "selected": spec.category == option["value"],
            "url": toggle_category_url(params, option["value"]),
            "count": sum(
                1
                for listing in candidates
                if listing.category == option["value"]
                and spec.matches(listing, skip={"category"})
            ),
        }
        for option in rail_categories()
    ]
    flower_options = []
    for flower in flowers:
        count = sum(
            1
            for listing in candidates
            if spec.matches(listing, extra_flower=str(flower.pk))
        )
        flower_options.append(
            {
                "pk": flower.pk,
                "name": flower.name,
                "palette": flower.tag_palette,
                "count": count,
                "selected": str(flower.pk) in chosen,
                "zero": not count and str(flower.pk) not in chosen,
            }
        )
    rail = flower_rail(params)
    active_chips = filter_chips(params, categories, flowers, shops)
    return render(
        request,
        "market/home.html",
        {
            "page": Paginator(items, 12).get_page(params.get("page")),
            "total_count": len(items),
            "filters": params,
            "categories": categories,
            "category_label": dict(categories).get(spec.category, "Категория"),
            "category_options": category_options,
            "category_total": sum(
                1 for listing in candidates if spec.matches(listing, skip={"category"})
            ),
            "flower_rail": rail,
            "flower_options": flower_options,
            "flower_pill": flower_label(params, {str(f.pk): f.name for f in flowers}),
            "flowers_selected": len(spec.flowers),
            "flowers_reset_url": without_url(params, "flower"),
            "price_pill": price_label(params) or "Цена",
            "price_set": bool(price_label(params)),
            "price_ranges": price_range_links(params),
            "price_reset_url": without_url(params, "min_price", "max_price"),
            "reset_url": without_url(
                params, "q", "category", "flower", "min_price", "max_price", "shop"
            ),
            "query_string": query.urlencode(),
            "filter_chips": filter_chips(
                params,
                categories,
                flowers,
                shops,
                hide_category=True,
                hide_flowers={str(item["pk"]) for item in rail},
            ),
            "can_reset_filters": len(active_chips) > 1,
        },
    )


@require_GET
def catalogue(request):
    """``/catalogue/`` moved to the home page; old links and filters keep working."""
    query = request.META.get("QUERY_STRING", "")
    target = reverse("market:home")
    return HttpResponsePermanentRedirect(
        f"{target}?{query}#catalogue" if query else f"{target}#catalogue"
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
    listing.display_price = listing.price_for(context.method)
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
    return _address_response(request)


@require_GET
def address_resolve(request):
    return _address_response(request, resolve=True)


def _address_response(request, *, resolve=False):
    import hashlib

    from django.core.cache import cache

    key = (
        "addr-limit:"
        + hashlib.sha256(request.META.get("REMOTE_ADDR", "local").encode()).hexdigest()
    )
    count = cache.get(key, 0)
    if count >= 120:
        response = JsonResponse(
            {"results": [], "message": "Подождите немного перед следующим поиском."},
            status=429,
        )
        response["Cache-Control"] = "no-store"
        return response
    cache.set(key, count + 1, 60)
    if resolve:
        from market.addresses import resolve_address

        try:
            response = JsonResponse(
                {"result": resolve_address(request.GET.get("token", ""))}
            )
        except ValidationError as error:
            response = JsonResponse({"message": error.messages[0]}, status=400)
        response["Cache-Control"] = "no-store"
        return response
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
        if not 1 <= quantity <= 101 or not listing.can_supply(current + quantity):
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
        listing = Listing.objects.filter(product_id=line.product_id).first()
        if not 0 <= quantity <= 101 or (
            quantity
            and (
                not line.stockrecord or not listing or not listing.can_supply(quantity)
            )
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
        group["delivery_goods_total"] = 0
        group["pickup_goods_total"] = 0
        for item in group["lines"]:
            listing = item["listing"]
            delivery_unit, pickup_unit = (
                listing.price_for("delivery"),
                listing.price_for("pickup"),
            )
            if delivery_unit is not None:
                quantity = item["line"].quantity
                group["delivery_goods_total"] += delivery_unit * quantity
                group["pickup_goods_total"] += pickup_unit * quantity
                item["customer_price"] = (
                    pickup_unit if context.method == "pickup" else delivery_unit
                )
        group["pickup_savings"] = (
            group["delivery_goods_total"] - group["pickup_goods_total"]
        )
        group["own_delivery"] = shop.own_delivery
        group["delivery_fee"] = 0 if shop.own_delivery else shop.delivery_fee
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
                and item["listing"].can_supply(item["line"].quantity)
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
