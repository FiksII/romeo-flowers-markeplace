from django.contrib import admin

from market.models import (
    AuditEntry,
    DateException,
    Listing,
    Membership,
    Settlement,
    Shop,
    ShopOrder,
    WeeklyHours,
)


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


class HoursInline(admin.TabularInline):
    model = WeeklyHours
    extra = 0


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "settlement",
        "status",
        "delivery_enabled",
        "pickup_enabled",
    ]
    list_filter = ["status", "settlement__region"]
    inlines = [MembershipInline, HoursInline]
    filter_horizontal = ["delivery_settlements"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        AuditEntry.objects.create(
            actor=request.user, shop=obj, action="Изменение магазина в админ-панели"
        )


admin.site.register(Settlement)
admin.site.register(Listing)
admin.site.register(DateException)


@admin.register(ShopOrder)
class ShopOrderAdmin(admin.ModelAdmin):
    list_display = ["id", "shop", "order", "method", "status", "payment_status"]
    readonly_fields = [field.name for field in ShopOrder._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditEntry)
class AuditAdmin(admin.ModelAdmin):
    list_display = ["created_at", "actor", "shop", "action"]
    readonly_fields = ["created_at", "actor", "shop", "action"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
