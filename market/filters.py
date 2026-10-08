"""Links and chips for the catalogue filters on the home page."""

from urllib.parse import urlencode

from django.urls import reverse

from market.catalogue import _money, flower_values

ANCHOR = "catalogue"
PRICE_RANGES = (
    ("До 3 000 ₽", "", "3000"),
    ("3 000 – 5 000 ₽", "3000", "5000"),
    ("От 5 000 ₽", "5000", ""),
)


def _roubles(value) -> str:
    return f"{int(value):,}".replace(",", " ") + " ₽"


def _pairs(params, drop=()):
    """(name, value) pairs of the current query without blanks, ``page`` and ``drop``."""
    lists = (
        params.lists()
        if hasattr(params, "lists")
        else ((k, v if isinstance(v, list) else [v]) for k, v in params.items())
    )
    pairs = []
    for name, values in lists:
        if name in drop or name == "page":
            continue
        pairs += [(name, value) for value in values if value != ""]
    return pairs


def _url(pairs) -> str:
    base = reverse("market:home")
    query = urlencode(pairs)
    return f"{base}?{query}#{ANCHOR}" if query else f"{base}#{ANCHOR}"


def toggle_flower_url(params, pk) -> str:
    """The page with this flower added to the chosen ones, or removed when it is chosen."""
    pk, chosen = str(pk), flower_values(params)
    flowers = [v for v in chosen if v != pk] if pk in chosen else [*chosen, pk]
    return _url(_pairs(params, drop={"flower"}) + [("flower", v) for v in flowers])


def without_url(params, *names) -> str:
    """The page without the given filters."""
    return _url(_pairs(params, drop=set(names)))


def toggle_category_url(params, value) -> str:
    """Select a category or clear the selected one, keeping the other filters."""
    kept = _pairs(params, drop={"category"})
    extra = [] if params.get("category") == value else [("category", value)]
    return _url(kept + extra)


def price_range_links(params):
    """Quick price ranges as plain links that keep every other filter."""
    kept = _pairs(params, drop={"min_price", "max_price"})
    links = []
    for label, low, high in PRICE_RANGES:
        extra = [
            (name, value)
            for name, value in (("min_price", low), ("max_price", high))
            if value
        ]
        links.append(
            {
                "label": label,
                "low": low,
                "high": high,
                "url": _url(kept + extra),
                "active": str(params.get("min_price", "")) == low
                and str(params.get("max_price", "")) == high,
            }
        )
    return links


def price_label(params) -> str:
    low, high = _money(params.get("min_price")), _money(params.get("max_price"))
    low = low if low else None
    if low is not None and high is not None:
        return f"{_roubles(low)} – {_roubles(high)}"
    if low is not None:
        return f"от {_roubles(low)}"
    if high is not None:
        return f"до {_roubles(high)}"
    return ""


def flower_label(params, names) -> str:
    """Text of the flowers button: one or two names, or the first and how many more."""
    chosen = [names.get(value, value) for value in flower_values(params)]
    if not chosen:
        return "Цветы"
    return ", ".join(chosen) if len(chosen) <= 2 else f"{chosen[0]} +{len(chosen) - 1}"


def filter_chips(
    params, categories, flowers, shops, *, hide_category=False, hide_flowers=()
):
    """Applied filters as removable chips: [{"label", "url"}]; each link drops one filter.

    ``params`` is the request's GET, ``categories`` the (value, label) choices,
    ``flowers`` and ``shops`` the directory lists. Unknown values are skipped; the sort
    order is a preference, not a chip. Filters represented by toggle tiles can be hidden.
    """
    chips = []
    term = params.get("q", "").strip()
    if term:
        chips.append({"label": f"«{term[:40]}»", "url": without_url(params, "q")})
    category = dict(categories).get(params.get("category", ""))
    if category and not hide_category:
        chips.append({"label": category, "url": without_url(params, "category")})
    names = {str(item.pk): item.name for item in flowers}
    chosen = flower_values(params)
    for value in chosen:
        if value in hide_flowers:
            continue
        rest = [v for v in chosen if v != value]
        chips.append(
            {
                "label": names.get(value, value[:40]),
                "url": _url(
                    _pairs(params, drop={"flower"}) + [("flower", v) for v in rest]
                ),
            }
        )
    shop = {item.slug: item.name for item in shops}.get(params.get("shop", ""))
    if shop:
        chips.append({"label": shop, "url": without_url(params, "shop")})
    label = price_label(params)
    if label:
        chips.append(
            {"label": label, "url": without_url(params, "min_price", "max_price")}
        )
    return chips
