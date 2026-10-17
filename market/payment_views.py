"""Endpoints of the T-Bank online payment: start, status check and bank notifications."""

import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from oscar.core.loading import get_model

from market import online_payments
from market.tbank import TbankError


def _own_order(request, number):
    return get_object_or_404(
        get_model("order", "Order"), number=number, user=request.user
    )


@login_required
@require_POST
def order_pay(request, number):
    """Called by the SpeedPay buttons: returns the bank's PaymentURL for this order."""
    order = _own_order(request, number)
    order_url = reverse("market:order", args=[order.number])
    try:
        payment = online_payments.start_payment(
            order,
            notification_url=request.build_absolute_uri(
                reverse("market:tbank-notification")
            ),
            return_url=request.build_absolute_uri(order_url + "?payment=return"),
            now=timezone.now(),
        )
    except (ValidationError, TbankError) as error:
        message = (
            "; ".join(error.messages)
            if isinstance(error, ValidationError)
            else str(error)
        )
        return JsonResponse({"error": message}, status=409)
    return JsonResponse({"paymentUrl": payment.payment_url})


@login_required
@require_POST
def order_payment_status(request, number):
    """Asks the bank about the latest attempt; the page reloads once the order is paid."""
    order = _own_order(request, number)
    payment = order.online_payments.first()
    if payment:
        payment = online_payments.sync_status(payment)
    return JsonResponse(
        {
            "status": payment.status if payment else None,
            "waiting": online_payments.payable_parts(order).exists(),
        }
    )


@csrf_exempt
@require_POST
def tbank_notification(request):
    try:
        data = json.loads(request.body)
    except ValueError:
        return HttpResponseBadRequest()
    if not online_payments.handle_notification(data):
        return HttpResponse(status=403)
    # T-Bank needs exactly this body, otherwise it repeats the notification.
    return HttpResponse("OK", content_type="text/plain")
