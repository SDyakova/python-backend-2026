from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from shop_api.main import app

client = TestClient(app)


# --- ITEMS: Негативные сценарии (404, 304) ---


def test_get_item_not_found() -> None:
    response = client.get("/item/999999")
    assert response.status_code == HTTPStatus.NOT_FOUND


def test_put_item_not_found() -> None:
    response = client.put("/item/999999", json={"name": "x", "price": 10.0})
    assert response.status_code == HTTPStatus.NOT_FOUND


def test_delete_item_not_found() -> None:
    response = client.delete("/item/999999")
    assert response.status_code == HTTPStatus.OK


def test_patch_item_not_found() -> None:
    response = client.patch("/item/999999", json={"name": "x"})
    assert response.status_code == HTTPStatus.NOT_MODIFIED


# --- ITEMS: Фильтры и удаленные ---


def test_get_items_with_show_deleted() -> None:
    item = client.post("/item", json={"name": "deleted", "price": 10.0}).json()
    client.delete(f"/item/{item['id']}")

    # Передаем limit=100, чтобы точно получить все элементы
    response = client.get("/item", params={"limit": 100})
    assert all(i["id"] != item["id"] for i in response.json())

    # С флагом show_deleted=True — есть
    response = client.get("/item", params={"show_deleted": True, "limit": 100})
    assert any(i["id"] == item["id"] for i in response.json())


def test_get_items_filter_price() -> None:
    client.post("/item", json={"name": "cheap", "price": 5.0})
    client.post("/item", json={"name": "expensive", "price": 500.0})

    response = client.get("/item", params={"min_price": 100.0, "limit": 100})
    assert all(i["price"] >= 100.0 for i in response.json())

    response = client.get("/item", params={"max_price": 50.0, "limit": 100})
    assert all(i["price"] <= 50.0 for i in response.json())


# --- CARTS: Негативные сценарии ---


def test_get_cart_not_found() -> None:
    response = client.get("/cart/999999")
    assert response.status_code == HTTPStatus.NOT_FOUND


def test_add_to_cart_not_found() -> None:
    response = client.post("/cart/999999/add/1")
    assert response.status_code == HTTPStatus.NOT_FOUND

    cart_id = client.post("/cart").json()["id"]

    response = client.post(f"/cart/{cart_id}/add/999999")
    assert response.status_code == HTTPStatus.NOT_FOUND

    item = client.post("/item", json={"name": "del", "price": 10.0}).json()
    client.delete(f"/item/{item['id']}")
    response = client.post(f"/cart/{cart_id}/add/{item['id']}")
    assert response.status_code == HTTPStatus.NOT_FOUND


# --- CARTS: Фильтры и логика ---


def test_cart_filters() -> None:
    item1 = client.post("/item", json={"name": "i1", "price": 10.0}).json()
    item2 = client.post("/item", json={"name": "i2", "price": 100.0}).json()

    cart1 = client.post("/cart").json()["id"]
    cart2 = client.post("/cart").json()["id"]

    client.post(f"/cart/{cart1}/add/{item1['id']}")
    client.post(f"/cart/{cart2}/add/{item2['id']}")

    response = client.get("/cart", params={"min_price": 50.0, "limit": 100})
    assert all(c["price"] >= 50.0 for c in response.json())

    response = client.get("/cart", params={"max_price": 50.0, "limit": 100})
    assert all(c["price"] <= 50.0 for c in response.json())

    response = client.get("/cart", params={"min_quantity": 1, "limit": 100})
    assert all(
        sum(i["quantity"] for i in c["items"]) >= 1 for c in response.json()
    )

    response = client.get("/cart", params={"max_quantity": 0, "limit": 100})
    assert all(
        sum(i["quantity"] for i in c["items"]) <= 0 for c in response.json()
    )


def test_add_same_item_increments_quantity() -> None:
    item = client.post("/item", json={"name": "inc", "price": 10.0}).json()
    cart = client.post("/cart").json()["id"]

    client.post(f"/cart/{cart}/add/{item['id']}")
    client.post(f"/cart/{cart}/add/{item['id']}")

    response = client.get(f"/cart/{cart}")
    assert response.json()["items"][0]["quantity"] == 2


# --- MAIN: Покрытие lifespan ---


def test_lifespan() -> None:
    """Покрываем lifespan в main.py (он не вызывается, если TestClient создан без with)"""
    with TestClient(app) as test_client:
        response = test_client.get("/item")
        assert response.status_code == HTTPStatus.OK


def test_cart_with_missing_item() -> None:
    from shop_api.store.database import async_session_maker
    from shop_api.store.models import ItemORM
    import asyncio

    cart_id = client.post("/cart").json()["id"]
    item = client.post("/item", json={"name": "missing", "price": 10.0}).json()
    client.post(f"/cart/{cart_id}/add/{item['id']}")

    # Физически удаляем товар из БД (в обход API)
    async def hard_delete():
        async with async_session_maker() as session:
            item_orm = await session.get(ItemORM, item["id"])
            if item_orm:
                await session.delete(item_orm)
                await session.commit()

    asyncio.run(hard_delete())

    # Теперь корзина содержит ID несуществующего товара
    response = client.get(f"/cart/{cart_id}")
    assert response.status_code == HTTPStatus.OK
    assert len(response.json()["items"]) == 0
