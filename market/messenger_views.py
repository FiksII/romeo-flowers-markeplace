"""Webhooks of the Telegram bot and the VK community (Callback API)."""

import hmac
import json

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from market import messengers
from market.models import MessengerLink


def _json(request):
    try:
        return json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return None


@csrf_exempt
@require_POST
def max_webhook(request):
    secret = request.headers.get("X-Max-Bot-Api-Secret", "")
    if not messengers.channel_enabled(MessengerLink.MAX) or not hmac.compare_digest(secret, messengers.max_webhook_secret()):
        return HttpResponseForbidden()
    if len(request.body) > 512 * 1024:
        return HttpResponse(status=413)
    update = _json(request)
    if not isinstance(update, dict):
        return HttpResponse(status=400)
    from market.max_bot import accept
    accept(update)
    return JsonResponse({"success": True})


@csrf_exempt
@require_POST
def telegram_webhook(request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not messengers.telegram_enabled() or not hmac.compare_digest(
        secret, messengers.telegram_webhook_secret()
    ):
        return HttpResponseForbidden()
    update = _json(request)
    from market.telegram_catalogue import handle_update

    response = handle_update(update)
    if response is not None:
        return JsonResponse(response)
    chat_id, text = messengers.handle_telegram_update(update or {})
    if not chat_id:
        return JsonResponse({})
    # Telegram performs a method returned in the webhook answer: no extra request.
    reply = {"method": "sendMessage", "chat_id": chat_id, "text": text}
    from market.telegram_catalogue import menu_for_chat

    menu = menu_for_chat(chat_id)
    if menu:
        reply["reply_markup"] = menu
    return JsonResponse(reply)


@csrf_exempt
@require_POST
def vk_callback(request):
    event = _json(request)
    if (
        not messengers.vk_enabled()
        or not isinstance(event, dict)
        or str(event.get("group_id")) != str(settings.VK_GROUP_ID)
    ):
        return HttpResponseForbidden()
    if event.get("type") == "confirmation":
        return HttpResponse(settings.VK_CONFIRMATION_CODE, content_type="text/plain")
    if not hmac.compare_digest(
        str(event.get("secret", "")), messengers.vk_callback_secret()
    ):
        return HttpResponseForbidden()
    # VK repeats an event until it gets "ok"; handle every event_id once.
    event_id = str(event.get("event_id", ""))
    if not event_id or cache.add(f"market:vk-event:{event_id}", 1, 24 * 3600):
        chat_id, text = messengers.handle_vk_event(event)
        if chat_id:
            messengers.reply_later(MessengerLink.VK, chat_id, text)
    return HttpResponse("ok", content_type="text/plain")
