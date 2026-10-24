from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from market.loyalty import claim, progress
from market.models import CustomerPhone
from market.referrals import personal_link, qualify_part
from tests.test_loyalty import CONTACT, NOW, choice, purchase
from tests.test_referrals import invite

pytestmark = pytest.mark.django_db


def test_nine_purchases_and_referral_share_ten_stamps_and_correct_limit(
    buyer, friend, listing
):
    stock = listing.stockrecord
    stock.price = 3000
    stock.save()
    purchase(buyer, listing, 9)
    invite(buyer, friend)
    stock.price = 2000
    stock.save()
    qualify_part(purchase(friend, listing, 2))
    state = progress(buyer)
    assert (state["stamps"], state["available"], state["referral_stamps"]) == (10, 1, 1)
    assert state["limit"] == Decimal("2900.00")
    reward = claim(
        buyer,
        listing.pk,
        "standard",
        choice(listing.shop),
        None,
        CONTACT,
        sequence=1,
        now=NOW,
    )
    assert reward.limit == 2900
    assert reward.paid_total == 27000
    assert reward.purchases.get().units == 9
    assert reward.referral_uses.get().friend == friend
    assert progress(buyer)["stamps"] == 0
    assert (
        claim(
            buyer,
            listing.pk,
            "standard",
            choice(listing.shop),
            None,
            CONTACT,
            sequence=1,
            now=NOW,
        ).pk
        == reward.pk
    )


def test_ten_referrals_allow_gift_without_own_purchases(buyer, listing):
    for i in range(10):
        invited = get_user_model().objects.create_user(
            f"invited-{i}", f"invited-{i}@example.test"
        )
        CustomerPhone.objects.create(
            user=invited, phone=f"+7999000{i:04}", verified=True
        )
        invite(buyer, invited)
        qualify_part(purchase(invited, listing, 1))
    assert progress(buyer)["limit"] == 2500
    reward = claim(
        buyer,
        listing.pk,
        "standard",
        choice(listing.shop),
        None,
        CONTACT,
        sequence=1,
        now=NOW,
    )
    assert reward.paid_total == 0
    assert not reward.purchases.exists()
    assert reward.referral_uses.count() == 10
    assert progress(buyer)["available"] == 0


def test_refund_removes_unspent_credit_without_changing_spent_gift(
    buyer, friend, listing
):
    from market.payments import refund_online_payment

    invite(buyer, friend)
    part = purchase(friend, listing, 1)
    part.payment_method = "online"
    part.save()
    qualify_part(part)
    assert progress(buyer)["stamps"] == 1
    refund_online_payment(part)
    assert progress(buyer)["stamps"] == 0
    part.payment_status = "paid"
    part.save()
    purchase(buyer, listing, 9)
    reward = claim(
        buyer,
        listing.pk,
        "standard",
        choice(listing.shop),
        None,
        CONTACT,
        sequence=1,
        now=NOW,
    )
    refund_online_payment(part)
    reward.refresh_from_db()
    assert reward.limit == 2500 and reward.referral_uses.count() == 1
    assert progress(buyer)["stamps"] == 0


@pytest.mark.parametrize("page_name", ["market:account", "market:loyalty"])
def test_customer_sees_personal_link_and_copy_without_friend_data(
    client, buyer, friend, listing, page_name
):
    invite(buyer, friend)
    qualify_part(purchase(friend, listing, 1))
    client.force_login(buyer)
    page = client.get(reverse(page_name))
    html = page.content.decode()
    assert page.status_code == 200
    assert (
        "https://flowers.test"
        + reverse("market:referral", args=[personal_link(buyer).code])
        in html
    )
    assert "Скопировать ссылку" in html
    assert "referral.js" in html
    assert "первой оплаченной и полученной покупки" in html
    assert "Осталось 9 цветочков" in html
    assert "+79990000101" not in html
    assert friend.username not in html
    assert friend.email not in html
