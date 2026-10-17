from django.core.management.base import BaseCommand

from market.messengers import deliver_pending


class Command(BaseCommand):
    help = "Отправить уведомления о заказах, которые не ушли сразу."

    def handle(self, *args, **options):
        sent, tried = deliver_pending()
        self.stdout.write(f"Отправлено уведомлений: {sent} из {tried}")
