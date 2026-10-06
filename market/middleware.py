from django.core.cache import cache
from oscar.apps.basket.middleware import BasketMiddleware

from market.orders import expire_unpaid_orders
from market.strategy import MarketplaceStrategy


class MarketplaceBasketMiddleware(BasketMiddleware):
    def apply_offers_to_basket(self, request, basket):
        request.strategy = MarketplaceStrategy(request)
        basket.strategy = request.strategy
        # Discounts need explicit marketplace commission/refund semantics first.
        basket.reset_offer_applications()


class ReservationExpiryMiddleware:
    """Keep the local baseline usable; production also runs the scheduled command."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if cache.add("market-reservation-sweep", True, timeout=60):
            expire_unpaid_orders()
        return self.get_response(request)
