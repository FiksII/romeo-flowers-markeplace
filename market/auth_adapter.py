import re

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.fields import PhoneField
from allauth.core import context, ratelimit
from django import forms
from django.urls import reverse

from market.models import CustomerPhone
from market.sms import send_sms


class RussianPhoneField(PhoneField):
    def to_python(self, value):
        value = super().to_python(value)
        if not value:
            return value
        if not re.fullmatch(r"\+?[0-9 ()-]+", value):
            raise forms.ValidationError("Проверьте номер телефона.")
        digits = re.sub(r"\D", "", value)
        if len(digits) == 10:
            digits = "7" + digits
        elif len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        if len(digits) != 11 or not digits.startswith("7"):
            raise forms.ValidationError("Введите телефон полностью: +7(***)***-**-**.")
        return "+" + digits


class MarketAccountAdapter(DefaultAccountAdapter):
    def phone_form_field(self, **kwargs):
        field = RussianPhoneField(**kwargs)
        field.label = "Ваш телефон"
        field.widget.attrs.update(
            {
                "placeholder": "+7(***)***-**-**",
                "inputmode": "tel",
                "data-recipient-phone": "",
            }
        )
        return field

    def get_phone(self, user):
        return (
            CustomerPhone.objects.filter(user=user)
            .values_list("phone", "verified")
            .first()
        )

    def set_phone(self, user, phone, verified):
        CustomerPhone.objects.update_or_create(
            user=user, defaults={"phone": phone, "verified": verified}
        )

    def set_phone_verified(self, user, phone):
        CustomerPhone.objects.filter(user=user, phone=phone).update(verified=True)

    def get_user_by_phone(self, phone):
        record = (
            CustomerPhone.objects.select_related("user").filter(phone=phone).first()
        )
        return record.user if record else None

    def send_verification_code_sms(self, user, phone, code, **kwargs):
        # Login resends need the same shared limits as the initial request.
        ratelimit.consume(
            context.request, action="sms_send", key=phone, raise_exception=True
        )
        ratelimit.consume(
            context.request, action="sms_send_hourly", key=phone, raise_exception=True
        )
        send_sms(phone, f"Ромео: код {code}. Действует 5 мин. Никому не сообщайте.")

    def get_login_redirect_url(self, request):
        from market.access import shops_for_user

        if request.user.is_superuser:
            return reverse("market:operator")
        shops = list(shops_for_user(request.user)[:2])
        if len(shops) == 1:
            return reverse("market:partner-shop", kwargs={"slug": shops[0].slug})
        if shops:
            return reverse("market:partner")
        return reverse("market:account")
