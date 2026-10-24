from django.core.cache import cache
from django.shortcuts import redirect
from oscar.apps.basket.middleware import BasketMiddleware

from market.access import partner_dashboard_url
from market.orders import expire_unpaid_orders
from market.strategy import MarketplaceStrategy


class PartnerStorefrontMiddleware:
    """Shop members work in the portal instead of placing customer orders."""

    customer_routes = frozenset({
        "home", "catalogue", "product", "shop", "receiving", "basket",
        "basket-add", "basket-update", "checkout", "account",
        "customer-orders-live", "order", "order-pay", "order-pay-status",
        "customer-cancel", "loyalty", "loyalty-gift", "referral",
    })

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.resolver_match.view_name == "market:photo":
            return None
        request.partner_dashboard_url = partner_dashboard_url(request.user)
        match = request.resolver_match
        if (
            request.partner_dashboard_url
            and match.namespace == "market"
            and match.url_name in self.customer_routes
        ):
            return redirect(request.partner_dashboard_url)
        return None


class ReferralMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from market.referrals import bind_pending

        bind_pending(request)
        return self.get_response(request)


class SmsErrorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        from django.shortcuts import render

        from market.sms import SmsDeliveryError

        if isinstance(exception, SmsDeliveryError):
            return render(request, "account/sms_unavailable.html", status=503)
        return None


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
