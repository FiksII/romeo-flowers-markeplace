from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from market import messengers


class Command(BaseCommand):
    help = "Подключить вебхук Telegram-бота и показать настройки Callback API для ВК."

    def handle(self, *args, **options):
        if not settings.SITE_URL.startswith("https://"):
            raise CommandError(
                "Укажите SITE_URL с https:// — мессенджеры шлют события только на HTTPS."
            )
        if messengers.telegram_enabled():
            me = messengers.telegram_call("getMe")
            if me.get("username", "").lower() != settings.TELEGRAM_BOT_USERNAME.lower():
                raise CommandError(
                    f"TELEGRAM_BOT_USERNAME не совпадает с ботом @{me.get('username')}."
                )
            url = settings.SITE_URL + reverse("market:telegram-webhook")
            messengers.telegram_call(
                "setWebhook",
                url=url,
                secret_token=messengers.telegram_webhook_secret(),
                allowed_updates=["message", "callback_query"],
                max_connections=1,
            )
            messengers.telegram_call(
                "setMyCommands",
                commands=[
                    {"command": "start", "description": "Подключить уведомления"},
                    {"command": "stop", "description": "Отключить уведомления"},
                    {"command": "add", "description": "Добавить букет в магазин"},
                    {"command": "cancel", "description": "Отменить добавление букета"},
                ],
            )
            self.stdout.write(f"Telegram: вебхук @{me['username']} → {url}")
        else:
            self.stdout.write(
                "Telegram: не настроен (TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME)."
            )
        if messengers.vk_enabled():
            url = settings.SITE_URL + reverse("market:vk-callback")
            self.stdout.write(
                "ВК: в сообществе → Управление → Работа с API → Callback API укажите\n"
                f"  версию API {messengers.VK_API_VERSION}, адрес {url}\n"
                f"  секретный ключ {messengers.vk_callback_secret()}\n"
                "  события: «Входящее сообщение», «Запрет на получение».\n"
                "  Строку подтверждения сохраните в VK_CONFIRMATION_CODE."
            )
            if not settings.VK_CONFIRMATION_CODE:
                self.stdout.write(
                    "ВК: VK_CONFIRMATION_CODE пока пуст — сервер не пройдёт проверку."
                )
        else:
            self.stdout.write("ВК: не настроено (VK_GROUP_ID, VK_GROUP_TOKEN).")
