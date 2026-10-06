from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone

from market.availability import receiving_options
from market.models import Listing
from market.pricing import quote_product


def _money(value):
    try:
        result = Decimal(value)
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def public_listings(context, filters=None, now=None):
    filters, now = filters or {}, now or timezone.now()
    query = (
        Listing.objects.filter(
            product__is_public=True,
            shop__status="active",
            shop__settlement__region__in=["77", "50"],
        )
        .select_related("product", "shop", "shop__settlement")
        .prefetch_related(
            "product__stockrecords",
            "shop__hours",
            "shop__date_exceptions",
            "shop__delivery_settlements",
        )
    )
    if filters.get("q"):
        term = filters["q"].strip()[:100]
        query = query.filter(
            Q(product__title__icontains=term)
            | Q(product__description__icontains=term)
            | Q(shop__name__icontains=term)
        )
    if filters.get("category") in {"bouquet", "composition", "stems"}:
        query = query.filter(category=filters["category"])
    if filters.get("flower"):
        query = query.filter(flower_kind__iexact=filters["flower"][:80])
    if filters.get("shop"):
        query = query.filter(shop__slug=filters["shop"][:100])
    low, high = _money(filters.get("min_price")), _money(filters.get("max_price"))
    listings = []
    for listing in query:
        stock = listing.stockrecord
        if not stock or stock.price is None or stock.net_stock_level <= 0:
            continue
        customer_price = quote_product(
            listing.shop, stock.price, context.method
        ).customer
        if (low is not None and customer_price < low) or (
            high is not None and customer_price > high
        ):
            continue
        options = receiving_options(listing.shop, context, now)
        if options:
            listing.receiving_options = options
            listing.nearest_slot = min(options.values(), key=lambda slot: slot.start)
            listing.display_price = customer_price
            listings.append(listing)
    sort = filters.get("sort", "newest")
    if sort in {"price-asc", "price-desc"}:
        listings.sort(
            key=lambda item: (item.display_price, item.pk), reverse=sort == "price-desc"
        )
    elif sort == "soon":
        listings.sort(key=lambda item: (item.nearest_slot.start, item.pk))
    else:
        listings.sort(key=lambda item: item.pk, reverse=True)
    return listings
