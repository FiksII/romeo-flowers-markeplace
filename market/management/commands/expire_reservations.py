from django.core.management.base import BaseCommand

from market.orders import expire_unpaid_orders


class Command(BaseCommand):
    help = "Освободить остатки неоплаченных заказов после истечения резерва."

    def handle(self, *args, **options):
        count = expire_unpaid_orders(limit=1000)
        self.stdout.write(f"Отменено неоплаченных частей: {count}")
