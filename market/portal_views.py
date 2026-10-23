import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from oscar.core.loading import get_model

from market import messengers
from market.access import (
    get_shop_for_user,
    may_set_delivery_destination,
    shops_for_user,
)
from market.forms import (
    DeliveryDestinationForm,
    ExceptionForm,
    ListingPriceForm,
    OperatorShopForm,
    PayoutForm,
    ProductForm,
    ShopForm,
    ShopInfoForm,
    ShopNumberForm,
    ShopPayoutForm,
    WeeklyHoursForm,
    save_listing,
    update_sale_prices,
)
from market.models import (
    AuditEntry,
    DateException,
    Listing,
    Membership,
    MessengerLink,
    Payout,
    Shop,
    ShopOrder,
    WeeklyHours,
)
from market.orders import (
    ACTIVE_STATUSES,
    HISTORY_STATUSES,
    set_delivery_destination,
    transition_shop_order,
)
from market.payments import (
    balance_totals,
    balances_by_shop,
    cancel_payout,
    confirm_payout,
    create_payout,
    dispute_payout,
    shop_balance,
)
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
    report = dashboard_report(parts, request.GET.get("days"), value="partner_total")
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


def _orders_page(request, parts, *, shop=None, extra=None):
    report = dashboard_report(parts, request.GET.get("days"))
    return render(
        request,
        "market/portal_orders.html",
        {
            **(extra or {}),
            "shop": shop,
            "operator_orders": shop is None,
            "parts": Paginator(
                report["period_parts"].select_related("order", "shop"), 20
            ).get_page(request.GET.get("page")),
            "days": report["days"],
            "period_options": report["period_options"],
            "period_start": report["period_start"],
            "period_end": report["period_end"],
        },
    )


@login_required
@require_GET
def partner_orders_page(request, slug):
    shop = get_shop_for_user(request.user, slug)
    # Orders still to be handled are cards, whatever their date; the rest is history.
    active = list(
        shop.shop_orders.filter(status__in=ACTIVE_STATUSES)
        .select_related("order__user__customer_phone", "shop__settlement")
        .prefetch_related("items__listing")
        .order_by(F("slot_start").asc(nulls_last=True), "created_at", "pk")
    )
    return _orders_page(
        request,
        shop.shop_orders.filter(status__in=HISTORY_STATUSES).order_by(
            "-created_at", "-pk"
        ),
        shop=shop,
        extra={
            "new_parts": [part for part in active if part.status == "accepted"],
            "work_parts": [part for part in active if part.status != "accepted"],
            "history": True,
        },
    )


@login_required
@require_GET
def operator_orders_page(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    active = list(
        ShopOrder.objects.filter(status__in=ACTIVE_STATUSES)
        .select_related("order__user__customer_phone", "shop__settlement")
        .prefetch_related("items__listing")
        .order_by(F("slot_start").asc(nulls_last=True), "created_at", "pk")
    )
    romeo_parts = [
        part
        for part in active
        if part.method == "delivery" and part.delivery_owner == "platform"
    ]
    other_parts = [
        part
        for part in active
        if not (part.method == "delivery" and part.delivery_owner == "platform")
    ]
    return _orders_page(
        request,
        ShopOrder.objects.filter(status__in=HISTORY_STATUSES).order_by("-created_at", "-pk"),
        extra={
            "romeo_parts": romeo_parts,
            "new_parts": [part for part in other_parts if part.status == "accepted"],
            "work_parts": [part for part in other_parts if part.status != "accepted"],
            "history": True,
        },
    )


CHANNEL_NAMES = dict(MessengerLink.CHANNELS)
ROW_ACTIONS = {"on", "off", "test", "renew", "delete", "catalogue-on", "catalogue-off"}


def _send_test(request, link):
    name = CHANNEL_NAMES[link.channel]
    try:
        messengers.send_test(link)
    except messengers.RecipientGone:
        link.enabled = False
        link.save(update_fields=["enabled"])
        ok = False
        text = (
            f"{name} не принимает наши сообщения: похоже, бот или сообщество "
            "заблокированы. Выдайте новую ссылку и подключите заново."
        )
    except messengers.MessengerError:
        ok, text = False, "Не удалось отправить проверочное сообщение. Попробуйте позже."
    else:
        ok, text = True, "Проверочное сообщение отправлено."
    messages.add_message(request, messages.SUCCESS if ok else messages.ERROR, text)
    return ok, text


def _row_action(request, link, action):
    name = CHANNEL_NAMES[link.channel]
    if action == "delete":
        link.delete()
        messages.success(
            request,
            "Номер удалён из списка." if link.shop_id else f"{name} отключён.",
        )
    elif action == "renew":
        messengers.new_invitation(link)
        messages.success(
            request, "Выдана новая ссылка-приглашение, прежняя больше не работает."
        )
    elif not link.is_linked:
        messages.error(request, f"{name} по этой ссылке ещё не подключён.")
    elif action in {"catalogue-on", "catalogue-off"}:
        if link.shop_id and link.channel == MessengerLink.TELEGRAM:
            link.can_manage_catalogue = action == "catalogue-on"
            link.save(update_fields=["can_manage_catalogue"])
            AuditEntry.objects.create(
                shop=link.shop, actor=request.user,
                action=f"Добавление букетов в Telegram: {'разрешено' if link.can_manage_catalogue else 'запрещено'} · {link.phone}",
            )
            messages.success(request, "Право на добавление букетов сохранено.")
    elif action == "test":
        return _send_test(request, link)
    else:
        link.enabled = action == "on"
        link.save(update_fields=["enabled"])
        state = "включены" if link.enabled else "выключены"
        messages.success(request, f"Уведомления в {name} {state}.")


def _notification_post(request, shop):
    action = request.POST.get("action")
    if shop and action == "add":
        form = ShopNumberForm(request.POST)
        if not form.is_valid():
            messages.error(request, next(iter(form.errors.values()))[0])
            return
        try:
            messengers.add_shop_number(
                shop, form.cleaned_data["channel"], form.cleaned_data["phone"]
            )
        except ValueError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request, "Номер добавлен. Отправьте человеку ссылку-приглашение."
            )
        return
    if action not in ROW_ACTIONS:
        return
    if shop:
        link = MessengerLink.objects.filter(
            shop=shop, pk=request.POST.get("link") or 0
        ).first()
    else:
        link = MessengerLink.objects.filter(
            user=request.user, channel=request.POST.get("channel")
        ).first()
    if link is not None:
        return _row_action(request, link, action)


def _shop_channels(shop):
    channels = []
    for channel, label in MessengerLink.CHANNELS:
        available = messengers.channel_enabled(channel)
        numbers = []
        for link in MessengerLink.objects.filter(shop=shop, channel=channel):
            if available:
                messengers.ensure_code(link)
            numbers.append(
                {
                    "link": link,
                    "url": messengers.connect_url(link)
                    if available and not link.is_linked
                    else "",
                }
            )
        channels.append(
            {
                "id": channel,
                "label": label,
                "available": available,
                "numbers": numbers,
                "form": ShopNumberForm(
                    initial={"channel": channel}, auto_id=f"id_{channel}_%s"
                ),
            }
        )
    return channels


def _personal_channels(user):
    channels = []
    for channel, label in MessengerLink.CHANNELS:
        link = None
        if messengers.channel_enabled(channel):
            link = messengers.link_with_code(user, channel)
        channels.append(
            {
                "id": channel,
                "label": label,
                "link": link,
                "connect_url": messengers.connect_url(link)
                if link and not link.is_linked
                else "",
            }
        )
    return channels


def _notification_settings_page(request, *, shop=None):
    if request.method == "POST":
        outcome = _notification_post(request, shop)
        if request.POST.get("action") == "test" and "application/json" in request.headers.get("Accept", ""):
            from django.http import JsonResponse

            # Consume flash feedback: this request displays it beside the button.
            list(messages.get_messages(request))
            ok, text = outcome or (False, "Подключение не найдено или недоступно.")
            response = JsonResponse({"ok": ok, "message": text}, status=200 if ok else 400)
            response["Cache-Control"] = "no-store"
            return response
        return redirect(request.path)
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET", "POST"])
    context = {"shop": shop, "lifetime_days": messengers.CODE_LIFETIME.days}
    if shop:
        context["channels"] = _shop_channels(shop)
    else:
        context["channels"] = _personal_channels(request.user)
        context["watched_shops"] = Shop.objects.filter(notify_admins=True).count()
    return render(request, "market/notification_settings.html", context)


@login_required
def partner_settings(request, slug):
    shop = get_shop_for_user(request.user, slug)
    return _notification_settings_page(request, shop=shop)


@login_required
def operator_settings(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    return _notification_settings_page(request)


@login_required
@require_POST
def operator_notify_shops(request):
    """Save the «notify about Romeo deliveries» ticks of the shops on one list page."""
    if not request.user.is_superuser:
        raise PermissionDenied
    shown = [int(pk) for pk in request.POST.getlist("shown") if pk.isdigit()]
    ticked = {int(pk) for pk in request.POST.getlist("notify") if pk.isdigit()}
    Shop.objects.filter(pk__in=shown).update(notify_admins=False)
    Shop.objects.filter(pk__in=ticked.intersection(shown)).update(notify_admins=True)
    messages.success(request, "Уведомления о доставке Ромео сохранены.")
    back = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(back, {request.get_host()}):
        back = reverse("market:operator")
    return redirect(back + "#shops")


@login_required
def shop_new(request, operator=False):
    if operator and not request.user.is_superuser:
        raise PermissionDenied
    form_class = OperatorShopForm if operator else ShopForm
    form = form_class(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            shop = form.save(commit=False)
            shop.slug = "shop-" + uuid.uuid4().hex[:12]
            shop.partner = get_model("partner", "Partner").objects.create(
                name=shop.name, code=shop.slug
            )
            if not operator:
                shop.status = "review"
            shop.full_clean()
            shop.save()
            form.save_m2m()
            WeeklyHours.objects.bulk_create(
                [
                    WeeklyHours(
                        shop=shop, weekday=day, method=method,
                        start_minute=9 * 60, end_minute=21 * 60,
                    )
                    for day in range(7)
                    for method, _ in WeeklyHours.METHODS
                ]
            )
            if operator:
                form.save_memberships()
            else:
                Membership.objects.create(shop=shop, user=request.user)
            AuditEntry.objects.create(
                shop=shop,
                actor=request.user,
                action="Магазин создан администратором"
                if operator
                else "Заявка на подключение магазина",
            )
        messages.success(
            request,
            "Магазин создан. График — ежедневно 09:00–21:00. Добавьте товары или измените расписание."
            if operator
            else "Магазин отправлен на проверку. График — ежедневно 09:00–21:00. Можно добавить товары или изменить расписание.",
        )
        return redirect("market:partner-shop", slug=shop.slug)
    return render(
        request,
        "market/form.html",
        {
            "form": form,
            "heading": "Создать магазин" if operator else "Подключить магазин",
            "button": "Создать магазин" if operator else "Отправить на проверку",
            "operator_form": operator,
            "address_form": True,
        },
    )


@login_required
def shop_settings(request, slug, operator=False):
    """The administrator's page with every profile field; sellers use the cabinet pages."""
    if operator and not request.user.is_superuser:
        raise PermissionDenied
    shop = get_shop_for_user(request.user, slug)
    if not operator:
        return redirect("market:shop-info", slug=slug)
    form = OperatorShopForm(
        request.POST if request.method == "POST" else None, instance=shop
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
            form.save_memberships()
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
            "operator_form": True,
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
            "tags": listing.tags,
            "sizes": listing.sizes,
            "pickup_price": listing.pickup_price,
            "delivery_price": listing.delivery_price,
            "always_in_stock": listing.always_in_stock,
            "stock": listing.stockrecord.num_in_stock,
        }
        if listing
        else None
    )
    if listing:
        for code in listing.sizes:
            if code != "standard":
                pickup, delivery = listing.prices_for_size(code)
                initial[f"pickup_price_{code}"] = pickup
                initial[f"delivery_price_{code}"] = delivery
    form = ProductForm(
        request.POST or None, request.FILES or None, initial=initial, shop=shop, listing=listing
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
    part = get_object_or_404(
        shop.shop_orders.select_related("order__user__customer_phone"), pk=pk
    )
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
        if request.POST.get("next") == "orders":
            if request.user.is_superuser:
                return redirect("market:operator-orders")
            return redirect("market:partner-orders", slug=slug)
        return redirect("market:partner-order", slug=slug, pk=pk)
    return render(request, "market/partner_order.html", {"shop": shop, "part": part})


@login_required
@require_http_methods(["GET", "POST"])
def delivery_destination(request, slug, pk):
    shop = get_shop_for_user(request.user, slug)
    part = get_object_or_404(
        shop.shop_orders.select_related("order", "shop"),
        pk=pk,
        method="delivery",
        status__in=ACTIVE_STATUSES,
    )
    if not may_set_delivery_destination(request.user, part):
        raise PermissionDenied
    form = DeliveryDestinationForm(
        request.POST if request.method == "POST" else None, part=part
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            set_delivery_destination(
                part,
                request.user,
                data["verified_address"],
                data["address_details"],
                data.get("slot_start"),
                data.get("slot_end"),
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Конечная точка доставки сохранена.")
            return redirect("market:partner-order", slug=slug, pk=pk)
    return render(
        request,
        "market/delivery_destination.html",
        {"shop": shop, "part": part, "form": form},
    )


@login_required
def operator_index(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    report = dashboard_report(ShopOrder.objects.all(), request.GET.get("days"))
    shops = Shop.objects.select_related("settlement").prefetch_related(
        "memberships__user"
    )
    query = request.GET.get("q", "").strip()[:180]
    status = request.GET.get("status", "")
    if query:
        shops = shops.filter(
            Q(name__icontains=query)
            | Q(address__icontains=query)
            | Q(settlement__name__icontains=query)
            | Q(phone__icontains=query)
            | Q(memberships__user__username__icontains=query)
            | Q(memberships__user__email__icontains=query)
        ).distinct()
    if status in dict(Shop.STATUSES):
        shops = shops.filter(status=status)
    else:
        status = ""
    return render(
        request,
        "market/operator.html",
        {
            "shops": Paginator(shops.order_by("name", "pk"), 25).get_page(
                request.GET.get("shop_page")
            ),
            "shop_query": query,
            "shop_status": status,
            "shop_statuses": Shop.STATUSES,
            "parts": Paginator(
                report["period_parts"].select_related("shop", "order"), 20
            ).get_page(request.GET.get("page")),
            "audit": AuditEntry.objects.select_related("actor", "shop").order_by(
                "-created_at"
            )[:30],
            **report,
        },
    )


@login_required
@require_GET
def partner_balance(request, slug):
    """Balance with Romeo, every movement behind it and the payouts to confirm."""
    shop = get_shop_for_user(request.user, slug)
    entries = shop.balance_entries.select_related("part__order", "payout")
    return render(
        request,
        "market/partner_balance.html",
        {
            "shop": shop,
            "balance": shop_balance(shop),
            "entries": Paginator(entries, 25).get_page(request.GET.get("page")),
            "payouts": shop.payouts.select_related("created_by")[:20],
            "can_confirm": Membership.objects.filter(
                user=request.user, shop=shop
            ).exists(),
        },
    )


@login_required
@require_POST
def partner_payout_answer(request, slug, pk, action):
    shop = get_shop_for_user(request.user, slug)
    if action not in {"confirm", "dispute"}:
        raise PermissionDenied
    payout = get_object_or_404(shop.payouts, pk=pk)
    try:
        if action == "confirm":
            confirm_payout(payout, request.user)
            messages.success(request, "Спасибо! Получение выплаты подтверждено.")
        else:
            dispute_payout(payout, request.user)
            messages.success(
                request, "Отметили, что выплата не пришла. Ромео проверит."
            )
        AuditEntry.objects.create(
            shop=shop,
            actor=request.user,
            action=f"Выплата #{payout.pk}: "
            + ("получение подтверждено" if action == "confirm" else "не получена"),
        )
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    return redirect("market:partner-balance", slug=slug)


@login_required
@require_GET
def operator_balances(request):
    """Who owes whom: every shop's balance, with payouts to make and track."""
    if not request.user.is_superuser:
        raise PermissionDenied
    balances = balances_by_shop()
    shops = list(Shop.objects.select_related("settlement").order_by("name"))
    unconfirmed = {}
    for payout in Payout.objects.filter(status__in=["sent", "disputed"]):
        unconfirmed[payout.shop_id] = unconfirmed.get(payout.shop_id, 0) + payout.amount
    rows = [
        {
            "shop": shop,
            "balance": balances.get(shop.pk, 0),
            "unconfirmed": unconfirmed.get(shop.pk, 0),
            "form": PayoutForm(
                initial={"amount": balances[shop.pk]}
                if balances.get(shop.pk, 0) > 0
                else None,
                prefix=f"shop{shop.pk}",
            ),
        }
        for shop in shops
    ]
    owed_to_shops, owed_to_romeo = balance_totals(balances)
    return render(
        request,
        "market/operator_balances.html",
        {
            "rows": rows,
            "owed_to_shops": owed_to_shops,
            "owed_to_romeo": owed_to_romeo,
            "payouts": Payout.objects.select_related("shop", "created_by")[:30],
        },
    )


@login_required
@require_POST
def operator_payout_create(request, slug):
    if not request.user.is_superuser:
        raise PermissionDenied
    shop = get_object_or_404(Shop, slug=slug)
    form = PayoutForm(request.POST, prefix=f"shop{shop.pk}")
    try:
        if not form.is_valid():
            raise ValidationError("Укажите сумму выплаты в рублях и копейках.")
        payout = create_payout(
            shop, form.cleaned_data["amount"], request.user, form.cleaned_data["note"]
        )
        AuditEntry.objects.create(
            shop=shop,
            actor=request.user,
            action=f"Выплата #{payout.pk} на {payout.amount} ₽ отправлена",
        )
        messages.success(
            request, f"Выплата {payout.amount} ₽ записана. Магазин получит уведомление."
        )
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    return redirect("market:operator-balances")


@login_required
@require_POST
def operator_payout_cancel(request, pk):
    if not request.user.is_superuser:
        raise PermissionDenied
    payout = get_object_or_404(Payout, pk=pk)
    try:
        cancel_payout(payout, request.user)
        AuditEntry.objects.create(
            shop=payout.shop,
            actor=request.user,
            action=f"Выплата #{payout.pk} отменена",
        )
        messages.success(
            request, "Выплата отменена, долг перед магазином восстановлен."
        )
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    return redirect("market:operator-balances")


@login_required
def operator_listing_price(request, pk):
    """The administrator changes the prices of any shop's bouquet in one place."""
    if not request.user.is_superuser:
        raise PermissionDenied
    listing = get_object_or_404(
        Listing.objects.select_related("shop__settlement", "product"), pk=pk
    )
    form = ListingPriceForm(
        request.POST if request.method == "POST" else None, listing=listing
    )
    back = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(back, {request.get_host()}):
        back = ""
    if request.method == "POST" and form.is_valid():
        try:
            update_sale_prices(listing, form.sale_prices())
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
        else:
            AuditEntry.objects.create(
                shop=listing.shop,
                actor=request.user,
                action=f"Цена продажи букета #{listing.pk} изменена",
            )
            messages.success(
                request,
                "Цена продажи сохранена. Она действует для новых заказов; цена магазина не изменилась.",
            )
            return redirect(back) if back else redirect("market:product", pk=pk)
    return render(
        request,
        "market/listing_price.html",
        {"listing": listing, "form": form, "back": back},
    )


@login_required
@require_GET
def partner_live(request, slug):
    """What the cabinet polls: new orders and payouts waiting for the shop."""
    from django.db.models import Max
    from django.http import JsonResponse

    from market.orders import new_orders

    shop = get_shop_for_user(request.user, slug)
    fresh = new_orders(shop).aggregate(count=Count("pk"), latest=Max("pk"))
    response = JsonResponse(
        {
            "new_orders": fresh["count"],
            "latest_order": fresh["latest"] or 0,
            "payouts": shop.payouts.filter(status="sent").count(),
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_GET
def operator_live(request):
    from django.http import JsonResponse

    from market.templatetags.market_tags import romeo_order_summary

    if not request.user.is_superuser:
        raise PermissionDenied
    fresh = romeo_order_summary()
    response = JsonResponse(
        {
            "new_orders": fresh["count"],
            "latest_order": fresh["latest"] or 0,
            "payouts": 0,
            "revision": fresh["revision"],
        }
    )
    response["Cache-Control"] = "no-store"
    return response
