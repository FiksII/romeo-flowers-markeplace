from urllib.parse import urlencode

from allauth.account.adapter import get_adapter
from allauth.account.internal.flows.login_by_code import LoginCodeVerificationProcess
from allauth.account.internal.stagekit import clear_login
from allauth.account.utils import get_next_redirect_url
from allauth.account.views import SignupView
from allauth.core import ratelimit
from allauth.core.exceptions import RateLimited
from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from market.sms import SmsDeliveryError


@require_POST
def restart_login(request):
    next_url = get_next_redirect_url(request)
    clear_login(request)
    target = reverse("market:login")
    if next_url:
        target += "?" + urlencode({"next": next_url})
    return HttpResponseRedirect(target)


class SmsSignupView(SignupView):
    """Let existing phone owners use the same SMS entrance as new customers."""

    template_name = "account/signup.html"

    def form_valid(self, form):
        phone = form.cleaned_data["phone"]
        if not settings.SMSPILOT_API_KEY:
            form.add_error(None, "Отправка SMS временно недоступна. Попробуйте позже.")
            return self.form_invalid(form)
        if not ratelimit.consume(self.request, action="request_login_code", key=phone):
            form.add_error(
                None, "Слишком много запросов. Подождите перед отправкой нового кода."
            )
            return self.form_invalid(form)
        try:
            with transaction.atomic():
                user = get_adapter().get_user_by_phone(phone)
                if user:
                    LoginCodeVerificationProcess.initiate(
                        request=self.request, user=user, phone=phone
                    )
                    return HttpResponseRedirect(
                        self.passthrough_next_url(reverse("account_confirm_login_code"))
                    )
                return super().form_valid(form)
        except SmsDeliveryError as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)
        except RateLimited:
            form.add_error(None, "Слишком много запросов SMS. Попробуйте позже.")
            return self.form_invalid(form)
        except IntegrityError:
            form.add_error(None, "Не удалось создать аккаунт. Попробуйте ещё раз.")
            return self.form_invalid(form)
