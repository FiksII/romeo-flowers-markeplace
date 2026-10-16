from contextlib import contextmanager
from decimal import Decimal

from django.core.exceptions import ValidationError
from oscar.apps.partner.availability import Unavailable as UnavailableStock
from oscar.apps.partner.prices import FixedPrice, Unavailable
from oscar.apps.partner.strategy import Default

from market.bouquet_options import line_size
from market.models import Listing
from market.pricing import quote_product


class MarketplaceStrategy(Default):
    def __init__(self, request=None, *, methods=None, shops=None):
        super().__init__(request)
        from market.context import get_context

        self.method = get_context(request).method if request else "delivery"
        self.methods, self.shops = methods or {}, shops or {}
        self.listings = {}
        self.size = None

    @contextmanager
    def for_size(self, size):
        previous, self.size = self.size, size
        try:
            yield
        finally:
            self.size = previous

    def listing_for(self, product):
        if product.pk not in self.listings:
            self.listings[product.pk] = (
                Listing.objects.select_related("shop").filter(product=product).first()
            )
        return self.listings[product.pk]

    def select_stockrecord(self, product):
        listing = self.listing_for(product)
        return listing.stockrecord if listing else None

    def pricing_policy(self, product, stockrecord):
        listing = self.listing_for(product)
        if not listing or not stockrecord or stockrecord.price is None:
            return Unavailable()
        shop = self.shops.get(listing.shop_id, listing.shop)
        method = self.methods.get(shop.pk, self.method)
        try:
            size = self.size or min(
                listing.sizes, key=lambda code: listing.price_for(method, code)
            )
            pickup, delivery = listing.prices_for_size(size)
        except ValidationError:
            return Unavailable()
        # Standard prices always use the locked stock row supplied at checkout.
        if size == "standard":
            pickup = stockrecord.price
        quote = quote_product(
            shop, pickup, delivery, method, listing.sale_price_for(size, method)
        )
        return FixedPrice(
            currency=stockrecord.price_currency, excl_tax=quote.customer, tax=Decimal(0)
        )

    def fetch_for_line(self, line, stockrecord=None):
        with self.for_size(line_size(line)):
            info = self.fetch_for_product(
                line.product, stockrecord=stockrecord or line.stockrecord
            )
        if not info.price.exists:
            # Keep the basket readable when a seller removes a selected size.
            # Checkout rejects it; the last known price is only for display.
            price = FixedPrice(
                currency=line.price_currency,
                excl_tax=line.price_excl_tax or Decimal(0),
                tax=(line.price_incl_tax - line.price_excl_tax)
                if line.price_incl_tax is not None and line.price_excl_tax is not None
                else Decimal(0),
            )
            return info._replace(price=price, availability=UnavailableStock())
        return info
