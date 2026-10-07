from django.contrib.auth import views as auth_views
from django.urls import path

from market import portal_views as portal
from market import storefront_views as views
from market import table_views as tables

app_name = "market"
urlpatterns = [
    path("", views.home, name="home"),
    path("catalogue/", views.catalogue, name="catalogue"),
    path("products/<int:pk>/", views.product_detail, name="product"),
    path("shops/<slug:slug>/", views.shop_detail, name="shop"),
    path("receiving/", views.set_context, name="receiving"),
    path("addresses/", views.address_suggestions, name="addresses"),
    path("basket/", views.basket_view, name="basket"),
    path("basket/add/<int:pk>/", views.basket_add, name="basket-add"),
    path("basket/update/<int:pk>/", views.basket_update, name="basket-update"),
    path("checkout/", views.checkout, name="checkout"),
    path("account/orders/", views.account_orders, name="account"),
    path("account/orders/<str:number>/", views.order_detail, name="order"),
    path("account/cancel/<int:pk>/", views.customer_cancel, name="customer-cancel"),
    path("signup/", views.signup, name="signup"),
    path(
        "login/",
        views.MarketLoginView.as_view(),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("partner/", portal.partner_index, name="partner"),
    path("partner/new/", portal.shop_new, name="shop-new"),
    path("partner/<slug:slug>/", portal.partner_dashboard, name="partner-shop"),
    path(
        "partner/<slug:slug>/orders/data/",
        tables.partner_orders,
        name="partner-orders-data",
    ),
    path("partner/<slug:slug>/settings/", portal.shop_settings, name="shop-settings"),
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
    path("operator/", portal.operator_index, name="operator"),
    path("operator/orders/data/", tables.operator_orders, name="operator-orders-data"),
    path(
        "operator/<slug:slug>/",
        portal.shop_settings,
        {"operator": True},
        name="operator-shop",
    ),
]
