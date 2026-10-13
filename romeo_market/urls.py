from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from market.auth_views import SmsSignupView

urlpatterns = [path("admin/", admin.site.urls), path("", include("market.urls"))]
urlpatterns += [
    path("accounts/login/", SmsSignupView.as_view(), name="account_login"),
    path("accounts/signup/", SmsSignupView.as_view(), name="account_signup"),
    path("accounts/", include("allauth.urls")),
]
if settings.DEBUG and not settings.S3_ENABLED:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
