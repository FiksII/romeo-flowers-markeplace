"""Endpoints of the T-Bank online payment: start, status check and bank notifications."""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from oscar.core.loading import get_model

from market import online_payments
from market.models import OnlinePayment
from market.tbank import TbankError


def _own_order(request, number):
    return get_object_or_404(
        get_model("order", "Order"), number=number, user=request.user
    )


@login_required
@require_POST
def order_pay(request, number):
    """T-Bank's payment form for this order: its URL for the iframe, or a redirect."""
    wants_json = "application/json" in request.headers.get("Accept", "")
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
        if wants_json:
            return JsonResponse({"error": message}, status=409)
        messages.error(request, message)
        return redirect(order_url)
    if wants_json:
        return JsonResponse({"paymentUrl": payment.payment_url})
    return redirect(payment.payment_url)


@login_required
@require_POST
def order_payment_status(request, number):
    """Asks the bank about the latest attempt; the page reloads once it has finished."""
    order = _own_order(request, number)
    payment = order.online_payments.first()
    if payment:
        payment = online_payments.sync_status(payment)
    return JsonResponse(
        {
            "status": payment.status if payment else None,
            "in_progress": bool(payment)
            and payment.status in OnlinePayment.IN_PROGRESS,
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
