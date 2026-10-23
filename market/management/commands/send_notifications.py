from django.core.management.base import BaseCommand

from market.messengers import deliver_pending


class Command(BaseCommand):
    help = "Отправить уведомления о заказах, которые не ушли сразу."

    def handle(self, *args, **options):
        sent, tried = deliver_pending()
        self.stdout.write(f"Отправлено уведомлений: {sent} из {tried}")
        from market.max_bot import deliver_pending as deliver_max_events
        sent, tried = deliver_max_events()
        self.stdout.write(f"Обработано событий MAX: {sent} из {tried}")
