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
from market.forms import ExceptionForm, HoursForm, ProductForm, ShopForm, save_listing
from market.models import (
    AuditEntry,
    DateException,
    Membership,
    Shop,
    ShopOrder,
    WeeklyHours,
)
from market.orders import transition_shop_order
from market.reporting import dashboard_report


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
            "listings": shop.listings.select_related("product"),
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
    if operator and not request.user.is_superuser:
        raise PermissionDenied
    shop = get_shop_for_user(request.user, slug)
    form = ShopForm(request.POST or None, instance=shop, operator=operator)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
            AuditEntry.objects.create(
                shop=shop,
                actor=request.user,
                action="Изменение магазина администратором"
                if operator
                else "Настройки и реквизиты магазина",
            )
        messages.success(request, "Настройки сохранены.")
        return redirect(
            "market:operator" if operator else "market:partner-shop",
            **({} if operator else {"slug": slug}),
        )
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
            "price": listing.base_price,
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
            return redirect("market:partner-shop", slug=slug)
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
    return render(
        request,
        "market/form.html",
        {
            "form": form,
            "shop": shop,
            "heading": "Изменить товар" if listing else "Новый товар",
            "button": "Сохранить товар",
        },
    )


@login_required
def hours_settings(request, slug):
    shop = get_shop_for_user(request.user, slug)
    is_exception = request.POST.get("form_kind") == "exception"
    form = HoursForm(
        request.POST if request.method == "POST" and not is_exception else None
    )
    exception_form = ExceptionForm(
        request.POST if request.method == "POST" and is_exception else None
    )
    active = exception_form if is_exception else form
    if request.method == "POST" and active.is_valid():
        data = active.cleaned_data
        if is_exception:
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
        elif data["closed"]:
            WeeklyHours.objects.filter(
                shop=shop, weekday=data["weekday"], method=data["method"]
            ).delete()
        else:
            WeeklyHours.objects.update_or_create(
                shop=shop,
                weekday=data["weekday"],
                method=data["method"],
                defaults={
                    "start_minute": data["start_minute"],
                    "end_minute": data["end_minute"],
                },
            )
        AuditEntry.objects.create(
            shop=shop, actor=request.user, action="Расписание получения изменено"
        )
        messages.success(request, "Расписание сохранено.")
        return redirect("market:hours", slug=slug)
    return render(
        request,
        "market/hours.html",
        {
            "shop": shop,
            "form": form,
            "exception_form": exception_form,
            "hours": shop.hours.order_by("method", "weekday"),
            "exceptions": shop.date_exceptions.order_by("day"),
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
    return redirect("market:hours", slug=slug)


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
