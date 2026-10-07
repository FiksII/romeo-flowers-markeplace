import pytest


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/"])
def test_public_pages_need_no_address(client, path):
    response = client.get(path)
    assert response.status_code == 200
    content = response.content.decode()
    assert "Цветы от Ромео" in content
    assert "Куда доставить" in content
    assert 'required name="address"' not in content
