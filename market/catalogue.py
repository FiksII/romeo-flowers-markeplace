from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone

from market.availability import receiving_options
from market.models import Listing
from market.pricing import quote_product

# Old links used plural names (?flower=розы); they map to directory names.
CANONICAL_FLOWERS = {
    "розы": "Роза",
    "пионы": "Пион",
    "тюльпаны": "Тюльпан",
    "хризантемы": "Хризантема",
    "лилии": "Лилия",
    "герберы": "Гербера",
}
MAX_FLOWERS = 8


def _money(value):
    try:
        result = Decimal(value)
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def flower_values(filters) -> list[str]:
    """Flowers asked for with ``?flower=…`` (repeatable), without blanks and repeats."""
    if hasattr(filters, "getlist"):
        raw = filters.getlist("flower")
    else:
        value = filters.get("flower")
        raw = value if isinstance(value, (list, tuple)) else [value] if value else []
    values: list[str] = []
    for item in raw:
        item = str(item).strip()[:80]
        if item and item not in values:
            values.append(item)
    return values[:MAX_FLOWERS]


def has_flower(listing, value: str) -> bool:
    """Whether the listing contains the flower: a directory id or an old text name."""
    flowers = listing.flowers.all()
    if value.isdecimal():
        return any(flower.pk == int(value) for flower in flowers)
    canonical = CANONICAL_FLOWERS.get(value.casefold(), value).casefold()
    return (
        any(flower.name.casefold() == canonical for flower in flowers)
        or listing.flower_kind.casefold() == value.casefold()
    )


@dataclass(frozen=True)
class ListingFilter:
    """Category, flowers and price of the catalogue; every chosen flower must be present."""

    category: str = ""
    flowers: tuple[str, ...] = ()
    low: Decimal | None = None
    high: Decimal | None = None

    @classmethod
    def parse(cls, filters):
        filters = filters or {}
        category = filters.get("category", "")
        if category not in dict(Listing._meta.get_field("category").choices):
            category = ""
        return cls(
            category=category,
            flowers=tuple(flower_values(filters)),
            low=_money(filters.get("min_price")),
            high=_money(filters.get("max_price")),
        )

    def matches(self, listing, skip=(), extra_flower="") -> bool:
        """``skip`` leaves a facet out ("category", "flower", "price") to count its choices;
        ``extra_flower`` asks what the result would be with one more flower chosen."""
        if (
            "category" not in skip
            and self.category
            and listing.category != self.category
        ):
            return False
        if "flower" not in skip:
            wanted = list(self.flowers)
            if extra_flower and extra_flower not in wanted:
                wanted.append(extra_flower)
            if not all(has_flower(listing, value) for value in wanted):
                return False
        if "price" not in skip:
            price = listing.display_price
            if self.low is not None and price < self.low:
                return False
            if self.high is not None and price > self.high:
                return False
        return True


def catalogue_candidates(context, filters=None, now=None):
    """Bouquets that can be received in the chosen place and time, narrowed by the
    search text and the shop. Category, flowers and price are left to ListingFilter so
    one pass also gives the counts shown next to each choice."""
    filters, now = filters or {}, now or timezone.now()
    query = (
        Listing.objects.filter(
            product__is_public=True,
            shop__status="active",
            shop__settlement__region__in=["77", "50"],
        )
        .select_related("product", "shop", "shop__settlement")
        .prefetch_related(
            "flowers",
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
    if filters.get("shop"):
        query = query.filter(shop__slug=filters["shop"][:100])
    listings = []
    for listing in query:
        stock = listing.stockrecord
        if not stock or stock.price is None or stock.net_stock_level <= 0:
            continue
        options = receiving_options(listing.shop, context, now)
        if options:
            listing.receiving_options = options
            listing.nearest_slot = min(options.values(), key=lambda slot: slot.start)
            listing.display_price = quote_product(
                listing.shop, stock.price, context.method
            ).customer
            listings.append(listing)
    return listings


def sort_listings(listings, sort="newest"):
    if sort in {"price-asc", "price-desc"}:
        listings.sort(
            key=lambda item: (item.display_price, item.pk), reverse=sort == "price-desc"
        )
    elif sort == "soon":
        listings.sort(key=lambda item: (item.nearest_slot.start, item.pk))
    else:
        listings.sort(key=lambda item: item.pk, reverse=True)
    return listings


def public_listings(context, filters=None, now=None):
    filters = filters or {}
    spec = ListingFilter.parse(filters)
    listings = [
        listing
        for listing in catalogue_candidates(context, filters, now)
        if spec.matches(listing)
    ]
    return sort_listings(listings, filters.get("sort", "newest"))
