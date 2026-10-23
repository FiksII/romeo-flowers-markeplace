from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from market import messengers


class Command(BaseCommand):
    help = "Подключить вебхуки Telegram/MAX и показать настройки Callback API для ВК."

    def add_arguments(self, parser):
        parser.add_argument(
            "--channel", choices=["all", "telegram", "vk", "max"], default="all",
            help="Настроить только выбранный канал (по умолчанию все).",
        )

    def handle(self, *args, **options):
        if not settings.SITE_URL.startswith("https://"):
            raise CommandError(
                "Укажите SITE_URL с https:// — мессенджеры шлют события только на HTTPS."
            )
        channel = options["channel"]
        if channel in {"all", "telegram"}:
            self.setup_telegram()
        if channel in {"all", "vk"}:
            self.setup_vk()
        if channel in {"all", "max"}:
            self.setup_max()

    def setup_telegram(self):
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

    def setup_vk(self):
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

    def setup_max(self):
        if not messengers.max_enabled():
            self.stdout.write("MAX: не настроен (MAX_BOT_TOKEN, MAX_BOT_USERNAME).")
            return
        try:
            me = messengers.max_call("get_me")
            username = str(me.get("username") or "")
            if username.lower() != settings.MAX_BOT_USERNAME.lower():
                raise CommandError("MAX_BOT_USERNAME не совпадает с ботом MAX.")
            url = settings.SITE_URL + reverse("market:max-webhook")
            messengers.max_call(
                "subscribe_webhook", url=url, secret=messengers.max_webhook_secret(),
                update_types=["bot_started", "message_created", "message_callback", "bot_stopped"],
            )
            messengers.max_call(
                "set_commands", commands=[
                    {"name": "start", "description": "Подключить уведомления"},
                    {"name": "stop", "description": "Отключить уведомления"},
                    {"name": "add", "description": "Добавить букет в магазин"},
                    {"name": "cancel", "description": "Отменить добавление букета"},
                ],
            )
        except messengers.MessengerError:
            # Transport exceptions must never expose the token or webhook secret.
            raise CommandError("MAX: не удалось настроить бота. Проверьте настройки и доступность API.") from None
        self.stdout.write(f"MAX: вебхук @{username} → {url}")
