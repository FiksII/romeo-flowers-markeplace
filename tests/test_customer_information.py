import pytest

pytestmark = pytest.mark.django_db


def test_delivery_and_payment_are_public_and_linked_from_the_storefront(
    client, listing
):
    response = client.get("/delivery/")
    assert response.status_code == 200
    html = response.content.decode()
    for heading in (
        "Цены и состав заказа",
        "Сроки и условия доставки",
        "Самовывоз",
        "Отмена заказа и обращения",
    ):
        assert heading in html
    assert "Документ готовится" not in html
    assert client.head("/delivery/").status_code == 200
    for path in ("/", f"/products/{listing.pk}/"):
        assert 'href="/delivery/"' in client.get(path).content.decode()


def test_information_describes_only_available_payment_methods(client, settings):
    settings.MARKET_ONLINE_PAYMENTS = False
    html = client.get("/delivery/").content.decode()
    assert "Наличными при получении" in html
    assert "защищённую платёжную страницу Т-Банка" not in html

    settings.MARKET_ONLINE_PAYMENTS = True
    settings.MARKET_RESERVATION_MINUTES = 45
    html = client.get("/delivery/").content.decode()
    assert "защищённую платёжную страницу Т-Банка" in html
    assert "45 минут" in html


@pytest.mark.parametrize("path", ("/offer/", "/requisites/"))
def test_published_documents_disclose_complete_operator_details(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "X-Robots-Tag" not in response
    html = response.content.decode()
    for text in (
        "ИП Алиева Ольга Игоревна",
        "772157821549",
        "326774600654754",
        "Московская область, Одинцовский городской округ, Звенигород, район Восточный, 3-й микрорайон, дом 17",
        'href="mailto:info@romeo-flowers.ru"',
        'href="tel:+79691892222"',
    ):
        assert text in html
    assert "Проект для ознакомления" not in html
    assert "Проект публичной оферты" not in html
    assert "После публикации действующей" not in html
    assert "Документ готовится" not in html
    assert client.head(path).status_code == 200


def test_offer_distinguishes_sellers_and_operator_and_explains_returns(client):
    html = client.get("/offer/").content.decode()
    for text in (
        "Продавцом товара является магазин",
        "Ромео выступает посредником",
        "7 дней",
        "10 дней",
        "товар ненадлежащего качества",
        "не ограничивают предусмотренные законом права",
    ):
        assert text in html
    assert client.get("/terms/").url == "/offer/"


def test_seller_identity_is_public_but_bank_details_remain_private(client, shop):
    shop.legal_name = "ООО «Тестовый продавец»"
    shop.inn = "7701234567"
    shop.bank_account = "40802810000000000000"
    shop.bank_bik = "044525225"
    shop.save()
    html = client.get(f"/shops/{shop.slug}/").content.decode()
    assert shop.legal_name in html
    assert shop.inn in html
    assert shop.bank_account not in html
    assert shop.bank_bik not in html


def test_checkout_links_to_seller_and_offer_before_order_creation(
    client, owner, listing
):
    client.force_login(owner)
    client.post(f"/basket/add/{listing.pk}/")
    response = client.get("/checkout/")
    assert response.status_code == 200
    html = response.content.decode()
    assert f'href="/shops/{listing.shop.slug}/"' in html
    assert 'href="/offer/"' in html
    assert "Проект публичной оферты" not in html
    assert 'href="/delivery/"' in html
    assert "Нажимая «Создать заказ», вы принимаете" in html


def test_approved_support_contacts_are_public_on_the_storefront(client):
    html = client.get("/").content.decode()
    assert 'href="mailto:info@romeo-flowers.ru"' in html
    assert 'href="tel:+79691892222"' in html
