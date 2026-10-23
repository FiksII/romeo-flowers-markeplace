from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection

from market.forms import ShopForm
from market.models import Settlement

pytestmark = pytest.mark.django_db


def test_shop_form_offers_all_cities_of_moscow_and_oblast():
    choices = ShopForm().fields['settlement'].queryset
    assert choices.filter(region='50').count() == 74
    for name, region in [('Одинцово', '50'), ('Звенигород', '50'),
                         ('Зеленоград', '77'), ('Троицк', '77'),
                         ('Московский', '77'), ('Щербинка', '77')]:
        assert choices.filter(name=name, region=region).exists()


def test_directory_migration_is_additive_and_preserves_shop_links(shop):
    migration = import_module('market.migrations.0023_settlement_directory')
    before = shop.settlement_id
    editor = SimpleNamespace(connection=connection)
    migration.seed_cities(apps, editor)
    migration.seed_cities(apps, editor)
    shop.refresh_from_db()
    assert shop.settlement_id == before
    assert Settlement.objects.filter(region='77', name='Москва').count() == 1
    assert Settlement.objects.filter(region='50').count() == 74
