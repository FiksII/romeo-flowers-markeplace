import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from oscar.core.loading import get_model

from market.access import get_shop_for_user, shops_for_user
from market.forms import (
    ExceptionForm,
    ProductForm,
    ShopForm,
    ShopInfoForm,
    ShopPayoutForm,
    WeeklyHoursForm,
    save_listing,
)
from market.models import (
    AuditEntry,
    DateException,
    Membership,
    Shop,
    ShopOrder,
)
from market.orders import transition_shop_order
from market.reporting import dashboard_report


def bouquet_summary(shop):
    """Counts shown on the dashboard and the bouquets page."""
    listings = list(
        shop.listings.select_related("product__product_class").prefetch_related(
            "product__stockrecords"
        )
    )
    return {
        "total": len(listings),
        "on_sale": sum(
            1 for row in listings if row.product.is_public and not row.sold_out
        ),
        "sold_out": sum(1 for row in listings if row.sold_out),
        "hidden": sum(1 for row in listings if not row.product.is_public),
        "no_delivery_price": sum(
            1 for row in listings if shop.own_delivery and row.delivery_price is None
        ),
        "listings": listings,
    }


def partner_index(request):
    shops = shops_for_user(request.user)
    return render(request, "market/partner_index.html", {"shops": shops})


@login_required
def partner_dashboard(request, slug):
    shop = get_shop_for_user(request.user, slug)
    parts = shop.shop_orders.select_related("order").order_by("-created_at")
    report = dashboard_report(parts, request.GET.get("days"))
    return render(
        request,
        "market/dashboard.html",
        {
            "shop": shop,
            "parts": Paginator(report["period_parts"], 20).get_page(
                request.GET.get("page")
            ),
            "bouquets": bouquet_summary(shop),
            **report,
        },
    )


@login_required
def shop_new(request):
    form = ShopForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            shop = form.save(commit=False)
            shop.slug = "shop-" + uuid.uuid4().hex[:12]
            shop.partner = get_model("partner", "Partner").objects.create(
                name=shop.name, code=shop.slug
            )
            shop.status = "review"
            shop.full_clean()
            shop.save()
            form.save_m2m()
            Membership.objects.create(shop=shop, user=request.user)
            AuditEntry.objects.create(
                shop=shop, actor=request.user, action="Заявка на подключение магазина"
            )
        messages.success(
            request,
            "Магазин отправлен на проверку. Можно добавить товары и настроить расписание.",
        )
        return redirect("market:partner-shop", slug=shop.slug)
    return render(
        request,
        "market/form.html",
        {
            "form": form,
            "heading": "Подключить магазин",
            "button": "Отправить на проверку",
            "address_form": True,
        },
    )


@login_required
def shop_settings(request, slug, operator=False):
    """The administrator's page with every profile field; sellers use the cabinet pages."""
    shop = get_shop_for_user(request.user, slug)
    if not operator:
        return redirect("market:shop-info", slug=slug)
    if not request.user.is_superuser:
        raise PermissionDenied
    form = ShopForm(request.POST or None, instance=shop, operator=True)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
            AuditEntry.objects.create(
                shop=shop,
                actor=request.user,
                action="Изменение магазина администратором",
            )
        messages.success(request, "Настройки сохранены.")
        return redirect("market:operator")
    return render(
        request,
        "market/form.html",
        {
            "form": form,
            "shop": shop,
            "heading": "Настройки магазина",
            "button": "Сохранить",
            "address_form": True,
        },
    )


@login_required
def shop_info(request, slug):
    """«Информация о магазине»: address with a map, options and the weekly schedules."""
    shop = get_shop_for_user(request.user, slug)
    is_exception = request.POST.get("form_kind") == "exception"
    posted = request.method == "POST" and not is_exception
    form = ShopInfoForm(request.POST if posted else None, instance=shop)
    hours_form = WeeklyHoursForm(request.POST if posted else None, shop=shop)
    exception_form = ExceptionForm(request.POST if is_exception else None)
    if posted and form.is_valid() and hours_form.is_valid():
        with transaction.atomic():
            form.save()
            hours_form.save()
            AuditEntry.objects.create(
                shop=shop, actor=request.user, action="Информация и расписание магазина"
            )
        messages.success(request, "Информация о магазине сохранена.")
        return redirect("market:shop-info", slug=slug)
    if is_exception and exception_form.is_valid():
        data = exception_form.cleaned_data
        DateException.objects.update_or_create(
            shop=shop,
            day=data["day"],
            method=data["method"],
            defaults={
                "closed": data["closed"],
                "start_minute": data["start_minute"],
                "end_minute": data["end_minute"],
            },
        )
        AuditEntry.objects.create(
            shop=shop, actor=request.user, action="Расписание получения изменено"
        )
        messages.success(request, "Особая дата сохранена.")
        return redirect("market:shop-info", slug=slug)
    return render(
        request,
        "market/shop_info.html",
        {
            "shop": shop,
            "form": form,
            "hours_form": hours_form,
            "exception_form": exception_form,
            "exceptions": shop.date_exceptions.order_by("day"),
            "address_form": True,
        },
    )


@login_required
def shop_payout(request, slug):
    """«Реквизиты и зона доставки»: bank details and the delivery polygon."""
    shop = get_shop_for_user(request.user, slug)
    form = ShopPayoutForm(request.POST or None, instance=shop)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
            AuditEntry.objects.create(
                shop=shop, actor=request.user, action="Реквизиты и зона доставки"
            )
        messages.success(request, "Реквизиты и зона доставки сохранены.")
        return redirect("market:shop-payout", slug=slug)
    return render(request, "market/shop_payout.html", {"shop": shop, "form": form})


@login_required
def product_list(request, slug):
    shop = get_shop_for_user(request.user, slug)
    summary = bouquet_summary(shop)
    show = request.GET.get("show", "all")
    listings = summary["listings"]
    if show == "on-sale":
        listings = [
            row for row in listings if row.product.is_public and not row.sold_out
        ]
    elif show == "sold-out":
        listings = [row for row in listings if row.sold_out]
    elif show == "hidden":
        listings = [row for row in listings if not row.product.is_public]
    else:
        show = "all"
    return render(
        request,
        "market/products.html",
        {"shop": shop, "bouquets": summary, "listings": listings, "show": show},
    )


@login_required
def product_edit(request, slug, pk=None):
    shop = get_shop_for_user(request.user, slug)
    listing = (
        get_object_or_404(shop.listings.select_related("product"), pk=pk)
        if pk
        else None
    )
    initial = (
        {
            "title": listing.product.title,
            "description": listing.product.description,
            "is_public": listing.product.is_public,
            "category": listing.category,
            "flowers": listing.flowers.all(),
            "pickup_price": listing.pickup_price,
            "delivery_price": listing.delivery_price,
            "always_in_stock": listing.always_in_stock,
            "stock": listing.stockrecord.num_in_stock,
        }
        if listing
        else None
    )
    form = ProductForm(
        request.POST or None, request.FILES or None, initial=initial, shop=shop
    )
    if request.method == "POST" and form.is_valid():
        try:
            item = save_listing(shop, form.cleaned_data, listing)
            AuditEntry.objects.create(
                shop=shop, actor=request.user, action=f"Товар #{item.pk} сохранён"
            )
            messages.success(request, "Товар сохранён.")
            return redirect("market:products", slug=slug)
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
    return render(
        request,
        "market/form.html",
        {
            "form": form,
            "shop": shop,
            "heading": "Изменить букет" if listing else "Новый букет",
            "button": "Сохранить букет",
            "back": ("market:products", "Букеты"),
            "product_form": True,
        },
    )


@login_required
@require_POST
def exception_remove(request, slug, pk):
    shop = get_shop_for_user(request.user, slug)
    get_object_or_404(shop.date_exceptions, pk=pk).delete()
    AuditEntry.objects.create(
        shop=shop, actor=request.user, action="Исключение расписания удалено"
    )
    return redirect("market:shop-info", slug=slug)


@login_required
def partner_order(request, slug, pk):
    shop = get_shop_for_user(request.user, slug)
    part = get_object_or_404(shop.shop_orders.select_related("order"), pk=pk)
    if request.method == "POST":
        try:
            transition_shop_order(part, request.user, request.POST.get("status"))
            AuditEntry.objects.create(
                shop=shop,
                actor=request.user,
                action=f"Статус части заказа #{part.pk} изменён",
            )
            messages.success(request, "Статус сохранён.")
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
        return redirect("market:partner-order", slug=slug, pk=pk)
    return render(request, "market/partner_order.html", {"shop": shop, "part": part})


@login_required
def operator_index(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    report = dashboard_report(ShopOrder.objects.all(), request.GET.get("days"))
    return render(
        request,
        "market/operator.html",
        {
            "shops": Shop.objects.select_related("settlement"),
            "parts": Paginator(
                report["period_parts"].select_related("shop", "order"), 20
            ).get_page(request.GET.get("page")),
            "audit": AuditEntry.objects.select_related("actor", "shop").order_by(
                "-created_at"
            )[:30],
            **report,
        },
    )
