from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic.base import RedirectView

from market import messenger_views
from market import payment_views as payments
from market import portal_views as portal
from market import storefront_views as views
from market import table_views as tables
from market.auth_views import SmsSignupView, restart_login

app_name = "market"
urlpatterns = [
    path("", views.home, name="home"),
    path("catalogue/", views.catalogue, name="catalogue"),
    path("delivery/", views.legal_page, {"page": "delivery"}, name="delivery"),
    path("privacy/", views.legal_page, {"page": "privacy"}, name="privacy"),
    path("offer/", views.legal_page, {"page": "offer"}, name="offer"),
    path(
        "terms/",
        RedirectView.as_view(pattern_name="market:offer", permanent=True),
        name="terms",
    ),
    path("requisites/", views.legal_page, {"page": "requisites"}, name="requisites"),
    path("products/<int:pk>/", views.product_detail, name="product"),
    path("shops/<slug:slug>/", views.shop_detail, name="shop"),
    path("receiving/", views.set_context, name="receiving"),
    path("addresses/", views.address_suggestions, name="addresses"),
    path("addresses/resolve/", views.address_resolve, name="address-resolve"),
    path("basket/", views.basket_view, name="basket"),
    path("basket/add/<int:pk>/", views.basket_add, name="basket-add"),
    path("basket/update/<int:pk>/", views.basket_update, name="basket-update"),
    path("checkout/", views.checkout, name="checkout"),
    path("account/orders/", views.account_orders, name="account"),
    path("account/orders/live/", views.customer_orders_live, name="customer-orders-live"),
    path("account/orders/<str:number>/", views.order_detail, name="order"),
    path("account/orders/<str:number>/pay/", payments.order_pay, name="order-pay"),
    path(
        "account/orders/<str:number>/pay/status/",
        payments.order_payment_status,
        name="order-pay-status",
    ),
    path(
        "payments/tbank/notification/",
        payments.tbank_notification,
        name="tbank-notification",
    ),
    path(
        "messengers/telegram/webhook/",
        messenger_views.telegram_webhook,
        name="telegram-webhook",
    ),
    path("messengers/max/webhook/", messenger_views.max_webhook, name="max-webhook"),
    path("messengers/vk/callback/", messenger_views.vk_callback, name="vk-callback"),
    path("account/cancel/<int:pk>/", views.customer_cancel, name="customer-cancel"),
    path("signup/", SmsSignupView.as_view(), name="signup"),
    path("login/", SmsSignupView.as_view(), name="login"),
    path("login/restart/", restart_login, name="restart-login"),
    path(
        "login/password/",
        views.MarketLoginView.as_view(),
        name="password-login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("partner/", portal.partner_index, name="partner"),
    path("partner/new/", portal.shop_new, name="shop-new"),
    path("partner/storefront/", portal.partner_storefront, name="partner-storefront"),
    path("partner/<slug:slug>/", portal.partner_dashboard, name="partner-shop"),
    path(
        "partner/<slug:slug>/orders/", portal.partner_orders_page, name="partner-orders"
    ),
    path(
        "partner/<slug:slug>/orders/data/",
        tables.partner_orders,
        name="partner-orders-data",
    ),
    path(
        "partner/<slug:slug>/settings/", portal.partner_settings, name="shop-settings"
    ),
    path("partner/<slug:slug>/info/", portal.shop_info, name="shop-info"),
    path("partner/<slug:slug>/payout/", portal.shop_payout, name="shop-payout"),
    path("partner/<slug:slug>/products/", portal.product_list, name="products"),
    path(
        "partner/<slug:slug>/exceptions/<int:pk>/remove/",
        portal.exception_remove,
        name="exception-remove",
    ),
    path("partner/<slug:slug>/products/new/", portal.product_edit, name="product-new"),
    path(
        "partner/<slug:slug>/products/<int:pk>/",
        portal.product_edit,
        name="product-edit",
    ),
    path(
        "partner/<slug:slug>/orders/<int:pk>/",
        portal.partner_order,
        name="partner-order",
    ),
    path(
        "partner/<slug:slug>/orders/<int:pk>/destination/",
        portal.delivery_destination,
        name="delivery-destination",
    ),
    path(
        "partner/<slug:slug>/balance/", portal.partner_balance, name="partner-balance"
    ),
    path("partner/<slug:slug>/live/", portal.partner_live, name="partner-live"),
    path(
        "partner/<slug:slug>/payouts/<int:pk>/<str:action>/",
        portal.partner_payout_answer,
        name="payout-answer",
    ),
    path("operator/", portal.operator_index, name="operator"),
    path("operator/balances/", portal.operator_balances, name="operator-balances"),
    path(
        "operator/listings/<int:pk>/price/",
        portal.operator_listing_price,
        name="listing-price",
    ),
    path(
        "operator/balances/<slug:slug>/pay/",
        portal.operator_payout_create,
        name="payout-create",
    ),
    path(
        "operator/payouts/<int:pk>/cancel/",
        portal.operator_payout_cancel,
        name="payout-cancel",
    ),
    path("operator/orders/", portal.operator_orders_page, name="operator-orders"),
    path("operator/live/", portal.operator_live, name="operator-live"),
    path("operator/settings/", portal.operator_settings, name="operator-settings"),
    path(
        "operator/notify-shops/",
        portal.operator_notify_shops,
        name="operator-notify-shops",
    ),
    path(
        "operator/new/", portal.shop_new, {"operator": True}, name="operator-shop-new"
    ),
    path("operator/orders/data/", tables.operator_orders, name="operator-orders-data"),
    path(
        "operator/<slug:slug>/",
        portal.shop_settings,
        {"operator": True},
        name="operator-shop",
    ),
]
