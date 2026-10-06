from oscar.apps.partner.strategy import Default

from market.models import Listing


class MarketplaceStrategy(Default):
    def select_stockrecord(self, product):
        listing = Listing.objects.select_related("shop").filter(product=product).first()
        return listing.stockrecord if listing else None

    def fetch_for_line(self, line, stockrecord=None):
        return self.fetch_for_product(line.product, stockrecord=stockrecord)
