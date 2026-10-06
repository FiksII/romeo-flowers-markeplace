from decimal import Decimal

from oscar.apps.partner.prices import FixedPrice, Unavailable
from oscar.apps.partner.strategy import Default

from market.models import Listing
from market.pricing import quote_product


class MarketplaceStrategy(Default):
    def __init__(self, request=None, *, methods=None, shops=None):
        super().__init__(request)
        from market.context import get_context

        self.method = get_context(request).method if request else "delivery"
        self.methods, self.shops = methods or {}, shops or {}
        self.listings = {}

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
        quote = quote_product(shop, stockrecord.price, method)
        return FixedPrice(
            currency=stockrecord.price_currency, excl_tax=quote.customer, tax=Decimal(0)
        )

    def fetch_for_line(self, line, stockrecord=None):
        return self.fetch_for_product(line.product, stockrecord=stockrecord)
